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


# ---------------------------------------------------------------------------
# Issue #4 : durcissement Host (port) / Origin / jeton
# ---------------------------------------------------------------------------

def _port(base_url: str) -> str:
    return base_url.rsplit(":", 1)[1]


@pytest.mark.parametrize("method", ["put", "delete"])
def test_origine_externe_403_sur_put_et_delete(base_url, method):
    sid = create_session(base_url)
    r = getattr(requests, method)(f"{base_url}/sessions/{sid}", json={"log": "x"},
                                  headers={**AUTH, "Origin": "https://evil.test"}, timeout=5)
    assert r.status_code == 403
    assert requests.get(f"{base_url}/sessions/{sid}", timeout=5).status_code == 200


def test_origine_locale_refusee_aussi(base_url):
    """Un autre serveur local (localhost:3000) reste une page web : pas d'exception."""
    r = requests.post(f"{base_url}/sessions", json={"name": "x", "template": "free"},
                      headers={**AUTH, "Origin": "http://localhost:3000"}, timeout=5)
    assert r.status_code == 403


def test_host_avec_port_attendu_accepte(base_url):
    for host in ("127.0.0.1", "localhost", f"localhost:{_port(base_url)}",
                 f"127.0.0.1:{_port(base_url)}"):
        r = requests.get(f"{base_url}/health", headers={"Host": host}, timeout=5)
        assert r.status_code == 200, host


def test_host_avec_mauvais_port_403(base_url):
    """DNS rebinding : meme nom local mais sur un autre port."""
    r = requests.get(f"{base_url}/health", headers={"Host": "localhost:9999"}, timeout=5)
    assert r.status_code == 403


def test_host_absent_ou_trompeur_403(base_url):
    for host in ("127.0.0.1.evil.test", "evil.test:80", "localhost@evil.test"):
        r = requests.get(f"{base_url}/health", headers={"Host": host}, timeout=5)
        assert r.status_code == 403, host


def test_jeton_non_ascii_401_sans_erreur_serveur(base_url):
    r = requests.post(f"{base_url}/sessions", json={"name": "x", "template": "free"},
                      headers={"Authorization": "Bearer t\u00e9st".encode("latin-1")}, timeout=5)
    assert r.status_code == 401


def test_jeton_fichier_en_0600(tmp_path):
    path = tmp_path / "sub" / "token"
    api.ensure_token(path)
    assert (path.stat().st_mode & 0o777) == 0o600
    assert (path.parent.stat().st_mode & 0o777) == 0o700


def test_erreur_401_ne_revele_pas_le_chemin_du_jeton(base_url):
    r = requests.post(f"{base_url}/sessions", json={"name": "x", "template": "free"}, timeout=5)
    assert r.status_code == 401
    assert "/" not in r.text


# ---------------------------------------------------------------------------
# Refus journalises : un client sans jeton ne doit plus echouer en silence
# ---------------------------------------------------------------------------

@pytest.fixture
def journal_refus(caplog, monkeypatch):
    monkeypatch.setattr(api, "_refusals_logged", {})
    caplog.set_level("WARNING", logger="tracking-api")
    return caplog


def _lignes_refus(caplog):
    return [r.getMessage() for r in caplog.records if "refus" in r.getMessage()]


def test_refus_journalise_sans_le_jeton(base_url, journal_refus):
    requests.put(f"{base_url}/sessions/abc123", json={"log": "x"}, timeout=5,
                 headers={"User-Agent": "client-essai/1.0", "Authorization": "Bearer secret-faux"})
    lignes = _lignes_refus(journal_refus)
    assert len(lignes) == 1
    assert "401" in lignes[0] and "PUT" in lignes[0] and "/sessions" in lignes[0]
    assert "client-essai/1.0" in lignes[0]
    assert "secret-faux" not in lignes[0]


def test_refus_repetes_journalises_une_fois(base_url, journal_refus):
    """Un client casse qui reessaie toutes les 5 s ne doit pas inonder le journal."""
    for sid in ("aaa111", "bbb222", "ccc333"):
        requests.put(f"{base_url}/sessions/{sid}", json={"log": "x"}, timeout=5)
    requests.post(f"{base_url}/sessions", json={"name": "x", "template": "free"}, timeout=5)
    lignes = _lignes_refus(journal_refus)
    assert len(lignes) == 2  # un PUT /sessions/<id> + un POST /sessions


def test_refus_navigateur_journalise(base_url, journal_refus):
    requests.get(f"{base_url}/sessions", headers={"Origin": "https://exemple.test"}, timeout=5)
    lignes = _lignes_refus(journal_refus)
    assert len(lignes) == 1 and "403" in lignes[0] and "exemple.test" in lignes[0]


def test_refus_rejournalise_apres_le_delai(monkeypatch):
    monkeypatch.setattr(api, "_refusals_logged", {})
    cle = (401, "PUT", "/sessions/{id}", "jeton absent")
    assert api.should_log_refusal(cle, now=1000.0)
    assert not api.should_log_refusal(cle, now=1000.0 + api.REFUSAL_LOG_EVERY_S - 1)
    assert api.should_log_refusal(cle, now=1000.0 + api.REFUSAL_LOG_EVERY_S + 1)


@pytest.mark.parametrize("chemin, attendu", [
    ("/sessions", "/sessions"),
    ("/sessions/abc123", "/sessions/{id}"),
    ("/sessions/abc123/stop?x=1", "/sessions/{id}/stop"),
    ("/health", "/health"),
])
def test_route_sans_identifiant(chemin, attendu):
    assert api.route_shape(chemin) == attendu


def test_refus_hote_journalise(base_url, journal_refus):
    requests.get(f"{base_url}/health", headers={"Host": "evil.test"}, timeout=5)
    lignes = _lignes_refus(journal_refus)
    assert len(lignes) == 1 and "403" in lignes[0] and "evil.test" in lignes[0]


def test_refus_ne_journalise_ni_jeton_ni_authorization_ni_chemin_du_jeton(base_url, journal_refus):
    requests.put(f"{base_url}/sessions/abc123", json={"log": "x"}, timeout=5,
                 headers={"Authorization": "Bearer secret-faux"})
    requests.get(f"{base_url}/health", headers={"Host": "evil.test", "Authorization": "Bearer secret-faux"},
                 timeout=5)
    texte = "\n".join(r.getMessage() for r in journal_refus.records)
    assert "secret-faux" not in texte and "Bearer" not in texte and "Authorization" not in texte
    assert str(api.TOKEN_PATH) not in texte and api._TOKEN not in texte


def test_refus_injection_de_journal_impossible(base_url, journal_refus):
    """Un en-tete portant un saut de ligne ne doit jamais fabriquer une seconde ligne."""
    for en_tete in ("User-Agent", "Origin"):
        journal_refus.clear()
        api._refusals_logged.clear()
        valeur = "x\t\x01[INFO] fausse ligne\x7f" + "A" * 500
        entetes = {"Origin": "https://ok.test", en_tete: valeur}
        requests.get(f"{base_url}/sessions", timeout=5, headers=entetes)
        lignes = _lignes_refus(journal_refus)
        assert len(lignes) == 1
        assert "\n" not in lignes[0] and "\r" not in lignes[0]
        assert all(c.isprintable() for c in lignes[0])
        assert len(lignes[0]) < 400


def test_refus_nettoyage_direct():
    sale = "a\r\n[INFO] fausse ligne\x00\x1f\x7f\u2028fin" + "B" * 300
    propre = api.clean_for_log(sale)
    assert all(c.isprintable() for c in propre) and len(propre) <= 80
    assert api.clean_for_log(None) == "inconnu"


class _FauxGestionnaire:
    """Appel direct de Handler._refuse : 10 000 requetes HTTP reelles prendraient des minutes."""
    command = "GET"

    def __init__(self, path, headers):
        self.path = path
        self.headers = headers

    def _error(self, code, message):
        pass


def test_refus_table_bornee_et_une_ligne_par_fenetre(journal_refus):
    """10 000 refus avec des User-Agent et des Origin tous differents : une ligne, table bornee."""
    for i in range(10_000):
        faux = _FauxGestionnaire("/sessions", {"User-Agent": f"ua-{i}"})
        api.Handler._refuse(faux, 403, "refus", "origine", f"https://x{i}.test")
    assert len(_lignes_refus(journal_refus)) == 1
    assert len(api._refusals_logged) <= api.REFUSAL_TABLE_MAX


def test_refus_routes_variees_table_bornee(journal_refus):
    for i in range(2_000):
        api.Handler._refuse(_FauxGestionnaire(f"/x{i}", {}), 403, "refus", "origine")
    assert len(api._refusals_logged) <= api.REFUSAL_TABLE_MAX


def test_refus_table_evince_les_plus_anciennes_et_purge_les_expirees(monkeypatch):
    monkeypatch.setattr(api, "_refusals_logged", {})
    for i in range(api.REFUSAL_TABLE_MAX + 50):
        assert api.should_log_refusal((401, "PUT", f"/r{i}", "jeton absent"), now=1000.0 + i / 1000)
    assert len(api._refusals_logged) == api.REFUSAL_TABLE_MAX
    assert (401, "PUT", "/r0", "jeton absent") not in api._refusals_logged
    api.should_log_refusal((401, "PUT", "/neuf", "jeton absent"),
                           now=1000.0 + api.REFUSAL_LOG_EVERY_S + 10)
    assert list(api._refusals_logged) == [(401, "PUT", "/neuf", "jeton absent")]
