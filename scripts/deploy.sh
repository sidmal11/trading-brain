#!/usr/bin/env bash
# scripts/deploy.sh
#
# End-to-end deployment: package the Lambda then apply the Terraform module.
#
# Usage:
#   ./scripts/deploy.sh [environment] [extra terraform flags...]
#
# Examples:
#   ./scripts/deploy.sh                         # deploy to dev
#   ./scripts/deploy.sh prod                    # deploy to prod using prod.tfvars
#   ./scripts/deploy.sh dev -target=aws_lambda_function.poll_handler
#
# The script expects:
#   - AWS credentials available (env vars, ~/.aws/credentials, or an instance profile)
#   - Terraform >= 1.0 on PATH
#   - Python 3 + pip on PATH (used by package_lambda.sh)
#   - A terraform.tfvars or <env>.tfvars file if you need to override variables

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INFRA_DIR="${REPO_ROOT}/infrastructure"

ENVIRONMENT="${1:-dev}"
shift || true   # remaining args passed through to terraform apply

TFVARS_FILE="${INFRA_DIR}/${ENVIRONMENT}.tfvars"
PACKAGE_PATH="${INFRA_DIR}/lambda/poll_handler.zip"

echo "================================================"
echo " Trading Brain — Deploy"
echo " Environment : ${ENVIRONMENT}"
echo " Infra dir   : ${INFRA_DIR}"
echo "================================================"

# ── Step 1: Package Lambda ────────────────────────────────────────────────────
echo ""
echo "[1/3] Packaging Lambda..."
bash "${SCRIPT_DIR}/package_lambda.sh" "${PACKAGE_PATH}"

# ── Step 2: Terraform init (if .terraform is stale or missing) ────────────────
echo ""
echo "[2/3] Initialising Terraform..."
cd "${INFRA_DIR}"

# Only re-init if the lock file or provider cache is absent; -upgrade is
# intentionally not set here so pinned provider versions are respected.
if [ ! -f ".terraform.lock.hcl" ] || [ ! -d ".terraform/providers" ]; then
  terraform init
else
  terraform init -reconfigure 2>/dev/null || terraform init
fi

# ── Step 3: Plan + Apply ──────────────────────────────────────────────────────
echo ""
echo "[3/3] Applying Terraform..."

TFVARS_ARGS=""
if [ -f "${TFVARS_FILE}" ]; then
  echo "      Using var file: ${TFVARS_FILE}"
  TFVARS_ARGS="-var-file=${TFVARS_FILE}"
else
  echo "      No ${ENVIRONMENT}.tfvars found — using variable defaults."
fi

terraform apply \
  ${TFVARS_ARGS} \
  -var="environment=${ENVIRONMENT}" \
  -var="lambda_package_path=${PACKAGE_PATH}" \
  "$@"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "================================================"
echo " Deployment complete. Key outputs:"
echo "================================================"
terraform output -json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for k, v in sorted(data.items()):
    print(f'  {k} = {v[\"value\"]}')
"
