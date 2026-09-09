#!/usr/bin/env python3
"""
Poller media-server -> tracking API
- qBittorrent : poll toutes les 10 secondes (downloads actifs)
- Bazarr      : poll toutes les 60 secondes (sous-titres manquants)

Tourne comme service systemd (tracking-poller.service).
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path

CREDS_DIR = Path(__file__).parent / "credentials"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TRACKING_API   = "http://127.0.0.1:8765"

QBT_HOST       = "http://localhost:8080"
QBT_USER       = "admin"
QBT_POLL_SECS  = 10

BAZARR_HOST    = "http://localhost:6767"
BAZARR_POLL_SECS = 60


def _secret(cred_name: str, env_var: str) -> str | None:
    """
    Charge un secret sans jamais le coder en dur, dans l'ordre :
    1. credential systemd injecte ($CREDENTIALS_DIRECTORY/<cred_name>)
       -- cas d'un service *user* avec LoadCredentialEncrypted ;
    2. dechiffrement direct du blob credentials/<cred_name>.cred via
       `systemd-creds decrypt --user` -- cas de ce service *systeme* tournant
       en tant qu'amineutron (chiffre au repos, gere par rotate-secrets.sh) ;
    3. variable d'environnement <env_var> (dev / debug ponctuel).
    Renvoie None si aucune source -> le poll concerne est desactive.
    """
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir:
        p = Path(cred_dir) / cred_name
        if p.exists():
            return p.read_text().strip()

    blob = CREDS_DIR / f"{cred_name}.cred"
    if blob.exists():
        env = dict(os.environ)
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        try:
            out = subprocess.run(
                ["systemd-creds", "decrypt", "--user",
                 f"--name={cred_name}", str(blob), "-"],
                capture_output=True, env=env, timeout=10,
            )
            if out.returncode == 0:
                return out.stdout.decode().strip()
            print(f"systemd-creds decrypt {cred_name} -> "
                  f"{out.stderr.decode(errors='replace').strip()}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"systemd-creds decrypt {cred_name} -> {e}", flush=True)

    return os.environ.get(env_var)


# Secrets sources depuis Vaultwarden via systemd-creds (voir
# media-server/scripts/secrets/). Plus aucun mot de passe en clair ici.
QBT_PASSWORD   = _secret("qbt-password", "QBT_PASSWORD")
BAZARR_KEY     = _secret("bazarr-api-key", "BAZARR_KEY")

sys.path.insert(0, str(Path(__file__).parent))
from storage import POLLER_STATE_FILE as STATE_FILE  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers HTTP
# ---------------------------------------------------------------------------

def _get(url: str, headers: dict = None, cookies: str = None) -> dict | list | None:
    req = urllib.request.Request(url, headers=headers or {})
    if cookies:
        req.add_header("Cookie", cookies)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"GET {url} -> {e}", flush=True)
        return None


def _api(method: str, path: str, body: dict = None) -> dict | None:
    url = TRACKING_API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"{method} {path} -> {e}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Backoff : quand une cible est injoignable, on espace les tentatives
# (10s -> 20s -> 40s ... cap 5 min) et on ne logue qu'au changement d'etat.
# ---------------------------------------------------------------------------

BACKOFF_MAX_S = 300


class Backoff:
    def __init__(self, name: str, base_s: float):
        self.name = name
        self.base = base_s
        self.delay = base_s
        self.next_at = 0.0
        self.down = False

    def ready(self, now: float) -> bool:
        return now >= self.next_at

    def ok(self, now: float) -> None:
        if self.down:
            print(f"{self.name}: de nouveau joignable", flush=True)
        self.down = False
        self.delay = self.base
        self.next_at = now + self.base

    def fail(self, now: float, reason: str = "") -> None:
        if not self.down:
            print(f"{self.name}: injoignable ({reason}) -- backoff active", flush=True)
        self.down = True
        self.delay = min(self.delay * 2, BACKOFF_MAX_S)
        self.next_at = now + self.delay


# ---------------------------------------------------------------------------
# Etat persistant (hash -> session_id)
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"qbt": {}, "bazarr_session": None, "bazarr_total": -1}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _fmt_size(b: int) -> float:
    """Bytes -> MB arrondi a 1 decimale."""
    return round(b / (1024 * 1024), 1)


def _fmt_size_gb(b: int) -> str:
    """Bytes -> string GB (ex: '27.7 GB')."""
    return f"{b / (1024 ** 3):.1f} GB"


def _fmt_speed(bps: int) -> str:
    if bps <= 0:
        return "0 MB/s"
    mbs = bps / (1024 * 1024)
    return f"{mbs:.1f} MB/s"


def _fmt_eta(secs: int) -> str:
    if secs <= 0 or secs >= 8_000_000:
        return "..."
    td = timedelta(seconds=secs)
    h, rem = divmod(td.seconds, 3600)
    m, s = divmod(rem, 60)
    if td.days:
        return f"{td.days}j {h}h"
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _detect_template(name: str, category: str) -> str:
    """Detecte le template tracking depuis la categorie qBittorrent et le nom."""
    cat = category.lower()
    if "radarr" in cat:
        return "movie"
    if "sonarr" in cat:
        # Episode seul : SxxExx dans le nom
        if re.search(r'[Ss]\d{2}[Ee]\d{2}', name):
            return "series_episode"
        # Saison complete : Sxx sans numero d'episode
        if re.search(r'[Ss]\d{2}(?![Ee])', name):
            return "series_season"
        return "series_episode"
    return "download"


def _parse_season(name: str) -> str:
    """Extrait 'S01' depuis le nom du torrent."""
    m = re.search(r'([Ss]\d{2})', name)
    return m.group(1).upper() if m else ""


def _parse_episode(name: str) -> str:
    """Extrait 'E03' depuis le nom du torrent."""
    m = re.search(r'[Ss]\d{2}([Ee]\d{2})', name)
    return m.group(1).upper() if m else ""


def _parse_media_info(name: str) -> dict:
    """Extrait qualite / codec / audio / source / dv depuis le nom du torrent."""
    n = name.upper()
    info: dict = {}

    # Qualite
    if "2160P" in n:
        info["quality"] = "2160p UHD"
    elif "1080P" in n:
        info["quality"] = "1080p"
    elif "720P" in n:
        info["quality"] = "720p"

    # Source
    if any(x in n for x in ("BLURAY", "BDRIP", "BRRIP")):
        info["source"] = "BluRay"
    elif any(x in n for x in ("WEB-DL", "WEBDL", "WEBRIP")):
        info["source"] = "WEB-DL"
    elif "HDTV" in n:
        info["source"] = "HDTV"

    # Codec
    codec = []
    if any(x in n for x in ("HEVC", "X265", "H.265", "H265")):
        codec.append("HEVC")
    elif any(x in n for x in ("X264", "H.264", "H264", "AVC")):
        codec.append("AVC")
    elif "XVID" in n:
        codec.append("XviD")
    if re.search(r'[.\s]DV[.\s]|[.\s]DV$', n):
        codec.append("DV")
        info["dv"] = "DV"
    if codec:
        info["codec"] = " / ".join(codec)

    # Audio
    audio = []
    if "TRUEHD" in n:
        audio.append("TrueHD")
    elif "DTS-HD" in n:
        audio.append("DTS-HD")
    elif "DTS" in n:
        audio.append("DTS")
    elif any(x in n for x in ("AC3", "DD5", "DD2")):
        audio.append("Dolby Digital")
    elif "AAC" in n:
        audio.append("AAC")
    if "ATMOS" in n:
        audio.append("Atmos")
    if audio:
        info["audio"] = " ".join(audio)

    return info


# ---------------------------------------------------------------------------
# qBittorrent
# ---------------------------------------------------------------------------

QBT_ACTIVE_STATES = {
    "downloading", "stalledDL", "checkingDL", "pausedDL",
    "queuedDL", "metaDL", "forcedDL", "forcedMetaDL",
}


class QbtPoller:
    def __init__(self):
        self._sid: str = ""
        self.quiet = False  # True pendant un backoff : pas de spam de logs

    def _log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)

    def _login(self) -> bool:
        # Relire le mot de passe a chaque login : apres une rotation mensuelle,
        # le cred change ; ainsi le poller se resynchronise sans redemarrage
        # (au prochain login declenche par l'expiration de session / 403).
        pw = _secret("qbt-password", "QBT_PASSWORD")
        if not pw:
            self._log("qbt login -> mot de passe absent (credential systemd manquant)")
            return False
        data = urllib.parse.urlencode({"username": QBT_USER, "password": pw}).encode()
        # qBittorrent exige le header Referer et pose un cookie QBT_SID_<port>.
        req = urllib.request.Request(
            f"{QBT_HOST}/api/v2/auth/login", data=data,
            headers={"Referer": QBT_HOST},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                # qBittorrent 5.x renvoie 204 (corps vide) sur succes, pas
                # "200 Ok." : on se fie au code HTTP + a la presence du cookie.
                if r.status in (200, 204):
                    hdrs = r.getheader("Set-Cookie", "")
                    for part in hdrs.split(";"):
                        p = part.strip()
                        if p.startswith("QBT_SID_") or p.startswith("SID="):
                            self._sid = p
                            return True
        except Exception as e:
            self._log(f"qbt login -> {e}")
        return False

    def _get_torrent_files(self, hash: str) -> list:
        """Recupere la liste des fichiers d'un torrent (pour series_season)."""
        url = f"{QBT_HOST}/api/v2/torrents/files?hash={hash}"
        req = urllib.request.Request(url, headers={"Referer": QBT_HOST})
        if self._sid:
            req.add_header("Cookie", self._sid)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            print(f"qbt files -> {e}", flush=True)
            return []

    def _get_torrents(self) -> list | None:
        url = f"{QBT_HOST}/api/v2/torrents/info"
        req = urllib.request.Request(url, headers={"Referer": QBT_HOST})
        if self._sid:
            req.add_header("Cookie", self._sid)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 403:
                self._sid = ""  # Session expiree
            return None
        except Exception as e:
            self._log(f"qbt torrents -> {e}")
            return None

    def poll(self, state: dict) -> bool:
        """Retourne False si qBittorrent est injoignable (declenche le backoff)."""
        if not self._sid and not self._login():
            return False

        torrents = self._get_torrents()
        if torrents is None:
            # Retry login
            if self._login():
                torrents = self._get_torrents()
            if torrents is None:
                return False

        qbt_state = state.setdefault("qbt", {})

        # Index par hash
        active = {
            t["hash"]: t for t in torrents
            if t.get("state") in QBT_ACTIVE_STATES
        }

        # Supprimer les sessions des torrents termines/disparus
        for h in list(qbt_state.keys()):
            if h not in active:
                sid = qbt_state.pop(h)
                _api("DELETE", f"/sessions/{sid}")
                print(f"qbt: session supprimee pour torrent disparu {h[:8]}", flush=True)

        # Creer ou mettre a jour
        for h, t in active.items():
            total_mb  = _fmt_size(t.get("total_size", 0))
            done_mb   = _fmt_size(t.get("downloaded", 0))
            speed_str = _fmt_speed(t.get("dlspeed", 0))
            eta_str   = _fmt_eta(t.get("eta", 0))
            seeds     = t.get("num_seeds", 0)
            name      = t.get("name", "inconnu")
            category  = t.get("category", "")

            template = _detect_template(name, category)
            media    = _parse_media_info(name) if template != "download" else {}

            qstate    = t.get("state", "")
            status    = "paused" if qstate == "pausedDL" else "running"

            extra: dict = {"speed": speed_str, "eta": eta_str, "phase": "download"}
            extra["seeds"] = str(seeds)
            if media:
                extra.update(media)
                extra["release"] = name
                extra["size"]    = _fmt_size_gb(t.get("total_size", 0))
            if template in ("series_episode", "series_season"):
                extra["season"] = _parse_season(name)
            if template == "series_episode":
                extra["episode"] = _parse_episode(name)

            # Verifier que la session existe toujours, sinon la recreer
            if h in qbt_state and _api("GET", f"/sessions/{qbt_state[h]}") is None:
                print(f"qbt: session disparue pour {name}, recreation", flush=True)
                del qbt_state[h]

            if h not in qbt_state:
                # Items pour series_season : fichiers video du torrent
                items = None
                if template == "series_season":
                    files = self._get_torrent_files(h)
                    video_exts = {".mkv", ".mp4", ".avi", ".m4v"}
                    ep_files = sorted(
                        [f for f in files if Path(f["name"]).suffix.lower() in video_exts],
                        key=lambda f: f["name"],
                    )
                    if ep_files:
                        items = [{"name": Path(f["name"]).name, "status": "running"}
                                 for f in ep_files[:50]]

                body: dict = {
                    "name":     name,
                    "template": template,
                    "total":    total_mb,
                    "unit":     " MB",
                    "extra":    extra,
                }
                if items:
                    body["items"] = items
                resp = _api("POST", "/sessions", body)
                if resp and "id" in resp:
                    qbt_state[h] = resp["id"]
                    print(f"qbt: session creee '{name}' [{resp['id']}] ({template})", flush=True)
            else:
                # Mise a jour progression + seeds/speed. Le nom est renvoye a
                # chaque poll : un torrent cree en metaDL (magnet) porte le hash
                # comme nom jusqu'a l'arrivee des metadonnees.
                _api("PUT", f"/sessions/{qbt_state[h]}", {
                    "name":      name,
                    "processed": done_mb,
                    "total":     total_mb,
                    "extra":     extra,
                    "status":    status,
                })
        return True


# ---------------------------------------------------------------------------
# Bazarr
# ---------------------------------------------------------------------------

class BazarrPoller:

    quiet = False

    def _fetch_wanted(self, endpoint: str) -> list | None:
        """None = Bazarr injoignable (distinct d'une liste vide)."""
        url = f"{BAZARR_HOST}/api/{endpoint}?start=0&length=500"
        req = urllib.request.Request(url)
        req.add_header("X-API-KEY", BAZARR_KEY)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())
                return data.get("data", [])
        except Exception as e:
            if not self.quiet:
                print(f"bazarr {endpoint} -> {e}", flush=True)
            return None

    def poll(self, state: dict) -> bool:
        """Retourne False si Bazarr est injoignable (declenche le backoff)."""
        if not BAZARR_KEY:
            return True  # cle API absente (credential systemd manquant) -> poll desactive
        episodes = self._fetch_wanted("episodes/wanted")
        if episodes is None:
            return False
        movies = self._fetch_wanted("movies/wanted")
        if movies is None:
            return False

        items_ep = [
            {
                "name": f"{e['seriesTitle']} {e['episode_number']}",
                "status": "pending",
                "note": e.get("episodeTitle", ""),
            }
            for e in episodes
        ]
        items_mv = [
            {
                "name": m.get("title", "inconnu"),
                "status": "pending",
                "note": "",
            }
            for m in movies
        ]
        all_items = items_ep + items_mv
        total = len(all_items)

        bsid          = state.get("bazarr_session")
        initial_total = state.get("bazarr_initial_total", 0)
        state.get("bazarr_total", -1)

        # Verifier que la session existe toujours, sinon repartir a zero
        if bsid and _api("GET", f"/sessions/{bsid}") is None:
            bsid = None
            state["bazarr_session"]       = None
            state["bazarr_initial_total"] = 0
            state["bazarr_total"]         = -1
            initial_total = 0

        if total == 0:
            # Tous trouves -- marquer done et effacer
            if bsid:
                _api("PUT", f"/sessions/{bsid}", {"processed": float(initial_total), "status": "done"})
                _api("DELETE", f"/sessions/{bsid}")
                state["bazarr_session"]       = None
                state["bazarr_initial_total"] = 0
                state["bazarr_total"]         = -1
                print("bazarr: tous les sous-titres sont presents", flush=True)
            return True

        if not bsid:
            # Nouvelle session : le total actuel devient la reference
            resp = _api("POST", "/sessions", {
                "name":     f"Sous-titres manquants ({total})",
                "template": "subtitles",
                "total":    float(total),
                "unit":     " fichiers",
                "extra": {
                    "source":           "Bazarr",
                    "missing_episodes": str(len(items_ep)),
                    "missing_movies":   str(len(items_mv)),
                },
                "items": all_items[:20],
            })
            if resp and "id" in resp:
                state["bazarr_session"]       = resp["id"]
                state["bazarr_initial_total"] = total
                state["bazarr_total"]         = total
                print(f"bazarr: session creee ({total} manquants)", flush=True)
            return True

        # Session existante -- mettre a jour la progression
        if total > initial_total:
            # Le backlog a grossi (nouvelles series ajoutees) : etendre le total
            state["bazarr_initial_total"] = total
            initial_total = total

        found = initial_total - total   # sous-titres trouves depuis le debut
        state["bazarr_total"] = total

        _api("PUT", f"/sessions/{bsid}", {
            "name":          f"Sous-titres manquants ({total})",
            "processed":     float(found),
            "total":         float(initial_total),
            "replace_items": all_items[:20],   # liste courante, pas celle du premier poll
            "extra": {
                "source":           "Bazarr",
                "missing_episodes": str(len(items_ep)),
                "missing_movies":   str(len(items_mv)),
            },
        })
        return True


# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------

def main() -> None:
    print("Tracking poller demarre", flush=True)

    # Attendre que l'API tracking soit disponible
    for _ in range(30):
        try:
            urllib.request.urlopen(f"{TRACKING_API}/health", timeout=2)
            break
        except Exception:
            time.sleep(1)
    else:
        print("ERREUR: API tracking inaccessible apres 30s", flush=True)
        sys.exit(1)

    state   = _load_state()
    qbt     = QbtPoller()
    bazarr  = BazarrPoller()

    bo_qbt    = Backoff("qbt", QBT_POLL_SECS)
    bo_bazarr = Backoff("bazarr", BAZARR_POLL_SECS)

    while True:
        now = time.time()

        if bo_qbt.ready(now):
            qbt.quiet = bo_qbt.down
            try:
                ok = qbt.poll(state)
            except Exception as e:
                print(f"qbt poll erreur: {e}", flush=True)
                ok = False
            if ok:
                bo_qbt.ok(time.time())
            else:
                bo_qbt.fail(time.time(), "timeout/refus")
            _save_state(state)

        if bo_bazarr.ready(now):
            bazarr.quiet = bo_bazarr.down
            try:
                ok = bazarr.poll(state)
            except Exception as e:
                print(f"bazarr poll erreur: {e}", flush=True)
                ok = False
            if ok:
                bo_bazarr.ok(time.time())
            else:
                bo_bazarr.fail(time.time(), "timeout/refus")
            _save_state(state)

        time.sleep(1)


if __name__ == "__main__":
    main()
