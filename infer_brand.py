# -*- coding: utf-8 -*-
"""
브랜드 미보유 상품명에서 브랜드를 추론하는 모듈 (GS 전용 보조)

GS는 라방바 API 특성상 브랜드 필드가 항상 비어있다. 분류 모델은
"브랜드명 + 상품명"으로 학습되어 브랜드가 핵심 신호인데, GS는 그 신호가
없어서 분류 확신도가 크게 떨어진다(예: "LG 통돌이 세탁기"가 생활용품/일반식품
등으로 헷갈림 - 확신도 20% 이하).

이 모듈은 학습 데이터(상품분류학습데이터_정제.xlsx)의 브랜드 목록에서
핵심 토큰("LG(엘지)" -> "LG", "삼성(SAMSUNG)" -> "삼성")을 추출해 사전을 만들고,
GS 상품명 안에 그 토큰이 포함돼 있으면 찾아내 분류 모델 입력에 보강해준다.

== 매칭 안전장치 ==
- 긴 토큰을 먼저 매칭한다 (longest-match-first) - "LG전자"가 "LG"보다 먼저 매칭되도록
- 2글자 이하 토큰은 상품명 맨 앞 단어와 "정확히 일치"할 때만 인정한다
  (예: "로던"이 상품명 중간 어딘가에 우연히 끼어 있는 경우는 무시,
   상품명이 "로던 ..."으로 시작할 때만 인정)

== 사용법 ==
  from infer_brand import infer_brand
  infer_brand("LG 통돌이 세탁기 T19MX7A 미드 블랙")
  # -> "LG"  (매칭 안 되면 "")
"""

import os
import re
import pandas as pd

_TRAINING_XLSX_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "training_data.xlsx"),
    "training_data.xlsx",
]

_brand_tokens = None  # 길이 내림차순 정렬된 (토큰, 원본브랜드) 리스트


def _extract_core(brand: str) -> str:
    """브랜드명에서 괄호 안내문 제거: 'LG(엘지)' -> 'LG'"""
    return re.sub(r"\([^)]*\)", "", str(brand)).strip()


def _load_brand_tokens():
    global _brand_tokens
    if _brand_tokens is not None:
        return _brand_tokens

    xlsx_path = None
    for cand in _TRAINING_XLSX_CANDIDATES:
        if os.path.exists(cand):
            xlsx_path = cand
            break

    if xlsx_path is None:
        _brand_tokens = []
        return _brand_tokens

    df = pd.read_excel(xlsx_path, sheet_name=0)
    raw_brands = df["브랜드명"].dropna().unique()

    seen = set()
    tokens = []
    for b in raw_brands:
        core = _extract_core(b)
        if core and core not in seen:
            seen.add(core)
            tokens.append((core, str(b)))

    # 긴 토큰을 먼저 매칭하도록 길이 내림차순 정렬
    tokens.sort(key=lambda t: len(t[0]), reverse=True)
    _brand_tokens = tokens
    return _brand_tokens


def infer_brand(product_name: str) -> str:
    """
    상품명 안에서 학습 데이터 브랜드 사전과 매칭되는 브랜드를 찾아 반환.
    모델은 학습 데이터의 정확한 표기(예: "LG(엘지)")에 민감하므로,
    매칭에 쓴 핵심 토큰이 아니라 원본 표기를 그대로 반환한다.
    매칭 안 되면 빈 문자열.
    """
    if not product_name:
        return ""

    tokens = _load_brand_tokens()
    if not tokens:
        return ""

    text = product_name.strip()
    first_word = text.split()[0] if text.split() else ""

    for token, original in tokens:
        if len(token) <= 2:
            # 짧은 토큰은 오매칭 위험이 커서 맨 앞 단어와 완전히 같을 때만 인정
            if token == first_word:
                return original
        else:
            if token in text:
                return original

    return ""


if __name__ == "__main__":
    samples = [
        "LG 통돌이 세탁기 T19MX7A 미드 블랙",
        "원스톱프리미엄암보험_치료비플랜",
        "삼성 비스포크 김치냉장고 4도어",
        "스테파넬 26SS 썸머 쿨드레이프 팬츠",
    ]
    for s in samples:
        print(f"{s[:35]:35s} -> 추론 브랜드: {infer_brand(s) or '(없음)'}")
