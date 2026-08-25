#!/bin/bash
# Bascule des services tracking sur le code refactore (a lancer avec sudo).
# 1. arrete api + poller, 2. resynchronise l'etat vers ~/.local/state/tracking,
# 3. installe les unites a jour, 4. redemarre et verifie.
set -e
DIR=/home/amineutron/dev/MCP/tracking
STATE=/home/amineutron/.local/state/tracking

systemctl stop tracking-poller.service tracking-api.service

sudo -u amineutron mkdir -p "$STATE"
for f in tracking_state.json poller_state.json; do
    [ -f "$DIR/$f" ] && sudo -u amineutron cp -p "$DIR/$f" "$STATE/$f"
done

cp "$DIR/tracking-api.service" "$DIR/tracking-poller.service" /etc/systemd/system/
systemctl daemon-reload
systemctl start tracking-api.service tracking-poller.service
sleep 2
systemctl is-active tracking-api.service tracking-poller.service
curl -sf http://127.0.0.1:8765/health && echo " -> API OK"
curl -sf http://127.0.0.1:8765/sessions | python3 -c "import sys,json; print(len(json.load(sys.stdin)), 'sessions rechargees')"
