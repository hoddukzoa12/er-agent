"""ER dispatch agent CLI.

  er-agent serve [--port 8000]                 web UI
  er-agent plan "환자 설명" --at 37.57,126.98   one-shot plan in the terminal
  er-agent collect [--every 300] [--count N]   record live snapshots for replay
"""
import argparse
import json
import time


def _print_event(ev: dict) -> None:
    t = ev.get("type")
    if t == "step":
        print(f"  · {ev['msg']}")
    elif t == "assessment":
        a = ev["assessment"]
        print(f"\n[환자] {a['summary']}  ({a['age_group']}, {a['acuity']})")
        print(f"  필요 역량: {', '.join(a['required_labels']) or '-'}  소아={a['needs_pediatric']}  격리={a['needs_isolation']}")
        if a.get("advise_119"):
            print("  ⚠️  119 신고 권고")
    elif t == "plan":
        p = ev["plan"]
        print(f"\n[추천] 데이터 {p['data_time']} ({p['source']})  A={p['tiers']['A']} B={p['tiers']['B']} X={p['tiers']['X']}")
        for i, c in enumerate(p["candidates"][:8], 1):
            er = c["beds"]["er"]
            print(f"  {i}. [{c['tier']}] {c['name']} · {c['eta_min']}분 · 응급실 {er[0]}/{er[1]} · {c['tel']}")
            for r in c["reasons"][:2]:
                print(f"       - {r}")
    elif t in ("message", "error"):
        print(f"\n[{t}] {ev.get('text') or ev.get('msg')}")
    elif t == "monitor":
        print(f"[monitor {ev['time']}] " + ("; ".join(ev["alerts"]) or "변화 없음"))


def main() -> None:
    ap = argparse.ArgumentParser(prog="er-agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--host", default="127.0.0.1")
    pl = sub.add_parser("plan")
    pl.add_argument("text")
    pl.add_argument("--at", default="37.5714,126.9768", help="lat,lon")
    pl.add_argument("--mode", default="ems", choices=["ems", "guardian"])
    pl.add_argument("--replay", default=None, help="snapshot name (default: live)")
    pl.add_argument("--json", action="store_true")
    co = sub.add_parser("collect")
    co.add_argument("--every", type=int, default=300)
    co.add_argument("--count", type=int, default=1)
    args = ap.parse_args()

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("er_agent.server:app", host=args.host, port=args.port)
    elif args.cmd == "plan":
        from . import agent
        lat, lon = map(float, args.at.split(","))
        s = agent.Session(mode=args.mode, text=args.text, lat=lat, lon=lon, replay=args.replay)
        events: list[dict] = []
        agent.start(s, (lambda ev: events.append(ev)) if args.json else _print_event)
        if args.json:
            print(json.dumps(events[-1], ensure_ascii=False, default=str, indent=1))
    elif args.cmd == "collect":
        from . import nemc
        for i in range(args.count):
            snap = nemc.load_live()
            print(f"[{snap.fetched_at:%H:%M:%S}] snapshot saved {snap.raw_counts}")
            if i < args.count - 1:
                time.sleep(args.every)
