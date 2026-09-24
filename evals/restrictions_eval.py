"""Restriction-message judging: tricky real NEMC messages against specific patients (expected 10/10).

    uv run --no-sync python evals/restrictions_eval.py
"""
import datetime as dt
import time

from er_agent import restrictions

NOW = dt.datetime(2026, 9, 24, 21, 10)  # Thursday 21:10
CASES = [  # (patient, message, expected impact)
    ("7세 남아 가와사키 의심 고열 5일", "소아심장분과 의료진 부재로 인해 소아심장환자의 진료 및 입원 일시적 불가(가와사키환자 포함)", "block"),
    ("7세 남아 가와사키 의심 고열 5일", "입원치료가 필요한 중증 소아환자 진료 제한", "caution"),
    ("7세 남아 가와사키 의심 고열 5일", "인력 부족으로 월~금 9A ~ 5P30'  진료가능", "block"),
    ("7세 남아 가와사키 의심 고열 5일", "[구강악안면외과] 단순(치통, 치관파절, 보철물 탈락 등 비응급 질환) 치아 질환 응급실 진료 불가", "none"),
    ("45세 남성 좌측 손가락 절단", "성형외과 :(월-목)오후 9시까지 가능 / (금-토)24시간 가능 / (일)오후 4시 ~ 익일 오전 7시 가능", "block"),
    ("45세 남성 좌측 손가락 절단", "(수족지접합/정형외과) 의료진 부족", "block"),
    ("30세 여성 임신 38주 진통", "NICU 부재, 고위험산모실 부재 등으로 모든 산모 수용 불가", "block"),
    ("68세 남성 흉통 30분, 식은땀", "NICU 부재, 고위험산모실 부재 등으로 모든 산모 수용 불가", "none"),
    ("55세 여성 혈액암 항암치료 중 발열 (본원 추적관찰 환자)", "[혈액종양내과] 의료진 인력 부족으로 신환 수용 불가, FU 환자 가능", "none"),
    ("40세 남성 급성 백혈병 첫 진단 의심, 타원 환자", "[혈액종양내과] 의료진 인력 부족으로 신환 수용 불가, FU 환자 가능", "block"),
]

if __name__ == "__main__":
    ok, t0 = 0, time.time()
    for i, (patient, msg, expected) in enumerate(CASES):
        hpid = f"H{i}"
        got = restrictions.judge(patient, NOW, [(hpid, "병원", [{"symblkmsg": msg}])]).get(hpid, {})
        ok += got.get("impact") == expected
        mark = "✓" if got.get("impact") == expected else "✗"
        print(f"{mark} {expected:7s} got={got.get('impact')!s:7s} | {patient[:18]} | {msg[:40]}")
    print(f"\n{ok}/{len(CASES)} correct · {time.time() - t0:.0f}s")
