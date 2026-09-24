#!/usr/bin/env bash
# Build the image, register provider profiles/credentials, and start the ER agent in an
# OpenShell sandbox with the UI forwarded to http://127.0.0.1:8000.
#
# Needs: a running OpenShell gateway with a Docker driver, and .env with
#   data_key=<공공데이터포털 key>  nvidia_api_key=<nvapi-...>
# Keys are handed to the gateway as provider credentials; the sandbox only sees placeholders.
set -euo pipefail
cd "$(dirname "$0")/../.."
SB="${SANDBOX:-er-agent}"
DOCKER="docker ${DOCKER_CONTEXT:+--context $DOCKER_CONTEXT}"

$DOCKER build -q -f deploy/openshell/Dockerfile -t er-agent-sandbox:latest .

for f in nemc-profile.yaml nim-profile.yaml; do
  openshell provider profile lint -f "deploy/openshell/$f"
  openshell provider profile import -f "deploy/openshell/$f" </dev/null 2>/dev/null || true  # already imported
done

set -a; . ./.env; set +a
DATA_KEY="$data_key" openshell provider create --name er-nemc --type nemc --credential DATA_KEY </dev/null 2>/dev/null || true
NVIDIA_API_KEY="$nvidia_api_key" openshell provider create --name er-nim --type er-nim --credential NVIDIA_API_KEY </dev/null 2>/dev/null || true

openshell sandbox delete "$SB" </dev/null >/dev/null 2>&1 || true
openshell sandbox create --name "$SB" --from er-agent-sandbox:latest \
  --provider er-nemc --provider er-nim \
  --policy deploy/openshell/policy.yaml \
  --forward 8000 --no-tty \
  -- /app/.venv/bin/er-agent serve --host 127.0.0.1 --port 8000
