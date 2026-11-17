#!/usr/bin/env bash
# One-time setup for a fresh Debian or Ubuntu VM. Safe to re-run.
# Installs Docker, opens only SSH/HTTP/HTTPS, clones the app, and turns on security updates.
# It does NOT create .env or upload the dashboard: those hold secrets and stay manual.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/sidmal11/trading-brain.git}"
APP_DIR="${APP_DIR:-/opt/trading_brain}"

echo "==> Packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  ca-certificates curl git ufw sqlite3 unattended-upgrades

echo "==> Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"

echo "==> Firewall (SSH, HTTP, HTTPS only)"
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

echo "==> Automatic security updates"
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> App code"
if [ ! -d "$APP_DIR/.git" ]; then
  sudo mkdir -p "$APP_DIR"
  sudo chown "$USER:$USER" "$APP_DIR"
  git clone "$REPO_URL" "$APP_DIR"
fi
mkdir -p "$APP_DIR/data" "$APP_DIR/frontend-dist"

echo "==> Done. Next, by hand:"
echo "  1. Create $APP_DIR/.env (chmod 600) with your secrets."
echo "  2. Upload the dashboard build to $APP_DIR/frontend-dist."
echo "  3. Run: $APP_DIR/deploy/update-server.sh"
