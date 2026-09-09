#!/bin/bash
# (Re)deploiement des services tracking -- a lancer avec sudo.
# 1. arrete api + poller, 2. resynchronise l'etat legacy (a cote du code)
# vers ~/.local/state/tracking si present, 3. installe les unites,
# 4. redemarre et verifie.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
OWNER="$(stat -c %U "$DIR")"
STATE="$(getent passwd "$OWNER" | cut -d: -f6)/.local/state/tracking"

systemctl stop tracking-poller.service tracking-api.service 2>/dev/null || true

sudo -u "$OWNER" mkdir -p "$STATE"
for f in tracking_state.json poller_state.json; do
    if [ -f "$DIR/$f" ] && [ "$DIR/$f" -nt "$STATE/$f" ]; then
        sudo -u "$OWNER" cp -p "$DIR/$f" "$STATE/$f"
    fi
done

# Unites rendues depuis les modeles *.service.in : chemin du depot et utilisateur courant
for u in tracking-api tracking-poller; do
    sed -e "s#@DIR@#$DIR#g" -e "s#@USER@#$OWNER#g" "$DIR/$u.service.in" > "/etc/systemd/system/$u.service"
done
systemctl daemon-reload
systemctl enable --now tracking-api.service tracking-poller.service
sleep 2
systemctl is-active tracking-api.service tracking-poller.service
curl -sf http://127.0.0.1:8765/health && echo " -> API OK"
curl -sf http://127.0.0.1:8765/sessions | python3 -c "import sys,json; print(len(json.load(sys.stdin)), 'session(s) chargee(s)')"
