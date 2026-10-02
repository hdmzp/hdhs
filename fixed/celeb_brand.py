# -*- coding: utf-8 -*-
"""
셀럽PGM 공통: GS 상품의 브랜드 추론 (regs.py 수집 + tools/backfill_celeb_gs_brand.py 소급용)

GS 상품 상세의 og:title은 "[GS SHOP] 상품명" 꼴이라 대괄호 안이 브랜드가 아니라
쇼핑몰명이다. 그래서 GS 셀럽PGM(지금 백지연/소유진쇼)은 전 상품 브랜드가
'GS SHOP' 자리표시자로 저장됐고, 화면(index.html CEL_BRAND_PLACEHOLDER)과
알림(notify/send_celeb_pgm.py)은 그 값을 '브랜드 없음'으로 취급해 상품명만
보여줬다. 셀럽PGM 간 중복 브랜드를 비교하려면 GS도 브랜드가 있어야 해서,
자리표시자일 때만 상품명에서 브랜드를 추론한다.

추론에 실패하면 원래 값을 그대로 둔다. 추정치를 지어내느니 지금처럼 상품명만
보여주는 쪽을 택한다 (실측: 2026-07~10 GS 셀럽PGM 상품명 267종 중 215종 매칭,
나머지는 키직/샤크/폴리보이처럼 사전에도 대괄호에도 없는 브랜드거나
"국산 서리태 콩물두유"처럼 브랜드 자체가 없는 상품).

== 추론 규칙 (셀럽PGM GS 한정 - infer_brand 자체는 건드리지 않는다) ==
1) 상품명 맨 앞 대괄호/소괄호가 브랜드면 그걸 쓴다.
   "[파이토리진] 유기농 야생 빌베리", "[HL사이언스] 닥터슈퍼칸 레이디" -> 파이토리진, HL사이언스
   GS는 브랜드를 대괄호로 붙이는 표기가 흔한데 학습데이터 사전에 없는 브랜드가
   많아 사전 매칭만으로는 놓친다. 안내 문구([GS단독], [공식], [소유진쇼],
   [올해단한번], (국산) ...)는 건너뛴다. 대괄호 안에 사전 브랜드가 들어 있으면
   그쪽을 쓴다 ("[조선호텔김치]" -> 조선호텔, HD 쪽 표기와 맞춘다).
2) 그 다음 홈쇼핑 탭 GS 편성과 같은 사전 매칭(infer_brand)으로 본문에서 찾되,
   - 단어 시작 위치의 매칭만 인정한다 ("오즈베 14브릭스"의 '브릭스'는 거부)
   - 여러 브랜드가 있으면 맨 앞 것을 택한다. infer_brand는 가장 긴 브랜드를
     고르는데, 사은품으로 뒤에 붙은 브랜드가 더 길면 그게 잡힌다
     ("비트 캡슐세제 ... + 아이깨끗해 용기" -> 아이깨끗해가 아니라 비트).
3) 같은 브랜드의 다른 표기는 하나로 묶는다 (VASAK -> 바삭). 표기 때문에
   셀럽PGM 간 중복 비교가 갈리지 않게.
infer_brand를 고치면 홈쇼핑 탭 등 다른 결과까지 같이 바뀌므로, 위 규칙은 전부
여기서 infer_brand 결과를 받아 다듬는 식으로만 건다.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from infer_brand import infer_brand, extract_core_brand, is_marketing_copy

# regs.py가 브랜드를 못 뽑았을 때 넣는 자리표시자
GS_PLACEHOLDER = "GS SHOP"

# 상품명 맨 앞의 대괄호/소괄호 하나
_LEADING_BRACKET_RE = re.compile(r"^\s*(?:\[([^\[\]]*)\]|\(([^()]*)\))")

# 브랜드 자리(맨 앞 대괄호)에 오지만 브랜드가 아닌 GS 셀럽PGM 문구 중
# is_marketing_copy가 못 거르는 것들. 실측: [공식] [GS ONLY] [GS단독] [소유진쇼]
# [소유진패키지] [소쇼패키지] [올해단한번] [방송중에만] (국산) [지금,백지연 단독].
# 프로그램명(소유진/백지연/소쇼)과 원산지·정품 표기도 브랜드가 아니다.
_NOT_A_BRAND_RE = re.compile(
    r"GS|공식|패키지|소유진|백지연|소쇼|단한번|방송중|PICK|단독|국산|국내산|수입|정품",
    re.IGNORECASE,
)

# 같은 브랜드의 다른 표기 -> 대표 표기
BRAND_ALIASES = {"VASAK": "바삭"}

# 사전 브랜드가 '단어 시작'에 있는지 볼 때의 앞 경계. 영숫자/한글 바로 뒤면 단어
# 중간이다. infer_brand보다 엄격하다(그쪽은 "L카사베르디"처럼 영문 한 글자 뒤의
# 한글 브랜드를 허용) - GS 셀럽PGM 상품명 267종에서 이 차이로 잃는 매칭은 없었다.
_WORD_START = r"(?<![0-9A-Za-z가-힣])"


def is_placeholder(brand) -> bool:
    """'GS SHOP'/'gs shop'/'GS샵' 처럼 쇼핑몰명이 브랜드 자리에 들어온 값인지
    (notify/send_celeb_pgm.py, index.html과 같은 판정)."""
    return str(brand or "").strip().upper().replace(" ", "") in ("GSSHOP", "GS샵")


def _loose(brand: str) -> str:
    """글자 사이 공백을 허용하는 패턴 (사전 '라이나생명' <-> 상품명 '라이나 생명')."""
    return r"\s*".join(re.escape(ch) for ch in brand.replace(" ", ""))


def _dictionary_brand(text: str) -> str:
    """text 안의 사전 브랜드(핵심 표기). 단어 시작 위치의 매칭만 인정하고, 여러 개면
    맨 앞 것을 택한다. infer_brand는 가장 긴 브랜드 하나만 알려주므로, 찾은 브랜드
    앞쪽 텍스트를 다시 뒤져 더 앞에 있는 브랜드가 있으면 그쪽을 택한다."""
    found = extract_core_brand(infer_brand(text))
    if not found:
        return ""
    m = re.search(_WORD_START + _loose(found), text)
    if not m:
        # 단어 중간 매칭("14브릭스")은 버리고 그 앞쪽만 다시 본다
        raw = re.search(_loose(found), text)
        return _dictionary_brand(text[:raw.start()]) if raw and raw.start() > 0 else ""
    earlier = _dictionary_brand(text[:m.start()]) if m.start() > 0 else ""
    return earlier or found


def _canonical(brand: str) -> str:
    return BRAND_ALIASES.get(brand, brand)


def infer_celeb_brand(name) -> str:
    """상품명에서 브랜드를 추론한다 (위 '추론 규칙'). 못 찾으면 빈 문자열."""
    rest = str(name or "")
    while True:
        m = _LEADING_BRACKET_RE.match(rest)
        if not m:
            break
        label = (m.group(1) or m.group(2) or "").strip()
        rest = rest[m.end():]
        if label and not is_marketing_copy(label) and not _NOT_A_BRAND_RE.search(label):
            return _canonical(_dictionary_brand(label) or label)
    # 안내 대괄호를 뗀 본문만 본다. 뗀 대괄호 안 문구("GS ONLY")가 사전에 얻어걸리지 않게
    return _canonical(_dictionary_brand(rest))


def resolve_gs_brand(brand, name) -> str:
    """수집용: 브랜드가 비었거나 자리표시자면 상품명에서 추론한다.
    못 찾으면 원래 값을 그대로 돌려준다 ('GS SHOP'은 'GS SHOP'으로, ''은 ''으로)."""
    brand = str(brand or "").strip()
    if brand and not is_placeholder(brand):
        return brand
    return infer_celeb_brand(name) or brand


def recompute_gs_brand(brand, name) -> str:
    """소급용: GS 기록의 브랜드는 전부 추론값이라(상세페이지엔 브랜드가 없다) 현재
    규칙으로 다시 계산한다. 추론 실패면 자리표시자로 되돌리되, 원래 빈 값이던
    상품(편성표로 채운 것)은 빈 값 그대로 둔다."""
    inferred = infer_celeb_brand(name)
    if inferred:
        return inferred
    return "" if not str(brand or "").strip() else GS_PLACEHOLDER
