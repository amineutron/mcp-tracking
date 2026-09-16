"""Tests d'integration de api.py : le vrai serveur HTTP sur un port ephemere,
le vrai storage (redirige vers tmp_path par la fixture autouse de conftest).
Pas de mock : on teste le comportement que dv_convert.py et le poller voient.
"""
import threading
from http.server import ThreadingHTTPServer

import pytest
import requests

import api


# En-tetes d'ecriture : l'API exige le jeton local depuis l'issue #40.
AUTH: dict = {}


@pytest.fixture
def base_url(tmp_path):
    """Demarre l'API reelle sur un port libre, l'arrete a la fin du test."""
    token = api.ensure_token(tmp_path / "token")
    AUTH.clear()
    AUTH["Authorization"] = f"Bearer {token}"
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
    r = requests.post(f"{base_url}/sessions", json=payload, timeout=5, headers=AUTH)
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
                     json={"processed": 1}, timeout=5, headers=AUTH)
    assert r.status_code == 404


def test_delete_unknown_session_404(base_url):
    r = requests.delete(f"{base_url}/sessions/inexistant", timeout=5, headers=AUTH)
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
    }, timeout=5, headers=AUTH)
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
    }, timeout=5, headers=AUTH)
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
                     json={"status": "statut_invalide"}, timeout=5, headers=AUTH)
    assert r.status_code == 200

    data = requests.get(f"{base_url}/sessions/{sid}", timeout=5).json()
    assert data["status"] == "running"  # inchange


def test_delete_session(base_url):
    sid = create_session(base_url)

    r = requests.delete(f"{base_url}/sessions/{sid}", timeout=5, headers=AUTH)
    assert r.status_code == 200
    assert requests.get(f"{base_url}/sessions/{sid}", timeout=5).status_code == 404


# ---------------------------------------------------------------------------
# Issue #40 : l'API locale n'est pas pilotable par une page web
# ---------------------------------------------------------------------------

def test_ecriture_sans_jeton_401(base_url):
    r = requests.post(f"{base_url}/sessions", json={"name": "x", "template": "free"}, timeout=5)
    assert r.status_code == 401


def test_ecriture_mauvais_jeton_401(base_url):
    r = requests.post(f"{base_url}/sessions", json={"name": "x", "template": "free"},
                      headers={"Authorization": "Bearer faux"}, timeout=5)
    assert r.status_code == 401


def test_requete_depuis_un_navigateur_403(base_url):
    """Un en-tete Origin signe une requete emise par une page web : refusee, meme avec le jeton."""
    for method, kwargs in (("get", {}), ("post", {"json": {"name": "x", "template": "free"}})):
        r = getattr(requests, method)(f"{base_url}/sessions",
                                      headers={**AUTH, "Origin": "https://exemple.test"}, timeout=5, **kwargs)
        assert r.status_code == 403


def test_hote_non_local_403(base_url):
    r = requests.get(f"{base_url}/sessions", headers={"Host": "tracking.exemple.test"}, timeout=5)
    assert r.status_code == 403


def test_type_de_contenu_refuse_415(base_url):
    r = requests.post(f"{base_url}/sessions", data="name=x",
                      headers={**AUTH, "Content-Type": "application/x-www-form-urlencoded"}, timeout=5)
    assert r.status_code == 415


def test_lecture_toujours_libre(base_url):
    assert requests.get(f"{base_url}/health", timeout=5).status_code == 200


def test_pid_non_enregistre_pas_de_signal(base_url):
    """Un pid fourni par le client est verifie par le serveur ; s'il est inutilisable,
    il n'est pas enregistre et aucun signal ne partira (issue #40)."""
    sid = create_session(base_url, pid=999999)          # pid inexistant
    r = requests.get(f"{base_url}/sessions/{sid}", timeout=5)
    assert r.json()["pid"] is None
    r = requests.post(f"{base_url}/sessions/{sid}/kill", headers=AUTH, timeout=5)
    assert r.status_code == 200 and r.json()["signal"] is None


def test_pid_recycle_refuse():
    """Meme pid, autre processus : l'empreinte de demarrage ne correspond plus, signal refuse."""
    import os
    import mutations
    from models import TrackingSession
    session = TrackingSession(name="x", template="free", pid=os.getpid(),
                              pid_starttime=(mutations.process_starttime(os.getpid()) or 0) + 1)
    note = mutations.stop(session)
    assert "recycle" in note and "refuse" in note
