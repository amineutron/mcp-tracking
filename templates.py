# Templates preddefinis pour le tracking
# Chaque template definit des champs extra optionnels et une unite par defaut

TEMPLATES: dict = {
    "download": {
        "description": "Suivi de telechargement de fichiers",
        "extra_fields": ["speed", "eta"],
        "default_unit": " MB",
        "example_extra": {"speed": "12 MB/s", "eta": "5m30s"},
    },
    "machine": {
        "description": "Operations sur machines (update, clone, snapshot, etc.)",
        "extra_fields": ["operation", "target"],
        "default_unit": " machines",
        "example_extra": {"operation": "update", "target": "cluster-dev"},
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
    },
    "movie": {
        "description": "Suivi complet d'un film : download -> sous-titres -> conversion DV",
        "extra_fields": ["phase", "quality", "codec", "audio", "source", "dv", "speed", "eta", "seeds", "release", "size"],
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
    },
}


def validate_template(template: str) -> bool:
    return template in TEMPLATES


def get_template_info(template: str) -> dict:
    return TEMPLATES.get(template, TEMPLATES["free"])


def list_templates() -> str:
    lines = []
    for name, info in TEMPLATES.items():
        fields = ", ".join(info["extra_fields"]) if info["extra_fields"] else "aucun"
        lines.append(f"  {name:<12} -- {info['description']}")
        lines.append(f"             champs extra: {fields}")
    return "\n".join(lines)
