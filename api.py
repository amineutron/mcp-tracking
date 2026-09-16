#!/usr/bin/env python3
"""
API HTTP locale pour le tracking -- 127.0.0.1:8765
Seul point d'ecriture recommande pour les scripts externes (dv_convert.py,
poller, tracking.sh, Lyra, lyra-control-api).

Routes :
  GET    /health
  GET    /templates
  GET    /sessions[?template=x&status=y]      (avec metriques calculees)
  POST   /sessions
  GET    /sessions/{id}
  PUT    /sessions/{id}                        (voir mutations.apply_update)
  DELETE /sessions/{id}
  POST   /sessions/{id}/stop                   (SIGTERM si pid + status paused)
  POST   /sessions/{id}/kill                   (SIGKILL si pid + suppression)
  GET    /events                               (SSE : etat complet a chaque changement)
"""
import hmac
import json
import logging
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field, ValidationError

sys.path.insert(0, str(Path(__file__).parent))

import metrics
import mutations
import storage
from models import TrackingItem, TrackingSession
from templates import TEMPLATES, validate_template

API_HOST = "127.0.0.1"
API_PORT = 8765
WRITE_METHODS = ("POST", "PUT", "DELETE")
# Jeton lu par les clients locaux (poller, dv_convert, Lyra, tracking.sh...) :
# lisible par le seul utilisateur du service, donc inaccessible a une page web.
# Ordre de recherche, du plus explicite au plus general :
#   TRACKING_TOKEN_FILE  -> chemin impose (tests, cas particuliers)
#   RUNTIME_DIRECTORY    -> /run/tracking, cree par systemd (RuntimeDirectory=tracking)
#   XDG_RUNTIME_DIR      -> /run/user/<uid>/tracking, session utilisateur
def token_path() -> Path:
    forced = os.environ.get("TRACKING_TOKEN_FILE")
    if forced:
        return Path(forced)
    runtime = os.environ.get("RUNTIME_DIRECTORY", "").split(":")[0]
    if runtime:
        return Path(runtime) / "token"
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(base) / "tracking" / "token"


TOKEN_PATH = token_path()
_TOKEN = ""

log = logging.getLogger("tracking-api")
MAX_BODY_BYTES = 2 * 1024 * 1024
SSE_POLL_S = 0.5
SSE_HEARTBEAT_S = 15.0
PURGE_INTERVAL_S = 3600


# ---------------------------------------------------------------------------
# Schemas d'entree (validation aux frontieres)
# ---------------------------------------------------------------------------

class ItemIn(BaseModel):
    name: str = Field(min_length=1)
    status: str = "pending"
    total: Optional[float] = None
    processed: Optional[float] = None
    unit: str = ""
    note: Optional[str] = None


class CreateSessionIn(BaseModel):
    name: str = Field(min_length=1)
    template: str = "free"
    total: float = 100
    unit: str = ""
    extra: Dict[str, Any] = {}
    items: List[ItemIn] = []
    pid: Optional[int] = None


class UpdateSessionIn(BaseModel):
    """Validation souple : chaque cle est optionnelle, les types sont verifies."""
    name: Optional[str] = None
    processed: Optional[float] = None
    total: Optional[float] = None
    status: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None
    log: Optional[str] = None
    log_level: Optional[str] = None
    item: Optional[Dict[str, Any]] = None
    items: Optional[List[Dict[str, Any]]] = None
    replace_items: Optional[List[Dict[str, Any]]] = None
    pid: Optional[int] = None


def _serialize(session: TrackingSession) -> Dict[str, Any]:
    data = session.model_dump(mode="json")
    data["metrics"] = metrics.compute(session)
    return data


def _snapshot() -> List[Dict[str, Any]]:
    return [_serialize(s) for s in storage.list_all()]


# ---------------------------------------------------------------------------
# Taches de fond
# ---------------------------------------------------------------------------

def _purge_loop() -> None:
    while True:
        time.sleep(PURGE_INTERVAL_S)
        try:
            n = storage.purge_old_sessions()
            if n:
                print(f"purge: {n} session(s) supprimee(s)", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"purge erreur: {exc}", flush=True)


def ensure_token(path: Optional[Path] = None) -> str:
    """Jeton de la session courante : relu s'il existe, genere sinon (fichier 0600)."""
    global _TOKEN, TOKEN_PATH
    path = path or token_path()
    TOKEN_PATH = path
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_file():
            existing = path.read_text().strip()
            if existing:
                _TOKEN = existing
                return _TOKEN
        _TOKEN = secrets.token_urlsafe(32)
        path.write_text(_TOKEN + "\n")
        path.chmod(0o600)
    except OSError as exc:  # pas de runtime dir (conteneur, cron) : on reste sans jeton
        log.warning("jeton non persiste (%s) : l'API refusera toute ecriture", exc)
        _TOKEN = _TOKEN or secrets.token_urlsafe(32)
    return _TOKEN


def register_pid(pid: Optional[int]) -> tuple:
    """Verifie qu'un pid declare est bien un processus vivant nous appartenant.

    Retourne (pid, empreinte) ou (None, None) si le pid est inutilisable : le serveur
    n'enverra alors jamais de signal pour cette session.
    """
    if not pid:
        return None, None
    if not mutations.owned_by_us(pid):
        log.warning("pid %s refuse : processus inexistant ou d'un autre utilisateur", pid)
        return None, None
    return pid, mutations.process_starttime(pid)


# ---------------------------------------------------------------------------
# Handler HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- utilitaires --------------------------------------------------------

    def _respond(self, code: int, data) -> None:
        body = json.dumps(data, default=str).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client parti (curl --max-time) : rien a faire

    def _error(self, code: int, message: str) -> None:
        self._respond(code, {"ok": False, "error": message})

    def _body(self) -> Optional[dict]:
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._error(400, "Content-Length invalide")
            return None
        if n > MAX_BODY_BYTES:
            self._error(413, "corps trop volumineux")
            return None
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            self._error(400, "JSON invalide")
            return None
        if not isinstance(data, dict):
            self._error(400, "un objet JSON est attendu")
            return None
        return data

    def _guard(self) -> bool:
        """Controles communs a toutes les requetes. Renvoie False si la requete est refusee.

        - un en-tete Origin signifie que la requete vient d'une page web : refuse (une page
          ouverte dans le navigateur peut sinon piloter l'API locale, cf. issue #40) ;
        - l'en-tete Host doit designer la boucle locale ;
        - toute ecriture exige le jeton local (Authorization: Bearer ...) et du JSON.
        """
        if self.headers.get("Origin"):
            self._error(403, "requete issue d'un navigateur refusee")
            return False
        host = (self.headers.get("Host") or "").split(":")[0]
        if host and host not in ("127.0.0.1", "localhost", "::1", "[::1]"):
            self._error(403, f"hote non local refuse : {host}")
            return False
        if self.command in WRITE_METHODS:
            sent = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            if not _TOKEN or not hmac.compare_digest(sent, _TOKEN):
                self._error(401, f"jeton requis : lire {TOKEN_PATH}")
                return False
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype and ctype != "application/json":
                self._error(415, "Content-Type application/json attendu")
                return False
        return True

    def _route(self) -> tuple:
        """Retourne (segments, query) : /sessions/abc/stop -> ["sessions","abc","stop"]."""
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        return parts, parse_qs(parsed.query)

    # -- GET ----------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._guard():
            return
        parts, query = self._route()
        if parts == ["health"]:
            self._respond(200, {"ok": True})
        elif parts == ["templates"]:
            self._respond(200, TEMPLATES)
        elif parts == ["events"]:
            self._sse()
        elif parts == ["sessions"]:
            template = (query.get("template") or [None])[0]
            status = (query.get("status") or [None])[0]
            sessions = storage.list_all(template=template, status=status)
            self._respond(200, [_serialize(s) for s in sessions])
        elif len(parts) == 2 and parts[0] == "sessions":
            session = storage.get(parts[1])
            if not session:
                self._error(404, "not found")
            else:
                self._respond(200, _serialize(session))
        else:
            self._error(404, "not found")

    # -- POST ---------------------------------------------------------------

    def do_POST(self) -> None:
        if not self._guard():
            return
        parts, _ = self._route()
        if parts == ["sessions"]:
            self._create()
        elif len(parts) == 3 and parts[0] == "sessions" and parts[2] in ("stop", "kill"):
            self._signal(parts[1], parts[2])
        else:
            self._error(404, "not found")

    def _create(self) -> None:
        body = self._body()
        if body is None:
            return
        try:
            req = CreateSessionIn.model_validate(body)
        except ValidationError as exc:
            self._error(400, f"corps invalide: {exc.errors()[0].get('msg')} "
                             f"({'.'.join(str(x) for x in exc.errors()[0].get('loc', ()))})")
            return
        if not validate_template(req.template):
            self._error(400, f"template inconnu: {req.template}")
            return

        pid, starttime = register_pid(req.pid)
        session = TrackingSession(
            name=req.name.strip(), template=req.template, total=req.total,
            unit=req.unit, extra=req.extra, pid=pid, pid_starttime=starttime,
        )
        for it in req.items:
            item = TrackingItem(name=it.name, total=it.total, processed=it.processed,
                                unit=it.unit, note=it.note or "")
            mutations.set_item_status(item, it.status)
            session.items.append(item)
        storage.save(session)
        self._respond(201, {"ok": True, "id": session.id})

    def _signal(self, sid: str, action: str) -> None:
        body = self._body() or {}
        if action == "stop":
            notes: list = []
            session = storage.modify(sid, lambda s: notes.append(
                mutations.stop(s, body.get("message"))))
            if session is None:
                self._error(404, f"session {sid} not found")
                return
            log.info("stop session=%s pid=%s -> %s", sid, session.pid, notes[0])
            self._respond(200, {"ok": True, "status": "paused", "signal": notes[0]})
        else:
            session = storage.get(sid)
            if session is None:
                self._error(404, f"session {sid} not found")
                return
            note = mutations.kill_process(session)
            log.info("kill session=%s pid=%s -> %s", sid, session.pid, note)
            storage.delete(sid)
            self._respond(200, {"ok": True, "deleted": True, "signal": note})

    # -- PUT ----------------------------------------------------------------

    def do_PUT(self) -> None:
        if not self._guard():
            return
        parts, _ = self._route()
        if len(parts) != 2 or parts[0] != "sessions":
            self._error(404, "not found")
            return
        sid = parts[1]
        body = self._body()
        if body is None:
            return
        try:
            UpdateSessionIn.model_validate(body)
        except ValidationError as exc:
            err = exc.errors()[0]
            self._error(400, f"corps invalide: {err.get('msg')} "
                             f"({'.'.join(str(x) for x in err.get('loc', ()))})")
            return

        session = storage.modify(sid, lambda s: mutations.apply_update(s, body))
        if session is None:
            self._error(404, f"session {sid} not found")
            return
        self._respond(200, {"ok": True, "metrics": metrics.compute(session)})

    # -- DELETE -------------------------------------------------------------

    def do_DELETE(self) -> None:
        if not self._guard():
            return
        parts, _ = self._route()
        if len(parts) != 2 or parts[0] != "sessions":
            self._error(404, "not found")
            return
        if not storage.delete(parts[1]):
            self._error(404, "not found")
            return
        self._respond(200, {"ok": True})

    # -- SSE ----------------------------------------------------------------

    def _sse(self) -> None:
        """Flux text/event-stream : un evenement `sessions` a chaque changement
        du fichier d'etat, un `ping` toutes les SSE_HEARTBEAT_S secondes."""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            last_mtime = None
            last_beat = time.monotonic()
            while True:
                mtime = storage._file_mtime_ns()
                if mtime != last_mtime:
                    payload = json.dumps(_snapshot(), default=str)
                    self.wfile.write(f"event: sessions\ndata: {payload}\n\n".encode())
                    self.wfile.flush()
                    last_mtime = mtime
                    last_beat = time.monotonic()
                elif time.monotonic() - last_beat >= SSE_HEARTBEAT_S:
                    self.wfile.write(b"event: ping\ndata: {}\n\n")
                    self.wfile.flush()
                    last_beat = time.monotonic()
                time.sleep(SSE_POLL_S)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def log_message(self, fmt, *args) -> None:
        pass  # silence logs HTTP


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def make_server(host: str = API_HOST, port: int = API_PORT) -> ThreadingHTTPServer:
    ensure_token()
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main() -> None:
    threading.Thread(target=_purge_loop, daemon=True).start()
    server = make_server()
    print(f"Tracking API ecoute sur http://{API_HOST}:{API_PORT}  "
          f"(etat: {storage.STORAGE_FILE})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
