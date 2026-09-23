# MCP Tracking

[![tests](https://github.com/amineutron/mcp-tracking/actions/workflows/tests.yml/badge.svg)](https://github.com/amineutron/mcp-tracking/actions/workflows/tests.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

**English summary.** Local-first tracking of long-running tasks: an MCP server for Claude or Lyra, a small HTTP API on 127.0.0.1:8765 and a Textual terminal dashboard. Sessions have items, progress, logs and templates (download, machine, free, lyra_task, movie); pollers feed qBittorrent and Bazarr sessions automatically. Install: `pip install .` then `mcp-tracking`, `mcp-tracking-api`, `mcp-tracking-ui`. No cloud, no telemetry.

Serveur MCP de suivi en temps reel avec dashboard terminal.
Permet a Claude/Lyra de tracker n'importe quelle operation longue ET alimente automatiquement
les sessions depuis le media-server (qBittorrent, Bazarr, conversion DV).

---

## Demo

![Dashboard terminal alimente par les simulations du mode test](docs/assets/demo.gif)

Enregistree avec [`docs/demo/record.sh`](docs/demo/record.sh) : `server.py --test` alimente quatre sessions simulees dans un repertoire d'etat temporaire (`TRACKING_STATE_DIR`), puis `server.py --ui` ouvre le dashboard dessus. Les sessions reelles ne sont pas touchees.

## Sommaire

- [Architecture](#architecture)
- [Installation](#installation)
- [Services systemd](#services-systemd)
- [Lancement](#lancement)
- [Dashboard](#dashboard)
- [Integration media-server](#integration-media-server)
- [Outils MCP](#outils-mcp)
- [Templates](#templates)
- [Securite](#securite)
- [Ajouter un template](#ajouter-un-template)

---

## Architecture

```
MCP/tracking/
  server.py              -- Serveur MCP (outils Claude/Lyra) + point d'entree --ui / --test
  api.py                 -- API HTTP locale (127.0.0.1:8765) pour les scripts externes
  mutations.py           -- Mutations d'une session, partagees par api.py ET server.py
                            (horodatage items, historique, niveaux de log, auto-completion)
  metrics.py             -- Metriques derivees (vitesse, ETA, ecoule, stale) -- logique pure,
                            calculees a la lecture, jamais stockees
  storage.py             -- Persistence JSON atomique + verrou fichier + cache mtime + purge TTL
  models.py              -- Modeles pydantic (TrackingSession, TrackingItem, LogEntry, ProgressPoint)
  templates.py           -- Templates builtin + templates utilisateur (JSON)
  ui.py                  -- Dashboard Textual (TUI temps reel) + modales stop/kill
  sim.py                 -- Simulations de demo (server.py --test)
  poller.py              -- Daemon polling qBittorrent (10s) + Bazarr (60s)
  tracking-api.service   -- Unite systemd (systeme) pour api.py
  tracking-poller.service -- Unite systemd (systeme) pour poller.py
  install.sh / deploy.sh -- Installation initiale / redeploiement des services
  Makefile               -- make test | smoke | deploy | ui
  tests/                 -- unitaires (storage, metrics) + integration/ (API HTTP reelle)
```

### Fichiers d'etat et configuration

| Fichier | Emplacement | Surcharge |
|---------|-------------|-----------|
| `tracking_state.json` | `~/.local/state/tracking/` | `TRACKING_STATE_DIR` |
| `poller_state.json`   | `~/.local/state/tracking/` | `TRACKING_STATE_DIR` |
| `templates.json` (templates utilisateur, optionnel) | `~/.config/tracking/` | `TRACKING_TEMPLATES_FILE` |
| `credentials/*.cred` (qBittorrent, Bazarr) | a cote du code, gitignore | -- |

Un ancien `tracking_state.json` a cote du code est migre automatiquement au premier
demarrage (copie, jamais supprime).

Variables d'environnement de retention :

| Variable | Defaut | Role |
|----------|--------|------|
| `TRACKING_TTL_DAYS` | 7 | Purge des sessions done / error / paused |
| `TRACKING_TTL_RUNNING_H` | 24 | Purge des sessions running orphelines (plus mises a jour) |

### Flux de donnees complet

```
Claude/Lyra (outils MCP)
      |
      v
  server.py ─────────────────────────────────────────┐
                                                      |
qBittorrent API (poll 10s)                            |
      |                                               |
Bazarr API (poll 60s)    ──> poller.py ──> api.py ──> mutations.py ──> storage.py ──> ~/.local/state/tracking/tracking_state.json
      |                                               |                        |
dv_webhook_server.py                                  |                        v
      |                                               |                     ui.py
      v                                               |               (rafraichit chaque seconde)
dv_convert.py ──────────────────────────────────────>
   (metriques temps reel ffmpeg/dovi_tool)
```

Le fichier d'etat est ecrit a chaque modification via ecriture atomique (`os.replace`) sous verrou
fichier (`tracking_state.lock`). Tous les processus (MCP, API, poller, dashboard) partagent cet
unique fichier ; chaque lecture verifie le mtime pour invalider son cache.

Toute mutation (HTTP ou MCP) passe par `mutations.py`, qui garantit le meme comportement sur les
deux chemins : `started_at` / `finished_at` poses sur les items et la session, historique de
progression (fenetre glissante de 40 points), niveaux de log `info` / `warn` / `error`,
auto-completion quand tous les items sont termines.

### Metriques derivees

`GET /sessions` et `tracking_get` renvoient un bloc `metrics` calcule a la volee par `metrics.py` :

| Champ | Sens |
|-------|------|
| `percent` | progression (plafonnee a 100) |
| `rate`, `rate_str` | vitesse sur les 120 dernieres secondes (`2.0 MB/s`, `30.0 u/min`) |
| `eta_seconds`, `eta_str` | temps restant estime (session running uniquement) |
| `elapsed_seconds`, `elapsed_str` | depuis `created_at` jusqu'a `finished_at` ou maintenant |
| `idle_seconds`, `stale` | `stale` = running sans mise a jour depuis 10 min (affiche dans le TUI) |

---

## Installation en une ligne

```bash
uvx mcp-tracking          # serveur MCP (stdio) ; avant publication : uvx --from git+https://github.com/amineutron/mcp-tracking mcp-tracking
uvx --from mcp-tracking mcp-tracking-api   # API HTTP 127.0.0.1:8765
uvx --from mcp-tracking mcp-tracking-ui    # tableau de bord terminal
```

Configuration Claude Desktop / Claude Code (`mcpServers`) :

```json
{ "tracking": { "command": "uvx", "args": ["mcp-tracking"] } }
```

## Installation

```bash
cd <dossier du dépôt>

# Creer le venv et installer les dependances
uv venv .venv
uv pip install "mcp[cli]>=1.0.0" "pydantic>=2.0" "textual>=0.80.0" "fastapi"
```

Le MCP est enregistre dans Claude Code (scope user) :

```bash
claude mcp list        # -> tracking: Connected
```

Pour reenregistrer :

```bash
claude mcp add tracking -s user -- \
  <dossier du dépôt>/.venv/bin/python \
  <dossier du dépôt>/server.py
```

---

## Services systemd

Deux services tournent en permanence et se lancent au boot :

| Service | Role | Port |
|---------|------|------|
| `tracking-api.service` | API HTTP locale pour scripts externes | 127.0.0.1:8765 |
| `tracking-poller.service` | Poll qBittorrent (10s) + Bazarr (60s) | -- |

### Installation initiale et redeploiement

```bash
cd <dossier du dépôt>
./install.sh        # premiere fois : venv + services (demande sudo)
sudo ./deploy.sh    # apres chaque mise a jour du code : stop, unites, restart, verif
make smoke          # sante rapide
```

Les instances MCP `server.py` deja ouvertes par des sessions Claude Code ne sont pas
redemarrees par `deploy.sh` : reconnecter `tracking` via `/mcp` dans ces sessions.

### Commandes utiles

```bash
# Etat
systemctl status tracking-api.service tracking-poller.service

# Logs en direct
journalctl -fu tracking-poller.service
journalctl -fu tracking-api.service

# Redemarrage
sudo systemctl restart tracking-api.service tracking-poller.service

# Test API
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/sessions
```

---

## Lancement

### Dashboard (raccourci wofi)

Cherche "MCP Tracking" dans wofi/launcher. Lance le dashboard dans Kitty.

### Dashboard (terminal)

```bash
# Toutes les sessions
<dossier du dépôt>/.venv/bin/python \
  <dossier du dépôt>/server.py --ui

# Filtre direct au lancement
.venv/bin/python server.py --ui --filter download
.venv/bin/python server.py --ui --filter movie
.venv/bin/python server.py --ui --filter errors
```

### Via outil MCP (depuis Claude/Lyra)

```python
open_tracking_ui()                             # toutes les sessions
open_tracking_ui(filter_template="lyra_task")  # vue Lyra uniquement
open_tracking_ui(filter_template="errors")     # erreurs uniquement
```

### Mode test (demo)

```bash
.venv/bin/python server.py --test
```

Simule 4 sessions en parallele : download, machine (12 noeuds), free, movie (pipeline DV complet).

---

## Dashboard

### Layout d'une session

```
[TEMPLATE]  Nom de la session  id:xxxxxxxx  (status)
  [=============>            ] 54.2%  27100 MB / 50000 MB
  champ_extra1: valeur  |  champ_extra2: valeur

  [ok]  item-1                          100.0 GB     -- termine
  [>]   item-2                          frame: 94231 / 172800  (54.5%)  speed: 3.2x
  [ ]   item-3                          --
  [!]   item-4                          erreur detail

  Logs                                  Erreurs
  14:32:01  Message log 1               [!] item-4
  14:32:04  Message log 2               14:32:08  ECHEC: details
  14:32:07  Message log 3               --
  --                                    --
  --                                    --
```

### Icones items

| Icone  | Statut  | Couleur |
|--------|---------|---------|
| `[ ]`  | pending | gris    |
| `[>]`  | running | cyan    |
| `[ok]` | done    | vert    |
| `[!]`  | error   | rouge   |

### Couleurs de session

| Couleur | Statut  |
|---------|---------|
| cyan    | running |
| vert    | done    |
| rouge   | error   |
| jaune   | paused  |

### Raccourcis clavier

| Touche | Action |
|--------|--------|
| `f`    | Filtre suivant (cycle dynamique par template) |
| `e`    | Basculer filtre erreurs uniquement |
| `r`    | Refresh manuel |
| `s`    | Stop propre d'une session (saisir l'ID) -> status paused |
| `k`    | Kill force d'une session (saisir l'ID) -> suppression |
| `q`    | Quitter |
| Fleches / Molette | Scroll |

### Modales stop/kill

Appuyer sur `s` ou `k` ouvre un modal avec un champ de saisie pour l'ID de session.
- `s` marque la session en `paused` et ajoute un log
- `k` supprime definitivement la session du dashboard
- `Echap` annule

### Filtrage dynamique

Le cycle de filtres est construit automatiquement depuis les sessions presentes :

```
all -> download -> free -> movie -> lyra_task -> errors -> all -> ...
```

- `all` toujours present
- Chaque template present dans le JSON s'ajoute automatiquement
- `errors` n'apparait que si au moins une session a une erreur
- Filtre actif affiche dans le sous-titre : `filtre: movie  |  2/5 session(s)`
- Si le template filtre disparait du JSON, retour automatique a `all`

---

## Integration media-server

### qBittorrent (automatique)

Le poller interroge `http://localhost:8080/api/v2/torrents/info` toutes les 10 secondes.

- Un torrent actif = une session `[DOWNLOAD]` avec nom, taille, vitesse, ETA
- La session est supprimee automatiquement quand le torrent termine ou disparait
- Credentials : `credentials/qbt-password.cred` (chiffre `systemd-creds --user`, genere par `media-server/scripts/secrets/rotate-secrets.sh`)

### Bazarr sous-titres manquants (automatique)

Le poller interroge l'API Bazarr toutes les 60 secondes.

- Une session `[SUBTITLES]` unique liste tous les episodes/films sans sous-titres FR
- Le titre de la session indique le total : `Sous-titres manquants (151)`
- Les 50 premiers fichiers manquants sont listes comme items
- API key Bazarr : `credentials/bazarr-api-key.cred` (meme mecanisme). Sans credential, le poll concerne est simplement desactive.

### Conversion Dolby Vision (automatique)

Declenche par `dv-webhook.service` quand Radarr/Sonarr importent un film DV Profile 4 ou 7.

**Flux :**
```
Radarr/Sonarr import
      |
      v
dv_webhook_server.py (port 8787)
      |-- cree session tracking via api.py
      |-- passe DV_TRACKING_SESSION_ID en env
      v
dv_convert.py
      |-- 6 etapes avec metriques temps reel
      |-- ffmpeg   : frame / speed / size / time (parse stderr)
      |-- dovi_tool: frames X/Y ou X% (parse stderr indicatif)
      v
session tracking completee ou en erreur
```

**Les 6 etapes trackees avec leurs metriques :**

| Etape | Outil | Metriques affichees |
|-------|-------|---------------------|
| 1/6 extraction HEVC | ffmpeg | frame / speed / size / time |
| 2/6 demux BL/EL | dovi_tool | frames X/Y (%), bl: X GB, el: X GB |
| 3/6 extraction RPU + conv P8 | dovi_tool | frames X/Y (%), RPU: X KB |
| 4/6 injection RPU P8 dans BL | dovi_tool | frames X/Y (%), P8 HEVC: X GB |
| 5/6 reconstruction timestamps | ffmpeg | frame / fps / size |
| 6/6 remuxage MKV final | ffmpeg | frame / speed / size |

La barre de progression globale avance en continu pendant chaque etape
(pas par sauts de 1/6 a la fin de chaque etape).

**Mode manuel :**

```bash
# Fichier unique
python <media-server>/scripts/dv_convert.py /chemin/film.mkv

# Scan dossier
python <media-server>/scripts/dv_convert.py --scan /mnt/media/media/movies
```

En mode manuel, la session tracking est creee automatiquement dans `process_file`.

### API HTTP locale (port 8765)

Scripts externes peuvent creer/modifier des sessions directement :

```bash
# Creer une session
curl -X POST http://127.0.0.1:8765/sessions \
  -H "Content-Type: application/json" \
  -d '{"name":"Mon operation","template":"free","total":100,"unit":"%"}'
# -> {"id": "a1b2c3d4"}

# Mettre a jour
curl -X PUT http://127.0.0.1:8765/sessions/a1b2c3d4 \
  -H "Content-Type: application/json" \
  -d '{"processed":45,"log":"Etape 2/5 en cours","extra":{"phase":"etape 2"}}'

# Mettre a jour un item
curl -X PUT http://127.0.0.1:8765/sessions/a1b2c3d4 \
  -H "Content-Type: application/json" \
  -d '{"item":{"name":"mon-item","status":"done","note":"100 frames  speed: 2x"}}'

# Supprimer
curl -X DELETE http://127.0.0.1:8765/sessions/a1b2c3d4

# Lister
curl http://127.0.0.1:8765/sessions
```

**Corps PUT complet (tous les champs optionnels) :**

```json
{
  "processed": 45.0,
  "total":     100.0,
  "status":    "running",
  "extra":     {"phase": "etape 2"},
  "log":       "message de log",
  "item": {
    "name":      "nom-de-l-item",
    "status":    "running",
    "note":      "metriques ici",
    "processed": 50.0,
    "total":     100.0
  }
}
```

---

## Outils MCP

### `tracking_create`

```
Parametres:
  name      (str)          Nom de la session
  template  (str)          "download" | "machine" | "free" | "movie" | "lyra_task" |
                           "subtitles" | "series_episode" | "series_season" | template utilisateur
  total     (float)        Valeur totale
  unit      (str, opt)     Unite affichee (ex: " MB", " machines", "%")
  items     (list, opt)    Liste d'elements a suivre
  extra     (dict, opt)    Champs specifiques au template

Format items:
  [{"name": "fichier.iso", "total": 5100, "unit": " MB", "note": "info"}]

Retourne: ID de session + etat initial formate
```

### `tracking_update`

```
Parametres:
  session_id    (str)          ID de la session
  processed     (float, opt)   Nouvelle valeur de progression
  message       (str, opt)     Message de log
  item_updates  (list, opt)    Mises a jour des items
  extra         (dict, opt)    Champs extra a merger

Format item_updates:
  [{"name": "item-1", "status": "done", "processed": 1200, "note": "detail"}]
  Status: "pending" | "running" | "done" | "error"
```

### `tracking_log`

Ajoute un log sans modifier la progression.

```
Parametres:
  session_id  (str)
  message     (str)
```

### `tracking_complete`

Marque done a 100%.

```
Parametres:
  session_id  (str)
  message     (str, opt)
```

### `tracking_error`

Marque en erreur (prefixe "ERREUR:" auto, remonte dans colonne Erreurs).

```
Parametres:
  session_id  (str)
  message     (str)
```

### `tracking_stop`

Arrete proprement une session (status -> paused). Reste visible dans le dashboard.

```
Parametres:
  session_id  (str)
  message     (str, opt)
```

### `tracking_kill`

Supprime une session en force. Disparait immediatement du dashboard.

```
Parametres:
  session_id  (str)
```

### `tracking_get`

Retourne l'etat complet formate d'une session.

### `tracking_list`

```
Parametres:
  template  (str, opt)   Filtrer par template
  status    (str, opt)   Filtrer par statut ("running", "done", "error", "paused")
```

### `tracking_delete`

Supprime une session (equivalent de tracking_kill).

### `tracking_templates`

Affiche la liste des templates et leurs champs.

### `open_tracking_ui`

Ouvre le dashboard dans un terminal Kitty.

```
Parametres:
  filter_template  (str, opt)   Template a afficher au lancement
```

---

## Templates

### `download`

Telechargement de fichiers. Alimente automatiquement par qBittorrent via le poller.

```
Champs extra : speed, eta
Unite par defaut : MB
```

### `machine`

Operations sur des machines (update, clone, snapshot, deploy).
Utilise par Lyra pour les operations VM/cluster.

```
Champs extra : operation, target
Unite par defaut : machines
```

### `free`

Format libre. Utilise par le poller pour les sous-titres Bazarr manquants.

```
Aucun champ extra impose, aucune unite par defaut.
```

### `lyra_task`

Operations Lyra (VM clone, backup, update, snapshot).

```
Champs extra : operation, target, phase, eta
Unite par defaut : %
```

### `movie`

Pipeline complet d'un film : telechargement -> conversion Dolby Vision.
Alimente automatiquement par dv_convert.py quand Radarr/Sonarr importent un fichier DV P4/P7.

```
Champs extra : phase, quality, codec, audio, source, dv, speed, eta
Unite par defaut : %

Les 6 etapes DV trackees avec metriques temps reel :
  "1/6 extraction HEVC"
  "2/6 demux BL/EL"
  "3/6 extraction RPU + conv P8"
  "4/6 injection RPU P8 dans BL"
  "5/6 reconstruction timestamps"
  "6/6 remuxage MKV final"
```

---

## Securite

### Modele de menace

Deux attaquants realistes sur une machine de bureau :

1. **Une page web ouverte dans le navigateur.** « localhost n'est pas une frontiere » : une page
   peut emettre des requetes vers `127.0.0.1:8765`. Sans protection, elle pourrait creer des
   sessions, en supprimer, et surtout demander l'envoi d'un signal a un processus.
2. **Un autre utilisateur local** (ou un service compromis) qui tenterait de lire l'etat ou de
   piloter l'API.

### Mesures

- `api.py` ecoute uniquement sur `127.0.0.1:8765` -- inaccessible depuis le reseau
- **Jeton local obligatoire en ecriture** : genere au premier demarrage dans
  `$XDG_RUNTIME_DIR/tracking/token` (droits 0600, donc illisible par un autre utilisateur), exige
  en `Authorization: Bearer ...` sur POST, PUT et DELETE. Une page web ne peut pas le lire.
- **Requetes de navigateur refusees** : tout en-tete `Origin` donne un 403, meme avec le jeton.
- **En-tete `Host` verifie** (boucle locale uniquement) et `Content-Type: application/json` exige
  en ecriture.
- **Signaux limites aux processus enregistres par le serveur** : a la creation d'une session, le
  serveur verifie que le `pid` annonce existe et appartient au meme utilisateur, puis releve son
  heure de demarrage. `/stop` et `/kill` refusent d'agir si cette empreinte a change (numero de
  processus recycle par un autre programme) ou si le pid n'a jamais ete enregistre. Le `pid` ne
  peut plus etre modifie par un `PUT`. Chaque signal envoye est journalise.
- **Services systemd durcis** : `ProtectSystem=strict`, `ProtectHome=read-only` avec le seul etat
  en ecriture, `PrivateTmp`, `SystemCallFilter=@system-service`, `CapabilityBoundingSet=` vide,
  `UMask=0077`. Verifiable avec `systemd-analyze security tracking-api.service`.
- **Limite connue** : `lyra-daemon` garde un `sudo NOPASSWD` pour piloter la machine (services,
  VMs, audio). Ce n'est pas le tracking qui l'accorde, et le durcir releve du projet Lyra ; tant
  que ce daemon existe, un attaquant qui obtiendrait l'execution de code sous cet utilisateur
  disposerait de ce pouvoir, independamment des protections ci-dessus.
- n8n restreint a `127.0.0.1:5678` dans `docker-compose.yml`
- `dv_webhook_server.py` ecoute sur `0.0.0.0:8787` (necessaire pour recevoir les webhooks Docker) -- proteger ce port avec un firewall si la machine est exposee
- Les services systemd tournent avec `NoNewPrivileges=true`
- Aucun secret en clair dans le code : `poller.py` lit `$CREDENTIALS_DIRECTORY` (service user) ou dechiffre `credentials/*.cred` via `systemd-creds decrypt --user` (service systeme), avec repli sur les variables `QBT_PASSWORD` / `BAZARR_KEY` pour le debug

---

## Ajouter un template

1. Ouvrir `templates.py` et ajouter une entree dans `TEMPLATES` :

```python
"mon_template": {
    "description": "Description courte",
    "extra_fields": ["champ1", "champ2"],
    "default_unit": " unites",
    "example_extra": {"champ1": "valeur", "champ2": "valeur"},
},
```

2. Optionnel : ajouter une simulation `_sim_mon_template()` dans `sim.py`.

Le template est immediatement disponible sans autre modification.

Sans toucher au code, un template peut aussi etre declare dans `~/.config/tracking/templates.json`
(meme structure, cle = nom du template) ; il est charge au demarrage.

---

## Tests

```bash
make test     # unitaires (storage, metrics) + integration (API HTTP reelle sur port ephemere)
```

La fixture autouse de `conftest.py` redirige la persistence vers un `tmp_path` : les tests ne
touchent jamais l'etat de production.

## Part of the Lyra ecosystem

| Dépôt | Rôle |
|---|---|
| [lyra](https://github.com/amineutron/lyra) | assistant DevOps vocal, local par défaut (AGPL-3.0) |
| [fedora-agents](https://github.com/amineutron/fedora-agents) | MCP : machines virtuelles KVM et sauvegardes |
| [mcp-tracking](https://github.com/amineutron/mcp-tracking) | MCP + API + tableau de bord des tâches longues |
| [neutroncore](https://github.com/amineutron/neutroncore) | hub PWA du homelab |
| [hue-mcp](https://github.com/amineutron/hue-mcp) | MCP Philips Hue (fork de ThomasRohde/hue-mcp) |
| [pylips-mcp](https://github.com/amineutron/pylips-mcp) | MCP TV Philips |
| [denon-mcp](https://github.com/amineutron/denon-mcp) | MCP ampli Denon |
| [catt-mcp](https://github.com/amineutron/catt-mcp) | MCP Chromecast et DLNA |
