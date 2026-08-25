"""Configuration pytest commune au projet tracking.

La fixture isolated_storage est autouse : aucun test ne peut toucher le
tracking_state.json de production (le service tracking-api tourne en
permanence sur cette machine).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import storage


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Redirige la persistence vers un fichier temporaire et reset le cache."""
    monkeypatch.setattr(storage, "STORAGE_FILE", tmp_path / "tracking_state.json")
    storage._cache = None
    storage._cache_mtime = -1.0
    yield storage
    storage._cache = None
    storage._cache_mtime = -1.0
