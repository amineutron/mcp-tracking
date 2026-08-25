"""Metriques derivees d'une session : vitesse, ETA, temps ecoule, stale.

Logique pure (aucun I/O) : calculee a la volee cote lecture, jamais stockee.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from models import TrackingItem, TrackingSession

# Nombre minimal de points et fenetre pour lisser la vitesse
MIN_POINTS = 2
RATE_WINDOW_S = 120.0
# Session running sans mise a jour depuis ce delai : consideree "stale"
STALE_AFTER_S = 600.0


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "--"
    s = int(seconds)
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}j{h:02d}h"
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def rate(session: TrackingSession, now: Optional[datetime] = None) -> Optional[float]:
    """Vitesse en unites/seconde sur la fenetre recente. None si indeterminable."""
    pts = session.history
    if len(pts) < MIN_POINTS:
        return None
    now = now or datetime.now()
    window_start = now - timedelta(seconds=RATE_WINDOW_S)
    recent = [p for p in pts if p.t >= window_start]
    if len(recent) < MIN_POINTS:
        recent = pts[-MIN_POINTS:]
    first, last = recent[0], recent[-1]
    dt = (last.t - first.t).total_seconds()
    if dt <= 0:
        return None
    dv = last.v - first.v
    if dv < 0:
        return None
    return dv / dt


def eta_seconds(session: TrackingSession, now: Optional[datetime] = None) -> Optional[float]:
    r = rate(session, now)
    if not r or r <= 0:
        return None
    remaining = session.total - session.processed
    if remaining <= 0:
        return 0.0
    return remaining / r


def elapsed_seconds(session: TrackingSession, now: Optional[datetime] = None) -> float:
    end = session.finished_at or (now or datetime.now())
    return max(0.0, (end - session.created_at).total_seconds())


def idle_seconds(session: TrackingSession, now: Optional[datetime] = None) -> float:
    return max(0.0, ((now or datetime.now()) - session.updated_at).total_seconds())


def is_stale(session: TrackingSession, now: Optional[datetime] = None) -> bool:
    return session.status.value == "running" and idle_seconds(session, now) >= STALE_AFTER_S


def item_duration_seconds(item: TrackingItem, now: Optional[datetime] = None) -> Optional[float]:
    if item.started_at is None:
        return None
    end = item.finished_at or (now or datetime.now())
    return max(0.0, (end - item.started_at).total_seconds())


def format_rate(r: Optional[float], unit: str) -> str:
    if r is None:
        return "--"
    u = unit.strip() or "u"
    if r >= 100:
        return f"{r:.0f} {u}/s"
    if r >= 1:
        return f"{r:.1f} {u}/s"
    per_min = r * 60
    if per_min >= 1:
        return f"{per_min:.1f} {u}/min"
    return f"{r * 3600:.1f} {u}/h"


def compute(session: TrackingSession, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Toutes les metriques d'une session, pretes a serialiser."""
    now = now or datetime.now()
    r = rate(session, now)
    eta = eta_seconds(session, now)
    pct = min(session.processed / session.total, 1.0) if session.total > 0 else 0.0
    return {
        "percent": round(pct * 100, 1),
        "rate": r,
        "rate_str": format_rate(r, session.unit),
        "eta_seconds": eta,
        "eta_str": format_duration(eta) if session.status.value == "running" else "--",
        "elapsed_seconds": elapsed_seconds(session, now),
        "elapsed_str": format_duration(elapsed_seconds(session, now)),
        "idle_seconds": idle_seconds(session, now),
        "stale": is_stale(session, now),
    }
