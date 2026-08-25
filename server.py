import argparse
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

from mcp.server.fastmcp import FastMCP

import storage
from models import TrackingSession, TrackingItem, LogEntry, ItemStatus, SessionStatus
from templates import validate_template, TEMPLATES, list_templates

mcp = FastMCP("tracking")

BAR_WIDTH = 22

# ---------------------------------------------------------------------------
# Formateur
# ---------------------------------------------------------------------------

def _bar(processed: float, total: float) -> str:
    pct = min(processed / total, 1.0) if total > 0 else 0
    filled = int(BAR_WIDTH * pct)
    if filled >= BAR_WIDTH:
        return "=" * BAR_WIDTH
    return "=" * filled + ">" + " " * (BAR_WIDTH - filled - 1)


def _format_session(session: TrackingSession) -> str:
    pct = min(session.processed / session.total, 1.0) if session.total > 0 else 0
    bar = _bar(session.processed, session.total)

    lines = [
        f"[{session.template}] {session.name}  ({session.status.value})",
        f"[{bar}] {pct * 100:.1f}%  ({session.processed}{session.unit} / {session.total}{session.unit})",
    ]

    # Champs extra du template (speed, eta, operation, etc.)
    if session.extra:
        extra_parts = [f"{k}: {v}" for k, v in session.extra.items() if v is not None]
        if extra_parts:
            lines.append("  " + "  |  ".join(extra_parts))

    # Liste des items
    if session.items:
        lines.append("")
        lines.append("Items:")
        icons = {
            "pending": "[ ]",
            "running": "[>]",
            "done": "[ok]",
            "error": "[!]",
        }
        for item in session.items:
            icon = icons.get(item.status, "[ ]")
            if item.processed is not None and item.total is not None:
                prog = f"  {item.processed}{item.unit} / {item.total}{item.unit}"
            elif item.total is not None:
                prog = f"  {item.total}{item.unit}"
            else:
                prog = ""
            note = f"  ({item.note})" if item.note else ""
            lines.append(f"  {icon}  {item.name:<32}{prog}{note}")

    # Logs (5 derniers)
    if session.logs:
        lines.append("")
        lines.append("Logs:")
        for entry in session.logs[-5:]:
            ts = entry.timestamp.strftime("%H:%M:%S")
            lines.append(f"  {ts}  {entry.message}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Outils MCP
# ---------------------------------------------------------------------------

@mcp.tool()
def tracking_create(
    name: str,
    template: str,
    total: float,
    unit: str = "",
    items: Optional[List[Dict[str, Any]]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Cree une nouvelle session de tracking.

    Args:
        name:     Nom de la session (ex: "Download Ubuntu ISO")
        template: "download" | "machine" | "free"
        total:    Valeur totale (ex: 15300 pour 15300 MB)
        unit:     Unite affichee (ex: " MB", " machines")
        items:    Liste optionnelle d'elements [{name, total?, unit?, note?}]
        extra:    Champs specifiques au template (speed, eta, operation, target...)
    """
    if not validate_template(template):
        return (
            f"Template inconnu: '{template}'\n\n"
            f"Templates disponibles:\n{list_templates()}"
        )

    session_items = []
    if items:
        for item_data in items:
            session_items.append(TrackingItem(
                name=item_data.get("name", ""),
                total=item_data.get("total"),
                unit=item_data.get("unit", unit),
                note=item_data.get("note", ""),
            ))

    session = TrackingSession(
        name=name,
        template=template,
        total=total,
        unit=unit,
        items=session_items,
        extra=extra or {},
    )
    session.logs.append(LogEntry(message=f"Session creee: {name}"))
    storage.save(session)

    return f"Session creee [id: {session.id}]\n\n{_format_session(session)}"


@mcp.tool()
def tracking_update(
    session_id: str,
    processed: Optional[float] = None,
    message: Optional[str] = None,
    item_updates: Optional[List[Dict[str, Any]]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Met a jour la progression d'une session.

    Args:
        session_id:   ID de la session
        processed:    Nouvelle valeur de progression (optionnel)
        message:      Message de log a ajouter (optionnel)
        item_updates: Mises a jour des items [{name?, id?, status?, processed?, note?}]
        extra:        Champs extra a mettre a jour (speed, eta, etc.)
    """
    session = storage.get(session_id)
    if not session:
        return f"Session introuvable: {session_id}"

    if processed is not None:
        session.processed = min(processed, session.total)

    if message:
        session.logs.append(LogEntry(message=message))

    if item_updates:
        for upd in item_updates:
            item_name = upd.get("name")
            item_id = upd.get("id")
            for item in session.items:
                if (item_id and item.id == item_id) or (item_name and item.name == item_name):
                    if "status" in upd:
                        item.status = ItemStatus(upd["status"])
                    if "processed" in upd:
                        item.processed = upd["processed"]
                    if "note" in upd:
                        item.note = upd["note"]
                    break

    if extra:
        session.extra.update(extra)

    session.updated_at = datetime.now()
    storage.save(session)

    return _format_session(session)


@mcp.tool()
def tracking_log(session_id: str, message: str) -> str:
    """Ajoute une entree de log a une session sans modifier la progression."""
    session = storage.get(session_id)
    if not session:
        return f"Session introuvable: {session_id}"

    session.logs.append(LogEntry(message=message))
    session.updated_at = datetime.now()
    storage.save(session)

    return f"Log ajoute a '{session.name}'"


@mcp.tool()
def tracking_complete(session_id: str, message: Optional[str] = None) -> str:
    """Marque une session comme terminee et met la progression a 100%."""
    session = storage.get(session_id)
    if not session:
        return f"Session introuvable: {session_id}"

    session.status = SessionStatus.DONE
    session.processed = session.total
    session.logs.append(LogEntry(message=message or "Session terminee"))
    session.updated_at = datetime.now()
    storage.save(session)

    return _format_session(session)


@mcp.tool()
def tracking_error(session_id: str, message: str) -> str:
    """Marque une session en erreur."""
    session = storage.get(session_id)
    if not session:
        return f"Session introuvable: {session_id}"

    session.status = SessionStatus.ERROR
    session.logs.append(LogEntry(message=f"ERREUR: {message}"))
    session.updated_at = datetime.now()
    storage.save(session)

    return _format_session(session)


@mcp.tool()
def tracking_get(session_id: str) -> str:
    """Retourne l'etat formate complet d'une session."""
    session = storage.get(session_id)
    if not session:
        return f"Session introuvable: {session_id}"
    return _format_session(session)


@mcp.tool()
def tracking_list(
    template: Optional[str] = None,
    status: Optional[str] = None,
) -> str:
    """
    Liste les sessions avec filtres optionnels.

    Args:
        template: Filtrer par template ("download", "machine", "free", "movie", "lyra_task"...)
        status:   Filtrer par statut ("running", "done", "error", "paused")
    """
    sessions = storage.list_all(template=template, status=status)
    if not sessions:
        label = []
        if template:
            label.append(f"template={template}")
        if status:
            label.append(f"status={status}")
        suffix = f" ({', '.join(label)})" if label else ""
        return f"Aucune session{suffix}."

    header = f"Sessions ({len(sessions)})"
    if template or status:
        parts = []
        if template:
            parts.append(f"template={template}")
        if status:
            parts.append(f"status={status}")
        header += f"  --  filtre: {', '.join(parts)}"

    lines = [header, ""]
    for s in sessions:
        pct = s.processed / s.total * 100 if s.total > 0 else 0
        lines.append(
            f"  [{s.id}]  {s.template:<12}  {s.name:<32}  {pct:5.1f}%  ({s.status.value})"
        )

    return "\n".join(lines)


@mcp.tool()
def tracking_delete(session_id: str) -> str:
    """Supprime une session de la memoire."""
    session = storage.get(session_id)
    if not session:
        return f"Session introuvable: {session_id}"
    name = session.name
    storage.delete(session_id)
    return f"Session '{name}' [{session_id}] supprimee."


@mcp.tool()
def tracking_stop(session_id: str, message: Optional[str] = None) -> str:
    """
    Arrete proprement une session (SIGTERM) -- marque en 'paused'.
    La session reste visible dans le dashboard avec son historique.

    Args:
        session_id: ID de la session
        message:    Raison de l'arret (optionnel)
    """
    session = storage.get(session_id)
    if not session:
        return f"Session introuvable: {session_id}"

    session.status = SessionStatus.PAUSED
    session.logs.append(LogEntry(message=message or "Arretee manuellement"))
    session.updated_at = datetime.now()
    storage.save(session)

    return _format_session(session)


@mcp.tool()
def tracking_kill(session_id: str) -> str:
    """
    Supprime une session en force (SIGKILL) -- disparait du dashboard.

    Args:
        session_id: ID de la session
    """
    session = storage.get(session_id)
    if not session:
        return f"Session introuvable: {session_id}"

    name = session.name
    storage.delete(session_id)
    return f"Session '{name}' [{session_id}] supprimee en force."


@mcp.tool()
def tracking_templates() -> str:
    """Liste les templates disponibles et leurs champs."""
    return "Templates disponibles:\n\n" + list_templates()


@mcp.tool()
def open_tracking_ui(filter_template: Optional[str] = None) -> str:
    """
    Ouvre le dashboard de tracking dans un terminal Kitty.

    Args:
        filter_template: Template a afficher au demarrage ("lyra_task", "movie", "download"...)
                         Si absent, affiche toutes les sessions.
    """
    import subprocess

    cmd = [
        "kitty", "--detach",
        "/home/amineutron/dev/MCP/tracking/.venv/bin/python",
        str(__file__),
        "--ui",
    ]
    if filter_template:
        cmd.extend(["--filter", filter_template])

    try:
        subprocess.Popen(cmd, start_new_session=True)
        suffix = f" (filtre: {filter_template})" if filter_template else ""
        return f"Dashboard ouvert dans Kitty{suffix}."
    except FileNotFoundError:
        return "Erreur: kitty introuvable -- verifier que Kitty est installe."


# ---------------------------------------------------------------------------
# Simulation de test (tourne dans un thread background)
# ---------------------------------------------------------------------------

def _step(seconds: float = 1.5) -> None:
    time.sleep(seconds)


def _extract_id(result: str) -> str:
    return result.split("[id: ")[1].split("]")[0]


def _sim_download() -> None:
    """Template download -- 6 fichiers, ~19 GB total."""
    files = [
        ("debian-12.10.0-amd64-DVD-1.iso",   3800),
        ("archlinux-2026.03.01-x86_64.iso",   1100),
        ("fedora-42-x86_64-dvd.iso",          2300),
        ("ubuntu-22.04.5-desktop-amd64.iso",  5100),
        ("ubuntu-24.04.2-desktop-amd64.iso",  4200),
        ("nixos-24.11-x86_64-linux.iso",      2700),
    ]
    total_mb = sum(s for _, s in files)

    sid = _extract_id(tracking_create(
        name="Distros Linux -- nightly mirror sync",
        template="download",
        total=total_mb,
        unit=" MB",
        items=[{"name": n, "total": s, "unit": " MB"} for n, s in files],
        extra={"speed": "--", "eta": "calcul..."},
    ))

    cumul = 0
    for idx, (name, size) in enumerate(files):
        tracking_update(
            session_id=sid,
            message=f"Debut : {name}",
            item_updates=[{"name": name, "status": "running", "processed": 0}],
            extra={"speed": "--", "eta": "..."},
        )
        # 3 etapes par fichier
        for step, (chunk, speed, eta) in enumerate([
            (size // 3, "24 MB/s", f"{int((total_mb - cumul) / 24 / 60)}m{int((total_mb - cumul) / 24 % 60):02d}s"),
            (size * 2 // 3, "31 MB/s", f"{int((total_mb - cumul - size // 3) / 31 / 60)}m{int((total_mb - cumul - size // 3) / 31 % 60):02d}s"),
            (size, "28 MB/s", "--"),
        ]):
            _step(1.2)
            tracking_update(
                session_id=sid,
                processed=cumul + chunk,
                message=f"{name} : {chunk} MB / {size} MB",
                item_updates=[{"name": name, "status": "running", "processed": chunk}],
                extra={"speed": speed, "eta": eta},
            )
        _step(0.6)
        tracking_update(
            session_id=sid,
            processed=cumul + size,
            message=f"{name} -- OK",
            item_updates=[{"name": name, "status": "done"}],
        )
        cumul += size

    _step(0.5)
    tracking_complete(sid, f"Miroir synchronise -- {total_mb // 1024:.1f} GB recus")


def _sim_machine() -> None:
    """Template machine -- 12 noeuds (force le scroll)."""
    nodes = [
        "prod-web-01", "prod-web-02", "prod-web-03", "prod-web-04",
        "prod-api-01", "prod-api-02", "prod-api-03",
        "prod-db-01",  "prod-db-02",
        "prod-worker-01", "prod-worker-02", "prod-worker-03",
    ]

    sid = _extract_id(tracking_create(
        name="Rolling update -- prod cluster",
        template="machine",
        total=len(nodes),
        unit=" noeuds",
        items=[{"name": n} for n in nodes],
        extra={"operation": "docker pull + restart", "target": "prod"},
    ))

    for i, node in enumerate(nodes, 1):
        tracking_update(
            session_id=sid,
            message=f"Drain + update : {node}",
            item_updates=[{"name": node, "status": "running"}],
        )
        _step(1.4)
        tracking_update(
            session_id=sid,
            processed=i,
            message=f"{node} -- OK  (v2.4.1 deploye)",
            item_updates=[{"name": node, "status": "done"}],
        )
        _step(0.8)

    tracking_complete(sid, f"Rolling update termine -- {len(nodes)}/{len(nodes)} noeuds OK")


def _sim_free() -> None:
    """Template free -- pipeline ETL avec 5 etapes."""
    stages = [
        ("extract",   12000, "Extraction depuis PostgreSQL prod"),
        ("transform",  8000, "Nettoyage et normalisation"),
        ("validate",   8000, "Validation des contraintes"),
        ("load",      12000, "Chargement dans l'entrepot"),
        ("index",      8000, "Construction des index"),
    ]
    total = sum(s for _, s, _ in stages)

    sid = _extract_id(tracking_create(
        name="Pipeline ETL -- prod -> warehouse",
        template="free",
        total=total,
        unit=" enreg.",
        items=[{"name": name, "total": size, "unit": " enreg."} for name, size, _ in stages],
        extra={"source": "postgresql://prod", "dest": "bigquery://warehouse"},
    ))

    cumul = 0
    for name, size, desc in stages:
        tracking_update(
            session_id=sid,
            message=f"Etape '{name}' : {desc}",
            item_updates=[{"name": name, "status": "running", "processed": 0}],
        )
        # 2 etapes intermediaires
        _step(1.3)
        tracking_update(
            session_id=sid,
            processed=cumul + size // 2,
            message=f"{name} : {size // 2} / {size} enreg.",
            item_updates=[{"name": name, "status": "running", "processed": size // 2}],
        )
        _step(1.3)
        cumul += size
        tracking_update(
            session_id=sid,
            processed=cumul,
            message=f"{name} -- termine ({size} enreg.)",
            item_updates=[{"name": name, "status": "done"}],
        )
        _step(0.5)

    tracking_complete(sid, f"Pipeline ETL termine -- {total} enregistrements charges")


def _sim_movie() -> None:
    """Template movie -- pipeline complet : download -> sous-titres -> conversion DV P7->P8."""

    _FILE    = "Dune.Part.Two.2024.2160p.UHD.BluRay.TrueHD.Atmos.7.1.DV.HEVC-FraMeSToR.mkv"
    _FILE_MB = 58_400

    # Tous les items definis des le debut pour montrer le pipeline complet
    sid = _extract_id(tracking_create(
        name="Dune Part Two (2024)",
        template="movie",
        total=100,
        unit="%",
        items=[
            # -- download --
            {"name": _FILE, "total": _FILE_MB, "unit": " MB"},
            # -- sous-titres --
            {"name": "Sous-titres FR",     "note": "5 tentatives restantes"},
            {"name": "Sous-titres VOSTFR", "note": "5 tentatives restantes"},
            # -- conversion DV (6 etapes) --
            {"name": "1. Extraction HEVC brut"},
            {"name": "2. Demux BL / EL (dovi_tool)"},
            {"name": "3. Extraction RPU"},
            {"name": "4. Conversion RPU -> P8"},
            {"name": "5. Injection RPU dans BL"},
            {"name": "6. Remux MKV final"},
        ],
        extra={
            "phase":   "download",
            "quality": "2160p UHD",
            "codec":   "HEVC / DV P7",
            "audio":   "TrueHD Atmos 7.1",
            "source":  "FraMeSToR",
            "speed":   "--",
            "eta":     "calcul...",
        },
    ))

    # ---------------------------------------------------------------
    # Phase 1 : Download
    # ---------------------------------------------------------------
    for dl_mb, speed, eta in [
        ( 8_760, "45 MB/s", "21m30s"),
        (17_520, "52 MB/s", "17m10s"),
        (26_280, "61 MB/s", "12m40s"),
        (35_040, "58 MB/s",  "8m20s"),
        (46_720, "63 MB/s",  "4m50s"),
        (58_400, "67 MB/s",      "--"),
    ]:
        pct = int(dl_mb / _FILE_MB * 33)  # download = 0->33%
        tracking_update(
            session_id=sid,
            processed=pct,
            message=f"Download : {dl_mb:,} MB / {_FILE_MB:,} MB",
            item_updates=[{"name": _FILE, "status": "running", "processed": dl_mb}],
            extra={"speed": speed, "eta": eta},
        )
        _step(1.2)

    tracking_update(
        session_id=sid,
        processed=33,
        message=f"Telechargement termine -- {_FILE_MB // 1024:.1f} GB recus",
        item_updates=[{"name": _FILE, "status": "done"}],
        extra={"phase": "subtitles", "speed": "--", "eta": "--"},
    )
    _step(1.0)

    # ---------------------------------------------------------------
    # Phase 2 : Sous-titres
    # FR trouve apres 3 tentatives -- VOSTFR echec a 0 tentative
    # ---------------------------------------------------------------
    tracking_update(
        session_id=sid,
        processed=34,
        message="Bazarr : recherche sous-titres en cours...",
        item_updates=[
            {"name": "Sous-titres FR",     "status": "running", "note": "5 tentatives restantes"},
            {"name": "Sous-titres VOSTFR", "status": "running", "note": "5 tentatives restantes"},
        ],
    )
    _step(1.0)

    # Tentatives qui s'epuisent
    for attempt in [4, 3]:
        tracking_update(
            session_id=sid,
            processed=35,
            message=f"Bazarr : tentative {6 - attempt}/5 (aucun resultat)...",
            item_updates=[
                {"name": "Sous-titres FR",     "note": f"{attempt} tentatives restantes"},
                {"name": "Sous-titres VOSTFR", "note": f"{attempt} tentatives restantes"},
            ],
        )
        _step(1.2)

    # FR trouve a la 3e tentative
    tracking_update(
        session_id=sid,
        processed=40,
        message="Sous-titres FR trouves sur OpenSubtitles",
        item_updates=[
            {"name": "Sous-titres FR", "status": "done", "note": "OpenSubtitles -- 3e tentative"},
        ],
    )
    _step(1.0)

    # VOSTFR : descente a 0
    for attempt in [2, 1, 0]:
        status = "error" if attempt == 0 else "running"
        note   = "0 tentatives -- ECHEC" if attempt == 0 else f"{attempt} tentative{'s' if attempt > 1 else ''} restante{'s' if attempt > 1 else ''}"
        msg    = "ERREUR: Sous-titres VOSTFR introuvables -- 0 tentatives restantes" if attempt == 0 else f"Bazarr VOSTFR : {attempt} tentative(s) restante(s)..."
        tracking_update(
            session_id=sid,
            processed=42 + (2 - attempt),
            message=msg,
            item_updates=[{"name": "Sous-titres VOSTFR", "status": status, "note": note}],
        )
        _step(1.0)

    tracking_update(
        session_id=sid,
        processed=45,
        message="Passage en conversion DV P7 -> P8",
        extra={"phase": "convert", "dv": "P7 -> P8", "eta": "~15min"},
    )
    _step(0.8)

    # ---------------------------------------------------------------
    # Phase 3 : Conversion DV (6 etapes)
    # ---------------------------------------------------------------
    dv_steps = [
        ("1. Extraction HEVC brut",       "ffmpeg : extraction stream video brut"),
        ("2. Demux BL / EL (dovi_tool)",  "dovi_tool demux : separation BL et EL"),
        ("3. Extraction RPU",              "dovi_tool extract-rpu depuis BL"),
        ("4. Conversion RPU -> P8",        "dovi_tool convert -m 2 : Profile 8"),
        ("5. Injection RPU dans BL",       "dovi_tool inject-rpu dans base layer"),
        ("6. Remux MKV final",             "ffmpeg remux : video P8 + audio + subs"),
    ]

    for i, (step, msg) in enumerate(dv_steps):
        tracking_update(
            session_id=sid,
            message=f"Etape {i + 1}/6 : {msg}",
            item_updates=[{"name": step, "status": "running"}],
        )
        _step(1.3)

        pct = 50 + round((i + 1) / 6 * 50)
        tracking_update(
            session_id=sid,
            processed=pct,
            message=f"Etape {i + 1}/6 OK",
            item_updates=[{"name": step, "status": "done"}],
        )
        _step(0.7)

    tracking_complete(sid, "Dune Part Two (2024) -- disponible sur Plex")


def _sim_series_episode() -> None:
    """Template series_episode -- Breaking Bad S03E07, pipeline complet avec DV."""
    _FILE    = "Breaking.Bad.S03E07.One.Minute.2160p.UHD.BluRay.TrueHD.7.1.DV.HEVC-NOGROUP.mkv"
    _FILE_MB = 28_400

    sid = _extract_id(tracking_create(
        name="Breaking Bad S03E07",
        template="series_episode",
        total=100,
        unit="%",
        items=[
            {"name": _FILE, "total": _FILE_MB, "unit": " MB"},
            {"name": "Sous-titres FR",     "note": "5 tentatives"},
            {"name": "Sous-titres VOSTFR", "note": "5 tentatives"},
            {"name": "1. Extraction HEVC brut"},
            {"name": "2. Demux BL / EL"},
            {"name": "3. Conversion RPU -> P8"},
            {"name": "4. Remux MKV final"},
        ],
        extra={
            "season":        "S03",
            "episode":       "E07",
            "episode_title": "One Minute",
            "phase":         "download",
            "quality":       "2160p UHD",
            "codec":         "HEVC / DV P7",
            "audio":         "TrueHD 7.1",
            "source":        "NOGROUP",
            "speed":         "--",
            "eta":           "calcul...",
            "release":       _FILE,
            "size":          "27.7 GB",
        },
    ))

    # Download
    for dl_mb, speed, eta in [
        ( 5_680, "38 MB/s", "11m40s"),
        (11_360, "45 MB/s",  "8m10s"),
        (17_040, "52 MB/s",  "5m30s"),
        (22_720, "49 MB/s",  "3m00s"),
        (28_400, "55 MB/s",      "--"),
    ]:
        pct = int(dl_mb / _FILE_MB * 40)
        tracking_update(
            session_id=sid,
            processed=pct,
            message=f"Download : {dl_mb:,} MB / {_FILE_MB:,} MB",
            item_updates=[{"name": _FILE, "status": "running", "processed": dl_mb}],
            extra={"speed": speed, "eta": eta},
        )
        _step(1.0)

    tracking_update(
        session_id=sid,
        processed=40,
        message="Telechargement termine -- 27.7 GB recus",
        item_updates=[{"name": _FILE, "status": "done"}],
        extra={"phase": "subtitles", "speed": "--", "eta": "--"},
    )
    _step(0.8)

    # Sous-titres
    tracking_update(
        session_id=sid,
        processed=42,
        message="Bazarr : recherche sous-titres...",
        item_updates=[
            {"name": "Sous-titres FR",     "status": "running"},
            {"name": "Sous-titres VOSTFR", "status": "running"},
        ],
    )
    _step(1.2)
    tracking_update(
        session_id=sid,
        processed=47,
        message="Sous-titres FR trouves (SubDL)",
        item_updates=[{"name": "Sous-titres FR", "status": "done", "note": "SubDL"}],
    )
    _step(0.8)
    tracking_update(
        session_id=sid,
        processed=50,
        message="Sous-titres VOSTFR trouves (OpenSubtitles)",
        item_updates=[{"name": "Sous-titres VOSTFR", "status": "done", "note": "OpenSubtitles"}],
        extra={"phase": "dv-conv", "dv": "P7 -> P8", "eta": "~8min"},
    )
    _step(0.8)

    # Conversion DV
    dv_steps = [
        ("1. Extraction HEVC brut",  "ffmpeg : extraction stream video"),
        ("2. Demux BL / EL",         "dovi_tool demux : BL + EL"),
        ("3. Conversion RPU -> P8",  "dovi_tool convert -m 2"),
        ("4. Remux MKV final",       "ffmpeg : video P8 + audio + subs"),
    ]
    for i, (step, msg) in enumerate(dv_steps):
        tracking_update(
            session_id=sid,
            message=f"DV {i + 1}/4 : {msg}",
            item_updates=[{"name": step, "status": "running"}],
        )
        _step(1.2)
        pct = 55 + round((i + 1) / 4 * 44)
        tracking_update(
            session_id=sid,
            processed=pct,
            message=f"DV {i + 1}/4 OK",
            item_updates=[{"name": step, "status": "done"}],
        )
        _step(0.6)

    tracking_complete(sid, "Breaking Bad S03E07 -- disponible sur Plex")


def _sim_series_season() -> None:
    """Template series_season -- Better Call Saul S05 (10 episodes), batch pack."""
    episodes = [
        (1,  "Magic Man"),
        (2,  "50% Off"),
        (3,  "The Guy For This"),
        (4,  "Namaste"),
        (5,  "Dedicado a Max"),
        (6,  "Wexler v. Goodman"),
        (7,  "JMM"),
        (8,  "Bagman"),
        (9,  "Bad Choice Road"),
        (10, "Something Unforgivable"),
    ]
    total_eps = len(episodes)

    sid = _extract_id(tracking_create(
        name="Better Call Saul S05",
        template="series_season",
        total=total_eps,
        unit=" ep",
        items=[{"name": f"S05E{ep:02d} - {title}"} for ep, title in episodes],
        extra={
            "season":          "S05",
            "quality":         "2160p UHD",
            "codec":           "HEVC / DV P7->P8",
            "audio":           "TrueHD 7.1",
            "source":          "GROUP",
            "dv":              "P7 -> P8",
            "release":         "Better.Call.Saul.S05.2160p.UHD.BluRay.TrueHD.7.1.DV.HEVC-GROUP",
            "phase":           "download",
            "current_episode": None,
        },
    ))

    for ep_num, ep_title in episodes:
        ep_name = f"S05E{ep_num:02d} - {ep_title}"

        # Phase download
        tracking_update(
            session_id=sid,
            message=f"Download : {ep_name}",
            item_updates=[{"name": ep_name, "status": "running"}],
            extra={"phase": "download", "current_episode": ep_name},
        )
        _step(1.4)

        # Phase sous-titres
        tracking_update(
            session_id=sid,
            message=f"Sous-titres : {ep_name}",
            extra={"phase": "subtitles"},
        )
        _step(0.9)

        # Phase conversion DV
        tracking_update(
            session_id=sid,
            message=f"DV P7->P8 : {ep_name}",
            extra={"phase": "dv-conv"},
        )
        _step(1.1)

        # Episode termine
        tracking_update(
            session_id=sid,
            processed=ep_num,
            message=f"{ep_name} -- OK",
            item_updates=[{"name": ep_name, "status": "done"}],
        )
        _step(0.4)

    tracking_complete(sid, f"Better Call Saul S05 -- {total_eps} episodes disponibles sur Plex")


def _run_simulation() -> None:
    """Lance les 3 simulations en parallele. Tourne dans un thread daemon."""
    import threading

    _step(2.0)
    storage.clear_storage()

    # Les 3 templates demarrent avec un leger decalage pour que l'UI les affiche
    # progressivement et non tous d'un coup
    t_dl     = threading.Thread(target=_sim_download,       daemon=True)
    t_vm     = threading.Thread(target=_sim_machine,        daemon=True)
    t_etl    = threading.Thread(target=_sim_free,           daemon=True)
    t_movie  = threading.Thread(target=_sim_movie,          daemon=True)
    t_ep     = threading.Thread(target=_sim_series_episode, daemon=True)
    t_season = threading.Thread(target=_sim_series_season,  daemon=True)

    t_dl.start()
    _step(0.8)
    t_vm.start()
    _step(0.8)
    t_etl.start()
    _step(0.8)
    t_movie.start()
    _step(0.8)
    t_ep.start()
    _step(0.8)
    t_season.start()

    t_dl.join()
    t_vm.join()
    t_etl.join()
    t_movie.join()
    t_ep.join()
    t_season.join()


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="MCP Tracking Server")
    parser.add_argument("--test", action="store_true",
                        help="Ouvrir le dashboard et lancer la simulation")
    parser.add_argument("--ui",   action="store_true",
                        help="Ouvrir le dashboard sans simulation (mode serveur)")
    parser.add_argument("--filter", default="all", metavar="TEMPLATE",
                        help="Filtre initial du dashboard (ex: lyra_task, movie, errors)")
    args = parser.parse_args()

    if args.test or args.ui:
        from ui import TrackingDashboard
        app = TrackingDashboard(initial_filter=args.filter)

        if args.test:
            import threading
            thread = threading.Thread(target=_run_simulation, daemon=True)
            thread.start()

        app.run()
    else:
        mcp.run()


if __name__ == "__main__":
    main()
