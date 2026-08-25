"""Tests d'integration de api.py : le vrai serveur HTTP sur un port ephemere,
le vrai storage (redirige vers tmp_path par la fixture autouse de conftest).
Pas de mock : on teste le comportement que dv_convert.py et le poller voient.
"""
import threading
from http.server import ThreadingHTTPServer

import pytest
import requests

import api


@pytest.fixture
def base_url():
    """Demarre l'API reelle sur un port libre, l'arrete a la fin du test."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)


def create_session(base_url: str, **overrides) -> str:
    payload = {"name": "session de test", "template": "machine"}
    payload.update(overrides)
    r = requests.post(f"{base_url}/sessions", json=payload, timeout=5)
    assert r.status_code == 201
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Health et 404
# ---------------------------------------------------------------------------

def test_health(base_url):
    r = requests.get(f"{base_url}/health", timeout=5)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_get_unknown_session_404(base_url):
    r = requests.get(f"{base_url}/sessions/inexistant", timeout=5)
    assert r.status_code == 404


def test_put_unknown_session_404(base_url):
    r = requests.put(f"{base_url}/sessions/inexistant",
                     json={"processed": 1}, timeout=5)
    assert r.status_code == 404


def test_delete_unknown_session_404(base_url):
    r = requests.delete(f"{base_url}/sessions/inexistant", timeout=5)
    assert r.status_code == 404


def test_unknown_route_404(base_url):
    r = requests.get(f"{base_url}/nimporte/quoi", timeout=5)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Cycle de vie complet (le flux que dv_convert.py utilise)
# ---------------------------------------------------------------------------

def test_create_then_get(base_url):
    sid = create_session(base_url, name="conversion DV", total=6, unit="etapes")

    r = requests.get(f"{base_url}/sessions/{sid}", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "conversion DV"
    assert data["template"] == "machine"
    assert data["total"] == 6
    assert data["status"] == "running"


def test_create_with_items(base_url):
    sid = create_session(base_url, items=[
        {"name": "Etape 1", "status": "running", "note": "demux"},
        {"name": "Etape 2"},
    ])

    data = requests.get(f"{base_url}/sessions/{sid}", timeout=5).json()
    assert [i["name"] for i in data["items"]] == ["Etape 1", "Etape 2"]
    assert data["items"][0]["status"] == "running"
    assert data["items"][0]["note"] == "demux"
    assert data["items"][1]["status"] == "pending"


def test_list_sessions(base_url):
    create_session(base_url, name="a")
    create_session(base_url, name="b")

    r = requests.get(f"{base_url}/sessions", timeout=5)
    assert r.status_code == 200
    assert {s["name"] for s in r.json()} == {"a", "b"}


def test_update_progress_status_and_log(base_url):
    sid = create_session(base_url)
    created = requests.get(f"{base_url}/sessions/{sid}", timeout=5).json()

    r = requests.put(f"{base_url}/sessions/{sid}", json={
        "processed": 42,
        "status": "done",
        "log": "conversion terminee",
        "extra": {"fichier": "film.mkv"},
    }, timeout=5)
    assert r.status_code == 200

    data = requests.get(f"{base_url}/sessions/{sid}", timeout=5).json()
    assert data["processed"] == 42
    assert data["status"] == "done"
    assert data["logs"][-1]["message"] == "conversion terminee"
    assert data["extra"]["fichier"] == "film.mkv"
    assert data["updated_at"] > created["updated_at"]


def test_update_item_by_name(base_url):
    sid = create_session(base_url, items=[
        {"name": "Etape 1", "status": "running"},
        {"name": "Etape 2"},
    ])

    r = requests.put(f"{base_url}/sessions/{sid}", json={
        "item": {"name": "Etape 1", "status": "done",
                 "processed": 100, "note": "ok"},
    }, timeout=5)
    assert r.status_code == 200

    items = requests.get(f"{base_url}/sessions/{sid}", timeout=5).json()["items"]
    etape1 = next(i for i in items if i["name"] == "Etape 1")
    etape2 = next(i for i in items if i["name"] == "Etape 2")
    assert etape1["status"] == "done"
    assert etape1["processed"] == 100
    assert etape1["note"] == "ok"
    assert etape2["status"] == "pending"  # pas touche


def test_invalid_status_is_ignored(base_url):
    sid = create_session(base_url)

    r = requests.put(f"{base_url}/sessions/{sid}",
                     json={"status": "statut_invalide"}, timeout=5)
    assert r.status_code == 200

    data = requests.get(f"{base_url}/sessions/{sid}", timeout=5).json()
    assert data["status"] == "running"  # inchange


def test_delete_session(base_url):
    sid = create_session(base_url)

    r = requests.delete(f"{base_url}/sessions/{sid}", timeout=5)
    assert r.status_code == 200
    assert requests.get(f"{base_url}/sessions/{sid}", timeout=5).status_code == 404
