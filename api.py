#!/usr/bin/env python3
"""
API HTTP locale pour le tracking -- 127.0.0.1:8765
Appelee par dv_convert.py et le poller pour creer/mettre a jour des sessions.
"""
import json
import sys
import time
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).parent))

import storage
from models import ItemStatus, LogEntry, SessionStatus, TrackingItem, TrackingSession

API_HOST = "127.0.0.1"
API_PORT = 8765

_file_lock = Lock()


def _purge_loop() -> None:
    """Purge les sessions terminees toutes les heures."""
    while True:
        time.sleep(3600)
        try:
            storage.purge_old_sessions()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Handler HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):

    def _respond(self, code: int, data) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n)) if n else {}

    def _session_id(self) -> str:
        # /sessions/{id}  ->  id
        parts = self.path.strip("/").split("/")
        return parts[1] if len(parts) >= 2 else ""

    # -- GET -----------------------------------------------------------------

    def do_GET(self) -> None:
        if self.path in ("/sessions", "/sessions/"):
            sessions = storage.list_all()
            self._respond(200, [s.model_dump(mode="json") for s in sessions])
        elif self.path.startswith("/sessions/"):
            sid = self._session_id()
            session = storage.get(sid)
            if not session:
                self._respond(404, {"error": "not found"})
            else:
                self._respond(200, session.model_dump(mode="json"))
        elif self.path == "/health":
            self._respond(200, {"ok": True})
        else:
            self._respond(404, {"error": "not found"})

    # -- POST ----------------------------------------------------------------

    def do_POST(self) -> None:
        if self.path in ("/sessions", "/sessions/"):
            body = self._body()
            session = TrackingSession(
                name=body["name"],
                template=body.get("template", "free"),
                total=float(body.get("total", 100)),
                unit=body.get("unit", ""),
                extra=body.get("extra", {}),
            )
            for item_def in (body.get("items") or []):
                item_kwargs = dict(
                    name=item_def["name"],
                    status=ItemStatus(item_def.get("status", "pending")),
                    total=item_def.get("total"),
                    unit=item_def.get("unit", ""),
                )
                if item_def.get("note") is not None:
                    item_kwargs["note"] = item_def["note"]
                session.items.append(TrackingItem(**item_kwargs))
            with _file_lock:
                storage.save(session)
            self._respond(201, {"id": session.id})
        else:
            self._respond(404, {"error": "not found"})

    # -- PUT -----------------------------------------------------------------

    def do_PUT(self) -> None:
        sid = self._session_id()
        if not sid:
            self._respond(400, {"error": "missing session id"})
            return

        body = self._body()

        with _file_lock:
            session = storage.get(sid)
            if not session:
                self._respond(404, {"error": f"session {sid} not found"})
                return

            if "name" in body and str(body["name"]).strip():
                # renommage (ex: torrent metaDL dont les metadonnees arrivent)
                session.name = str(body["name"]).strip()
            if "processed" in body:
                session.processed = float(body["processed"])
            if "total" in body:
                session.total = float(body["total"])
            if "status" in body:
                try:
                    session.status = SessionStatus(body["status"])
                except ValueError:
                    pass
            if "extra" in body and isinstance(body["extra"], dict):
                session.extra.update(body["extra"])
            if "log" in body:
                session.logs.append(LogEntry(message=str(body["log"])))

            # Mise a jour d'un item par nom
            if "item" in body:
                item_data = body["item"]
                item_name = item_data.get("name", "")
                item = next((i for i in session.items if i.name == item_name), None)
                if item:
                    if "status" in item_data:
                        try:
                            item.status = ItemStatus(item_data["status"])
                        except ValueError:
                            pass
                    if "note" in item_data:
                        item.note = item_data["note"]
                    if "processed" in item_data:
                        item.processed = item_data["processed"]
                    if "total" in item_data:
                        item.total = item_data["total"]

            session.updated_at = datetime.now()
            storage.save(session)

        self._respond(200, {"ok": True})

    # -- DELETE --------------------------------------------------------------

    def do_DELETE(self) -> None:
        sid = self._session_id()
        if not sid:
            self._respond(400, {"error": "missing session id"})
            return
        with _file_lock:
            deleted = storage.delete(sid)
        if not deleted:
            self._respond(404, {"error": "not found"})
            return
        self._respond(200, {"ok": True})

    def log_message(self, fmt, *args) -> None:
        pass  # silence logs HTTP


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    t = threading.Thread(target=_purge_loop, daemon=True)
    t.start()

    server = HTTPServer((API_HOST, API_PORT), Handler)
    print(f"Tracking API ecoute sur http://{API_HOST}:{API_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
