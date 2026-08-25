"""Tests unitaires de storage.py : persistence atomique, cache, purge."""
import json
import os
from datetime import datetime, timedelta

import pytest

import storage
from models import LogEntry, SessionStatus, TrackingSession


def make_session(**kwargs) -> TrackingSession:
    defaults = {"name": "test", "template": "free"}
    defaults.update(kwargs)
    return TrackingSession(**defaults)


# ---------------------------------------------------------------------------
# CRUD de base
# ---------------------------------------------------------------------------

def test_save_and_get_roundtrip():
    session = make_session(name="conversion", total=50, unit="MB")
    storage.save(session)

    loaded = storage.get(session.id)
    assert loaded is not None
    assert loaded.name == "conversion"
    assert loaded.total == 50
    assert loaded.unit == "MB"
    assert loaded.status == SessionStatus.RUNNING


def test_get_unknown_returns_none():
    assert storage.get("inexistant") is None


def test_delete_existing():
    session = make_session()
    storage.save(session)

    assert storage.delete(session.id) is True
    assert storage.get(session.id) is None


def test_delete_unknown_returns_false():
    assert storage.delete("inexistant") is False


@pytest.mark.parametrize(
    "template_filter,status_filter,expected_names",
    [
        ("download", None, {"dl"}),
        ("machine", None, {"build"}),
        (None, "done", {"build"}),
        (None, "running", {"dl", "libre"}),
        ("download", "running", {"dl"}),
        (None, None, {"dl", "build", "libre"}),
    ],
)
def test_list_all_filters(template_filter, status_filter, expected_names):
    storage.save(make_session(name="dl", template="download"))
    storage.save(make_session(name="build", template="machine",
                              status=SessionStatus.DONE))
    storage.save(make_session(name="libre", template="free"))

    result = storage.list_all(template=template_filter, status=status_filter)
    assert {s.name for s in result} == expected_names


# ---------------------------------------------------------------------------
# Persistence atomique et robustesse fichier
# ---------------------------------------------------------------------------

def test_persist_is_atomic_no_tmp_left():
    storage.save(make_session())

    assert storage.STORAGE_FILE.exists()
    assert not storage.STORAGE_FILE.with_suffix(".tmp").exists()
    # Le fichier doit etre du JSON valide a tout moment
    json.loads(storage.STORAGE_FILE.read_text(encoding="utf-8"))


def test_corrupted_file_returns_empty_not_crash():
    storage.STORAGE_FILE.write_text("{pas du json", encoding="utf-8")

    assert storage.list_all() == []
    assert storage.get("x") is None


def test_missing_file_returns_empty():
    assert not storage.STORAGE_FILE.exists()
    assert storage.list_all() == []


# ---------------------------------------------------------------------------
# Cache in-process
# ---------------------------------------------------------------------------

def test_cache_reloads_after_external_write():
    """Un autre processus (api.py, poller) peut ecrire le fichier : le cache
    doit se rafraichir quand le mtime change."""
    session = make_session(name="avant")
    storage.save(session)

    # Simule une ecriture externe : modifie le fichier directement
    data = json.loads(storage.STORAGE_FILE.read_text(encoding="utf-8"))
    data[session.id]["name"] = "apres"
    storage.STORAGE_FILE.write_text(json.dumps(data, default=str),
                                    encoding="utf-8")
    # Force un mtime different (les ecritures rapides peuvent partager le meme)
    stat = storage.STORAGE_FILE.stat()
    os.utime(storage.STORAGE_FILE, (stat.st_atime, stat.st_mtime + 10))

    loaded = storage.get(session.id)
    assert loaded is not None
    assert loaded.name == "apres"


def test_cache_hit_without_file_change():
    session = make_session()
    storage.save(session)

    first = storage.get(session.id)
    second = storage.get(session.id)
    # Meme objet : le cache a servi la deuxieme lecture sans relire le fichier
    assert first is second


# ---------------------------------------------------------------------------
# Cap des logs
# ---------------------------------------------------------------------------

def test_logs_capped_at_max_keeps_most_recent():
    session = make_session()
    session.logs = [LogEntry(message=f"log {i}")
                    for i in range(storage.MAX_LOGS + 50)]
    storage.save(session)

    loaded = storage.get(session.id)
    assert len(loaded.logs) == storage.MAX_LOGS
    assert loaded.logs[-1].message == f"log {storage.MAX_LOGS + 49}"
    assert loaded.logs[0].message == "log 50"


# ---------------------------------------------------------------------------
# Purge des vieilles sessions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status,age,should_survive",
    [
        (SessionStatus.DONE, timedelta(days=storage.TTL_DAYS + 1), False),
        (SessionStatus.DONE, timedelta(days=1), True),
        (SessionStatus.ERROR, timedelta(days=storage.TTL_DAYS + 1), False),
        (SessionStatus.PAUSED, timedelta(days=storage.TTL_DAYS + 1), False),
        (SessionStatus.RUNNING, timedelta(hours=25), False),  # orphelin
        (SessionStatus.RUNNING, timedelta(hours=1), True),
    ],
)
def test_purge_old_sessions(status, age, should_survive):
    session = make_session(status=status)
    session.updated_at = datetime.now() - age
    storage.save(session)

    purged = storage.purge_old_sessions()

    if should_survive:
        assert purged == 0
        assert storage.get(session.id) is not None
    else:
        assert purged == 1
        assert storage.get(session.id) is None
