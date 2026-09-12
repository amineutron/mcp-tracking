"""Serveur MCP tracking : outils exposes a Claude / Lyra.

Ecrit dans le meme stockage que l'API HTTP, sous le meme verrou
inter-process (storage.modify), avec la meme logique (mutations.py).
"""
import argparse
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer

import metrics
import mutations
import storage
from models import TrackingItem, TrackingSession
from templates import list_templates, validate_template

mcp = MCPServer("tracking")

BAR_WIDTH = 22
ITEM_ICONS = {"pending": "[ ]", "running": "[>]", "done": "[ok]", "error": "[!]"}


# ---------------------------------------------------------------------------
# Formateur texte (retour des outils)
# ---------------------------------------------------------------------------

def _bar(processed: float, total: float) -> str:
    pct = min(processed / total, 1.0) if total > 0 else 0
    filled = int(BAR_WIDTH * pct)
    if filled >= BAR_WIDTH:
        return "=" * BAR_WIDTH
    return "=" * filled + ">" + " " * (BAR_WIDTH - filled - 1)


def _format_item(item: TrackingItem) -> str:
    icon = ITEM_ICONS.get(item.status.value, "[ ]")
    if item.processed is not None and item.total is not None:
        prog = f"  {item.processed}{item.unit} / {item.total}{item.unit}"
    elif item.total is not None:
        prog = f"  {item.total}{item.unit}"
    else:
        prog = ""
    dur = metrics.item_duration_seconds(item)
    dur_str = f"  [{metrics.format_duration(dur)}]" if dur is not None else ""
    note = f"  ({item.note})" if item.note else ""
    return f"  {icon}  {item.name:<32}{prog}{dur_str}{note}"


def _format_session(session: TrackingSession) -> str:
    m = metrics.compute(session)
    bar = _bar(session.processed, session.total)
    stale = "  [STALE]" if m["stale"] else ""
    lines = [
        f"[{session.template}] {session.name}  ({session.status.value}){stale}",
        f"[{bar}] {m['percent']}%  ({session.processed}{session.unit} / {session.total}{session.unit})",
        f"  elapsed: {m['elapsed_str']}  |  rate: {m['rate_str']}  |  eta: {m['eta_str']}",
    ]
    if session.extra:
        extra_parts = [f"{k}: {v}" for k, v in session.extra.items() if v is not None]
        if extra_parts:
            lines.append("  " + "  |  ".join(extra_parts))
    if session.items:
        lines.append("")
        lines.append("Items:")
        lines.extend(_format_item(it) for it in session.items)
    if session.logs:
        lines.append("")
        lines.append("Logs:")
        for entry in session.logs[-5:]:
            ts = entry.timestamp.strftime("%H:%M:%S")
            lvl = "" if entry.level.value == "info" else f"[{entry.level.value.upper()}] "
            lines.append(f"  {ts}  {lvl}{entry.message}")
    return "\n".join(lines)


def _modify(session_id: str, fn) -> Optional[TrackingSession]:
    return storage.modify(session_id, fn)


def _not_found(session_id: str) -> str:
    return f"Session introuvable: {session_id}"


# ---------------------------------------------------------------------------
# Outils MCP
# ---------------------------------------------------------------------------

@mcp.tool()
def tracking_create(
    name: str,
    template: str,
    total: float,
    unit: str = "",
    items: Optional[List[Dict[str, Any]]] = None,
    extra: Optional[Dict[str, Any]] = None,
    pid: Optional[int] = None,
) -> str:
    """
    Cree une nouvelle session de tracking.

    Args:
        name:     Nom de la session (ex: "[DEV] Build worldmonitor")
        template: voir tracking_templates() ("free", "machine", "download", "lyra_task"...)
        total:    Valeur totale (ex: 15300 pour 15300 MB, 6 pour 6 etapes)
        unit:     Unite affichee (ex: " MB", " etapes")
        items:    Liste optionnelle d'etapes [{name, status?, total?, unit?, note?}]
        extra:    Champs specifiques au template (speed, eta, operation, target...)
        pid:      PID du processus a signaler par tracking_stop / tracking_kill
    """
    if not validate_template(template):
        return (f"Template inconnu: '{template}'\n\n"
                f"Templates disponibles:\n{list_templates()}")

    session = TrackingSession(name=name, template=template, total=total,
                              unit=unit, extra=extra or {}, pid=pid)
    for item_data in items or []:
        mutations.upsert_item(session, {"unit": unit, **item_data})
    mutations.add_log(session, f"Session creee: {name}")
    storage.save(session)
    return f"Session creee [id: {session.id}]\n\n{_format_session(session)}"


@mcp.tool()
def tracking_update(
    session_id: str,
    processed: Optional[float] = None,
    message: Optional[str] = None,
    item_updates: Optional[List[Dict[str, Any]]] = None,
    extra: Optional[Dict[str, Any]] = None,
    level: str = "info",
) -> str:
    """
    Met a jour la progression d'une session. La vitesse, l'ETA et le temps
    ecoule sont calcules automatiquement a partir de `processed`.

    Args:
        session_id:   ID de la session
        processed:    Nouvelle valeur de progression (optionnel)
        message:      Message de log a ajouter (optionnel)
        item_updates: Etapes a mettre a jour ou creer [{name?, id?, status?, processed?, note?}]
        extra:        Champs extra a mettre a jour (speed, eta, phase...)
        level:        Niveau du message : "info" | "warn" | "error"
    """
    body: Dict[str, Any] = {}
    if processed is not None:
        body["processed"] = processed
    if message:
        body["log"] = message
        body["log_level"] = level
    if item_updates:
        body["items"] = item_updates
    if extra:
        body["extra"] = extra
    session = _modify(session_id, lambda s: mutations.apply_update(s, body))
    return _format_session(session) if session else _not_found(session_id)


@mcp.tool()
def tracking_log(session_id: str, message: str, level: str = "info") -> str:
    """Ajoute une entree de log (info | warn | error) sans modifier la progression."""
    session = _modify(session_id, lambda s: mutations.apply_update(
        s, {"log": message, "log_level": level}))
    return f"Log ajoute a '{session.name}'" if session else _not_found(session_id)


@mcp.tool()
def tracking_complete(session_id: str, message: Optional[str] = None) -> str:
    """Marque une session comme terminee et met la progression a 100%."""
    session = _modify(session_id, lambda s: mutations.complete(s, message))
    return _format_session(session) if session else _not_found(session_id)


@mcp.tool()
def tracking_error(session_id: str, message: str) -> str:
    """Marque une session en erreur."""
    session = _modify(session_id, lambda s: mutations.fail(s, message))
    return _format_session(session) if session else _not_found(session_id)


@mcp.tool()
def tracking_get(session_id: str) -> str:
    """Retourne l'etat formate complet d'une session (avec metriques)."""
    session = storage.get(session_id)
    return _format_session(session) if session else _not_found(session_id)


@mcp.tool()
def tracking_list(
    template: Optional[str] = None,
    status: Optional[str] = None,
) -> str:
    """
    Liste les sessions avec filtres optionnels.

    Args:
        template: Filtrer par template ("download", "machine", "free", "movie", "lyra_task"...)
        status:   Filtrer par statut ("running", "done", "error", "paused")
    """
    sessions = storage.list_all(template=template, status=status)
    filt = ", ".join(p for p in (f"template={template}" if template else "",
                                 f"status={status}" if status else "") if p)
    if not sessions:
        return f"Aucune session{f' ({filt})' if filt else ''}."

    header = f"Sessions ({len(sessions)})" + (f"  --  filtre: {filt}" if filt else "")
    lines = [header, ""]
    for s in sessions:
        m = metrics.compute(s)
        flag = " STALE" if m["stale"] else ""
        lines.append(
            f"  [{s.id}]  {s.template:<14}  {s.name:<32}  {m['percent']:5.1f}%  "
            f"eta {m['eta_str']:<7} ({s.status.value}){flag}"
        )
    return "\n".join(lines)


@mcp.tool()
def tracking_delete(session_id: str) -> str:
    """Supprime une session (sans toucher au processus)."""
    session = storage.get(session_id)
    if not session:
        return _not_found(session_id)
    storage.delete(session_id)
    return f"Session '{session.name}' [{session_id}] supprimee."


@mcp.tool()
def tracking_stop(session_id: str, message: Optional[str] = None) -> str:
    """
    Arret propre : envoie SIGTERM au processus si la session a un `pid`,
    puis marque la session 'paused' (elle reste visible dans le dashboard).

    Args:
        session_id: ID de la session
        message:    Raison de l'arret (optionnel)
    """
    session = _modify(session_id, lambda s: mutations.stop(s, message))
    return _format_session(session) if session else _not_found(session_id)


@mcp.tool()
def tracking_kill(session_id: str) -> str:
    """
    Arret force : envoie SIGKILL au processus si la session a un `pid`,
    puis supprime la session du dashboard.

    Args:
        session_id: ID de la session
    """
    session = storage.get(session_id)
    if not session:
        return _not_found(session_id)
    note = mutations.kill_process(session)
    storage.delete(session_id)
    suffix = f" ({note})" if note else ""
    return f"Session '{session.name}' [{session_id}] supprimee en force{suffix}."


@mcp.tool()
def tracking_templates() -> str:
    """Liste les templates disponibles (builtins + ~/.config/tracking/templates.json)."""
    return "Templates disponibles:\n\n" + list_templates()


@mcp.tool()
def open_tracking_ui(filter_template: Optional[str] = None) -> str:
    """
    Ouvre le dashboard de tracking dans un terminal Kitty.

    Args:
        filter_template: Template a afficher au demarrage ("lyra_task", "movie", "download"...)
                         Si absent, affiche toutes les sessions.
    """
    import subprocess
    import sys

    cmd = ["kitty", "--detach", sys.executable, str(__file__), "--ui"]
    if filter_template:
        cmd.extend(["--filter", filter_template])
    try:
        subprocess.Popen(cmd, start_new_session=True)
        suffix = f" (filtre: {filter_template})" if filter_template else ""
        return f"Dashboard ouvert dans Kitty{suffix}."
    except FileNotFoundError:
        return "Erreur: kitty introuvable -- verifier que Kitty est installe."


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="MCP Tracking Server")
    parser.add_argument("--test", action="store_true",
                        help="Ouvrir le dashboard et lancer la simulation")
    parser.add_argument("--ui", action="store_true",
                        help="Ouvrir le dashboard sans simulation (mode serveur)")
    parser.add_argument("--filter", default="all", metavar="TEMPLATE",
                        help="Filtre initial du dashboard (ex: lyra_task, movie, errors)")
    args = parser.parse_args()

    if args.test or args.ui:
        from ui import TrackingDashboard
        app = TrackingDashboard(initial_filter=args.filter)
        if args.test:
            import threading

            from sim import _run_simulation
            threading.Thread(target=_run_simulation, daemon=True).start()
        app.run()
    else:
        mcp.run()


if __name__ == "__main__":
    main()
