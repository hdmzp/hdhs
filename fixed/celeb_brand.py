# -*- coding: utf-8 -*-
"""
셀럽PGM 공통: GS 상품의 브랜드 추론 (regs.py 수집 + tools/backfill_celeb_gs_brand.py 소급용)

GS 상품 상세의 og:title은 "[GS SHOP] 상품명" 꼴이라 대괄호 안이 브랜드가 아니라
쇼핑몰명이다. 그래서 GS 셀럽PGM(지금 백지연/소유진쇼)은 전 상품 브랜드가
'GS SHOP' 자리표시자로 저장됐고, 화면(index.html CEL_BRAND_PLACEHOLDER)과
알림(notify/send_celeb_pgm.py)은 그 값을 '브랜드 없음'으로 취급해 상품명만
보여줬다. 셀럽PGM 간 중복 브랜드를 비교하려면 GS도 브랜드가 있어야 해서,
자리표시자일 때만 상품명에서 브랜드를 추론한다 - 홈쇼핑 탭 GS 편성이 쓰는
것과 같은 학습데이터 브랜드 사전 매칭(infer_brand).

추론에 실패하면 원래 값을 그대로 둔다. 추정치를 지어내느니 지금처럼 상품명만
보여주는 쪽을 택한다 (실측: 2026-07~10 GS 셀럽PGM 상품명 255종 중 200종 매칭,
나머지는 키직/샤크/폴리보이처럼 사전에 없는 브랜드거나 "국산 서리태 콩물두유"
처럼 브랜드 자체가 없는 상품).

적용 범위는 셀럽PGM의 GS뿐이다. infer_brand 자체는 건드리지 않는다
(그쪽을 고치면 홈쇼핑 탭 등 다른 결과까지 같이 바뀐다).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from infer_brand import infer_brand, extract_core_brand

# regs.py가 브랜드를 못 뽑았을 때 넣는 자리표시자
GS_PLACEHOLDER = "GS SHOP"


def is_placeholder(brand) -> bool:
    """'GS SHOP'/'gs shop'/'GS샵' 처럼 쇼핑몰명이 브랜드 자리에 들어온 값인지
    (notify/send_celeb_pgm.py, index.html과 같은 판정)."""
    return str(brand or "").strip().upper().replace(" ", "") in ("GSSHOP", "GS샵")


def resolve_gs_brand(brand, name) -> str:
    """브랜드가 비었거나 자리표시자면 상품명에서 추론한다.
    못 찾으면 원래 값을 그대로 돌려준다 ('GS SHOP'은 'GS SHOP'으로, ''은 ''으로).
    사전 표기의 괄호 부기("LG(엘지)")는 떼고 화면 표시용 핵심 브랜드명만 쓴다
    (홈쇼핑 탭 GS 편성, 롯데 접두어 정제와 같은 형태)."""
    brand = str(brand or "").strip()
    if brand and not is_placeholder(brand):
        return brand
    inferred = infer_brand(name)
    return extract_core_brand(inferred) if inferred else brand
