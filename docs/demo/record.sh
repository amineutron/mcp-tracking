#!/usr/bin/env bash
# Regenere docs/assets/demo.gif : les simulations du mode test alimentent un repertoire d'etat
# temporaire, puis le dashboard s'ouvre dessus. Les sessions reelles ne sont pas touchees.
# Prerequis : asciinema (pip/uv tool), agg (https://github.com/asciinema/agg), uv.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"
export TRACKING_STATE_DIR="$(mktemp -d)"
uv run --frozen python server.py --test >/dev/null 2>&1 &
SIM=$!
sleep 4
asciinema rec --overwrite --cols 110 --rows 34 --idle-time-limit 1 \
  --command "timeout 22 uv run --frozen python server.py --ui" /tmp/tracking-demo.cast || true
kill "$SIM" 2>/dev/null || true
agg --font-size 13 --cols 110 --rows 34 --theme monokai /tmp/tracking-demo.cast "$ROOT/docs/assets/demo.gif"
ls -la "$ROOT/docs/assets/demo.gif"
