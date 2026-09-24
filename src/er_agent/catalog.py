"""Field dictionaries from the NEMC OpenAPI guide (응급의료정보조회서비스 V13).

Keys are lower-cased because the client normalises every XML tag to lower case.
Note: getSrsillDissAceptncPosblInfoInqire (27 severe-disease groups) and
getEgytBassInfoInqire (legacy 11 groups) reuse the same MKioskTy numbers for
different diseases. Only the 27-group table below is used.
"""

# 중증질환자 수용가능정보 (getSrsillDissAceptncPosblInfoInqire)
SEVERE = {
    "mkioskty28": "응급실(Emergency gate keeper)",
    "mkioskty1": "[재관류중재술] 심근경색",
    "mkioskty2": "[재관류중재술] 뇌경색",
    "mkioskty3": "[뇌출혈수술] 거미막하출혈",
    "mkioskty4": "[뇌출혈수술] 거미막하출혈 외",
    "mkioskty5": "[대동맥응급] 흉부",
    "mkioskty6": "[대동맥응급] 복부",
    "mkioskty7": "[담낭담관질환] 담낭질환",
    "mkioskty8": "[담낭담관질환] 담도포함질환",
    "mkioskty9": "[복부응급수술] 비외상",
    "mkioskty10": "[장중첩/폐색] 영유아",
    "mkioskty11": "[응급내시경] 성인 위장관",
    "mkioskty12": "[응급내시경] 영유아 위장관",
    "mkioskty13": "[응급내시경] 성인 기관지",
    "mkioskty14": "[응급내시경] 영유아 기관지",
    "mkioskty15": "[저체중출생아] 집중치료",
    "mkioskty16": "[산부인과응급] 분만",
    "mkioskty17": "[산부인과응급] 산과수술",
    "mkioskty18": "[산부인과응급] 부인과수술",
    "mkioskty19": "[중증화상] 전문치료",
    "mkioskty20": "[사지접합] 수족지접합",
    "mkioskty21": "[사지접합] 수족지접합 외",
    "mkioskty22": "[응급투석] HD",
    "mkioskty23": "[응급투석] CRRT",
    "mkioskty24": "[정신과적응급] 폐쇄병동입원",
    "mkioskty25": "[안과적수술] 응급",
    "mkioskty26": "[영상의학혈관중재] 성인",
    "mkioskty27": "[영상의학혈관중재] 영유아",
}

# Age-limit notes that accompany some pediatric groups (e.g. "9개월부터 가능")
SEVERE_AGE_MSG = {
    "mkioskty10": "mkioskty10msg",
    "mkioskty12": "mkioskty12msg",
    "mkioskty14": "mkioskty14msg",
    "mkioskty15": "mkioskty15msg",
    "mkioskty27": "mkioskty27msg",
}

# 실시간 가용병상 (getEmrrmRltmUsefulSckbdInfoInqire): available / baseline pairs used by the planner
BEDS = {
    "er": ("hvec", "hvs01", "응급실 일반"),
    "peds": ("hv28", "hvs02", "응급실 소아"),
    "er_neg_iso": ("hv29", "hvs03", "응급실 음압격리"),
    "er_iso": ("hv30", "hvs04", "응급실 일반격리"),
    "or": ("hvoc", "hvs22", "수술실"),
    "icu": ("hvicc", "hvs17", "일반 중환자실"),
    "nicu": ("hvncc", "hvs08", "신생아 중환자실"),
    "ward": ("hvgc", "hvs38", "일반 입원실"),
}

EQUIPMENT = {
    "hvctayn": "CT",
    "hvmriayn": "MRI",
    "hvangioayn": "혈관촬영기",
    "hvventiayn": "인공호흡기",
    "hvincuayn": "인큐베이터",
    "hvcrrtayn": "CRRT",
    "hvecmoayn": "ECMO",
}

# dutyEmclsName → rank (higher = more capable centre)
LEVELS = [
    ("권역응급의료센터", 3),
    ("전문응급의료센터", 3),
    ("지역응급의료센터", 2),
    ("지역응급의료기관", 1),
]


def level_rank(name: str) -> int:
    for key, rank in LEVELS:
        if key in (name or ""):
            return rank
    return 0
