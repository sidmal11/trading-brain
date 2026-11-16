#!/usr/bin/env bash
# scripts/package_lambda.sh
#
# Builds the Lambda deployment package for the Trading Brain poll handler.
#
# Usage:
#   ./scripts/package_lambda.sh [output_path]
#
# output_path defaults to infrastructure/lambda/poll_handler.zip, which is
# the same default Terraform reads from var.lambda_package_path.
#
# Prerequisites: Python 3, pip, zip (all available in the standard Lambda
# build environment and on any developer machine with the AWS CLI installed).
#
# The script is idempotent: re-running it overwrites the previous zip.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_PATH="${1:-${REPO_ROOT}/infrastructure/lambda/poll_handler.zip}"
BUILD_DIR="${REPO_ROOT}/.lambda_build"

echo "==> Cleaning build directory: ${BUILD_DIR}"
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

echo "==> Installing dependencies into build directory"
pip install \
  --quiet \
  --target "${BUILD_DIR}" \
  --requirement "${REPO_ROOT}/requirements.txt"

echo "==> Copying application source"
# Copy every Python file and package at the top level of trading_brain/.
# Exclude tests, infrastructure, scripts, and __pycache__ so the zip stays small.
rsync -a \
  --include="*.py" \
  --include="utils/" \
  --include="utils/*.py" \
  --exclude="tests/" \
  --exclude="infrastructure/" \
  --exclude="scripts/" \
  --exclude="__pycache__/" \
  --exclude=".git/" \
  --exclude=".pytest_cache/" \
  --exclude="*.pyc" \
  "${REPO_ROOT}/" "${BUILD_DIR}/"

echo "==> Creating zip archive: ${OUTPUT_PATH}"
mkdir -p "$(dirname "${OUTPUT_PATH}")"
# Use -j to strip directory paths only for the top-level (we cd into BUILD_DIR
# so all paths in the zip are relative, matching Lambda's expected layout).
cd "${BUILD_DIR}"
zip -q -r "${OUTPUT_PATH}" .
cd - > /dev/null

ZIP_SIZE_KB=$(du -k "${OUTPUT_PATH}" | cut -f1)
echo "==> Done. Package size: ${ZIP_SIZE_KB} KB"
echo "    Output: ${OUTPUT_PATH}"

# Lambda's unzipped limit is 250 MB; the compressed limit is 50 MB (direct
# upload) or 250 MB (via S3). Warn but don't fail — S3 upload handles large
# packages fine and Terraform can be configured to use it.
if [ "${ZIP_SIZE_KB}" -gt 51200 ]; then
  echo "WARNING: zip exceeds 50 MB (${ZIP_SIZE_KB} KB). Upload via S3 or trim dependencies."
fi
