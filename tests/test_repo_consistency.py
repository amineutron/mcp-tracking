"""Garde-fous de coherence du depot (dette zero) : le code, la doc et les
metadonnees de release ne doivent pas diverger.

Ces tests echouent des qu'une version est bumpee a un seul endroit, qu'une
variable de resolution du jeton change sans mise a jour de la doc, ou qu'une
release part sans entree de CHANGELOG.
"""
import inspect
import json
import re
import tomllib
from pathlib import Path

import api

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def test_version_identique_dans_pyproject_et_server_json():
    server = json.loads((ROOT / "server.json").read_text())
    versions = {server["version"], *(p["version"] for p in server.get("packages", []))}
    assert versions == {_pyproject_version()}, f"versions divergentes : {versions}"


def test_changelog_documente_la_version_courante():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    version = re.escape(_pyproject_version())
    assert re.search(rf"^##\s*\[?v?{version}\b", changelog, re.MULTILINE), (
        f"CHANGELOG.md sans entree pour {_pyproject_version()}"
    )


def test_la_doc_du_jeton_cite_chaque_variable_de_resolution():
    """Les variables lues par token_path() doivent toutes figurer dans docs/api-token.md."""
    source = inspect.getsource(api.token_path)
    variables = set(re.findall(r'environ\.get\("([A-Z_]+)"', source))
    assert variables, "token_path() ne lit plus aucune variable : test a revoir"
    doc = (ROOT / "docs" / "api-token.md").read_text()
    manquantes = sorted(v for v in variables if v not in doc)
    assert not manquantes, f"variables absentes de docs/api-token.md : {manquantes}"
