#!/usr/bin/env bash
# Validate, test and deploy the bundle to a target.
# Usage: ./scripts/deploy.sh [dev|staging|prod]
set -euo pipefail

TARGET="${1:-dev}"

echo "==> Linting"
ruff check .

echo "==> Unit tests"
pytest tests/unit -m "not integration"

echo "==> Validating bundle (${TARGET})"
databricks bundle validate -t "${TARGET}"

if [[ "${TARGET}" == "prod" ]]; then
  read -r -p "Deploy to PROD? [y/N] " reply
  [[ "${reply}" == "y" ]] || { echo "Aborted."; exit 1; }
fi

echo "==> Deploying (${TARGET})"
databricks bundle deploy -t "${TARGET}"
