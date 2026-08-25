# MCP Tracking

Serveur MCP de suivi en temps reel avec dashboard terminal.
Permet a Claude/Lyra de tracker n'importe quelle operation longue ET alimente automatiquement
les sessions depuis le media-server (qBittorrent, Bazarr, conversion DV).

---

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
  server.py              -- Serveur MCP + simulations de test
  ui.py                  -- Dashboard Textual (TUI temps reel) + modales stop/kill
  models.py              -- Modeles de donnees (TrackingSession, TrackingItem, LogEntry)
  storage.py             -- Stockage en memoire + persistence JSON atomique
  templates.py           -- Definition des templates disponibles
  api.py                 -- API HTTP locale (127.0.0.1:8765) pour les scripts externes
  poller.py              -- Daemon polling qBittorrent (10s) + Bazarr (60s)
  tracking-api.service   -- Service systemd pour api.py
  tracking-poller.service -- Service systemd pour poller.py
  pyproject.toml         -- Dependances Python
  tracking_state.json    -- Etat courant (cree automatiquement)
  poller_state.json      -- Etat interne du poller (cree automatiquement)
```

### Flux de donnees complet

```
Claude/Lyra (outils MCP)
      |
      v
  server.py ─────────────────────────────────────────┐
                                                      |
qBittorrent API (poll 10s)                            |
      |                                               |
Bazarr API (poll 60s)    ──> poller.py ──> api.py ──> storage.py ──> tracking_state.json
      |                                               |                        |
dv_webhook_server.py                                  |                        v
      |                                               |                     ui.py
      v                                               |               (rafraichit chaque seconde)
dv_convert.py ──────────────────────────────────────>
   (metriques temps reel ffmpeg/dovi_tool)
```

Le fichier `tracking_state.json` est ecrit a chaque modification via ecriture atomique (`os.replace`).
Tous les processus (MCP, poller, dashboard) partagent cet unique fichier.

---

## Installation

```bash
cd /home/amineutron/dev/MCP/tracking

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
  /home/amineutron/dev/MCP/tracking/.venv/bin/python \
  /home/amineutron/dev/MCP/tracking/server.py
```

---

## Services systemd

Deux services tournent en permanence et se lancent au boot :

| Service | Role | Port |
|---------|------|------|
| `tracking-api.service` | API HTTP locale pour scripts externes | 127.0.0.1:8765 |
| `tracking-poller.service` | Poll qBittorrent (10s) + Bazarr (60s) | -- |

### Installation initiale

```bash
cd /home/amineutron/dev/MCP/tracking
./install.sh
```

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
/home/amineutron/dev/MCP/tracking/.venv/bin/python \
  /home/amineutron/dev/MCP/tracking/server.py --ui

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
- Credentials : `admin / adminadmin`

### Bazarr sous-titres manquants (automatique)

Le poller interroge l'API Bazarr toutes les 60 secondes.

- Une session `[FREE]` unique liste tous les episodes/films sans sous-titres FR
- Le titre de la session indique le total : `Sous-titres manquants (151)`
- Les 50 premiers fichiers manquants sont listes comme items
- API key Bazarr : dans `/home/amineutron/dev/media-server/data/config/bazarr/config/config.ini`

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
python /home/amineutron/dev/media-server/scripts/dv_convert.py /chemin/film.mkv

# Scan dossier
python /home/amineutron/dev/media-server/scripts/dv_convert.py --scan /mnt/media/media/movies
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
  template  (str)          "download" | "machine" | "free" | "movie" | "lyra_task"
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

- `api.py` ecoute uniquement sur `127.0.0.1:8765` -- inaccessible depuis le reseau
- n8n restreint a `127.0.0.1:5678` dans `docker-compose.yml`
- `dv_webhook_server.py` ecoute sur `0.0.0.0:8787` (necessaire pour recevoir les webhooks Docker) -- proteger ce port avec un firewall si la machine est exposee
- Les services systemd tournent avec `NoNewPrivileges=true`
- Les API keys Radarr/Sonarr/Bazarr sont dans `media-server/.env` et `dv_convert.py`

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

2. Optionnel : ajouter une simulation `_sim_mon_template()` dans `server.py`.

Le template est immediatement disponible sans autre modification.
