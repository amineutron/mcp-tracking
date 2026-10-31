# Changelog

Format : [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), versions [SemVer](https://semver.org/lang/fr/).

## [0.3.0] - 2026-10-31

### Modifié
- API : l'en-tête `Host` doit aussi porter le port d'écoute (protection DNS rebinding) ; les 401/403 ne révèlent plus le chemin du jeton ; un en-tête `Authorization` non ASCII donne un 401 au lieu d'une erreur serveur.

### Ajouté
- `docs/api-token.md` : règles de l'API et récupération du jeton par les clients.
- Tests d'intégration : Origin sur PUT/DELETE, Host/port, jeton non ASCII, droits 0600/0700.

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
