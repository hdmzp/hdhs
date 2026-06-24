# -*- coding: utf-8 -*-
"""
상품명 정제기

크롤링한 원본 상품명에서 마케팅성 문구(괄호 안내문, 가격 강조, 사은품/경품,
용량, 개월수 등)를 제거해 화면에 보여줄 깔끔한 텍스트를 만든다.

== 중요: 분류와 표시를 분리 ==
카테고리 분류 모델은 원본 상품명(정제 전)으로 학습됐으므로,
분류는 항상 원본 텍스트로 수행하고, clean_product_name()은
화면에 보여줄 텍스트를 만들 때만 사용한다.

== 사용 순서(스크래퍼에서) ==
  1. category = classify(brand, raw_product)   # 원본으로 분류
  2. display_product = clean_product_name(raw_product)  # 표시용 정제
  3. 저장: {"product": display_product, "category": category, ...}

== 사용법 ==
  from clean_product import clean_product_name
  clean_product_name("[방송에서만 1+1] 삼성 비스포크 냉장고 (런칭가 99,000원)")
  # -> "삼성 비스포크 냉장고"
"""
# -*- coding: utf-8 -*-
import re

# 1) 대괄호/괄호로 감싸인 내용 전체 제거
_BRACKET_RE = re.compile(r"\[[^\[\]]*\]|\([^()]*\)")

# 2) 가격 강조 문구
_PRICE_PHRASE_RE = re.compile(r"(?:런칭가|론칭가|최종혜택가|방송가격)\s*[\d,]+\s*원?")

# 3) 사은품/경품/증정/체험분 안내 문구
_GIFT_PHRASE_RE = re.compile(r"(?:사은품|경품|리뷰시|증정|무료체험|체험분)\s*[^\[\]()]*?(?=[\[\(]|$)")

# 4) 용량/수량 표기 (숫자+단위)
_VOLUME_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ml|ML|mL|L|g|G|kg|KG|Kg|매|통|박스|종|세트|개)\b")

# 5) 개월수 표기
_MONTH_RE = re.compile(r"\d+(?:\s*\+\s*\d+)?\s*개월\S*")

# 6) [추가] 롯데홈쇼핑 등 특수문자/마케팅 강조 키워드
# 별표 처리, 단독, 최다구성 등 문구 추가
_MARKETING_KEYWORD_RE = re.compile(r"★.*?★|☆.*?☆|롯데단독|역대최다구성|최대구성|최신상|초특가|무료체험")

# 7) 기존 마케팅 키워드
_PLAIN_KEYWORDS_RE = re.compile(r"방송에서만|방송중\s*구매가능|런칭\s*가격|론칭\s*가격")

# 8) 잔여물 정리
_LEFTOVER_PUNCT_RE = re.compile(r"[\[\]()]+")
_DANGLING_SYMBOL_RE = re.compile(r"[*&+]")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")


def clean_product_name(raw: str) -> str:
    """화면에 표시할 깔끔한 상품명을 반환."""
    if not raw:
        return ""

    text = raw

    # 우선순위가 높은 마케팅/가격 문구 제거
    text = _MARKETING_KEYWORD_RE.sub("", text)
    text = _PRICE_PHRASE_RE.sub("", text)
    text = _GIFT_PHRASE_RE.sub("", text)

    # 대괄호·괄호 내용 제거
    text = _BRACKET_RE.sub("", text)

    # 용량, 개월수 제거
    text = _VOLUME_RE.sub("", text)
    text = _MONTH_RE.sub("", text)

    # 기타 키워드 제거
    text = _PLAIN_KEYWORDS_RE.sub("", text)

    # 잔여 기호 및 공백 정리
    text = _LEFTOVER_PUNCT_RE.sub("", text)
    text = _DANGLING_SYMBOL_RE.sub(" ", text)
    text = _MULTI_SPACE_RE.sub(" ", text).strip()

    return text if text else raw.strip()


if __name__ == "__main__":
    samples = [
        "[자동급배수/빌트인+리폼S] 삼성 Bespoke AI 스팀 울트라 로봇청소기 [VR90F01SAG]",
        "[역대최초] 2026 최신상 마데카 크림 에이징포커스 대용량 4통 & 최신상 버블세럼 (체험분2매)",
        "이지듀 스킨핏 톤업기미선크림 30ml*4통 + 1통(리뷰시) + 무료체험분 1매 기미풀패키지",
        "스테파넬 26SS 스트라이프 스트링 티셔츠 3종[런칭 가격 89,900원]",
        "★역대최다구성★☆롯데단독☆ 베지톨로지 비니페린 브라이트닝 기미앰플 8병",
    ]
    for s in samples:
        print(f"원본: {s}")
        print(f"정제: {clean_product_name(s)}\n")
