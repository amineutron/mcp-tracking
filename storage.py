"""Persistence JSON atomique partagee entre api.py, server.py (MCP) et ui.py.

Trois processus ecrivent le meme fichier : le verrou est donc inter-process
(fcntl.flock sur un fichier .lock a cote de l'etat). Toute sequence
lecture -> modification -> ecriture doit passer par `modify()` ou par
`with locked():` pour ne jamais perdre une mise a jour concurrente.
"""
import fcntl
import json
import os
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional

from models import TrackingSession

# Repertoire d'etat : ~/.local/state/tracking (XDG), surchargeable par env.
# Migration transparente depuis l'ancien emplacement (a cote du code).
_LEGACY_DIR = Path(__file__).parent


def _state_dir() -> Path:
    env = os.environ.get("TRACKING_STATE_DIR")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(xdg) / "tracking"


def _migrate(name: str) -> Path:
    target = _state_dir() / name
    legacy = _LEGACY_DIR / name
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() and legacy.exists():
            shutil.copy2(legacy, target)
    except OSError:
        pass
    return target


STORAGE_FILE = _migrate("tracking_state.json")
POLLER_STATE_FILE = _migrate("poller_state.json")

MAX_LOGS = 200            # logs max conserves par session
MAX_HISTORY = 40          # points de progression conserves (vitesse / ETA)
TTL_DAYS = int(os.environ.get("TRACKING_TTL_DAYS", "7"))          # done/error/paused
TTL_RUNNING_HOURS = int(os.environ.get("TRACKING_TTL_RUNNING_H", "24"))  # orphelins

# Cache in-process : evite de relire le JSON a chaque appel
_cache: Optional[Dict[str, TrackingSession]] = None
_cache_mtime_ns: int = -1


def _lock_file() -> Path:
    return STORAGE_FILE.with_suffix(".lock")


def _file_mtime_ns() -> int:
    try:
        return STORAGE_FILE.stat().st_mtime_ns if STORAGE_FILE.exists() else -1
    except OSError:
        return -1


@contextmanager
def locked() -> Iterator[None]:
    """Verrou exclusif inter-process sur le fichier d'etat (reentrant-safe par process)."""
    _lock_file().parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(_lock_file(), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _load_dict() -> Dict[str, TrackingSession]:
    global _cache, _cache_mtime_ns
    mtime = _file_mtime_ns()
    if _cache is not None and mtime == _cache_mtime_ns:
        return _cache
    if not STORAGE_FILE.exists():
        _cache = {}
        _cache_mtime_ns = mtime
        return _cache
    try:
        data = json.loads(STORAGE_FILE.read_text(encoding="utf-8"))
        _cache = {sid: TrackingSession.model_validate(v) for sid, v in data.items()}
    except Exception:
        _cache = {}
    _cache_mtime_ns = mtime
    return _cache


def _persist_dict(sessions: Dict[str, TrackingSession]) -> None:
    """Ecriture atomique de l'etat complet, puis mise a jour du cache."""
    global _cache, _cache_mtime_ns
    data = {sid: s.model_dump(mode="json") for sid, s in sessions.items()}
    STORAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORAGE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")
    os.replace(tmp, STORAGE_FILE)
    _cache = dict(sessions)
    _cache_mtime_ns = _file_mtime_ns()


def _cap(session: TrackingSession) -> None:
    """Tronque logs et historique aux N entrees les plus recentes."""
    if len(session.logs) > MAX_LOGS:
        session.logs = session.logs[-MAX_LOGS:]
    if len(session.history) > MAX_HISTORY:
        session.history = session.history[-MAX_HISTORY:]


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def get(session_id: str) -> Optional[TrackingSession]:
    return _load_dict().get(session_id)


def save(session: TrackingSession) -> None:
    """Sauvegarde une session (creation ou remplacement complet)."""
    _cap(session)
    with locked():
        sessions = dict(_load_dict())
        sessions[session.id] = session
        _persist_dict(sessions)


def modify(session_id: str,
           mutator: Callable[[TrackingSession], None]) -> Optional[TrackingSession]:
    """Lecture -> mutation -> ecriture sous verrou inter-process.
    Retourne la session modifiee, ou None si elle n'existe pas."""
    with locked():
        sessions = dict(_load_dict())
        session = sessions.get(session_id)
        if session is None:
            return None
        mutator(session)
        _cap(session)
        sessions[session_id] = session
        _persist_dict(sessions)
        return session


def delete(session_id: str) -> bool:
    with locked():
        sessions = dict(_load_dict())
        if session_id in sessions:
            del sessions[session_id]
            _persist_dict(sessions)
            return True
        return False


def delete_many(session_ids) -> int:
    with locked():
        sessions = dict(_load_dict())
        removed = [sid for sid in session_ids if sid in sessions]
        for sid in removed:
            del sessions[sid]
        if removed:
            _persist_dict(sessions)
        return len(removed)


def list_all(
    template: Optional[str] = None,
    status: Optional[str] = None,
) -> List[TrackingSession]:
    sessions = list(_load_dict().values())
    if template:
        sessions = [s for s in sessions if s.template == template]
    if status:
        sessions = [s for s in sessions if s.status.value == status]
    return sessions


def clear_storage() -> None:
    """Vide le fichier JSON et invalide le cache."""
    global _cache, _cache_mtime_ns
    with locked():
        if STORAGE_FILE.exists():
            STORAGE_FILE.unlink()
        _cache = None
        _cache_mtime_ns = -1


def load_from_file() -> List[TrackingSession]:
    """Lit les sessions depuis le fichier JSON (compatibilite ui.py)."""
    return list(_load_dict().values())


def purge_old_sessions() -> int:
    """Supprime les sessions terminees ou inactives depuis trop longtemps.
    - done/error/paused : supprimees apres TTL_DAYS jours
    - running sans mise a jour depuis TTL_RUNNING_HOURS : orphelins
    Retourne le nombre de sessions supprimees."""
    now = datetime.now()
    cutoff_done    = now - timedelta(days=TTL_DAYS)
    cutoff_running = now - timedelta(hours=TTL_RUNNING_HOURS)
    with locked():
        sessions = dict(_load_dict())
        to_delete = [
            sid for sid, s in sessions.items()
            if (s.status.value in ("done", "error", "paused") and s.updated_at < cutoff_done)
            or (s.status.value == "running" and s.updated_at < cutoff_running)
        ]
        if not to_delete:
            return 0
        for sid in to_delete:
            del sessions[sid]
        _persist_dict(sessions)
        return len(to_delete)
