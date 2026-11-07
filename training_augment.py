# -*- coding: utf-8 -*-
"""
분류 모델 학습용 증강 데이터 (train_model.py가 자동으로 붙여서 학습한다)

== 왜 필요한가 ==
학습데이터(training_data.xlsx)에 거의 없는 미용 품목명(립타투·틴트·워시오프 등)이
나오면 모델이 확신을 못 하고 기본 점수가 가장 높은 '일반식품'으로 보내는 경향이 있었다.
(2026-11-07 레쁠레뜨 '유기농 오트크림', 베리썸 '립타투 틴트', 소온하루 '링클 패치' 등)

== 어떻게 만드나 ==
학습데이터에서 '미용'으로만 운영된 브랜드 + 아래 미용 품목명 + 중립적인 구성 표현
(용량·개수·세트)을 조합해 문장을 만든다. 시드를 고정해 매번 같은 결과가 나온다.
품목 목록은 사람이 지정한 것 (2026-11-07). 추가/삭제는 BEAUTY_AUGMENT_ITEMS만 고치면 된다.

== 주의 ==
- 증강 문장은 학습에만 쓰고 검증(정확도 측정)에는 넣지 않는다 (train_model.py).
- 식품을 연상시키는 수식어(유기농·오트·쌀 등)는 사람이 지정한 품목명 안에서만 쓴다.
"""

import random

import pandas as pd

# 사람이 지정한 미용 품목명 (2026-11-07)
BEAUTY_AUGMENT_ITEMS = [
    # 메이크업
    "틴트", "립타투", "립스틱", "립밤", "립글로스", "립오일",
    "파운데이션", "쿠션", "비비크림", "톤업크림", "컨실러", "팩트",
    "루스파우더", "피니싱파우더", "픽서", "마스카라", "아이섀도우팔레트",
    "아이라이너", "아이브로우", "속눈썹영양제",
    # 기초
    "세럼", "앰플", "아이크림", "링클패치", "토너", "스킨", "로션", "에멀전",
    "에센스", "수분크림", "영양크림", "재생크림", "보습크림", "슬리핑팩",
    "탄력크림", "토너패드",
    # 팩
    "마스크팩", "시트팩", "콜라겐팩", "모델링팩",
    # 선케어
    "선크림", "선스틱", "선쿠션", "선젤",
    # 클렌징
    "클렌징", "클렌징오일", "클렌징워터", "클렌징밤", "클렌징밀크", "클렌징폼",
    "스크럽", "필링패드", "워시오프팩",
    # 향
    "향수", "퍼퓸", "오드뚜왈렛", "오드퍼퓸", "퍼퓸미스트", "바디미스트",
    # 바디
    "바디워시", "바디로션", "바디크림", "바디오일", "핸드크림",
    # 헤어
    "샴푸", "트리트먼트", "헤어팩", "헤어에센스", "헤어오일",
    # 2026-11-07 추가 지정 - 학습데이터에 거의 없던 품목
    "립스테인", "쿠션팩트", "클레이팩", "필링젤", "하이드로겔 패치", "새치커버",
    # 2026-11-07 추가 지정 - 식품 단어가 섞인 미용 품목 (일반식품 오분류의 핵심 원인)
    "오트크림", "쌀 클렌징폼", "율무팩", "꿀 립밤", "녹차 토너", "어성초 앰플", "콩 에센스",
    "유기농 바디로션", "프랑스 직수입 크림", "알로에 수분젤", "올리브 핸드크림", "카무트 세럼",
]

ROWS_PER_ITEM = 4          # 품목당 생성 문장 수

# 식품 단어 + 확실한 미용 품목 = 미용 (2026-11-07 사람 지정 원칙).
# 모델이 '유기농·오트·율무·콩' 같은 식품 단어에 끌려 일반식품으로 보내던 걸 바로잡기 위해
# 식품 단어 x 미용 품목 조합 문장을 따로 만든다 (조합마다 브랜드 1개, 시드 고정).
FOOD_WORDS = [
    "오트", "쌀", "율무", "꿀", "녹차", "어성초", "콩", "유기농", "프랑스 직수입",
    "알로에", "올리브", "카무트", "콜라겐", "홍삼", "흑염소", "레몬", "당근", "토마토",
    "블루베리", "아보카도", "마누카꿀", "발효", "곡물", "현미",
]
CORE_BEAUTY_ITEMS = [
    "크림", "수분크림", "세럼", "앰플", "토너", "에센스", "클렌징폼", "핸드크림",
    "바디로션", "팩", "립밤", "수분젤", "마스크팩", "선크림", "샴푸", "클렌징오일",
]
BRAND_POOL_SIZE = 60       # 학습데이터 미용 단일 브랜드 중 상위 N개를 사용
SEED = 20261107

# 문장 틀은 '브랜드 + 품목명'만 쓴다 (품목명이라는 신호만 학습시키려는 것).
# 판촉 문구('[방송에서만]', '패키지', '세트', 'NEW')나 용량·개수('500ml 4개')를 넣어봤더니
# 그 표현들이 미용 신호로 학습돼 여행('대만 4일 패키지')·가글·세제·생수 같은 상품까지
# 미용으로 끌려갔다 (2026-11-07 라방바 데이터로 검증).
_TEMPLATES = [
    "{brand} {item}",
]


def _beauty_brands(train_df: pd.DataFrame, size: int) -> list:
    """학습데이터에서 '미용'으로만 운영된 브랜드를 빈도순으로 size개."""
    df = train_df.dropna(subset=["브랜드명", "상품중분류명"])
    cats = df.groupby("브랜드명")["상품중분류명"].agg(lambda s: set(s))
    only = {b for b, c in cats.items() if c == {"미용"} and str(b).strip()}
    counts = df[df["브랜드명"].isin(only)]["브랜드명"].value_counts()
    return [str(b) for b in counts.index[:size]]


def build_augment_rows(train_df: pd.DataFrame) -> pd.DataFrame:
    """증강 문장 DataFrame (브랜드명/판매상품명/상품중분류명) 반환."""
    rng = random.Random(SEED)
    brands = _beauty_brands(train_df, BRAND_POOL_SIZE)
    if not brands:
        return pd.DataFrame(columns=["브랜드명", "판매상품명", "상품중분류명"])
    rows = []
    for item in BEAUTY_AUGMENT_ITEMS:
        # 품목마다 서로 다른 브랜드 ROWS_PER_ITEM개와 묶는다 (브랜드가 아니라 품목명을 배우도록)
        for brand in rng.sample(brands, k=min(ROWS_PER_ITEM, len(brands))):
            for tpl in _TEMPLATES:
                rows.append({"브랜드명": brand, "판매상품명": tpl.format(brand=brand, item=item),
                             "상품중분류명": "미용"})
    for food in FOOD_WORDS:
        for item in CORE_BEAUTY_ITEMS:
            # 상품명엔 '오트 크림'도 '오트크림'도 나온다. 모델이 단어 단위로 글자를 보므로 둘 다 넣는다.
            name = f"{food} {item}" if rng.random() < 0.5 else f"{food}{item}".replace(" ", "")
            brand = rng.choice(brands)
            rows.append({"브랜드명": brand, "판매상품명": f"{brand} {name}", "상품중분류명": "미용"})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "training_data.xlsx"
    aug = build_augment_rows(pd.read_excel(path))
    print(f"증강 {len(aug)}건")
    print(aug.sample(15, random_state=1).to_string(index=False))
