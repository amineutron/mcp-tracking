"""Mutations d'une session -- logique partagee par api.py (HTTP) et
server.py (MCP), pour que les deux chemins aient exactement le meme
comportement : horodatage des items, historique de progression, log leve,
auto-completion, upsert d'items.

Chaque fonction mute la session recue (appelee sous storage.modify()).
"""
import os
import signal
from datetime import datetime
from typing import Any, Dict, List, Optional

from models import ItemStatus, LogEntry, LogLevel, ProgressPoint, SessionStatus, TrackingItem, TrackingSession
from templates import get_template_info

_TERMINAL_ITEM = {ItemStatus.DONE, ItemStatus.ERROR}


def _now() -> datetime:
    return datetime.now()


# ---------------------------------------------------------------------------
# Briques elementaires
# ---------------------------------------------------------------------------

def add_log(session: TrackingSession, message: str, level: str = "info") -> None:
    try:
        lvl = LogLevel(level)
    except ValueError:
        lvl = LogLevel.INFO
    session.logs.append(LogEntry(message=str(message), level=lvl))


def set_processed(session: TrackingSession, value: float, clamp: bool = True) -> None:
    v = float(value)
    if clamp and session.total > 0:
        v = min(v, session.total)
    session.processed = max(0.0, v)
    session.history.append(ProgressPoint(t=_now(), v=session.processed))


def set_status(session: TrackingSession, value: str) -> bool:
    """Retourne False si le statut est invalide (ignore, sans erreur)."""
    try:
        new = SessionStatus(value)
    except ValueError:
        return False
    if new != session.status:
        session.status = new
        if new in (SessionStatus.DONE, SessionStatus.ERROR):
            session.finished_at = _now()
        elif new == SessionStatus.RUNNING:
            session.finished_at = None
    return True


def set_item_status(item: TrackingItem, value: str) -> bool:
    try:
        new = ItemStatus(value)
    except ValueError:
        return False
    if new == item.status:
        return True
    now = _now()
    if new == ItemStatus.RUNNING:
        item.started_at = item.started_at or now
        item.finished_at = None
    elif new in _TERMINAL_ITEM:
        item.started_at = item.started_at or now
        item.finished_at = now
    elif new == ItemStatus.PENDING:
        item.started_at = None
        item.finished_at = None
    item.status = new
    return True


def _find_item(session: TrackingSession, name: Optional[str],
               item_id: Optional[str]) -> Optional[TrackingItem]:
    for it in session.items:
        if (item_id and it.id == item_id) or (name and it.name == name):
            return it
    return None


def upsert_item(session: TrackingSession, data: Dict[str, Any]) -> TrackingItem:
    """Met a jour un item par nom/id ; le cree s'il n'existe pas."""
    name = data.get("name")
    item = _find_item(session, name, data.get("id"))
    if item is None:
        item = TrackingItem(name=str(name or ""), unit=str(data.get("unit", session.unit)))
        session.items.append(item)
    if "status" in data:
        set_item_status(item, str(data["status"]))
    if "note" in data and data["note"] is not None:
        item.note = str(data["note"])
    if "processed" in data and data["processed"] is not None:
        item.processed = float(data["processed"])
    if "total" in data and data["total"] is not None:
        item.total = float(data["total"])
    if "unit" in data and data["unit"] is not None:
        item.unit = str(data["unit"])
    return item


def replace_items(session: TrackingSession, items: List[Dict[str, Any]]) -> None:
    """Remplace la liste complete des items (poller Bazarr)."""
    session.items = []
    for d in items:
        upsert_item(session, d)


def maybe_auto_done(session: TrackingSession) -> None:
    """Si le template declare auto_done et que processed >= total : termine."""
    if session.status != SessionStatus.RUNNING:
        return
    if not get_template_info(session.template).get("auto_done"):
        return
    if session.total > 0 and session.processed >= session.total:
        set_status(session, "done")
        add_log(session, "Termine automatiquement (progression a 100%)")


# ---------------------------------------------------------------------------
# Mutation composite : le corps d'un PUT / tracking_update
# ---------------------------------------------------------------------------

def apply_update(session: TrackingSession, body: Dict[str, Any]) -> None:
    """Applique un dictionnaire de mise a jour (format PUT /sessions/{id}).

    Cles reconnues : name, processed, total, status, extra, log, log_level,
    item, items (upsert), replace_items, pid.
    Les cles inconnues sont ignorees.
    """
    if "name" in body and str(body["name"]).strip():
        session.name = str(body["name"]).strip()
    if "total" in body and body["total"] is not None:
        session.total = float(body["total"])
    if "processed" in body and body["processed"] is not None:
        set_processed(session, body["processed"])
    if "extra" in body and isinstance(body["extra"], dict):
        session.extra = {**session.extra, **body["extra"]}
    if "pid" in body and body["pid"] is not None:
        session.pid = int(body["pid"])
    if "log" in body and body["log"]:
        add_log(session, body["log"], str(body.get("log_level", "info")))
    if isinstance(body.get("item"), dict):
        upsert_item(session, body["item"])
    if isinstance(body.get("items"), list):
        for d in body["items"]:
            if isinstance(d, dict):
                upsert_item(session, d)
    if isinstance(body.get("replace_items"), list):
        replace_items(session, [d for d in body["replace_items"] if isinstance(d, dict)])
    if "status" in body and body["status"] is not None:
        set_status(session, str(body["status"]))
    maybe_auto_done(session)
    session.updated_at = _now()


def complete(session: TrackingSession, message: Optional[str] = None) -> None:
    session.processed = session.total
    session.history.append(ProgressPoint(t=_now(), v=session.processed))
    set_status(session, "done")
    add_log(session, message or "Session terminee")
    session.updated_at = _now()


def fail(session: TrackingSession, message: str) -> None:
    set_status(session, "error")
    add_log(session, f"ERREUR: {message}", "error")
    session.updated_at = _now()


# ---------------------------------------------------------------------------
# Signaux processus (stop / kill)
# ---------------------------------------------------------------------------

def signal_process(session: TrackingSession, sig: int) -> Optional[str]:
    """Envoie sig au pid de la session s'il existe. Retourne un message ou None."""
    if not session.pid:
        return None
    try:
        os.kill(session.pid, sig)
        return f"signal {signal.Signals(sig).name} envoye au pid {session.pid}"
    except ProcessLookupError:
        return f"pid {session.pid} introuvable (deja termine)"
    except PermissionError:
        return f"pid {session.pid} : permission refusee"


def stop(session: TrackingSession, message: Optional[str] = None) -> Optional[str]:
    note = signal_process(session, signal.SIGTERM)
    set_status(session, "paused")
    add_log(session, message or "Arretee manuellement", "warn")
    if note:
        add_log(session, note, "warn")
    session.updated_at = _now()
    return note


def kill_process(session: TrackingSession) -> Optional[str]:
    return signal_process(session, signal.SIGKILL)
