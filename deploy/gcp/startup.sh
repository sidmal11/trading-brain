#!/usr/bin/env bash
# Runs as root on every VM boot (GCE startup script). Idempotent.
# Sets up Docker and the firewall, pulls the code, loads secrets from Secret Manager,
# syncs the dashboard from Cloud Storage, and starts the containers.
set -euo pipefail
exec > >(tee -a /var/log/trading-brain-startup.log) 2>&1

PROJECT="$(curl -fs -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/project/project-id)"
APP_DIR=/opt/trading_brain
REPO_URL="${REPO_URL:-https://github.com/sidmal11/trading-brain.git}"
BUCKET="gs://${PROJECT}-dashboard"
SECRET="${SECRET:-trading-brain-env}"

echo "==> Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl git ufw sqlite3 unattended-upgrades
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

echo "==> Firewall (the GCP rule already limits ports; ufw is a second layer)"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> Automatic security updates"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> Code"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only
fi
mkdir -p "$APP_DIR/data" "$APP_DIR/frontend-dist"

echo "==> Secrets (written to .env, readable by root only)"
umask 077
gcloud secrets versions access latest --secret="$SECRET" --project="$PROJECT" > "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

echo "==> Dashboard"
gsutil -m rsync -r -d "$BUCKET" "$APP_DIR/frontend-dist"
chmod -R a+rX "$APP_DIR/frontend-dist"

echo "==> Restore the database from the newest backup if this is a fresh disk"
DB="$APP_DIR/data/trading_brain.db"
if [ ! -f "$DB" ]; then
  latest="$(gsutil ls "$BUCKET-backups/" 2>/dev/null | sort | tail -1 || true)"
  if [ -n "$latest" ]; then
    gsutil cp "$latest" "$DB"
    echo "Restored $latest"
  else
    echo "No backup found; starting with an empty database."
  fi
fi

echo "==> Containers"
cd "$APP_DIR"
docker compose up -d --build
docker image prune -f >/dev/null

echo "==> Daily SQLite backup to Cloud Storage (14 days kept)"
cat > /etc/cron.d/trading-brain-backup <<EOF
15 3 * * * root sqlite3 $APP_DIR/data/trading_brain.db ".backup '/tmp/tb.db'" && gsutil -q cp /tmp/tb.db $BUCKET-backups/tb-\$(date +\%F).db && rm -f /tmp/tb.db
EOF

echo "==> Telegram webhook"
set -a; . "$APP_DIR/.env"; set +a
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=https://${DOMAIN}/api/telegram/webhook" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}" \
  -d 'allowed_updates=["message","callback_query"]' || echo "webhook registration failed; see log"
echo
echo "==> Startup finished"
