#!/bin/bash
# Installation des services systemd pour le tracker media
set -e

echo "Installation des services systemd..."

sudo cp tracking-api.service    /etc/systemd/system/
sudo cp tracking-poller.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tracking-api.service
sudo systemctl enable --now tracking-poller.service

echo "Redemarrage n8n avec port restreint a localhost..."
cd /home/amineutron/n8n
docker compose down
docker compose up -d

echo ""
echo "Verification des services :"
systemctl is-active tracking-api.service
systemctl is-active tracking-poller.service
docker ps --filter name=n8n --format "{{.Names}} {{.Ports}}"

echo ""
echo "Test API tracking :"
sleep 2
curl -s http://127.0.0.1:8765/health && echo " -> OK"
