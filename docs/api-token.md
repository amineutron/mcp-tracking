# Jeton local de l'API

L'API HTTP (127.0.0.1:8765) protege ses routes d'ecriture. Cette page decrit le
mecanisme et la facon dont un client (script, service, autre projet) obtient le jeton.

## Regles appliquees par api.py

| Requete | Resultat |
|---|---|
| En-tete `Origin` present (toute requete issue d'une page web) | 403, meme avec le jeton |
| `Host` different de `127.0.0.1`, `localhost`, `[::1]` (avec, si precise, le port d'ecoute) | 403 |
| POST / PUT / DELETE sans `Authorization: Bearer <jeton>` valide | 401 |
| POST / PUT / DELETE avec un `Content-Type` autre que `application/json` | 415 |
| GET (dont `/health`, `/events`) | libre, sous reserve des controles Host et Origin |

Le jeton est compare en temps constant (`hmac.compare_digest`). Les messages d'erreur
ne revelent ni le chemin du jeton ni la valeur attendue.

## Ou se trouve le jeton

Genere au premier demarrage (`secrets.token_urlsafe(32)`), fichier en 0600 dans un
repertoire en 0700. Ordre de resolution, identique pour le serveur et les clients :

1. `TRACKING_TOKEN_FILE` : chemin impose (tests, cas particuliers)
2. `$RUNTIME_DIRECTORY/token` : `/run/tracking/token` sous le service systemd
   (`RuntimeDirectory=tracking`, mode 0700)
3. `$XDG_RUNTIME_DIR/tracking/token` : `/run/user/<uid>/tracking/token`

Le jeton vit dans un repertoire d'execution (tmpfs), pas dans le depot ni dans le
repertoire d'etat. Il est conserve tant que le fichier existe et regenere sinon (apres
un reboot) : un client le relit a chaque lancement, ou en cas de 401.

## Cote client

```bash
TOKEN=$(cat "${TRACKING_TOKEN_FILE:-/run/tracking/token}")
curl -s -X POST http://127.0.0.1:8765/sessions \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"[DEV] exemple","template":"free"}'
```

```python
import os, pathlib, requests
path = os.environ.get("TRACKING_TOKEN_FILE") or "/run/tracking/token"
headers = {"Authorization": f"Bearer {pathlib.Path(path).read_text().strip()}"}
requests.put(f"http://127.0.0.1:8765/sessions/{sid}", json={"processed": 3},
             headers=headers, timeout=5)
```

Le client doit tourner sous le meme utilisateur que le service (le fichier est illisible
pour les autres). Un client dans un conteneur Docker recoit le fichier en montage lecture
seule et pointe `TRACKING_TOKEN_FILE` dessus.

## Clients du depot

- `poller.py` : lit le jeton (`_tracking_token`) et l'envoie en Bearer.
- `server.py` (serveur MCP) et `ui.py` (TUI) : accedent a l'etat par `storage.py`, sans
  passer par HTTP ; aucun jeton necessaire.
