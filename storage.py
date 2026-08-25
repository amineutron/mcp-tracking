import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, List

from models import TrackingSession

# Fichier de persistence -- source de verite partagee entre api.py et server.py
STORAGE_FILE = Path(__file__).parent / "tracking_state.json"

MAX_LOGS = 200   # logs max conserves par session
TTL_DAYS = 7     # sessions done/error/paused supprimees apres N jours

# Cache in-process : evite de relire le JSON a chaque appel
_cache: Optional[Dict[str, TrackingSession]] = None
_cache_mtime: float = -1.0


def _file_mtime() -> float:
    try:
        return STORAGE_FILE.stat().st_mtime if STORAGE_FILE.exists() else -1.0
    except OSError:
        return -1.0


def _load_dict() -> Dict[str, TrackingSession]:
    global _cache, _cache_mtime
    mtime = _file_mtime()
    if _cache is not None and mtime == _cache_mtime:
        return _cache
    if not STORAGE_FILE.exists():
        _cache = {}
        _cache_mtime = mtime
        return _cache
    try:
        data = json.loads(STORAGE_FILE.read_text(encoding="utf-8"))
        _cache = {sid: TrackingSession.model_validate(v) for sid, v in data.items()}
    except Exception:
        _cache = {}
    _cache_mtime = mtime
    return _cache


def _persist_dict(sessions: Dict[str, TrackingSession]) -> None:
    """Ecriture atomique de l'etat complet dans le fichier JSON, puis mise a jour du cache."""
    global _cache, _cache_mtime
    data = {sid: s.model_dump(mode="json") for sid, s in sessions.items()}
    tmp = STORAGE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")
    os.replace(tmp, STORAGE_FILE)
    _cache = dict(sessions)
    _cache_mtime = _file_mtime()


def _cap_logs(session: TrackingSession) -> None:
    """Tronque les logs de la session au MAX_LOGS les plus recents."""
    if len(session.logs) > MAX_LOGS:
        session.logs = session.logs[-MAX_LOGS:]


def get(session_id: str) -> Optional[TrackingSession]:
    return _load_dict().get(session_id)


def save(session: TrackingSession) -> None:
    _cap_logs(session)
    sessions = _load_dict()
    sessions[session.id] = session
    _persist_dict(sessions)


def delete(session_id: str) -> bool:
    sessions = _load_dict()
    if session_id in sessions:
        del sessions[session_id]
        _persist_dict(sessions)
        return True
    return False


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
    global _cache, _cache_mtime
    if STORAGE_FILE.exists():
        STORAGE_FILE.unlink()
    _cache = None
    _cache_mtime = -1.0


def load_from_file() -> List[TrackingSession]:
    """Lit les sessions depuis le fichier JSON (compatibilite api.py)."""
    return list(_load_dict().values())


def purge_old_sessions() -> int:
    """Supprime les sessions terminees ou inactives depuis trop longtemps.
    - done/error/paused : supprimees apres TTL_DAYS jours
    - running sans mise a jour depuis 24h : orphelins de redemarrage
    Retourne le nombre de sessions supprimees."""
    sessions = _load_dict()
    now = datetime.now()
    cutoff_done    = now - timedelta(days=TTL_DAYS)
    cutoff_running = now - timedelta(hours=24)
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
