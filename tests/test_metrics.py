"""Tests unitaires de metrics.py (logique pure, table-driven)."""
from datetime import datetime, timedelta

import pytest

import metrics
from models import (ItemStatus, ProgressPoint, SessionStatus, TrackingItem,
                    TrackingSession)

NOW = datetime(2026, 8, 25, 12, 0, 0)


def _session(points=(), **kw) -> TrackingSession:
    """points : liste de (secondes avant NOW, valeur)."""
    defaults = dict(name="s", template="free", total=100, processed=0,
                    created_at=NOW - timedelta(seconds=300), updated_at=NOW)
    defaults.update(kw)
    s = TrackingSession(**defaults)
    s.history = [ProgressPoint(t=NOW - timedelta(seconds=ago), v=v)
                 for ago, v in points]
    return s


# ---------------------------------------------------------------------------
# format_duration / format_rate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (None, "--"),
    (-1, "--"),
    (0, "0s"),
    (59, "59s"),
    (60, "1m00s"),
    (3599, "59m59s"),
    (3600, "1h00m"),
    (3661, "1h01m"),
    (86400, "1j00h"),
    (90000, "1j01h"),
])
def test_format_duration(seconds, expected):
    assert metrics.format_duration(seconds) == expected


@pytest.mark.parametrize("rate,unit,expected", [
    (None, "MB", "--"),
    (250.0, "MB", "250 MB/s"),
    (2.5, " MB", "2.5 MB/s"),
    (0.5, "", "30.0 u/min"),
    (0.01, "frames", "36.0 frames/h"),
])
def test_format_rate(rate, unit, expected):
    assert metrics.format_rate(rate, unit) == expected


# ---------------------------------------------------------------------------
# rate / eta
# ---------------------------------------------------------------------------

def test_rate_needs_two_points():
    assert metrics.rate(_session([(0, 10)]), NOW) is None


def test_rate_simple_window():
    s = _session([(60, 0), (0, 120)])
    assert metrics.rate(s, NOW) == pytest.approx(2.0)


def test_rate_uses_recent_window_only():
    # Vieux point il y a 1h a 0, puis 2 points recents : 10 -> 20 en 10s
    s = _session([(3600, 0), (10, 10), (0, 20)])
    assert metrics.rate(s, NOW) == pytest.approx(1.0)


def test_rate_falls_back_to_last_points_when_window_empty():
    s = _session([(1000, 0), (500, 500)])
    assert metrics.rate(s, NOW) == pytest.approx(1.0)


def test_rate_none_when_regression_or_zero_dt():
    assert metrics.rate(_session([(10, 50), (0, 40)]), NOW) is None
    assert metrics.rate(_session([(0, 1), (0, 2)]), NOW) is None


def test_eta():
    s = _session([(10, 0), (0, 20)], processed=20, total=100)  # 2 u/s
    assert metrics.eta_seconds(s, NOW) == pytest.approx(40.0)


def test_eta_zero_when_done_and_none_without_rate():
    assert metrics.eta_seconds(_session([(10, 0), (0, 20)], processed=100), NOW) == 0.0
    assert metrics.eta_seconds(_session(), NOW) is None


# ---------------------------------------------------------------------------
# elapsed / idle / stale / item duration
# ---------------------------------------------------------------------------

def test_elapsed_running_and_finished():
    s = _session()
    assert metrics.elapsed_seconds(s, NOW) == pytest.approx(300.0)
    s.finished_at = NOW - timedelta(seconds=100)
    assert metrics.elapsed_seconds(s, NOW) == pytest.approx(200.0)


@pytest.mark.parametrize("status,idle,expected", [
    (SessionStatus.RUNNING, metrics.STALE_AFTER_S, True),
    (SessionStatus.RUNNING, metrics.STALE_AFTER_S - 1, False),
    (SessionStatus.DONE, metrics.STALE_AFTER_S * 2, False),
    (SessionStatus.PAUSED, metrics.STALE_AFTER_S * 2, False),
])
def test_is_stale(status, idle, expected):
    s = _session(status=status, updated_at=NOW - timedelta(seconds=idle))
    assert metrics.idle_seconds(s, NOW) == pytest.approx(idle)
    assert metrics.is_stale(s, NOW) is expected


def test_item_duration():
    assert metrics.item_duration_seconds(TrackingItem(name="x"), NOW) is None
    it = TrackingItem(name="x", status=ItemStatus.RUNNING,
                      started_at=NOW - timedelta(seconds=30))
    assert metrics.item_duration_seconds(it, NOW) == pytest.approx(30.0)
    it.finished_at = NOW - timedelta(seconds=10)
    assert metrics.item_duration_seconds(it, NOW) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# compute
# ---------------------------------------------------------------------------

def test_compute_running():
    s = _session([(10, 0), (0, 20)], processed=20, total=100, unit="MB")
    m = metrics.compute(s, NOW)
    assert m["percent"] == 20.0
    assert m["rate"] == pytest.approx(2.0)
    assert m["rate_str"] == "2.0 MB/s"
    assert m["eta_seconds"] == pytest.approx(40.0)
    assert m["eta_str"] == "40s"
    assert m["elapsed_str"] == "5m00s"
    assert m["stale"] is False


def test_compute_percent_capped_and_zero_total():
    assert metrics.compute(_session(processed=250, total=100), NOW)["percent"] == 100.0
    assert metrics.compute(_session(processed=5, total=0), NOW)["percent"] == 0.0


def test_compute_eta_hidden_when_not_running():
    s = _session([(10, 0), (0, 20)], processed=20, status=SessionStatus.PAUSED)
    assert metrics.compute(s, NOW)["eta_str"] == "--"
