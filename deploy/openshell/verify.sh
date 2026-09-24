#!/usr/bin/env bash
# Run the sandbox security probes. Usage: deploy/openshell/verify.sh [sandbox-name]
set -euo pipefail
SB="${1:-er-agent}"
cd "$(dirname "$0")/../.."
echo "== probes from the agent's Python interpreter"
openshell sandbox exec -n "$SB" --no-tty -- /app/.venv/bin/python - < deploy/openshell/probe.py
echo
echo "== curl started from a shell (not the agent) to an allowed host"
if openshell sandbox exec -n "$SB" --no-tty -- curl -sf -m 10 https://integrate.api.nvidia.com/v1/models </dev/null >/dev/null 2>&1; then
  echo "❌ ALLOWED  | curl is not in the policy binaries but reached NIM"; exit 1
else
  echo "✅ BLOCKED  | curl is not in the policy binaries"
fi
