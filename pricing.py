"""API 환산 비용용 모델 가격표 (USD / 100만 토큰).

출처: Anthropic 공식 가격(claude-api 참고 문서, 2026-10-06 기준 캐시). 구독 사용량을 'API로 썼다면 얼마'로 보여 주는 추정용이며
가격이 바뀌면 여기만 고치면 된다. 캐시 쓰기 = 입력 × 1.25(5분 캐시), 캐시 읽기는 모델별 표 값(없으면 입력 × 0.1).
"""
PRICES = {   # 모델 id 접두어: (입력, 출력, 캐시 읽기)
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-mythos-5-1": (10.0, 50.0, 0.25),
    "claude-fable-5": (10.0, 50.0, None),
    "claude-mythos-5": (10.0, 50.0, None),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, None),
    "claude-opus-4-8": (5.0, 25.0, None),
    "claude-opus-4-7": (5.0, 25.0, None),
    "claude-opus-4-6": (5.0, 25.0, None),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, None),
    "claude-sonnet-4-6": (3.0, 15.0, None),
    "claude-haiku-5-5": (0.10, 0.50, None),
    "claude-haiku-4-5": (1.0, 5.0, None),
}
PRICES_AS_OF = "2026-10-06"


def price_of(model):
    """모델 id(날짜·[1m] 같은 꼬리 포함 가능)에 맞는 가격. 가장 긴 접두어 우선. 모르면 None."""
    m = (model or "").lower()
    best = None
    for k in PRICES:
        if m.startswith(k) and (best is None or len(k) > len(best)):
            best = k
    if not best:
        return None
    i, o, cr = PRICES[best]
    return {"input": i, "output": o, "cache_write": i * 1.25, "cache_read": cr if cr is not None else i * 0.1}


def cost_usd(model, inp, out, cw, cr):
    p = price_of(model)
    if not p:
        return None
    return (inp * p["input"] + out * p["output"] + cw * p["cache_write"] + cr * p["cache_read"]) / 1e6
