#!/usr/bin/env bash
# Containers usage this month vs. the Workers Paid included allowance.
# Uses the wrangler OAuth token. Usage: ACCOUNT_ID=<id> ./usage.sh
set -euo pipefail
ACC="${ACCOUNT_ID:?set ACCOUNT_ID to your Cloudflare account id (npx wrangler whoami)}"
SINCE="${SINCE:-$(date -u +%Y-%m-01)}"
TOKEN=$(grep -E '^oauth_token' ~/Library/Preferences/.wrangler/config/default.toml | sed -E 's/.*= *"(.*)"/\1/')
curl -s https://api.cloudflare.com/client/v4/graphql -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"query\":\"{ viewer { accounts(filter:{accountTag:\\\"$ACC\\\"}) { containersUsageAdaptiveGroups(limit: 1, filter:{date_geq:\\\"$SINCE\\\"}) { sum { allocatedMemory allocatedDisk cpuTimeSec txBytes } } } } }\"}" \
| python3 -c '
import json, sys
d = json.load(sys.stdin)
if d.get("errors"): sys.exit(d["errors"])
g = d["data"]["viewer"]["accounts"][0]["containersUsageAdaptiveGroups"]
s = g[0]["sum"] if g else {"allocatedMemory": 0, "allocatedDisk": 0, "cpuTimeSec": 0, "txBytes": 0}
mem_h = s["allocatedMemory"] / 2**30 / 3600   # byte-seconds -> GiB-hours
disk_h = s["allocatedDisk"] / 1e9 / 3600      # byte-seconds -> GB-hours
cpu_m = s["cpuTimeSec"] / 60
print(f"memory {mem_h:8.3f} GiB-h / 25   ({mem_h/25:6.1%})")
print(f"disk   {disk_h:8.3f} GB-h  / 200  ({disk_h/200:6.1%})")
print(f"cpu    {cpu_m:8.3f} vCPU-min / 375 ({cpu_m/375:6.1%})")
tx = s["txBytes"] / 1e6
print(f"egress {tx:8.3f} MB")
print(f"basic instance hours left in allowance ≈ {max(0, 25 - mem_h):.1f} h (memory-bound)")
'
