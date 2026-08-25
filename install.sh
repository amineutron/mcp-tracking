#!/bin/bash
# Installation initiale : venv + services systemd. Idempotent.
# Pour redeployer apres une mise a jour du code : sudo ./deploy.sh
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "Creation du venv..."
    uv venv .venv
    uv pip install "mcp[cli]>=1.0.0" "pydantic>=2.0" "textual>=0.80.0" requests pytest
fi

echo "Installation des services systemd (sudo)..."
sudo ./deploy.sh
