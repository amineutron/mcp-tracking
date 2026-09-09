"""
Dashboard TUI pour le suivi MCP Tracking.
Lance avec : python server.py --ui [--filter <template>]

Le rendu est generique et pilote par la section `display` de chaque template
(templates.py / ~/.config/tracking/templates.json) : metrics, info, hide,
pipeline, layout. Les metriques universelles (elapsed, rate, eta, stale) sont
calculees par metrics.py et affichees pour toutes les sessions.
"""
from datetime import datetime
from typing import List, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, ListView, ListItem, Static

from rich.console import Group
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

import metrics
import mutations
import storage
from models import LogEntry, TrackingItem, TrackingSession
from storage import load_from_file
from templates import TEMPLATES, PHASE_ALIASES, get_display

BAR_WIDTH = 30
LOG_LINES = 5

STATUS_COLORS = {"running": "cyan", "done": "green", "error": "red", "paused": "yellow"}

TEMPLATE_COLORS = {
    "download":       "bright_blue",
    "machine":        "magenta",
    "free":           "white",
    "lyra_task":      "bright_cyan",
    "movie":          "yellow",
    "series_episode": "yellow",
    "series_season":  "bright_yellow",
    "subtitles":      "bright_magenta",
}

ITEM_ICON = {
    "pending": ("[ ]", "dim"),
    "running": ("[>]", "cyan"),
    "done":    ("[ok]", "green"),
    "error":   ("[!]", "red"),
}

FILTER_ALL    = "all"
FILTER_ERRORS = "errors"


def _esc(value) -> str:
    """Echappe les crochets pour le markup Rich."""
    return str(value).replace("[", "\\[")


# ---------------------------------------------------------------------------
# Helpers de rendu Rich
# ---------------------------------------------------------------------------

def _bar(processed: float, total: float) -> str:
    pct = min(processed / total, 1.0) if total > 0 else 0.0
    filled = int(BAR_WIDTH * pct)
    if filled >= BAR_WIDTH:
        return "=" * BAR_WIDTH
    return "=" * filled + ">" + " " * (BAR_WIDTH - filled - 1)


def _ts(entry: LogEntry) -> str:
    return (entry.timestamp.strftime("%H:%M:%S")
            if isinstance(entry.timestamp, datetime) else str(entry.timestamp)[:8])


def _has_error(s: TrackingSession) -> bool:
    return (s.status.value == "error"
            or any(i.status.value == "error" for i in s.items)
            or any(e.level.value == "error" for e in s.logs))


def _is_stalled(s: TrackingSession) -> bool:
    seeds = s.extra.get("seeds")
    if seeds is None:
        return False
    try:
        return int(seeds) == 0
    except (ValueError, TypeError):
        return str(seeds) == "0"


def _render_pipeline(phase: str, stages: List[str], has_dv: bool) -> Text:
    visible = [st for st in stages if st != "dv-conv" or has_dv]
    current_key = PHASE_ALIASES.get(phase, phase)
    current = visible.index(current_key) if current_key in visible else 0
    parts = []
    for i, label in enumerate(visible):
        if i < current:
            parts.append(f"[green]\\[ {label} ][/green]")
        elif i == current:
            parts.append(f"[bold cyan]\\[ {label} ][/bold cyan]")
        else:
            parts.append(f"[dim]\\[ {label} ][/dim]")
    return Text.from_markup("  " + " [dim]-->[/dim] ".join(parts))


def _render_item_grid(items: List[TrackingItem]) -> Text:
    per_row = 16
    lines, current = [], "  "
    for i, item in enumerate(items):
        label = f"E{i + 1:02d}"
        color = {"done": "green", "running": "bold cyan", "error": "red"}.get(
            item.status.value, "dim")
        current += f"[{color}]{label}[/{color}] "
        if (i + 1) % per_row == 0:
            lines.append(current)
            current = "  "
    if current.strip():
        lines.append(current)
    return Text.from_markup("\n".join(lines))


def _render_item_line(item: TrackingItem) -> Text:
    icon, color = ITEM_ICON.get(item.status.value, ("[ ]", "white"))
    if item.processed is not None and item.total is not None:
        prog = f"  {item.processed}{item.unit} / {item.total}{item.unit}"
    elif item.total is not None:
        prog = f"  {item.total}{item.unit}"
    else:
        prog = ""
    dur = metrics.item_duration_seconds(item)
    dur_str = f"  {metrics.format_duration(dur)}" if dur is not None else ""
    note = f"  ({_esc(item.note)})" if item.note else ""
    return Text.from_markup(
        f"  [{color}]{_esc(icon)}[/{color}]  [{color}]{_esc(item.name):<32}[/{color}]"
        f"[dim]{prog}{dur_str}{note}[/dim]"
    )


def _render_extras(s: TrackingSession) -> List[Text]:
    """Rendu generique des champs extra selon display du template."""
    disp = get_display(s.template)
    ex = s.extra
    parts: List[Text] = []

    if disp["pipeline"] and ex.get("phase"):
        parts.append(_render_pipeline(str(ex["phase"]), disp["pipeline"], bool(ex.get("dv"))))

    info = [str(ex[k]) for k in disp["info"] if ex.get(k)]
    if info:
        parts.append(Text.from_markup("  [dim]" + _esc("  |  ".join(info)) + "[/dim]"))

    shown = set(disp["info"]) | set(disp["hide"]) | set(disp["metrics"])
    metric_keys = [k for k in disp["metrics"] if ex.get(k) not in (None, "")]
    other_keys = [k for k in ex if k not in shown and ex.get(k) not in (None, "")]
    cells = [f"[yellow]{_esc(k)}[/yellow]: {_esc(ex[k])}" for k in metric_keys + other_keys]
    if cells:
        parts.append(Text.from_markup("  " + "  [dim]|[/dim]  ".join(cells)))
    return parts


def _render_metrics_line(s: TrackingSession, m: dict) -> Text:
    cells = [f"[dim]elapsed[/dim] {m['elapsed_str']}"]
    if s.status.value == "running":
        cells.append(f"[dim]rate[/dim] {m['rate_str']}")
        cells.append(f"[dim]eta[/dim] {m['eta_str']}")
    if m["stale"]:
        cells.append(f"[bold red]STALE[/bold red] [dim]({metrics.format_duration(m['idle_seconds'])} sans update)[/dim]")
    return Text.from_markup("  " + "   ".join(cells))


def _render_logs_table(s: TrackingSession) -> Table:
    error_items = [it for it in s.items if it.status.value == "error"]
    error_logs = [e for e in s.logs if e.level.value == "error"][-LOG_LINES:]

    def _log_line(e: LogEntry) -> Text:
        color = {"warn": "yellow", "error": "red"}.get(e.level.value, "dim")
        return Text.from_markup(f"  [{color}]{_ts(e)}  {_esc(e.message)}[/{color}]")

    empty = Text.from_markup("  [dim]--[/dim]")
    log_lines = [_log_line(e) for e in s.logs[-LOG_LINES:]]
    err_lines = [Text.from_markup(f"  [red]\\[!] {_esc(it.name)}[/red]") for it in error_items]
    err_lines += [Text.from_markup(f"  [red]{_ts(e)}  {_esc(e.message)}[/red]") for e in error_logs]
    log_lines += [empty] * (LOG_LINES - len(log_lines))
    err_lines = (err_lines + [empty] * LOG_LINES)[:LOG_LINES]

    err_color = "red" if (error_items or error_logs) else "dim"
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(ratio=3)
    table.add_column(ratio=2)
    table.add_row(Text.from_markup("[dim]Logs[/dim]"),
                  Text.from_markup(f"[{err_color}]Erreurs[/{err_color}]"))
    for a, b in zip(log_lines, err_lines):
        table.add_row(a, b)
    return table


def _render_session(s: TrackingSession, collapsed: bool = False) -> Group:
    m = metrics.compute(s)
    bar_color = STATUS_COLORS.get(s.status.value, "white")
    badge_color = TEMPLATE_COLORS.get(s.template, "white")
    arrow = ">" if collapsed else "v"
    stale_badge = "  [bold red]STALE[/bold red]" if m["stale"] else ""

    title = (f"[dim]{arrow}[/dim] [bold {badge_color}]\\[{s.template.upper()}][/bold {badge_color}]"
             f"  [bold]{_esc(s.name)}[/bold]"
             + (f"  [dim]id:{s.id}  ({s.status.value})[/dim]" if not collapsed else "")
             + stale_badge)
    title_text = Text.from_markup(title)

    bar = _bar(s.processed, s.total)
    if _is_stalled(s):
        tail = "  [dim red]seeds: 0 -- en attente[/dim red]"
    elif collapsed:
        tail = f"  [dim]eta {m['eta_str']}[/dim]" if s.status.value == "running" else ""
    else:
        tail = f"  [dim]{s.processed}{_esc(s.unit)} / {s.total}{_esc(s.unit)}[/dim]"
    bar_text = Text.from_markup(
        f"  [{bar_color}]\\[{bar}][/{bar_color}] [bold {bar_color}]{m['percent']}%[/bold {bar_color}]{tail}")

    if collapsed:
        return Group(title_text, bar_text)

    parts: list = [title_text, bar_text, _render_metrics_line(s, m)]
    parts.extend(_render_extras(s))

    if s.items:
        parts.append(Text(""))
        if get_display(s.template)["layout"] == "grid":
            parts.append(_render_item_grid(s.items))
        else:
            parts.extend(_render_item_line(it) for it in s.items)

    parts.append(Text(""))
    parts.append(_render_logs_table(s))
    return Group(*parts)


# ---------------------------------------------------------------------------
# Filtrage et tri
# ---------------------------------------------------------------------------

def _compute_filters(sessions: List[TrackingSession]) -> List[str]:
    filters = [FILTER_ALL] + sorted(TEMPLATES.keys())
    if any(_has_error(s) for s in sessions):
        filters.append(FILTER_ERRORS)
    return filters


def _apply_filter(sessions: List[TrackingSession], active: str) -> List[TrackingSession]:
    if active == FILTER_ALL:
        return sessions
    if active == FILTER_ERRORS:
        return [s for s in sessions if _has_error(s)]
    return [s for s in sessions if s.template == active]


def _sort_sessions(sessions: List[TrackingSession]) -> List[TrackingSession]:
    return sorted(sessions, key=lambda s: (
        1 if _is_stalled(s) else 0,
        0 if s.status.value == "running" else 1,
        s.created_at,
    ))


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class SessionCard(Static):
    """Carte de session. Cliquer bascule plie/deplie."""

    DEFAULT_CSS = """
    SessionCard { padding: 0 0; }
    SessionCard:hover { background: $boost; }
    """

    def __init__(self, session: TrackingSession, collapsed: bool = False) -> None:
        self._sid = session.id
        super().__init__(_render_session(session, collapsed=collapsed), id=f"card-{session.id}")

    def on_click(self) -> None:
        self.app.toggle_collapsed(self._sid)  # type: ignore


class FilterSelectScreen(ModalScreen):
    CSS = """
    FilterSelectScreen { align: center middle; }
    #filter-dialog { width: 40; height: auto; max-height: 20;
        border: thick $primary; background: $surface; padding: 1 2; }
    #filter-dialog Label { width: 100%; text-align: center; margin-bottom: 1; }
    ListView { height: auto; max-height: 14; border: none; }
    """
    BINDINGS = [Binding("escape", "cancel", "Annuler", show=True)]

    def __init__(self, filters: List[str], active: str) -> None:
        super().__init__()
        self._filters = filters
        self._active = active

    def compose(self) -> ComposeResult:
        with Center(id="filter-dialog"):
            yield Label("Choisir un filtre")
            yield ListView(*[
                ListItem(Label((">" if f == self._active else " ") + " " + f), id=f"filter-{f}")
                for f in self._filters
            ], id="filter-list")

    def on_mount(self) -> None:
        lv = self.query_one("#filter-list", ListView)
        lv.focus()
        if self._active in self._filters:
            lv.index = self._filters.index(self._active)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = self.query_one("#filter-list", ListView).index
        if idx is not None and 0 <= idx < len(self._filters):
            self.dismiss(self._filters[idx])

    def action_cancel(self) -> None:
        self.dismiss(None)


class SessionActionScreen(ModalScreen):
    CSS = """
    SessionActionScreen { align: center middle; }
    #dialog { width: 60; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    #dialog Label { width: 100%; text-align: center; margin-bottom: 1; }
    #session-input { width: 100%; margin-bottom: 1; }
    #buttons { width: 100%; height: auto; layout: horizontal; align: center middle; }
    Button { margin: 0 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Annuler", show=True)]

    def __init__(self, action: str) -> None:
        super().__init__()
        self._action = action

    def compose(self) -> ComposeResult:
        if self._action == "stop":
            label, btn_label, variant = "[S] Arret propre (SIGTERM si pid)", "Stop", "warning"
        else:
            label, btn_label, variant = "[K] Kill force (SIGKILL si pid)", "Kill", "error"
        with Center(id="dialog"):
            yield Label(label)
            yield Input(placeholder="ID de session (ex: a1b2c3d4)", id="session-input")
            with Center(id="buttons"):
                yield Button(btn_label, variant=variant, id="confirm")
                yield Button("Annuler", variant="default", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#session-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            sid = self.query_one("#session-input", Input).value.strip()
            self.dismiss(sid or None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Application Textual
# ---------------------------------------------------------------------------

class TrackingDashboard(App):
    TITLE = "MCP Tracking -- Dashboard"

    CSS = """
    Screen { background: $surface; }
    VerticalScroll { height: 1fr; padding: 0 1; }
    #cards { width: 100%; height: auto; padding: 1 0; }
    .sep { color: $panel; height: 1; }
    """

    BINDINGS = [
        Binding("q",     "quit",          "Quitter",            show=True),
        Binding("space", "filter_menu",   "Filtres",            show=True),
        Binding("e",     "errors_filter", "Erreurs",            show=True),
        Binding("r",     "refresh",       "Refresh",            show=True),
        Binding("c",     "clean",         "Nettoyer",           show=True),
        Binding("s",     "stop_session",  "Stop (propre)",      show=True),
        Binding("k",     "kill_session",  "Kill (force)",       show=True),
        Binding("a",     "toggle_all",    "Tout plier/deplier", show=True),
    ]

    def __init__(self, initial_filter: str = FILTER_ALL) -> None:
        super().__init__()
        self._active_filter = initial_filter
        self._collapsed: set = set()
        self._stalled_seen: set = set()
        self._prev_order: list = []
        self._refreshing = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll():
            yield Vertical(id="cards")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(1.0, self.do_refresh)
        self.call_later(self.do_refresh)

    def toggle_collapsed(self, session_id: str) -> None:
        if session_id in self._collapsed:
            self._collapsed.discard(session_id)
        else:
            self._collapsed.add(session_id)
        self._update_cards_content()

    # -- Refresh --

    async def do_refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            await self._do_refresh_inner()
        finally:
            self._refreshing = False

    async def _do_refresh_inner(self) -> None:
        sessions = load_from_file()
        _compute_filters(sessions)

        ids = {s.id for s in sessions}
        self._collapsed &= ids
        self._stalled_seen &= ids

        # Auto-plier les trackers media qui passent en attente (seeds=0)
        media = {t for t in TEMPLATES if get_display(t)["pipeline"]}
        currently_stalled = {s.id for s in sessions if s.template in media and _is_stalled(s)}
        self._collapsed |= currently_stalled - self._stalled_seen
        self._stalled_seen = currently_stalled

        self._update_subtitle(sessions)

        ordered = _sort_sessions(_apply_filter(sessions, self._active_filter))
        new_order = [s.id for s in ordered]
        if new_order != self._prev_order:
            await self._rebuild_dom(ordered)
            self._prev_order = new_order
        else:
            self._update_cards_content(ordered)

    async def _rebuild_dom(self, ordered: List[TrackingSession]) -> None:
        container = self.query_one("#cards", Vertical)
        await container.remove_children()
        if not ordered:
            msg = ("\n[dim]Aucune session active.[/dim]\n\n"
                   "[dim]Utilisez tracking_create() via MCP ou POST /sessions.[/dim]"
                   if not load_from_file() else
                   f"\n[dim]Aucune session pour le filtre '[bold]{self._active_filter}[/bold]'.[/dim]")
            await container.mount(Static(msg))
            return
        for i, session in enumerate(ordered):
            await container.mount(SessionCard(session, collapsed=session.id in self._collapsed))
            if i < len(ordered) - 1:
                await container.mount(Static(Rule(style="dim"), classes="sep"))

    def _update_cards_content(self, ordered: Optional[List[TrackingSession]] = None) -> None:
        if ordered is None:
            ordered = _sort_sessions(_apply_filter(load_from_file(), self._active_filter))
        for session in ordered:
            try:
                card = self.query_one(f"#card-{session.id}", SessionCard)
                card.update(_render_session(session, collapsed=session.id in self._collapsed))
            except Exception:
                self._prev_order = []
                self.call_later(self.do_refresh)
                return

    def _update_subtitle(self, sessions: List[TrackingSession]) -> None:
        total = len(sessions)
        running = sum(1 for s in sessions if s.status.value == "running")
        stale = sum(1 for s in sessions if metrics.is_stale(s))
        visible = len(_apply_filter(sessions, self._active_filter))
        label = "tout" if self._active_filter == FILTER_ALL else self._active_filter
        count = str(total) if self._active_filter == FILTER_ALL else f"{visible}/{total}"
        stale_str = f"  |  {stale} stale" if stale else ""
        self.sub_title = f"filtre: {label}  |  {count} session(s)  |  {running} running{stale_str}"

    # -- Actions clavier --

    def action_filter_menu(self) -> None:
        filters = _compute_filters(load_from_file())

        def _on_dismiss(chosen: Optional[str]) -> None:
            if chosen is not None:
                self._active_filter = chosen
            self.call_later(self.do_refresh)

        self.push_screen(FilterSelectScreen(filters, self._active_filter), _on_dismiss)

    def action_errors_filter(self) -> None:
        self._active_filter = FILTER_ALL if self._active_filter == FILTER_ERRORS else FILTER_ERRORS
        self.call_later(self.do_refresh)

    def action_refresh(self) -> None:
        self.call_later(self.do_refresh)

    def action_quit(self) -> None:
        self.exit()

    def action_toggle_all(self) -> None:
        ids = {s.id for s in _apply_filter(load_from_file(), self._active_filter)}
        if ids.issubset(self._collapsed):
            self._collapsed -= ids
        else:
            self._collapsed |= ids
        self._update_cards_content()

    def action_clean(self) -> None:
        visible = _apply_filter(load_from_file(), self._active_filter)
        to_clean = [s.id for s in visible if s.status.value in ("done", "error")]
        if not to_clean:
            self.notify("Rien a nettoyer.", severity="information")
            return
        n = storage.delete_many(to_clean)
        self.notify(f"{n} session(s) supprimee(s).", severity="warning")
        self.call_later(self.do_refresh)

    async def action_stop_session(self) -> None:
        sid = await self.push_screen_wait(SessionActionScreen("stop"))
        if not sid:
            return
        notes: list = []
        session = storage.modify(sid, lambda s: notes.append(
            mutations.stop(s, "Arretee manuellement depuis le dashboard")))
        if session is None:
            self.notify(f"Session introuvable: {sid}", severity="error")
            return
        suffix = f" ({notes[0]})" if notes and notes[0] else ""
        self.notify(f"Session '{session.name}' mise en pause{suffix}.", severity="warning")
        await self.do_refresh()

    async def action_kill_session(self) -> None:
        sid = await self.push_screen_wait(SessionActionScreen("kill"))
        if not sid:
            return
        session = storage.get(sid)
        if session is None:
            self.notify(f"Session introuvable: {sid}", severity="error")
            return
        note = mutations.kill_process(session)
        storage.delete(sid)
        suffix = f" ({note})" if note else ""
        self.notify(f"Session '{session.name}' supprimee en force{suffix}.", severity="error")
        await self.do_refresh()
