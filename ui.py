"""
Dashboard TUI pour le suivi MCP Tracking.
Lance avec : python server.py --ui [--filter <template>]
"""
import re
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

from models import LogEntry, SessionStatus, TrackingSession
from storage import STORAGE_FILE, load_from_file

BAR_WIDTH = 30

STATUS_COLORS = {
    "running": "cyan",
    "done":    "green",
    "error":   "red",
    "paused":  "yellow",
}

TEMPLATE_COLORS = {
    "download":       "bright_blue",
    "machine":        "magenta",
    "free":           "white",
    "lyra_task":      "bright_cyan",
    "movie":          "yellow",
    "series_episode": "yellow",
    "series_season":  "bright_yellow",
}

ITEM_ICON = {
    "pending": ("[ ]", "dim"),
    "running": ("[>]", "cyan"),
    "done":    ("[ok]","green"),
    "error":   ("[!]", "red"),
}

PIPELINE_PHASE_ORDER: dict = {
    "download":             0,
    "telechargement":       0,
    "en attente (seeds)":  0,
    "recuperation metadata": 0,
    "verification":         0,
    "en file":              0,
    "allocation disque":    0,
    "en pause":             0,
    "detection DV":         0,
    "subtitles":            1,
    "sous-titres":          1,
    "sous-titres manquants": 1,
    "dv-conv":              2,
    "convert":              2,
}

ERROR_KEYWORDS = ("erreur", "error", "failed", "echec", "exception", "critical")

FILTER_ALL    = "all"
FILTER_ERRORS = "errors"


# ---------------------------------------------------------------------------
# Helpers de rendu Rich
# ---------------------------------------------------------------------------

def _render_pipeline(phase: str, has_dv: bool) -> Text:
    stages = [("download", "download"), ("subtitles", "sous-titres")]
    if has_dv:
        stages.append(("dv-conv", "dv-conv"))
    current = PIPELINE_PHASE_ORDER.get(phase, 0)
    parts = []
    for i, (_key, label) in enumerate(stages):
        if i < current:
            parts.append(f"[green][ {label} ][/green]")
        elif i == current:
            parts.append(f"[bold cyan][ {label} ][/bold cyan]")
        else:
            parts.append(f"[dim][ {label} ][/dim]")
    arrow = " [dim]-->[/dim] "
    return Text.from_markup("  " + arrow.join(parts))


def _render_episode_grid(items) -> Text:
    PER_ROW = 16
    lines = []
    current_line = "  "
    for i, item in enumerate(items):
        ep_label = f"E{i + 1:02d}"
        status = item.status.value
        if status == "done":
            cell = f"[green]{ep_label}[/green]"
        elif status == "running":
            cell = f"[bold cyan]{ep_label}[/bold cyan]"
        elif status == "error":
            cell = f"[red]{ep_label}[/red]"
        else:
            cell = f"[dim]{ep_label}[/dim]"
        current_line += cell + " "
        if (i + 1) % PER_ROW == 0:
            lines.append(current_line)
            current_line = "  "
    if current_line.strip():
        lines.append(current_line)
    return Text.from_markup("\n".join(lines))


def _render_media_extras(s) -> list:
    parts = []
    ex = s.extra
    phase  = ex.get("phase", "download")
    has_dv = bool(ex.get("dv"))
    parts.append(_render_pipeline(phase, has_dv))
    info_cols = [ex.get("quality"), ex.get("codec"), ex.get("audio"), ex.get("source")]
    info_str = "  |  ".join(v for v in info_cols if v)
    if info_str:
        parts.append(Text.from_markup(f"  [dim]{info_str}[/dim]"))
    ops = []
    if ex.get("speed"): ops.append(f"[yellow]speed[/yellow]: {ex['speed']}")
    if ex.get("eta"):   ops.append(f"[yellow]eta[/yellow]: {ex['eta']}")
    if ex.get("seeds"): ops.append(f"[yellow]seeds[/yellow]: {ex['seeds']}")
    if ex.get("size"):  ops.append(f"[yellow]size[/yellow]: {ex['size']}")
    if ops:
        parts.append(Text.from_markup("  " + "  [dim]|[/dim]  ".join(ops)))
    if ex.get("release"):
        parts.append(Text.from_markup(f"  [dim]{ex['release']}[/dim]"))
    return parts


def _render_season_extras(s) -> list:
    parts = []
    ex = s.extra
    info_cols = [ex.get("season"), ex.get("quality"), ex.get("codec"),
                 ex.get("audio"), ex.get("source")]
    info_str = "  |  ".join(v for v in info_cols if v)
    if info_str:
        parts.append(Text.from_markup(f"  [yellow]{info_str}[/yellow]"))
    if ex.get("release"):
        parts.append(Text.from_markup(f"  [dim]{ex['release']}[/dim]"))
    parts.append(Text(""))
    if s.items:
        parts.append(_render_episode_grid(s.items))
    current_ep = ex.get("current_episode")
    phase      = ex.get("phase")
    has_dv     = bool(ex.get("dv"))
    if current_ep or phase:
        parts.append(Text(""))
        if current_ep:
            seeds = ex.get("seeds")
            seeds_str = f"  [dim]([yellow]seeds[/yellow]: {seeds})[/dim]" if seeds else ""
            parts.append(Text.from_markup(
                f"  [cyan]En cours :[/cyan] [bold]{current_ep}[/bold]{seeds_str}"
            ))
        if phase:
            parts.append(_render_pipeline(phase, has_dv))
    return parts


def _bar(processed: float, total: float) -> str:
    pct = min(processed / total, 1.0) if total > 0 else 0.0
    filled = int(BAR_WIDTH * pct)
    if filled >= BAR_WIDTH:
        return "=" * BAR_WIDTH
    return "=" * filled + ">" + " " * (BAR_WIDTH - filled - 1)


def _ts(entry: LogEntry) -> str:
    return (
        entry.timestamp.strftime("%H:%M:%S")
        if isinstance(entry.timestamp, datetime)
        else str(entry.timestamp)[:8]
    )


def _is_error_log(entry: LogEntry) -> bool:
    return any(kw in entry.message.lower() for kw in ERROR_KEYWORDS)


def _has_error(s: TrackingSession) -> bool:
    return (
        s.status.value == "error"
        or any(i.status.value == "error" for i in s.items)
        or any(_is_error_log(e) for e in s.logs)
    )


_DV_STEP_RE = re.compile(r"^\d+\.")

def _is_dv_step(item) -> bool:
    return bool(_DV_STEP_RE.match(item.name))


def _is_stalled(s: TrackingSession) -> bool:
    seeds = s.extra.get("seeds")
    if seeds is None:
        return False
    try:
        return int(seeds) == 0
    except (ValueError, TypeError):
        return str(seeds) == "0"


# ---------------------------------------------------------------------------
# Rendu d'une session (supporte plie/deplie)
# ---------------------------------------------------------------------------

def _render_session(s: TrackingSession, collapsed: bool = False) -> Group:
    pct         = min(s.processed / s.total, 1.0) if s.total > 0 else 0.0
    bar         = _bar(s.processed, s.total)
    bar_color   = STATUS_COLORS.get(s.status.value, "white")
    badge_color = TEMPLATE_COLORS.get(s.template, "white")
    # Fleche ASCII : > = plie, v = deplie
    arrow = ">" if collapsed else "v"

    # Ligne de titre
    if s.template == "series_episode":
        ep      = s.extra.get("episode", "")
        season  = s.extra.get("season", "")
        ep_title = s.extra.get("episode_title", "")
        ep_info = f"{season}{ep}" + (f" - {ep_title}" if ep_title else "")
        title_markup = (
            f"[dim]{arrow}[/dim] [bold {badge_color}][{s.template.upper()}][/bold {badge_color}]"
            f"  [bold]{s.name}[/bold]"
            + (f"  [dim]{ep_info}[/dim]" if ep_info else "")
            + (f"  [dim]id:{s.id}  ({s.status.value})[/dim]" if not collapsed else "")
        )
    else:
        title_markup = (
            f"[dim]{arrow}[/dim] [bold {badge_color}][{s.template.upper()}][/bold {badge_color}]"
            f"  [bold]{s.name}[/bold]"
            + (f"  [dim]id:{s.id}  ({s.status.value})[/dim]" if not collapsed else "")
        )

    title_text = Text.from_markup(title_markup)

    # Barre de progression
    if _is_stalled(s):
        bar_text = Text.from_markup(
            f"  [{bar_color}][{bar}][/{bar_color}]"
            f" [bold {bar_color}]{pct * 100:.1f}%[/bold {bar_color}]"
            f"  [dim red]seeds: 0 -- en attente[/dim red]"
        )
    else:
        bar_text = Text.from_markup(
            f"  [{bar_color}][{bar}][/{bar_color}]"
            f" [bold {bar_color}]{pct * 100:.1f}%[/bold {bar_color}]"
            + (f"  [dim]{s.processed}{s.unit} / {s.total}{s.unit}[/dim]" if not collapsed else "")
        )

    # Mode plie : seulement titre + barre
    if collapsed:
        return Group(title_text, bar_text)

    # Mode deplie : rendu complet
    parts: list = [title_text, bar_text]

    if s.template in ("movie", "series_episode"):
        parts.extend(_render_media_extras(s))
        visible_items = [it for it in s.items if not _is_dv_step(it)]
        if visible_items:
            parts.append(Text(""))
            for item in visible_items:
                icon, item_color = ITEM_ICON.get(item.status.value, ("[ ]", "white"))
                if item.processed is not None and item.total is not None:
                    prog = f"  {item.processed}{item.unit} / {item.total}{item.unit}"
                elif item.total is not None:
                    prog = f"  {item.total}{item.unit}"
                else:
                    prog = ""
                note = f"  ({item.note})" if item.note else ""
                parts.append(Text.from_markup(
                    f"  [{item_color}]{icon}[/{item_color}]"
                    f"  [{item_color}]{item.name:<32}[/{item_color}]"
                    f"[dim]{prog}{note}[/dim]"
                ))

    elif s.template == "series_season":
        parts.extend(_render_season_extras(s))

    else:
        if s.extra:
            extra_parts = [f"[yellow]{k}[/yellow]: {v}" for k, v in s.extra.items() if v is not None]
            if extra_parts:
                parts.append(Text.from_markup("  " + "  [dim]|[/dim]  ".join(extra_parts)))
        if s.items:
            parts.append(Text(""))
            for item in s.items:
                icon, item_color = ITEM_ICON.get(item.status.value, ("[ ]", "white"))
                if item.processed is not None and item.total is not None:
                    prog = f"  {item.processed}{item.unit} / {item.total}{item.unit}"
                elif item.total is not None:
                    prog = f"  {item.total}{item.unit}"
                else:
                    prog = ""
                note = f"  ({item.note})" if item.note else ""
                parts.append(Text.from_markup(
                    f"  [{item_color}]{icon}[/{item_color}]"
                    f"  [{item_color}]{item.name:<32}[/{item_color}]"
                    f"[dim]{prog}{note}[/dim]"
                ))

    # Logs / erreurs (5 lignes)
    parts.append(Text(""))
    log_entries = s.logs[-5:]
    error_items = [it for it in s.items if it.status.value == "error"]
    error_logs  = [e  for e  in s.logs  if _is_error_log(e)][-5:]

    def _log_line(entry: LogEntry) -> Text:
        return Text.from_markup(f"  [dim]{_ts(entry)}  {entry.message}[/dim]")

    def _empty() -> Text:
        return Text.from_markup("  [dim]--[/dim]")

    log_lines: List[Text] = [_log_line(e) for e in log_entries]
    while len(log_lines) < 5:
        log_lines.append(_empty())

    err_lines: List[Text] = []
    for it in error_items:
        err_lines.append(Text.from_markup(f"  [red][!] {it.name}[/red]"))
    for e in error_logs:
        err_lines.append(Text.from_markup(f"  [red]{_ts(e)}  {e.message}[/red]"))
    while len(err_lines) < 5:
        err_lines.append(_empty())
    err_lines = err_lines[:5]

    has_errors = bool(error_items or error_logs)
    err_color  = "red" if has_errors else "dim"

    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(ratio=3)
    table.add_column(ratio=2)
    table.add_row(Text.from_markup("[dim]Logs[/dim]"),
                  Text.from_markup(f"[{err_color}]Erreurs[/{err_color}]"))
    for log_line, err_line in zip(log_lines, err_lines):
        table.add_row(log_line, err_line)

    parts.append(table)
    return Group(*parts)


# ---------------------------------------------------------------------------
# Filtrage et tri
# ---------------------------------------------------------------------------

def _compute_filters(sessions: List[TrackingSession]) -> List[str]:
    from templates import TEMPLATES
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
    return sorted(
        sessions,
        key=lambda s: (
            1 if _is_stalled(s) else 0,
            0 if s.status.value == "running" else 1,
            s.created_at,
        ),
    )


# ---------------------------------------------------------------------------
# Widget SessionCard : un Static cliquable par session
# ---------------------------------------------------------------------------

class SessionCard(Static):
    """Carte de session. Cliquer sur le titre plie/deplie le detail."""

    DEFAULT_CSS = """
    SessionCard {
        padding: 0 0;
    }
    SessionCard:hover {
        background: $boost;
    }
    """

    def __init__(self, session: TrackingSession, collapsed: bool = False) -> None:
        self._sid = session.id
        super().__init__(
            _render_session(session, collapsed=collapsed),
            id=f"card-{session.id}",
        )

    def on_click(self) -> None:
        # Remonte a l'app pour basculer l'etat plie de cette session
        self.app.toggle_collapsed(self._sid)  # type: ignore


# ---------------------------------------------------------------------------
# Ecriture JSON depuis le UI
# ---------------------------------------------------------------------------

def _write_sessions(sessions: dict) -> None:
    import json, os
    data = {sid: s.model_dump(mode="json") for sid, s in sessions.items()}
    tmp = STORAGE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")
    os.replace(tmp, STORAGE_FILE)


# ---------------------------------------------------------------------------
# Modal choix de filtre
# ---------------------------------------------------------------------------

class FilterSelectScreen(ModalScreen):
    CSS = """
    FilterSelectScreen { align: center middle; }
    #filter-dialog {
        width: 40; height: auto; max-height: 20;
        border: thick $primary; background: $surface; padding: 1 2;
    }
    #filter-dialog Label { width: 100%; text-align: center; margin-bottom: 1; }
    ListView { height: auto; max-height: 14; border: none; }
    """
    BINDINGS = [Binding("escape", "cancel", "Annuler", show=True)]

    def __init__(self, filters: List[str], active: str) -> None:
        super().__init__()
        self._filters = filters
        self._active  = active

    def compose(self) -> ComposeResult:
        with Center(id="filter-dialog"):
            yield Label("Choisir un filtre")
            items = [
                ListItem(Label((">" if f == self._active else " ") + " " + f), id=f"filter-{f}")
                for f in self._filters
            ]
            yield ListView(*items, id="filter-list")

    def on_mount(self) -> None:
        lv = self.query_one("#filter-list", ListView)
        lv.focus()
        for i, f in enumerate(self._filters):
            if f == self._active:
                lv.index = i
                break

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = self.query_one("#filter-list", ListView).index
        if idx is not None and 0 <= idx < len(self._filters):
            self.dismiss(self._filters[idx])

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Modal Stop / Kill
# ---------------------------------------------------------------------------

class SessionActionScreen(ModalScreen):
    CSS = """
    SessionActionScreen { align: center middle; }
    #dialog {
        width: 60; height: auto;
        border: thick $primary; background: $surface; padding: 1 2;
    }
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
            label, btn_label, btn_variant = "[S] Arret propre", "Stop", "warning"
        else:
            label, btn_label, btn_variant = "[K] Kill force", "Kill", "error"
        with Center(id="dialog"):
            yield Label(label)
            yield Input(placeholder="ID de session (ex: a1b2c3d4)", id="session-input")
            with Center(id="buttons"):
                yield Button(btn_label, variant=btn_variant, id="confirm")
                yield Button("Annuler", variant="default", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#session-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            sid = self.query_one("#session-input", Input).value.strip()
            self.dismiss(sid if sid else None)
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
        Binding("q",     "quit",         "Quitter",           show=True),
        Binding("space", "filter_menu",  "Filtres",           show=True),
        Binding("e",     "errors_filter","Erreurs",           show=True),
        Binding("r",     "refresh",      "Refresh",           show=True),
        Binding("c",     "clean",        "Nettoyer",          show=True),
        Binding("s",     "stop_session", "Stop (propre)",     show=True),
        Binding("k",     "kill_session", "Kill (force)",      show=True),
        Binding("a",     "toggle_all",   "Tout plier/deplier",show=True),
    ]

    def __init__(self, initial_filter: str = FILTER_ALL) -> None:
        super().__init__()
        self._active_filter = initial_filter
        self._collapsed: set[str] = set()   # IDs des sessions pliees
        self._stalled_seen: set[str] = set() # IDs media auto-plies quand stalled
        self._prev_order: list[str] = []    # Ordre precedent (pour diff)
        self._refreshing: bool = False      # Garde anti-reentrance

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll():
            yield Vertical(id="cards")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(1.0, self.do_refresh)
        self.call_later(self.do_refresh)

    # -- API publique pour les SessionCards --

    def toggle_collapsed(self, session_id: str) -> None:
        """Bascule l'etat plie/deplie d'une session (appele par SessionCard.on_click)."""
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
        filters  = _compute_filters(sessions)

        # Nettoyer les IDs obsoletes
        session_ids = {s.id for s in sessions}
        self._collapsed &= session_ids
        self._stalled_seen &= session_ids

        # Auto-plier les trackers media (movie/serie) qui passent en attente (seeds=0)
        _MEDIA_TEMPLATES = {"movie", "series_episode", "series_season"}
        currently_stalled = {
            s.id for s in sessions
            if s.template in _MEDIA_TEMPLATES and _is_stalled(s)
        }
        new_stalled = currently_stalled - self._stalled_seen
        self._collapsed |= new_stalled
        self._stalled_seen = currently_stalled

        self._update_subtitle(sessions, filters)

        visible = _apply_filter(sessions, self._active_filter)
        ordered = _sort_sessions(visible)
        new_order = [s.id for s in ordered]

        if new_order != self._prev_order:
            # La liste a change : reconstruction complete du DOM
            await self._rebuild_dom(ordered)
            self._prev_order = new_order
        else:
            # Meme liste : simple mise a jour du contenu de chaque carte
            self._update_cards_content(ordered)

    async def _rebuild_dom(self, ordered: List[TrackingSession]) -> None:
        """Supprime et recrée toutes les cartes (quand la liste change)."""
        container = self.query_one("#cards", Vertical)
        await container.remove_children()

        if not ordered:
            sessions = load_from_file()
            if not sessions:
                await container.mount(Static(
                    "\n[dim]Aucune session active.[/dim]\n\n"
                    "[dim]Utilisez tracking_create() via MCP pour demarrer un suivi.[/dim]"
                ))
            else:
                await container.mount(Static(
                    f"\n[dim]Aucune session pour le filtre '[bold]{self._active_filter}[/bold]'.[/dim]"
                ))
            return

        for i, session in enumerate(ordered):
            collapsed = session.id in self._collapsed
            await container.mount(SessionCard(session, collapsed=collapsed))
            if i < len(ordered) - 1:
                await container.mount(Static(Rule(style="dim"), classes="sep"))

    def _update_cards_content(self, ordered: Optional[List[TrackingSession]] = None) -> None:
        """Met a jour le contenu des cartes existantes sans toucher au DOM."""
        if ordered is None:
            sessions = load_from_file()
            visible  = _apply_filter(sessions, self._active_filter)
            ordered  = _sort_sessions(visible)

        for session in ordered:
            try:
                card = self.query_one(f"#card-{session.id}", SessionCard)
                collapsed = session.id in self._collapsed
                card.update(_render_session(session, collapsed=collapsed))
            except Exception as exc:
                # Card absente du DOM : declenche un rebuild complet
                self._prev_order = []
                self.call_later(self.do_refresh)
                return

    def _update_subtitle(self, sessions: List[TrackingSession], filters: List[str]) -> None:
        total   = len(sessions)
        visible = len(_apply_filter(sessions, self._active_filter))
        if self._active_filter == FILTER_ALL:
            filter_label = "tout"
            count_label  = str(total)
        else:
            filter_label = self._active_filter
            count_label  = f"{visible}/{total}"
        self.sub_title = f"filtre: {filter_label}  |  {count_label} session(s)"

    # -- Actions clavier --

    def action_filter_menu(self) -> None:
        sessions = load_from_file()
        filters  = _compute_filters(sessions)

        def _on_dismiss(chosen: Optional[str]) -> None:
            if chosen is not None:
                self._active_filter = chosen
            self.call_later(self.do_refresh)

        self.push_screen(FilterSelectScreen(filters, self._active_filter), _on_dismiss)

    def action_errors_filter(self) -> None:
        if self._active_filter == FILTER_ERRORS:
            self._active_filter = FILTER_ALL
        else:
            self._active_filter = FILTER_ERRORS
        self.call_later(self.do_refresh)

    def action_refresh(self) -> None:
        self.call_later(self.do_refresh)

    def action_quit(self) -> None:
        self.exit()

    def action_toggle_all(self) -> None:
        """Plie ou deplie toutes les sessions visibles d'un coup."""
        sessions = load_from_file()
        visible  = _apply_filter(sessions, self._active_filter)
        ids      = {s.id for s in visible}
        if ids.issubset(self._collapsed):
            self._collapsed -= ids
        else:
            self._collapsed |= ids
        self._update_cards_content()

    def action_clean(self) -> None:
        all_sessions = load_from_file()
        visible      = _apply_filter(all_sessions, self._active_filter)
        to_clean     = {s.id for s in visible if s.status.value in ("done", "error")}
        if not to_clean:
            self.notify("Rien a nettoyer.", severity="information")
            return
        remaining = {s.id: s for s in all_sessions if s.id not in to_clean}
        _write_sessions(remaining)
        self.notify(f"{len(to_clean)} session(s) supprimee(s).", severity="warning")
        self.call_later(self.do_refresh)

    async def action_stop_session(self) -> None:
        session_id = await self.push_screen_wait(SessionActionScreen("stop"))
        if not session_id:
            return
        sessions = {s.id: s for s in load_from_file()}
        if session_id not in sessions:
            self.notify(f"Session introuvable: {session_id}", severity="error")
            return
        session = sessions[session_id]
        session.status = SessionStatus.PAUSED
        session.logs.append(LogEntry(message="Arretee manuellement depuis le dashboard"))
        session.updated_at = datetime.now()
        _write_sessions(sessions)
        self.notify(f"Session '{session.name}' mise en pause.", severity="warning")
        await self.do_refresh()

    async def action_kill_session(self) -> None:
        session_id = await self.push_screen_wait(SessionActionScreen("kill"))
        if not session_id:
            return
        sessions = {s.id: s for s in load_from_file()}
        if session_id not in sessions:
            self.notify(f"Session introuvable: {session_id}", severity="error")
            return
        name = sessions[session_id].name
        del sessions[session_id]
        _write_sessions(sessions)
        self.notify(f"Session '{name}' supprimee en force.", severity="error")
        await self.do_refresh()
