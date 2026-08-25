"""Templates de tracking : builtins + templates utilisateur.

Chaque template declare :
  description   -- texte libre
  extra_fields  -- champs extra attendus (documentation, non bloquant)
  default_unit  -- unite par defaut
  example_extra -- exemple pour l'aide MCP
  auto_done     -- (bool) passer en done des que processed >= total
  display       -- rendu generique du dashboard :
       metrics : champs extra mis en avant en couleur (speed, eta, phase...)
       info    : champs extra affiches en dim sur une ligne (quality, codec...)
       hide    : champs extra jamais affiches
       pipeline: liste ordonnee de phases (ex: ["download", "subtitles", "dv-conv"])
                 -- affichee comme frise si extra.phase est defini
       layout  : "list" (defaut) | "grid" (items en grille compacte, ex: episodes)
     Tout champ extra non liste est affiche apres les metrics.

Templates utilisateur : ~/.config/tracking/templates.json (ou
$TRACKING_TEMPLATES_FILE), meme structure, fusionnes par-dessus les builtins.
"""
import json
import os
from pathlib import Path

DEFAULT_DISPLAY: dict = {
    "metrics": [], "info": [], "hide": [], "pipeline": [], "layout": "list",
}

_MEDIA_PIPELINE = ["download", "subtitles", "dv-conv"]
_MEDIA_INFO     = ["quality", "codec", "audio", "source"]
_MEDIA_METRICS  = ["speed", "eta", "seeds", "size"]

BUILTIN_TEMPLATES: dict = {
    "download": {
        "description": "Suivi de telechargement de fichiers",
        "extra_fields": ["speed", "eta"],
        "default_unit": " MB",
        "example_extra": {"speed": "12 MB/s", "eta": "5m30s"},
        "display": {"metrics": ["speed", "eta", "seeds"]},
    },
    "machine": {
        "description": "Operations sur machines (update, clone, snapshot, etc.)",
        "extra_fields": ["operation", "target"],
        "default_unit": " machines",
        "example_extra": {"operation": "update", "target": "cluster-dev"},
        "display": {"metrics": ["operation", "target"]},
    },
    "free": {
        "description": "Format libre -- aucune contrainte supplementaire",
        "extra_fields": [],
        "default_unit": "",
        "example_extra": {},
    },
    "lyra_task": {
        "description": "Operations Lyra (VM clone, backup, update, snapshot...)",
        "extra_fields": ["operation", "target", "phase", "eta"],
        "default_unit": "%",
        "example_extra": {
            "operation": "vm_clone",
            "target":    "preprod-01",
            "phase":     "copie disque",
            "eta":       "45s",
        },
        "display": {"metrics": ["phase", "eta"], "info": ["operation", "target"]},
    },
    "movie": {
        "description": "Suivi complet d'un film : download -> sous-titres -> conversion DV",
        "extra_fields": ["phase", "quality", "codec", "audio", "source", "dv",
                         "speed", "eta", "seeds", "release", "size"],
        "default_unit": "%",
        "example_extra": {
            "phase":   "download",
            "quality": "2160p UHD",
            "codec":   "HEVC / DV P7",
            "audio":   "TrueHD Atmos 7.1",
            "source":  "FraMeSToR",
            "release": "Dune.Part.Two.2024.2160p.UHD.BluRay.TrueHD.Atmos.7.1.DV.HEVC-FraMeSToR.mkv",
            "size":    "57.0 GB",
        },
        "display": {"metrics": _MEDIA_METRICS, "info": _MEDIA_INFO + ["release"],
                    "hide": ["phase", "dv"], "pipeline": _MEDIA_PIPELINE},
    },
    "series_episode": {
        "description": "Suivi d'un episode seul : download -> sous-titres -> conversion DV",
        "extra_fields": ["season", "episode", "episode_title", "phase", "quality", "codec",
                         "audio", "source", "dv", "speed", "eta", "seeds", "release", "size"],
        "default_unit": "%",
        "example_extra": {
            "season":        "S03",
            "episode":       "E07",
            "episode_title": "One Minute",
            "phase":         "download",
            "quality":       "2160p UHD",
            "codec":         "HEVC / DV P7",
            "audio":         "TrueHD 7.1",
            "source":        "NOGROUP",
            "speed":         "45 MB/s",
            "eta":           "8m30s",
            "size":          "27.7 GB",
        },
        "display": {"metrics": _MEDIA_METRICS,
                    "info": ["season", "episode", "episode_title"] + _MEDIA_INFO + ["release"],
                    "hide": ["phase", "dv"], "pipeline": _MEDIA_PIPELINE},
    },
    "subtitles": {
        "description": "Sous-titres manquants surveilles par Bazarr",
        "extra_fields": ["source", "missing_episodes", "missing_movies"],
        "default_unit": " fichiers",
        "example_extra": {
            "source":           "Bazarr",
            "missing_episodes": "45",
            "missing_movies":   "12",
        },
        "display": {"metrics": ["missing_episodes", "missing_movies"], "info": ["source"]},
    },
    "series_season": {
        "description": "Suivi d'une saison complete (batch/pack) -- un item par episode",
        "extra_fields": ["season", "quality", "codec", "audio", "source", "dv",
                         "release", "phase", "current_episode", "seeds"],
        "default_unit": " ep",
        "example_extra": {
            "season":          "S05",
            "quality":         "2160p UHD",
            "codec":           "HEVC / DV P7->P8",
            "audio":           "TrueHD 7.1",
            "source":          "GROUP",
            "dv":              "P7 -> P8",
            "release":         "Better.Call.Saul.S05.2160p.UHD.BluRay.TrueHD.7.1.DV.HEVC-GROUP",
            "phase":           "download",
            "current_episode": "S05E03 - The Guy For This",
        },
        "display": {"metrics": ["current_episode", "speed", "eta", "seeds"],
                    "info": ["season"] + _MEDIA_INFO + ["release"],
                    "hide": ["phase", "dv"], "pipeline": _MEDIA_PIPELINE, "layout": "grid"},
    },
}

# Alias de phases acceptes dans extra.phase -> index de la frise media
PHASE_ALIASES: dict = {
    "download": "download", "telechargement": "download",
    "en attente (seeds)": "download", "recuperation metadata": "download",
    "verification": "download", "en file": "download", "allocation disque": "download",
    "en pause": "download", "detection DV": "download",
    "subtitles": "subtitles", "sous-titres": "subtitles", "sous-titres manquants": "subtitles",
    "dv-conv": "dv-conv", "convert": "dv-conv",
}


def _user_templates_file() -> Path:
    env = os.environ.get("TRACKING_TEMPLATES_FILE")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(xdg) / "tracking" / "templates.json"


def _normalize(name: str, raw: dict) -> dict:
    tpl = {
        "description":   str(raw.get("description", name)),
        "extra_fields":  list(raw.get("extra_fields", [])),
        "default_unit":  str(raw.get("default_unit", "")),
        "example_extra": dict(raw.get("example_extra", {})),
        "auto_done":     bool(raw.get("auto_done", False)),
        "display":       {**DEFAULT_DISPLAY, **(raw.get("display") or {})},
    }
    return tpl


def load_user_templates() -> dict:
    """Templates definis par l'utilisateur ; {} si absent ou invalide."""
    path = _user_templates_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"templates utilisateur ignores ({path}): {exc}", flush=True)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _build() -> dict:
    merged = {n: _normalize(n, t) for n, t in BUILTIN_TEMPLATES.items()}
    for n, t in load_user_templates().items():
        base = BUILTIN_TEMPLATES.get(n, {})
        merged[n] = _normalize(n, {**base, **t})
    return merged


TEMPLATES: dict = _build()


def reload() -> dict:
    """Recharge les templates (apres edition du fichier utilisateur)."""
    global TEMPLATES
    TEMPLATES = _build()
    return TEMPLATES


def validate_template(template: str) -> bool:
    return template in TEMPLATES


def get_template_info(template: str) -> dict:
    return TEMPLATES.get(template, TEMPLATES["free"])


def get_display(template: str) -> dict:
    return get_template_info(template)["display"]


def list_templates() -> str:
    lines = []
    for name, info in TEMPLATES.items():
        fields = ", ".join(info["extra_fields"]) if info["extra_fields"] else "aucun"
        lines.append(f"  {name:<14} -- {info['description']}")
        lines.append(f"               champs extra: {fields}")
    return "\n".join(lines)
