#!/usr/bin/env bash
# One command to create (or re-create) the trading-brain stack on Google Cloud.
# Run from Git Bash on your PC, after `gcloud auth login` and `gcloud init`.
#
#   ENV_FILE=../../.env ./deploy/gcp/up.sh
#
# Idempotent: existing firewall rules, IP, bucket and secret are reused. The VM is
# only created when it does not exist; pass RECREATE_VM=1 to replace it.
# Secrets go to Secret Manager, not to the repo or the image.
set -euo pipefail

PROJECT="${PROJECT:-trading-brain-514921}"
ZONE="${ZONE:-us-east1-c}"
REGION="${ZONE%-*}"
VM="${VM:-trading-brain}"
MACHINE="${MACHINE:-e2-micro}"
IP_NAME="${IP_NAME:-trading-brain-ip}"
BUCKET="gs://${PROJECT}-dashboard"
SECRET="${SECRET:-trading-brain-env}"
SA="trading-brain-vm@${PROJECT}.iam.gserviceaccount.com"
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${ENV_FILE:-$HERE/../../.env}"
DIST="${DIST:-$HERE/../../../frontend/dist}"

G() { gcloud --project "$PROJECT" "$@"; }

echo "==> APIs"
G services enable compute.googleapis.com secretmanager.googleapis.com storage.googleapis.com iam.googleapis.com

echo "==> Service account for the VM"
G iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  G iam service-accounts create trading-brain-vm --display-name="trading-brain VM"
G projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" \
  --role="roles/secretmanager.secretAccessor" --condition=None >/dev/null
G projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" \
  --role="roles/storage.objectViewer" --condition=None >/dev/null

echo "==> Static IP"
G compute addresses describe "$IP_NAME" --region "$REGION" >/dev/null 2>&1 || \
  G compute addresses create "$IP_NAME" --region "$REGION"

echo "==> Firewall (SSH, HTTP, HTTPS to the trading-brain tag only)"
G compute firewall-rules describe trading-brain-web >/dev/null 2>&1 || \
  G compute firewall-rules create trading-brain-web --allow=tcp:22,tcp:80,tcp:443 \
    --target-tags=trading-brain --source-ranges=0.0.0.0/0

echo "==> Dashboard and backup buckets"
G storage buckets describe "$BUCKET" >/dev/null 2>&1 || \
  G storage buckets create "$BUCKET" --location="$REGION" --uniform-bucket-level-access
G storage buckets describe "$BUCKET-backups" >/dev/null 2>&1 || \
  G storage buckets create "$BUCKET-backups" --location="$REGION" --uniform-bucket-level-access
G storage buckets add-iam-policy-binding "$BUCKET-backups" --member="serviceAccount:$SA" \
  --role="roles/storage.objectCreator" >/dev/null
if [ -d "$DIST" ]; then
  G storage rsync --delete-unmatched-destination-objects -r "$DIST" "$BUCKET"
else
  echo "    (no dashboard build at $DIST; run deploy-dashboard.ps1 first)"
fi

echo "==> Secrets"
if [ ! -f "$ENV_FILE" ]; then echo "Missing $ENV_FILE"; exit 1; fi
if G secrets describe "$SECRET" >/dev/null 2>&1; then
  G secrets versions add "$SECRET" --data-file="$ENV_FILE"
else
  G secrets create "$SECRET" --data-file="$ENV_FILE"
fi

echo "==> VM"
if G compute instances describe "$VM" --zone "$ZONE" >/dev/null 2>&1; then
  if [ "${RECREATE_VM:-0}" = "1" ]; then
    G compute instances delete "$VM" --zone "$ZONE" --quiet
  else
    echo "    $VM exists; leaving it alone (RECREATE_VM=1 to replace)."
    echo "    To apply new code on it, SSH in and run /opt/trading_brain/deploy/gcp/startup.sh"
    exit 0
  fi
fi
G compute instances create "$VM" --zone "$ZONE" --machine-type "$MACHINE" \
  --address "$IP_NAME" --tags trading-brain \
  --service-account "$SA" --scopes cloud-platform \
  --boot-disk-size 30GB --boot-disk-type pd-standard \
  --image-family debian-13 --image-project debian-cloud \
  --metadata-from-file startup-script="$HERE/startup.sh" \
  --maintenance-policy MIGRATE --restart-on-failure

echo "==> Done. First boot takes several minutes. Check progress with:"
echo "    gcloud compute ssh $VM --zone $ZONE -- sudo journalctl -u google-startup-scripts -f"
