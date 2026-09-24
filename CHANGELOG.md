# Changelog

Format : [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), versions [SemVer](https://semver.org/lang/fr/).

## [0.2.1] - 2026-09-24

### Ajouté

- **registry** : MCP registry manifest and package ownership marker

## [0.2.0] - 2026-09-24

### Ajouté
- Jeton local obligatoire sur l'API HTTP ; seuls les processus enregistrés peuvent recevoir un signal (stop/kill).
- Workflow de release sur tag `v*` : build du wheel, publication PyPI par Trusted Publishing, release GitHub.
- Démo enregistrée (GIF) et script de régénération.

### Modifié
- Serveur MCP sur `mcp` 2.

## [0.1.0] - 2026-09-09

Première version publiée : paquet installable (hatchling, points d'entrée console), modèles d'unités systemd, CI, Dependabot, README sans chemin personnel.
