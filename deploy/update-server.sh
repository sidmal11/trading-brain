#!/usr/bin/env bash
# Pull the latest code and restart the containers. Run on the server after each push.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/trading_brain}"
cd "$APP_DIR"

test -f .env || { echo "Missing .env; create it first."; exit 1; }
test "$(stat -c %a .env)" = "600" || { echo "Fix permissions: chmod 600 .env"; exit 1; }

git pull --ff-only
docker compose up -d --build
docker image prune -f >/dev/null
docker compose ps
