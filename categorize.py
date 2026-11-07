# -*- coding: utf-8 -*-
"""
상품 카테고리 분류기
#사용법: (1)동업계교류실적을 기반으로 브랜드+상품명+상품중분류 주기적 학습
(2)브랜드별 강제분류 일부추가 (특히 가전)

학습된 모델(category_model.pkl)을 로드해 브랜드+상품명으로 카테고리를 예측한다.
크롤러(hd_scraper.py 등)에서 이 모듈을 import해서 사용한다.

== 분류 순서 (우선순위) ==
0) 브랜드 강제 매핑 (BRAND_FORCE_MAP): 브랜드명이 정확히 일치하면 카테고리 확정
   (포함 방식이 아닌 완전일치 - "삼성금거래소"는 "삼성"과 다름)
   - 브랜드+품목 조합 매핑 (BRAND_ITEM_SPLIT): 생로랑 미용품목 -> 미용, 그 외 -> 잡화/주얼리
1) 키워드 규칙: 상품명에 명확한 단어가 있으면 확정
   - 동시출현 규칙(COMBO_RULES): 신호 단어가 여럿 함께 나오면 확정 (예: PDRN+세럼+글로우 -> 미용)
   - 사은품 제외: '+' 기준 첫 덩어리(본품)만 검사
   - 미용 확정 키워드(BEAUTY_KEYWORD_RE): 괄호·'+'·'증정' 등 사은품 부분을 걷어낸 본품만 검사
2) 브랜드명이 학습데이터에서 단일 카테고리로만 운영된 브랜드면 바로 확정
3) 브랜드가 비어있으면 상품명에서 브랜드 추론해서 보강 (infer_brand)
   - 추론된 브랜드도 BRAND_FORCE_MAP 검사
   - 추론까지 실패하면 상품명 강제 매핑(NO_BRAND_PRODUCT_FORCE) 검사
4) 모델 분류 - 확신도가 LOW_CONFIDENCE_THRESHOLD 미만이면 '미분류' (화면 슬라이서로 사람이 점검)

== 카테고리 그룹 ==
가전       = 대형가전 + 소형가전 + 다이슨 + 로보락
미용       = 듀얼소닉 + 미용
의류       = 패션의류 + 레포츠의류
잡화/주얼리 = 패션잡화 + 쥬얼리 + 수입명품
여행       = 여행 + 여행(결제)
리빙/주방   = 주방용품 + 인테리어/침구 + 생활용품
기타       = 건강식품, 일반식품, 보험, 일반렌탈, 문화/스포츠, GA 등
"""

import os
import re
import joblib
import pandas as pd
from infer_brand import infer_brand, extract_core_brand

MODEL_PATH = os.path.join(os.path.dirname(__file__), "category_model.pkl")

_TRAINING_XLSX_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "training_data.xlsx"),
    "training_data.xlsx",
]

# 세분류(모델 예측 클래스) -> 통합 그룹명
GROUP_MAP = {
    "대형가전": "가전", "소형가전": "가전", "다이슨": "가전", "로보락": "가전",
    "듀얼소닉": "미용", "미용": "미용",
    "패션의류": "의류", "레포츠의류": "의류",
    "패션잡화": "잡화/주얼리", "쥬얼리": "잡화/주얼리", "수입명품": "잡화/주얼리",
    "여행": "여행", "여행(결제)": "여행",
    "주방용품": "리빙/주방", "인테리어/침구": "리빙/주방", "생활용품": "리빙/주방",
}

# 브랜드명 완전일치 기반 강제 카테고리 매핑
# - 포함(contain)이 아니라 정확히 이 문자열이어야 적용됨
# - "삼성금거래소", "삼성화재"는 "삼성"과 다르므로 걸리지 않음
# - 학습데이터 확인 결과 기준으로 작성. 새 브랜드 추가 시 여기에도 추가할 것.
BRAND_FORCE_MAP = {
    # 가전
    "삼성":          "가전",
    "삼성(SAMSUNG)": "가전",
    "삼성전자":       "가전",
    "삼성화재":       "보험",
    "LG":            "가전",
    "LG(엘지)":      "가전",
    "LG전자":        "가전",
    "엘지":          "가전",
    "FILA":          "의류",
    "삼성금거래소":   "잡화/주얼리",
    "한국금거래소":   "잡화/주얼리",
    "캐롤프랑크":  "미용",
    "사임당": "리빙/주방",
    "Playback": "의류",
    "플레이백":"의류",
    "라피타":"미용",
    "BNS":"리빙/주방",
    "노랑풍선":"여행",
    "주영엔에스":"건강식품",
    "립밤":"의류",     # 의류 브랜드 Lipbbam (립밤 화장품 아님)
    "Lipbbam":"의류",
    "듀얼소닉":"미용",  # 헤어드라이어지만 브랜드 기준 미용 (사람 지정 - 고데기·드라이기=가전 규칙의 예외)
    "레쁠레뜨":"미용",  # 프랑스 화장품 (유기농 오트크림 - 모델이 일반식품으로 오분류)
    
    # 필요시 추가: "LG디스플레이": "가전", ...
}

# 브랜드 + 품목 조합 강제 매핑
# 명품 브랜드는 가방·지갑(잡화)과 향수·화장품(미용)을 같이 팔아서 브랜드 하나로
# 확정할 수 없고, 학습데이터에 없는 품목은 모델이 엉뚱하게 분류한다.
#   (2026-11-06 현대 22시 생로랑 '퀼팅 튜브백' -> 일반식품,
#    입생로랑 '리브르 오 드 빠르펭'(향수) -> 잡화/주얼리)
# 브랜드명에 키워드가 포함되면 적용: 미용 품목이면 미용, 그 외는 기본 카테고리.
# 각 항목: (브랜드 키워드, 미용 품목 패턴, 그 외 기본 카테고리)
_BEAUTY_ITEM_RE = re.compile(
    # 가방 이름과 겹치는 단어(램스킨, 크림 컬러, 베이스볼 등)는 넣지 않는다.
    r"향수|퍼퓸|빠르펭|빠퓸|오드|오 드|EDP|EDT|코롱|미스트|"
    r"립스틱|립밤|립글로|루쥬|루즈|틴트|쿠션|파운데이션|팩트|파우더|"
    r"아이섀도|섀도우|마스카라|아이라이너|블러셔|프라이머|컨실러|"
    r"세럼|앰플|에센스|토너|스킨케어|로션|아이크림|수분크림|핸드크림|"
    r"클렌징|선크림|화장품|메이크업|뷰티|샤워젤",
    re.IGNORECASE,
)
BRAND_ITEM_SPLIT = [
    (("생로랑", "입생로랑", "YSL", "SAINTLAURENT", "SAINT LAURENT"), _BEAUTY_ITEM_RE, "잡화/주얼리"),
]


# 명확한 키워드 규칙: (정규식 패턴, 확정 카테고리)
# 위에서부터 순서대로 검사하며 먼저 매칭되는 것을 채택
KEYWORD_RULES = [
    # '보험' 뒤 경계는 \b 대신 "한글이 이어지지 않음"으로 본다. \b는 '_'를 단어
    # 문자로 쳐서 "AIA파워100치매보험_M"(GS+ 데이터방송 표기)에서 안 걸렸다.
    (re.compile(r"암보험|치매보험|치료비|상해보험|운전자보험|간편보험|실손|여행자보험|건강보험|연금보험|종신보험|보험(?![가-힣])"), "보험"),
    (re.compile(r"항공권|왕복항공|패키지여행|호텔숙박권|자유숙박권|숙박권|크루즈여행"), "여행"),
    (re.compile(r"에어컨|세탁기|냉장고|비스포크|오브제\b|TV\b|티브이|건조기|스타일러|청소기|공기청정기|제습기|인덕션|식기세척기"), "가전"),
    (re.compile(r"24K|18K|목걸이|귀걸이"), "잡화/주얼리"),
    (re.compile(r"스킨기초|기초|스킨케어|기초세트|조윤주|헤어그릭스|샴푸"), "미용"),
    (re.compile(r"가발"), "리빙/주방"),
    # 발패치·풋패드·발패드는 브랜드 상관없이 리빙/주방 (2026-11-07 사람 지정)
    (re.compile(r"발\s?패치|풋\s?패드|발\s?패드"), "리빙/주방"),
]

# 미용 확정 키워드 (2026-11-07 기준 수립)
# 모델은 처음 보는 미용 단어를 만나면 확신이 없어 일반식품으로 보내는 경향이 있다
# (일반식품 클래스의 기본 점수가 가장 높음). 단어 하나로 미용이 확실한 것만 넣는다.
#   - 혼자서는 애매한 단어는 넣지 않음: 크림(아이스크림·생크림), 팩(식품 팩 단위),
#     패치·풋패드(생활용품), 오일, 향, 쿠션(쿠션 운동화), 링클(링클프리 셔츠),
#     스킨(램스킨), 팩트(임팩트), 로션·스크럽·픽서 -> 이런 건 모델 증강학습(training_augment.py)으로만 보강
#   - 브랜드 이력(학습데이터 단일카테고리 브랜드)보다 먼저 적용된다
#     (예: 김소형(건강식품 브랜드) '비단쌀 안색 선크림' -> 미용)
#   - **본품에만** 적용: 사은품·증정품 부분을 걷어낸 텍스트로 검사한다
#     (예: '냉장고 + 선크림 증정' 은 미용이 아니다) -> _beauty_main_text 참고
BEAUTY_KEYWORD_RE = re.compile(
    r"틴트|립타투|립스틱|립밤|립글로|립스테인|마스카라|아이라이너|아이섀도|파운데이션|컨실러|"
    r"선크림|선스틱|선쿠션|선세럼|선블록|쿠션팩트|커버쿠션|"
    r"세럼|앰플|에센스|토너|아이크림|수분크림|링클크림|리프팅크림|주름크림|"
    r"링클\s?패치|주름\S{0,4}패치|마스크팩|시트마스크|워시오프|클레이팩|필링젤|필링패드|"
    r"클렌징|클렌저|스킨케어|향수|퍼퓸|오드퍼퓸|오드뚜왈렛|오드투왈렛|"
    r"염색제|염색약|염색고|새치커버|샴푸|트리트먼트|"
    # 2026-11-07 사람 지정 품목 중 단어 하나로 확실한 것 (training_augment.py 목록과 같은 출처)
    r"립오일|비비크림|톤업크림|루스파우더|피니싱파우더|메이크업픽서|아이섀도우|아이브로우|속눈썹영양제|"
    r"에멀전|영양크림|재생크림|보습크림|탄력크림|슬리핑팩|토너패드|시트팩|콜라겐팩|모델링팩|선젤|"
    r"퍼퓸미스트|바디미스트|바디워시|바디로션|바디크림|바디오일|핸드크림|헤어팩|헤어에센스|헤어오일|"
    r"하이드로겔\s?패치|새치\s?커버|오트크림|율무팩|수분젤",
    re.IGNORECASE,
)
# 본품이 미용 단어를 가져도 미용이 아닌 경우 (먹는 제형, 생활용품 향 제품)
# 원칙(2026-11-07 사람 지정): 식품 단어(콜라겐·홍삼·유기농·N주분 등)와 확실한 미용 품목이
# 같이 있으면 미용. 그래서 여기엔 '먹는 제형'을 직접 말하는 표현만 둔다.
#   예) '에버콜라겐 동결건조 콜라겐 앰플 (20주분)' -> 미용 / '먹는 콜라겐 앰플' -> 미용 아님
BEAUTY_EXCLUDE_RE = re.compile(
    r"먹는|마시는|젤리|분말|드링크|타블렛|정제|캡슐(?!크림)|"
    r"섬유유연제|유연제|디퓨저|방향제|세제|탈취|"
    r"Lipbbam|LIPBBAM"               # 의류 브랜드 '립밤'
)
# 사은품·증정 표시. 이 단어가 나오면 그 앞 한 단어부터 끝까지를 사은품으로 보고 버린다.
_GIFT_MARKER_RE = re.compile(r"(\S+\s*)?(사은품|사은|증정|체험분|선물|덤으로|덤|무료\s*체험|한\s*개\s*더|1개\s*더)")
_GIFT_BRACKET_RE = re.compile(
    r"[\(\[\{（【][^\)\]\}）】]*[\)\]\}）】]"   # 괄호 문구
    r"|[★☆●○◎♥♡][^★☆●○◎♥♡]*[★☆●○◎♥♡]"           # ★전고객 드라이기★ 같은 별표 문구
    r"|전\s?고객\s*\S+"                             # '전고객 청소기' (전 고객 증정품)
)


def _beauty_main_text(product: str) -> str:
    """
    미용 키워드 검사용 본품 텍스트.
    - 괄호/대괄호 안은 버린다 (사은품·구성·프로모션 문구가 주로 들어감)
    - '+', '＋', ',' 로 나눈 첫 덩어리만 본품으로 본다
    - 첫 덩어리 안에 '증정/사은품/체험분' 등이 있으면 그 앞 단어부터 버린다
    """
    text = _GIFT_BRACKET_RE.sub(" ", str(product or ""))
    # 맨 앞 '+'(재방송 표시 등)로 빈 덩어리가 나오면 건너뛰고 첫 실제 덩어리를 쓴다
    first = next((seg for seg in re.split(r"[+＋,]", text) if seg.strip()), "")
    m = _GIFT_MARKER_RE.search(first)
    if m:
        # '증정' 앞이 비면(사은품 문구가 맨 앞) 그 뒤를 본품으로 본다
        first = first[:m.start()] if first[:m.start()].strip() else first[m.end():]
    return first.strip()


def _beauty_match(product: str) -> str:
    main = _beauty_main_text(product)
    # 제외어는 전체 상품명에서 본다 (괄호 속 브랜드 표기 등도 걸리게)
    if main and BEAUTY_KEYWORD_RE.search(main) and not BEAUTY_EXCLUDE_RE.search(str(product or "")):
        return "미용"
    return ""


# 헤어 기기(고데기·드라이기)는 가전 (2026-11-07 사람 지정).
# 학습데이터엔 글램팜 등 고데기 브랜드가 '미용'으로 들어가 있어 모델/브랜드 이력만으론 미용으로 간다.
# 본품만 본다(_beauty_main_text). 샴푸·염색제 사은품으로 고데기를 주는 경우가 많아서,
# 본품 텍스트에 미용 단어가 헤어기기 단어보다 **먼저** 나오면 미용 쪽이 본품으로 본다.
#   '타프 헤어 무선 고데기 단백질 샴푸' -> 가전 / '꾸띄르헤어 컬러체인지 샴푸 헤어고데기' -> 미용
HAIR_TOOL_RE = re.compile(
    r"고데기|매직기|드라이기|드라이어기|헤어\s?드라이어|바디\s?드라이어|드라이어(?!볼|시트)|"
    r"에어랩|헤어\s?스타일러|볼륨\s?롤러기"
)
_BEAUTY_BASIC_RE = re.compile(r"스킨기초|기초세트|스킨케어|샴푸|염색|트리트먼트")


def _hair_tool_match(product: str) -> str:
    main = _beauty_main_text(product)
    tool = HAIR_TOOL_RE.search(main)
    if not tool:
        return ""
    firsts = [m.start() for m in (BEAUTY_KEYWORD_RE.search(main), _BEAUTY_BASIC_RE.search(main)) if m]
    if firsts and min(firsts) < tool.start():
        return ""
    return "가전"


# 모델 확신도가 이보다 낮으면 '미분류'로 둔다 (화면의 '미분류' 슬라이서로 사람이 점검).
# 낮은 확신일 때 모델은 일반식품으로 몰아넣는 경향이 있다 (여행·미용 상품도 일반식품으로 감).
# 0.3~0.4 구간은 표본 확인 결과 대부분 맞게 분류돼 0.3으로 둠.
UNCLASSIFIED = "미분류"
LOW_CONFIDENCE_THRESHOLD = 0.3


# 동시출현(co-occurrence) 규칙: 신호 단어가 min_hits개 이상 '함께' 나오면 확정.
# 단어 하나로는 애매하지만 여럿이 같이 나오면 확실한 경우에 쓴다.
#   예) 'PDRN'만으로는 먹는 PDRN(건강식품)일 수도 있지만, '세럼'·'글로우'·
#       '부스터샷' 등이 같이 있으면 화장품이다.
#       (2026-11-02 LT 대원제약 "슈퍼셀 PDRN 글로우 부스터샷 PDRN 세럼"이
#        브랜드 단일카테고리(건강식품)로 분류됐던 건)
# 브랜드 강제배정과 달리 상품명 기준이라, 같은 브랜드가 건강식품을 팔면
# 그대로 건강식품으로 간다. exclude에 걸리면(먹는 제형) 규칙을 건너뛴다.
# 각 항목: (신호 단어 목록, 최소 매칭 수, 제외 패턴 or None, 카테고리)
COMBO_RULES = [
    (
        ("세럼", "앰플", "에센스", "토너", "글로우", "PDRN", "부스터샷", "스킨부스터",
         "마스크팩", "미스트", "선크림", "쿠션", "레티놀", "시카", "넥패치", "아이크림",
         "수분크림", "리프팅크림"),
        2,
        re.compile(r"먹는|젤리|스틱|분말|드링크|타블렛|정제|유산균|캡슐(?!크림)"),
        "미용",
    ),
]


_model = None
_brand_group_map = None


def _load_brand_group_map():
    """
    학습데이터를 브랜드별로 묶어서,
    단일 카테고리 그룹에서만 등장하면 {브랜드명: 그룹명},
    여러 그룹에 걸치면 {브랜드명: None} (모호 - 상품명까지 봐야 함).
    """
    global _brand_group_map
    if _brand_group_map is not None:
        return _brand_group_map

    xlsx_path = None
    for cand in _TRAINING_XLSX_CANDIDATES:
        if os.path.exists(cand):
            xlsx_path = cand
            break

    if xlsx_path is None:
        _brand_group_map = {}
        return _brand_group_map

    df = pd.read_excel(xlsx_path, sheet_name=0)
    mapping = {}
    for brand, sub in df.dropna(subset=["브랜드명", "상품중분류명"]).groupby("브랜드명"):
        groups = {_to_group(c) for c in sub["상품중분류명"].unique()}
        mapping[str(brand)] = groups.pop() if len(groups) == 1 else None
        core = extract_core_brand(str(brand))
        if core and core not in mapping:
            mapping[core] = mapping[str(brand)]

    _brand_group_map = mapping
    return _brand_group_map


def _brand_direct_category(brand: str) -> str:
    """학습데이터 상 단일 카테고리 브랜드면 그 카테고리, 아니면 빈 문자열."""
    # 숫자뿐인 브랜드('2026' 등 상품명 앞 연도가 브랜드로 잘못 들어간 것)는 브랜드로 안 본다
    # (학습데이터의 '2026' 브랜드 이력 때문에 '2026 햇 고춧가루'가 미용으로 가던 문제)
    if re.fullmatch(r"\s*\d+\s*", str(brand or "")):
        return ""
    mapping = _load_brand_group_map()
    group = mapping.get(brand)
    if group is None:
        core = extract_core_brand(brand)
        group = mapping.get(core)
    return group or ""


def _load_model():
    global _model
    if _model is None:
        _model = joblib.load(MODEL_PATH)
    return _model


def _to_group(raw_category: str) -> str:
    return GROUP_MAP.get(raw_category, raw_category)


def _split_plus_outside_brackets(text: str) -> list:
    """괄호 밖의 '+'에서만 나눈다. 맨 앞 '+'(재방송 표시 등)로 생긴 빈 덩어리는 버린다."""
    parts, buf, depth = [], [], 0
    for ch in text:
        if ch in "([{（【":
            depth += 1
        elif ch in ")]}）】" and depth:
            depth -= 1
        if ch == "+" and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    nonempty = [p for p in parts if p.strip()]
    return nonempty or [""]


def _main_item_text(brand: str, product: str) -> str:
    """
    키워드 규칙 검사용 본품 텍스트만 추출.
    - '+' 기준 첫 덩어리만 (사은품/구성품에 다른 카테고리 키워드가 섞여 오발동 방지)
    - 브랜드 부기 제거: "노랑풍선(TV)" 같은 표기가 TV 키워드에 걸리는 것 방지
    """
    # 괄호 안의 '+'는 구분자가 아니다 ("[3+3박스+세럼 1박스] 대원제약 ..." 이 "[3"만 남던 문제)
    main_part = _split_plus_outside_brackets(str(product or ""))[0].strip()
    if brand and main_part.startswith(brand):
        main_part = main_part[len(brand):].strip()
    core_brand = extract_core_brand(brand) if brand else ""
    return f"{core_brand} {main_part}".strip()


def _rule_match(text: str, product: str = None) -> str:
    # 헤어 기기는 KEYWORD_RULES의 미용(샴푸 등)보다 먼저 본다 (본품이 고데기면 가전)
    if product is not None:
        hair = _hair_tool_match(product)
        if hair:
            return hair
    for pattern, category in KEYWORD_RULES:
        if pattern.search(text):
            return category
    if product is not None:
        beauty = _beauty_match(product)
        if beauty:
            return beauty
    return _combo_match(text)


def _combo_match(text: str) -> str:
    """COMBO_RULES 검사. 공백 무시·대소문자 무시로 신호 단어 개수를 센다."""
    norm = re.sub(r"\s+", "", text).upper()
    for words, min_hits, exclude, category in COMBO_RULES:
        if exclude and exclude.search(norm):
            continue
        hits = sum(1 for w in words if w.upper() in norm)
        if hits >= min_hits:
            return category
    return ""


def _predict_with_model(brand: str, product: str) -> str:
    model = _load_model()
    text = f"{brand or ''} {product or ''}".strip()
    proba = model.predict_proba([text])[0]
    idx = proba.argmax()
    if proba[idx] < LOW_CONFIDENCE_THRESHOLD:
        return UNCLASSIFIED
    return _to_group(model.classes_[idx])


# 브랜드 추론까지 실패했을 때만 적용하는 상품명 기반 강제 매핑.
# BRAND_FORCE_MAP은 브랜드명 완전일치라서, 브랜드 칸이 비어 있고 infer_brand도
# 못 잡는 상품은 아무리 뻔해도 그냥 모델 분류로 넘어가 오분류가 난다.
# (예: NS샵플러스 '한국금자산1' - 브랜드 없음 -> 모델이 '일반식품'으로 분류)
# 키워드 규칙(1)·브랜드 확정(2)보다 뒤에 두므로 기존 분류 결과는 바꾸지 않는다.
# 비교 전에 공백을 제거하므로 '한국금자산 관리'처럼 띄어쓴 표기도 걸린다.
NO_BRAND_PRODUCT_FORCE = [
    (("한국금자산관리", "한국금자산"), "잡화/주얼리"),
]


def _no_brand_product_force(product: str) -> str:
    """브랜드를 못 잡은 상품의 상품명 키워드 강제 매핑. 없으면 빈 문자열."""
    text = (product or "").replace(" ", "")
    if not text:
        return ""
    for keywords, category in NO_BRAND_PRODUCT_FORCE:
        for keyword in keywords:
            if keyword.replace(" ", "") in text:
                return category
    return ""


def _brand_item_split(brand: str, product: str) -> str:
    """BRAND_ITEM_SPLIT 검사. 브랜드(또는 상품명 앞 [브랜드])가 해당되면 품목에 따라 확정."""
    brand_text = (brand or "").upper()
    product_text = product or ""
    for keywords, beauty_re, default_cat in BRAND_ITEM_SPLIT:
        kws = [k.upper() for k in keywords]
        hit = any(k in brand_text for k in kws)
        if not hit and not brand_text:
            # 브랜드 칸이 비어 있으면 상품명에서 브랜드 키워드 확인 (예: "[입생로랑] ...")
            hit = any(k in product_text.upper() for k in kws)
        if not hit:
            continue
        item = str(product_text).split("+")[0]
        for k in keywords:
            item = re.sub(re.escape(k), " ", item, flags=re.IGNORECASE)
        return "미용" if beauty_re.search(item) else default_cat
    return ""


def _brand_force(brand: str) -> str:
    """BRAND_FORCE_MAP 완전일치 검사. 매칭되면 확정 카테고리, 아니면 빈 문자열."""
    return BRAND_FORCE_MAP.get(brand, "")


def resolve_display_brand(brand: str, product: str) -> str:
    """
    화면에 보여줄 브랜드명 반환 (GS/CJ처럼 brand 비어있는 경우 product에서 추론).
    - brand 있으면: 괄호 부기 제거 후 반환 ("삼성(SAMSUNG)" -> "삼성")
    - brand 없으면: infer_brand로 추론 시도
    - 둘 다 실패하면 빈 문자열
    """
    if brand:
        return extract_core_brand(brand)
    inferred = infer_brand(product)
    if inferred:
        return extract_core_brand(inferred)
    return ""


def resolve_display_brand_batch(items: list) -> list:
    return [resolve_display_brand(brand, product) for brand, product in items]


def classify(brand: str, product: str) -> str:
    return classify_explain(brand, product)[0]


def classify_explain(brand: str, product: str):
    """
    (카테고리, 근거) 반환. 근거는 어느 단계에서 정해졌는지:
      brand_force / brand_split / hair_tool / keyword / beauty / combo /
      brand_direct / no_brand_force / model / ""
    소급 재분류(tools/recategorize_retro.py)가 근거별로 바꿀지 말지를 다르게 판단한다.
    """
    if not brand and not product:
        return "", ""

    # 0) 브랜드 완전일치 강제 매핑
    if brand:
        forced = _brand_force(brand)
        if forced:
            return forced, "brand_force"

    # 0-1) 브랜드 + 품목 조합 강제 매핑 (생로랑: 미용품목 -> 미용, 그 외 -> 잡화/주얼리)
    split = _brand_item_split(brand, product)
    if split:
        return split, "brand_split"

    # 1) 키워드 규칙 (본품만 검사)
    hair = _hair_tool_match(product)
    if hair:
        return hair, "hair_tool"
    text = _main_item_text(brand, product)
    for pattern, category in KEYWORD_RULES:
        if pattern.search(text):
            return category, "keyword"
    beauty = _beauty_match(product)
    if beauty:
        return beauty, "beauty"
    combo = _combo_match(text)
    if combo:
        return combo, "combo"

    # 2) 학습데이터 단일 카테고리 브랜드 직접 확정
    if brand:
        direct = _brand_direct_category(brand)
        if direct:
            return direct, "brand_direct"

    # 3) 브랜드 비어있으면 상품명에서 추론해서 보강
    effective_brand = brand
    if not effective_brand:
        effective_brand = infer_brand(product)
        if effective_brand:
            forced = _brand_force(effective_brand)
            if forced:
                return forced, "brand_force"
        else:
            forced = _no_brand_product_force(product)
            if forced:
                return forced, "no_brand_force"

    # 4) 모델 분류
    return _predict_with_model(effective_brand, product), "model"


def classify_batch(items: list) -> list:
    results = [None] * len(items)
    model_indices = []
    model_texts = []

    for i, (brand, product) in enumerate(items):
        if not brand and not product:
            results[i] = ""
            continue

        # 0) 브랜드 완전일치 강제 매핑
        if brand:
            forced = _brand_force(brand)
            if forced:
                results[i] = forced
                continue

        # 0-1) 브랜드 + 품목 조합 강제 매핑
        split = _brand_item_split(brand, product)
        if split:
            results[i] = split
            continue

        # 1) 키워드 규칙
        rule_hit = _rule_match(_main_item_text(brand, product), product)
        if rule_hit:
            results[i] = rule_hit
            continue

        # 2) 단일 카테고리 브랜드 직접 확정
        if brand:
            direct = _brand_direct_category(brand)
            if direct:
                results[i] = direct
                continue

        # 3)+4) 브랜드 추론 후 모델
        effective_brand = brand
        if not effective_brand:
            effective_brand = infer_brand(product)
            if effective_brand:
                forced = _brand_force(effective_brand)
                if forced:
                    results[i] = forced
                    continue
            else:
                forced = _no_brand_product_force(product)
                if forced:
                    results[i] = forced
                    continue
        model_indices.append(i)
        model_texts.append(f"{effective_brand or ''} {product or ''}".strip())

    if model_texts:
        model = _load_model()
        proba_matrix = model.predict_proba(model_texts)
        for j, proba in enumerate(proba_matrix):
            idx = proba.argmax()
            if proba[idx] < LOW_CONFIDENCE_THRESHOLD:
                results[model_indices[j]] = UNCLASSIFIED
            else:
                results[model_indices[j]] = _to_group(model.classes_[idx])

    return results


if __name__ == "__main__":
    samples = [
        ("삼성(SAMSUNG)", "삼성 비스포크 김치냉장고 4도어"),
        ("삼성",          "삼성 AI Q9000 에어컨 홈멀티"),
        ("삼성금거래소",   "24K 순금 더블볼륨 체인 목걸이"),
        ("삼성화재",       "다이렉트 운전자보험"),
        ("LG",            "LG 통돌이 세탁기 T19MX7A 미드 블랙"),
        ("LG생활건강",     "숨37 로시크숨 에센스"),
        ("닥터린",        "닥터린 하이퍼셀 대마종자유 12박스"),
        ("에이스바이옴",   "에이스바이옴 비에날씬 프로 12박스"),
        ("",              "원스톱프리미엄암보험_치료비플랜"),
        ("",              "[방송에서만 1박 더] 해피한 자유숙박권 총 7박"),
    ]
    for brand, product in samples:
        cat = classify(brand, product)
        print(f"[{brand or '(없음)'}] {product[:30]:30s} -> {cat}")
