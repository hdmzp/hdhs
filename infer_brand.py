# -*- coding: utf-8 -*-
"""
브랜드 미보유/부실 상품명에서 브랜드를 추론하는 모듈 (GS, CJ 공용 보조)

GS는 라방바 API 특성상 브랜드 필드가 항상 비어있다.
CJ는 API의 brandName이 거의 항상 None이고, 상품 상세페이지는 JS로 렌더링되는
SPA라 requests로는 브랜드를 가져올 수 없다(별도 상세 API 미확인). 그래서 CJ도
상품명(itemNm) 텍스트에서 브랜드를 추론해야 한다.

분류 모델은 "브랜드명 + 상품명"으로 학습되어 브랜드가 핵심 신호인데, 이 신호가
없으면 분류 확신도가 크게 떨어진다(예: "LG 통돌이 세탁기"가 생활용품/일반식품
등으로 헷갈림 - 확신도 20% 이하).

이 모듈은 학습 데이터(training_data.xlsx)의 브랜드 목록에서
핵심 토큰("LG(엘지)" -> "LG", "삼성(SAMSUNG)" -> "삼성")을 추출해 사전을 만들고,
상품명 안에 그 토큰이 포함돼 있으면 찾아내 분류 모델 입력 보강 및 화면 표시용
브랜드로 사용한다.

== 매칭 안전장치 ==
- 매칭 전에 대괄호/소괄호 마케팅 카피를 제거한다
  (예: "[방송에서만] 핏업 골드..." -> "핏업 골드..."로 정리 후 매칭)
  CJ 상품명은 "[최초가69,900원]", "(최신상)" 같은 안내문이 브랜드 앞에 자주 붙어
  있어, 이걸 제거하지 않으면 "맨 앞 단어 매칭"이 거의 항상 실패한다.
- 단, 대괄호 안에 브랜드명이 들어있는 경우도 있다(예: GS 상품명
  "[아로마티카] 스파 샴푸..."). 이런 케이스를 놓치지 않기 위해, 본문 매칭이
  실패하면 제거했던 대괄호/소괄호 안의 텍스트들도 같은 규칙으로 한 번 더
  시도한다(괄호 안 내용 자체를 "맨 앞 단어" 취급).
- 토큰은 "단어 시작 위치"에서만 인정한다. 단순 부분문자열 매칭은 단어
  한가운데에 얻어걸린다 ("뉴베리에이션"의 "베리에", "에어프라이어"의 "프라이",
  "프로폴리스"의 "폴리스"). 뒤 경계는 요구하지 않는다 - "닥터린대마종자유"처럼
  브랜드와 상품명을 붙여 쓰는 표기가 흔하기 때문.
- 후보가 여럿이면 (1) 단어 끝 경계까지 맞는 매칭 (2) 긴 토큰 (3) 앞쪽에서
  시작하는 매칭 순으로 고른다
  ("고트만 ... 트라이탄 밀폐용기" -> "트라이"가 아니라 "고트만").
- 2글자 이하 토큰은 "맨 앞 단어"와 "정확히 일치"할 때만 인정한다
  (예: "로던"이 상품명 중간 어딘가에 우연히 끼어 있는 경우는 무시,
   상품명이 "로던 ..."으로 시작할 때만 인정). 대괄호 안 텍스트를 검사할 때는
   그 괄호 안 텍스트의 첫 단어를 기준으로 동일하게 적용한다.
- 공백 유무 차이로 매칭이 실패하는 경우(학습데이터 "라이나생명" vs
  상품명 "라이나 생명", "세인트존스호텔" vs "강릉 세인트존스 호텔")를 위해,
  일반 매칭이 실패하면 글자 사이 공백을 허용해 한 번 더 시도한다.
- 숫자만 있는 브랜드 표기("2026")는 사전에서 제외한다 (연도에 얻어걸린다).

== 브랜드 자리에 들어온 마케팅 카피 ==
브랜드 필드가 없어 상품명 앞 대괄호를 브랜드로 쓰는 편성(롯데 등)에서는
그 자리에 "백화점가 106만원", "정상가 247,000원", "화이트" 같은 안내문이
들어온다. is_marketing_copy()로 이런 값을 걸러내고,
pick_brand_from_prefix()로 안내문 접두어를 건너뛰어 진짜 브랜드를 찾는다.

== 사용법 ==
  from infer_brand import infer_brand
  infer_brand("LG 통돌이 세탁기 T19MX7A 미드 블랙")
  # -> "LG(엘지)"  (매칭 안 되면 "")

  infer_brand("[최초가69,900원]배럴 커브드 데님")
  # -> 마케팅 카피 제거 후 "배럴 커브드 데님"으로 매칭 시도
"""

import os
import re
import pandas as pd

_TRAINING_XLSX_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "training_data.xlsx"),
    "training_data.xlsx",
]

_brand_tokens = None  # 길이 내림차순 정렬된 (토큰, 원본브랜드) 리스트
_brand_tokens_nospace = None  # 공백 제거 버전 (토큰_nospace, 원본브랜드), 길이 내림차순

# 상품명 앞에 자주 붙는 대괄호/소괄호 마케팅 카피 제거용
# (분류 모델 입력에는 영향 없음 - 브랜드 추론 매칭 전처리에만 사용)
_BRACKET_RE = re.compile(r"\[([^\[\]]*)\]|\(([^()]*)\)")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")


def extract_core_brand(brand: str) -> str:
    """브랜드명에서 괄호 안내문 제거해 화면 표시용 핵심 브랜드명만 반환.
    'LG(엘지)' -> 'LG', '교원투어(TV)' -> '교원투어'
    내부 매칭에 쓰는 _extract_core와 동일하나, 외부(categorize.py 등)에서
    표시용 브랜드 정제 목적으로 쓸 수 있게 공개 함수로 둔다."""
    return _extract_core(brand)


def _extract_core(brand: str) -> str:
    """브랜드명에서 괄호 안내문 제거: 'LG(엘지)' -> 'LG'"""
    return re.sub(r"\([^)]*\)", "", str(brand)).strip()


def _strip_marketing_copy(text: str):
    """매칭 전처리: 대괄호/소괄호 안내문을 제거한 본문과, 제거된 괄호 안
    내용들을 함께 반환한다. 분류용 원본 텍스트는 그대로 두고, 브랜드 매칭에만
    이 정제본/괄호내용을 사용한다.
    반환: (본문(괄호 제거+공백정리), [괄호 안 텍스트, ...])
    """
    bracket_contents = [g1 or g2 for g1, g2 in _BRACKET_RE.findall(text)]
    bracket_contents = [c.strip() for c in bracket_contents if c and c.strip()]

    body = _BRACKET_RE.sub(" ", text)
    body = _MULTI_SPACE_RE.sub(" ", body).strip()
    return body, bracket_contents


def _load_brand_tokens():
    global _brand_tokens, _brand_tokens_nospace
    if _brand_tokens is not None:
        return _brand_tokens, _brand_tokens_nospace

    xlsx_path = None
    for cand in _TRAINING_XLSX_CANDIDATES:
        if os.path.exists(cand):
            xlsx_path = cand
            break

    if xlsx_path is None:
        _brand_tokens = []
        _brand_tokens_nospace = []
        return _brand_tokens, _brand_tokens_nospace

    df = pd.read_excel(xlsx_path, sheet_name=0)
    raw_brands = df["브랜드명"].dropna().unique()

    seen = set()
    tokens = []
    for b in raw_brands:
        core = _extract_core(b)
        # 숫자만 있는 표기("2026", "8515")는 학습데이터 입력 오류로 보고 제외한다.
        # 연도/모델번호에 얻어걸려 진짜 브랜드를 밀어내기 때문
        # ("2026 아디다스 뉴 컴포트 드로즈" -> "2026")
        if core and re.fullmatch(r"\d+", core):
            continue
        if core and core not in seen:
            seen.add(core)
            tokens.append((core, str(b)))

    # 긴 토큰을 먼저 매칭하도록 길이 내림차순 정렬
    tokens.sort(key=lambda t: len(t[0]), reverse=True)
    _brand_tokens = tokens

    # 공백 제거 버전 (길이는 공백 제거 전 기준으로 정렬해 일관성 유지)
    tokens_nospace = [(t.replace(" ", ""), original) for t, original in tokens]
    tokens_nospace.sort(key=lambda t: len(t[0]), reverse=True)
    _brand_tokens_nospace = tokens_nospace

    return _brand_tokens, _brand_tokens_nospace


_token_re_cache = {}


def _token_pattern(token: str, loose: bool = False):
    """토큰이 '단어 시작 위치'에 있을 때만 매칭되는 정규식.

    단순 부분문자열 매칭(`token in text`)은 단어 한가운데에 얻어걸린다.
      "뉴베리에이션 4인조 홈세트"      -> "베리에"(브랜드)로 오매칭
      "코렐 일렉 에어프라이어"         -> "프라이"(브랜드)로 오매칭
      "아이클리어 루테인 아스타잔틴"     -> "아스타"(브랜드)로 오매칭
      "프로폴리스 가글"               -> "폴리스"(브랜드)로 오매칭
    그래서 앞 경계(문자열 시작 또는 한글/영숫자가 아닌 문자)를 요구한다.

    앞 경계는 '같은 문자종'만 막는다. 한글 브랜드는 앞이 한글이 아니면 되고
    ("L카사베르디", "T뉴케어"처럼 영문 한 글자가 붙는 표기가 흔하다),
    영문/숫자 브랜드는 앞이 영숫자가 아니면 된다("BBF-AM12"의 "M12"는 막힘).

    반대로 뒤 경계는 요구하지 않는다. 국내 편성 상품명은 브랜드와 상품명을
    붙여 쓰는 표기가 흔해서("닥터린대마종자유", "정성곳간갈비탕",
    "임성근의 특키로 갈비탕") 뒤까지 막으면 정상 매칭이 대량으로 깨진다.
    """
    cached = _token_re_cache.get((token, loose))
    if cached is not None:
        return cached

    head = token[0]
    if re.match(r"[가-힣]", head):
        lookbehind = r"(?<![가-힣])"
    elif re.match(r"[0-9A-Za-z]", head):
        lookbehind = r"(?<![0-9A-Za-z])"
    else:
        lookbehind = r"(?<![0-9A-Za-z가-힣])"
    # loose=True면 글자 사이 공백을 허용한다 ("세인트존스 호텔")
    body = r"\s*".join(re.escape(ch) for ch in token) if loose else re.escape(token)
    pattern = re.compile(lookbehind + body)
    _token_re_cache[(token, loose)] = pattern
    return pattern


def _trailing_boundary_ok(text: str, end: int, token: str) -> bool:
    """토큰이 끝나는 자리가 '단어 끝'인지.

    "트라이탄 밀폐용기"의 "트라이"처럼 뒤에 같은 문자종이 이어지면 단어
    중간을 자른 매칭이라 신뢰도가 낮다. 다만 국내 상품명은 브랜드와 상품명을
    붙여 쓰는 표기도 흔해서("닥터린대마종자유") 이걸로 탈락시키지는 않고,
    후보 우선순위(더 그럴듯한 매칭 고르기)에만 쓴다.
    """
    if end >= len(text):
        return True
    nxt = text[end]
    if re.match(r"[가-힣]", token[-1]):
        return not re.match(r"[가-힣]", nxt)
    if re.match(r"[0-9A-Za-z]", token[-1]):
        return not re.match(r"[0-9A-Za-z]", nxt)
    return True


def _match(text: str, first_word: str, tokens: list, loose: bool = False,
           text_nospace: str = None) -> str:
    """tokens 중 text에 나타나는 브랜드를 찾아 원본 브랜드 표기를 반환.

    후보가 여럿이면 (1) 단어 끝 경계가 맞는 매칭 (2) 긴 토큰 (3) 앞쪽에서
    시작하는 매칭 순으로 고른다.
      "NEW 고트만 네오 크리스탈락 트라이탄 밀폐용기"
      -> "트라이"(뒤에 '탄'이 붙음)보다 "고트만"(단어 끝 일치)을 택한다.

    loose=True면 토큰 글자 사이 공백을 허용해 매칭한다. 이때 text_nospace
    (공백 제거본)로 먼저 값싸게 걸러낸 뒤 정규식을 돌린다 (사전이 수천 개라
    전량 정규식은 느리다).
    """
    best = None
    best_rank = None
    for token, original in tokens:
        if not token:
            continue
        if len(token) <= 2:
            # 짧은 토큰은 오매칭 위험이 커서 맨 앞 단어와 완전히 같을 때만 인정
            if token != first_word:
                continue
            rank = (True, len(token), 0)
            if best_rank is None or rank > best_rank:
                best, best_rank = original, rank
            continue

        if loose:
            if text_nospace is not None and token not in text_nospace:
                continue
        elif token not in text:
            # 정규식 전에 값싼 부분문자열 검사로 거른다 (사전이 수천 개)
            continue

        m = _token_pattern(token, loose).search(text)
        if not m:
            continue
        rank = (_trailing_boundary_ok(text, m.end(), token), len(token), -m.start())
        if best_rank is None or rank > best_rank:
            best, best_rank = original, rank

    return best or ""


def infer_brand(product_name: str) -> str:
    """
    상품명 안에서 학습 데이터 브랜드 사전과 매칭되는 브랜드를 찾아 반환.
    모델은 학습 데이터의 정확한 표기(예: "LG(엘지)")에 민감하므로,
    매칭에 쓴 핵심 토큰이 아니라 원본 표기를 그대로 반환한다.
    매칭 안 되면 빈 문자열.
    """
    if not product_name:
        return ""

    tokens, tokens_nospace = _load_brand_tokens()
    if not tokens:
        return ""

    raw = str(product_name)
    body, bracket_contents = _strip_marketing_copy(raw)
    if not body:
        body = raw.strip()

    # 검사할 텍스트 후보들: 본문(괄호 제거) 먼저, 그 다음 괄호 안 내용들
    # (본문에 브랜드가 있는 경우가 더 흔하므로 우선 순위를 둔다.
    #  예: "[방송에서만] 핏업 골드..." -> 본문 "핏업 골드..."에서 먼저 매칭됨.
    #  본문에서 못 찾으면 "[아로마티카] 스파 샴푸..." 같이 브랜드가
    #  괄호 안에 있는 경우를 위해 괄호 내용도 차례로 시도한다.)
    candidates = [body] + bracket_contents

    for candidate in candidates:
        first_word = candidate.split()[0] if candidate.split() else ""

        result = _match(candidate, first_word, tokens)
        if result:
            return result

        # 공백 차이로 실패한 경우(학습데이터 "라이나생명" vs 상품명 "라이나 생명",
        # "세인트존스호텔" vs "강릉 세인트존스 호텔") 글자 사이 공백을 허용해
        # 한 번 더 시도한다. 단어 시작 경계 조건은 그대로 유지한다.
        candidate_nospace = candidate.replace(" ", "")
        first_word_nospace = first_word.replace(" ", "")
        result = _match(candidate, first_word_nospace, tokens_nospace, loose=True,
                        text_nospace=candidate_nospace)
        if result:
            return result
        # 상품명 쪽이 아니라 사전 쪽에 공백이 있는 경우까지 커버
        result = _match(candidate_nospace, first_word_nospace, tokens_nospace)
        if result:
            return result

    return ""



# ============================================================================
# 마케팅 카피 판별 (브랜드 자리에 들어온 안내문 걸러내기)
# ----------------------------------------------------------------------------
# 롯데 등 일부 편성 데이터는 브랜드 필드가 따로 없어 상품명 앞의 대괄호 접두어를
# 브랜드로 쓰는데, 그 자리에 브랜드가 아니라 가격/구성/색상 안내문이 들어오는
# 경우가 많다.
#   "[백화점가 106만원][포트메리온] 뉴베리에이션 4인조 홈세트 23P"
#   -> 맨 앞 대괄호만 보면 브랜드가 "백화점가 106만원"이 돼버린다.
# 실제로 수집된 오분류 사례: "정상가 247,000원", "상시가 479,000원",
# "런칭가 109,000원", "SALE", "기획특가", "1박스", "대용량", "단품",
# "화이트/레드/블랙"(색상), "4종 대용량세트", "롯데 단독", "공식수입정품" 등.
# 브랜드가 틀리면 화면 표시뿐 아니라 카테고리 분류도 같이 틀어진다
# (브랜드+상품명으로 학습된 모델이라 브랜드가 핵심 신호).
# ============================================================================

# 가격/할인 표기: "106만원", "247,000원", "30%", "1+1"
_PRICE_LIKE_RE = re.compile(r"\d[\d,\.]*\s*(?:만원|원|%|퍼센트)|\d{1,3}(?:,\d{3})+")

# 브랜드일 리 없는 마케팅/구성/안내 문구 (부분 일치)
_MARKETING_WORDS_RE = re.compile(
    r"정상가|상시가|백화점가|런칭가|론칭가|최초가|방송가|판매가|본품가|할인|특가|세일|SALE"
    r"|사은품|증정|무료|무이자|쿠폰|혜택|적립|페이백|추가구성"
    r"|단독|한정|최대|최저|최다|역대|마지막|찬스|기획|앵콜|앙콜|오늘만|마감"
    r"|단품|대용량|풀세트|세트|패키지|구성|택1|택일|더블|증량"
    r"|방송에서만|생방송|공식수입정품|직수입|병행수입|무료체험|체험분",
    re.IGNORECASE,
)

# 수량/기간만 적힌 표기: "1박스", "6개월", "20주", "8P"
_QUANTITY_ONLY_RE = re.compile(
    r"^\d+\s*(?:개월|주|일|박스|매|팩|병|종|개|입|구|인조|P|EA|SET)?$", re.IGNORECASE
)

# 색상/옵션만 적힌 표기 (완전 일치일 때만 - "골드에이스앤코" 같은 브랜드는 살린다)
_OPTION_ONLY = {
    "화이트", "블랙", "레드", "블루", "그린", "그레이", "그레이지", "핑크", "네이비",
    "아이보리", "베이지", "옐로우", "퍼플", "실버", "카키", "와인",
    "색상", "컬러", "옵션", "사이즈", "공통", "신상", "NEW", "HOT", "BEST", "LIVE",
    "ONLY", "TV", "온라인", "모바일",
}


# 상품명 맨 앞에 붙은 대괄호/소괄호 접두어 하나
_PREFIX_BRACKET_RE = re.compile(r"^\s*(?:\[([^\[\]]*)\]|\(([^()]*)\))")


def _is_known_brand(text: str) -> bool:
    """학습데이터 브랜드 사전에 그대로 존재하는 표기인지 (공백 무시)."""
    tokens, tokens_nospace = _load_brand_tokens()
    key = str(text or "").strip()
    if not key:
        return False
    key_nospace = key.replace(" ", "").lower()
    for token, _original in tokens:
        if token.replace(" ", "").lower() == key_nospace:
            return True
    return False


def is_marketing_copy(text: str) -> bool:
    """브랜드 자리에 들어온 값이 브랜드가 아니라 마케팅/안내 문구인지 판별.

    True면 브랜드로 쓰면 안 된다.
      is_marketing_copy("백화점가 106만원")  -> True
      is_marketing_copy("포트메리온")        -> False
    """
    t = str(text or "").strip()
    if not t:
        return True
    # 학습데이터에 실재하는 브랜드면 무조건 브랜드로 인정한다
    # ("2026", "닥터오기덤", "국내산 절단꽃게"처럼 규칙에 걸릴 표기가 실제 브랜드인 경우)
    if _is_known_brand(t):
        return False
    if t.upper() in _OPTION_ONLY:
        return True
    if _QUANTITY_ONLY_RE.match(t):
        return True
    if _PRICE_LIKE_RE.search(t):
        return True
    if _MARKETING_WORDS_RE.search(t):
        return True
    return False


def pick_brand_from_prefix(product_name: str) -> str:
    """상품명 앞의 대괄호/소괄호 접두어들을 앞에서부터 훑어 브랜드를 고른다.

    - 마케팅 카피 접두어는 건너뛰고 그 다음 접두어를 본다
      "[백화점가 106만원][포트메리온] 뉴베리에이션..." -> "포트메리온"
    - 접두어에서 못 찾으면 상품명 본문을 학습데이터 브랜드 사전으로 추론
      "[정상가 247,000원] 칼만 블랙 통5중 IH 저압냄비" -> "칼만"
    - 그래도 못 찾으면 빈 문자열 (추정 브랜드를 지어내지 않는다)
    """
    rest = str(product_name or "")
    while True:
        m = _PREFIX_BRACKET_RE.match(rest)
        if not m:
            break
        candidate = (m.group(1) or m.group(2) or "").strip()
        rest = rest[m.end():]
        if candidate and not is_marketing_copy(candidate):
            return candidate

    inferred = infer_brand(product_name)
    return _extract_core(inferred) if inferred else ""


if __name__ == "__main__":
    samples = [
        "LG 통돌이 세탁기 T19MX7A 미드 블랙",
        "원스톱프리미엄암보험_치료비플랜",
        "삼성 비스포크 김치냉장고 4도어",
        "스테파넬 26SS 썸머 쿨드레이프 팬츠",
        "[방송에서만]핏업 골드 유기농 대마종자유 18박스(12+6박스)",
        "[최초가69,900원]배럴 커브드 데님",
        "(최신상)아치나인Arch-9 Flux기능성 슬리퍼_블랙",
        "[LIVE]라이나 생명 The건강한치아보험V",
        "[아로마티카] 스파 샴푸 1등 패키지 (샴푸7+트리트먼트2)",  # 브랜드가 괄호 안
        "26년형 신일 써큘레이터 S11 미드나잇 블랙 (SIF-DH09BK) 1대 구성",  # 2글자 브랜드, 맨앞 아님
    ]
    for s in samples:
        print(f"{s[:45]:45s} -> 추론 브랜드: {infer_brand(s) or '(없음)'}")

    print()
    prefix_samples = [
        "[백화점가 106만원][포트메리온] 뉴베리에이션 4인조 홈세트 23P",
        "[정상가 247,000원] 칼만 블랙 통5중 IH 저압냄비 스테인리스 찜판",
        "[화이트] 무선 전동 그라인더 2.0 풀세트",
        "[아로마티카] 스파 샴푸 1등 패키지",
    ]
    for s in prefix_samples:
        print(f"{s[:45]:45s} -> 접두어 브랜드: {pick_brand_from_prefix(s) or '(없음)'}")
