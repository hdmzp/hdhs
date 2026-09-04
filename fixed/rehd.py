# -*- coding: utf-8 -*-
"""
rehd.py
현대홈쇼핑(HD) 대표 PGM(황정민쇼, 오감쇼 등)의 방송상품을 수집한다.
(순위 없음 - relt.py, recj.py와 동일 성격의 탭)

== 진행 기록 ==
1차: pgmComm 상세페이지(__NEXT_DATA__) -> 커뮤니티 게시판 구조. 폐기.
2차: searchSpexSectItem 목록 API의 itemList -> 프로그램당 최대 2개
     ("다음 방송 소개 상품" 프리뷰).
3차: 프론트가 실제로 부르는 GET /api/hf/dp/v1/shop/pgm-comm?sectId=...
     를 requests로 직접 호출 -> 401 (쿠키 선확보해도 동일).
4차: Playwright로 실제 모바일 브라우저를 띄워 pgmComm 페이지를 열고
     내부에서 나가는 pgm-comm API 응답을 가로챔 -> 성공(200). 응답을
     까본 결과:
       respData.pgmViewItem = 다음 방송의 "대표상품" 정보를 담은
       딕셔너리 1개 (리스트 아님!). 대신 정확한 방송일시 필드가 있음:
         brodDt: "20260707"
         brodDispNm: "07/07(화) 19:30 방송"   <- 그대로 쓸 라벨
         brodStrtDtmParam: "20260707193000"
       즉 이 API는 "리스트"가 아니라 "대표상품 1개 + 정확한 방송일시"를
       주는 API였음.
5차: pgmComm 페이지의 "가까운 방송 >"으로 들어가면 그 방송의 상품코드가
     전부 노출된다는 점에서 착안. 그 페이지가 쓰는 데이터는 결국
     편성표(tv-list) 데이터와 동일 -> hd_scraper.py가 이미 쓰고 있는
     공개 편성 API(md/api/cache 프록시, 인증 불필요)
       /api/hf/dp/v1/main-tv-new/tv-list?brodDt=YYYYMMDD&brodType=etv
     를 pgm-comm이 알려준 정확한 방송일시(brodDt + brodStrtDtm~brodEndDtm)
     로 필터링하면 해당 방송의 "전체 상품 라인업"(브랜드/가격 포함)이
     나온다. 검증: 2026-07-14(화) 19:30~21:45 창에서 3개,
     2026-07-09(목) 08:15~10:25 창에서 4개 확인.
6차: 5차의 '한 시간대만 본다'가 하루 2회 방송을 놓친다는 게 드러났다.
     2026-09-08 오감쇼는 08:15과 19:30 두 번 방송했는데, pgm-comm은 가장
     가까운 회차(08:15 세포랩)만 알려주므로 그 시간대만 필터링한 결과
     19:30 회차(신세계푸드 원육)가 통째로 누락됐다. 최유라쇼(relt.py)가
     방송예정 회차를 전부 받아오는 것처럼, HD도 "방송일 하루치 편성표를
     쭉 훑어" 방송 제목(brodTitl)이 그 프로그램인 회차를 전부 고른다.
     편성표는 한 방송당 대표상품 1개만 노출하고 나머지는 withItemList에
     들어있어서, 서브상품까지 펼쳐서 담는다.

== 결론 ==
소스 5개를 합쳐서 방송 라인업 전체를 수집한다 (우선순위 순):
  - tv-list 편성 API           : 방송일 하루치를 훑어 이 프로그램의 모든 회차
      (하루 2회 방송 포함)의 전체 상품 (브랜드/가격 포함) <- 6차, 메인
  - 로컬 편성 데이터(HD_live)   : hd_scraper.py가 수집해 둔 같은 편성.
      이 스크립트는 새벽 3시에 도는데, 그 시각엔 tv-list에 해당 방송의
      대표상품 1개만 있고 나머지 라인업("함께 나오는 대표상품들")은 낮에
      추가되는 경우가 있다 (예: 7/28 오감쇼 - 새벽엔 우정 1개, 낮에
      라오메뜨/리오마레 4개 추가). 전날 오전/점심(05:50/12:20)에 수집된
      로컬 편성엔 전체 라인업이 있으므로 이걸로 보충한다.
  - pgm-comm.respData.pgmViewItem: 대표상품 1개, 정확한 방송일시(brodDispNm)
  - searchSpexSectItem.itemList  : 최대 2개 (날짜 라벨 없음)
  - pgm-comm-html 스와이퍼       : 알리미 캐러셀 카드 (가격 없음)
중복 제거 키는 (방송회차 라벨, slitmCd)다. 같은 상품이 하루에 두 번
편성되는 날이 있어서(2026-09-08 오감쇼의 세포랩 - 08:15/19:30 양쪽)
상품코드만으로 묶으면 뒤 회차가 사라진다. 뒤 소스의 값으로 빈 필드
(브랜드/가격/이미지 등)는 채워 병합한다.
방송일(brodDt)은 pgm-comm이 알려준 값을 쓰고, pgm-comm 캡처가 실패하면
schedule_raw("매주 화요일 19시 30분")에서 다음 방송 날짜/시각을 계산해
폴백한다. 그 날 어느 시간대에 방송하는지는 편성표의 방송 제목으로
찾으므로, 편성표에 프로그램명이 안 붙은 경우에만 pgm-comm의 시간대
(brodStrtDtm~brodEndDtm)로 폴백한다.

== 출력 ==
homeshopping/representative_programs/HD_HJM.json (황정민)
homeshopping/representative_programs/HD_OGS.json (오감쇼)
homeshopping/representative_programs/HD_WYE.json (왕영은의 톡투게더)
homeshopping/representative_programs/HD_CEK.json (최은경쇼)

메인 JSON은 "다음 방송" 기준으로 매번 덮어쓴다.
월별 누적(월 조회용)은 fixed/build_celeb_history.py가 전 회사(HD/GS/LT/CJ)
공통으로 처리한다 -> homeshopping/representative_programs/history/{YYYY-MM}.json
{
  "company": "HD",
  "tab_name": "황정민",
  "program_title": "황정민쇼",
  "schedule_raw": "매주 목요일 08시 15분",
  "detail_link": "https://www.hmall.com/md/dpa/pgmComm?sectId=3094173",
  "products": [
    {"broadcast_date_label": "07/07(화) 19:30 방송", "brand": "", "name": "...",
     "price": 12345, "image": "https://...", "link": "https://..."},
    ...
  ]
}

== 사용법 ==
  pip install playwright requests
  playwright install chromium
  python rehd.py
"""

import os
import re
import sys
import json
import requests
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import scrape_guard
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
OUTPUT_DIR = os.path.join("homeshopping", "representative_programs")

LIST_PAGE_URL = (
    "https://www.hmall.com/md/dpa/searchSpexSectItem"
    "?sectId=3109281&dispTrtyNmCd=home_eventicon_2&dispOrdg=6"
)
IMAGE_BASE = "https://image.hmall.com/"
UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# pgm-comm: 대표상품 1개짜리 JSON API (지금까지 확인된 것)
PGM_COMM_API_PATTERN = re.compile(r"/api/hf/dp/v1/shop/pgm-comm(\?|$)")
# pgm-comm-html: 이름상 스와이퍼 마크업 자체를 서버에서 미리 렌더링해서
# 문자열로 주는 API로 추정됨. 스와이프해도 새 네트워크 요청이 안 뜨는 이유가
# 이거일 가능성이 큼 (3개 상품이 이미 HTML 문자열 안에 다 박혀있고
# 클라이언트는 그냥 넘기기만 함).
PGM_COMM_HTML_API_PATTERN = re.compile(r"/api/hf/dp/v1/shop/pgm-comm-html(\?|$)")

DAY_MAP = {
    "월요일": ("월", 0), "화요일": ("화", 1), "수요일": ("수", 2), "목요일": ("목", 3),
    "금요일": ("금", 4), "토요일": ("토", 5), "일요일": ("일", 6),
}
WEEKDAY_ABBR = ["월", "화", "수", "목", "금", "토", "일"]

# hd_scraper.py와 동일한 공개 편성 API (인증 불필요, requests로 호출 가능).
# "가까운 방송 >" 페이지가 보여주는 방송 라인업 전체가 여기서 나온다.
TV_LIST_API = (
    "https://www.hmall.com/md/api/cache?url=/api/hf/dp/v1/main-tv-new/tv-list"
    "&brodDt={brod_dt}&brodPrrgPage={page}&brodType=etv&deviceInfo=pc"
)

NAME_FIELD_CANDIDATES = ["slitmNm", "convertedSlitmNm", "goodsNm", "itemNm", "displayItemName", "name"]
PRICE_FIELD_CANDIDATES = ["sellPrc", "salePrice", "price", "bbprc"]
IMAGE_FIELD_CANDIDATES = ["orglImgNm", "simgNm", "imgUrl", "image", "thumbnail"]
CODE_FIELD_CANDIDATES = ["slitmCd", "itemCd", "goodsCd"]

# ============ 여기에 프로그램 추가 ============
# sect_id는 목록 API(searchSpexSectItem)의 spexSectId에서 이름으로 자동 조회되므로
# 새 프로그램을 넣을 땐 생략(None)해도 된다. 목록에서 이름이 안 잡히는 경우를
# 대비해 이미 확인된 ID는 그대로 박아둔다.
PROGRAMS = [
    {"tab_name": "황정민", "spex_sect_nm": "황정민쇼", "sect_id": "3094173", "output_file": "HD_HJM.json"},
    {"tab_name": "오감쇼", "spex_sect_nm": "오감쇼", "sect_id": "3094172", "output_file": "HD_OGS.json"},
    {"tab_name": "왕영은", "spex_sect_nm": "왕영은의 톡투게더", "sect_id": "2683513", "output_file": "HD_WYE.json"},
    # 최은경쇼: 2026-08 신규 편성(수 19:30). 고정PGM 수집분에서 확인된 ID.
    {"tab_name": "최은경", "spex_sect_nm": "최은경쇼", "sect_id": "3142330", "output_file": "HD_CEK.json"},
    # TODO: 오윤아 등 추가되면 여기에
]
# ==============================================


def to_image_url(path: str) -> str:
    if not path:
        return ""
    if str(path).startswith("http"):
        return path
    return IMAGE_BASE + str(path).lstrip("/")


def parse_price(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    cleaned = re.sub(r'[^\d]', '', str(value))
    return int(cleaned) if cleaned else None


def compute_this_week_date_label(schedule_raw: str) -> str:
    """정확한 brodDispNm이 없을 때(=searchSpexSectItem 쪽 아이템)의 폴백용.
    '이번주 해당 요일'이 아니라 '오늘 이후 가장 가까운 해당 요일'을 계산한다
    (예: 오늘이 토요일이면 이번주 화요일은 이미 지났으므로 다음주 화요일)."""
    today = datetime.now(KST).date()
    matched_abbr, matched_weekday = None, None
    for kr, (abbr, weekday) in DAY_MAP.items():
        if kr in schedule_raw:
            matched_abbr, matched_weekday = abbr, weekday
            break
    if matched_weekday is None:
        return "방송상품"
    days_ahead = (matched_weekday - today.weekday()) % 7
    target_date = today + timedelta(days=days_ahead)
    return f"{target_date.month}/{target_date.day}({matched_abbr}) 방송상품"


def compute_next_broadcast(schedule_raw: str):
    """schedule_raw('매주 화요일 19시 30분')에서 (다음 방송 date, 'HH:MM')을 계산.
    pgm-comm 캡처가 실패했을 때 tv-list 조회용 폴백. 파싱 실패 시 (None, None)."""
    matched_weekday = None
    for kr, (_abbr, weekday) in DAY_MAP.items():
        if kr in schedule_raw:
            matched_weekday = weekday
            break
    if matched_weekday is None:
        return None, None

    m = re.search(r"(\d{1,2})\s*시\s*(\d{1,2})\s*분", schedule_raw) \
        or re.search(r"(\d{1,2}):(\d{2})", schedule_raw)
    start_hm = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}" if m else None

    today = datetime.now(KST).date()
    days_ahead = (matched_weekday - today.weekday()) % 7
    return today + timedelta(days=days_ahead), start_hm


def make_exact_label(brod_date, start_hm: str) -> str:
    """pgm-comm의 brodDispNm과 같은 형식('07/21(화) 19:30 방송')의 라벨 생성."""
    abbr = WEEKDAY_ABBR[brod_date.weekday()]
    label = f"{brod_date.month:02d}/{brod_date.day:02d}({abbr})"
    if start_hm:
        label += f" {start_hm}"
    return label + " 방송"


def fetch_day_items(brod_dt: str) -> list:
    """tv-list 편성 API에서 brod_dt(YYYYMMDD) 하루치 편성 아이템을 전부 가져온다.
    (예전에는 여기서 방송 시간대까지 걸렀는데, 그러면 같은 날 2회 방송하는 날의
     나머지 회차를 통째로 놓친다 - 아래 select_program_slots가 프로그램명으로
     고른다. 이 함수는 '하루 편성 전체'만 책임진다.)

    대표상품(broadItemList)뿐 아니라 withItemList(같은 방송에 함께 나오는 상품)도
    펼쳐서 담는다. 편성표는 한 방송당 대표상품 1개만 노출하고 나머지는
    withItemList에 들어있어서, 이걸 안 펼치면 라인업이 1개로 보인다.
    반환값은 API 원본 아이템 리스트(부모의 방송시각/프로그램명을 물려받음)."""
    headers = {"User-Agent": UA_DESKTOP, "Referer": "https://www.hmall.com/"}
    seen = {}
    for page in range(0, 8):
        url = TV_LIST_API.format(brod_dt=brod_dt, page=page)
        try:
            data = scrape_guard.fetch_json(url, headers=headers, timeout=12,
                                           label=f"HD tv-list p{page}")
            if not data:
                continue
            items = data.get("respData", {}).get("broadItemList", []) or []
        except Exception as e:
            print(f"    -> [경고] tv-list page {page} 오류: {e}")
            continue
        for it in items:
            strt = it.get("brodStrtDtm") or ""
            code = it.get("slitmCd")
            if not strt:
                continue
            if code:
                seen[(strt, str(code))] = it
            for sub in it.get("withItemList") or []:
                sub_code = sub.get("slitmCd")
                if not sub_code:
                    continue
                child = dict(sub)
                # 서브상품엔 방송시각/프로그램명이 비어 있는 경우가 있어 부모 값을 물려준다.
                child["brodStrtDtm"] = sub.get("brodStrtDtm") or strt
                child["brodEndDtm"] = sub.get("brodEndDtm") or it.get("brodEndDtm")
                child["brodTitl"] = sub.get("brodTitl") or it.get("brodTitl")
                seen[(child["brodStrtDtm"], str(sub_code))] = child

    return list(seen.values())


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def title_matches(title: str, program_names) -> bool:
    """편성표의 방송 제목(brodTitl / 로컬 편성의 pgm)이 이 프로그램인지.
    표기 흔들림("최은경쇼" vs "최은경 쇼", "오감쇼 시즌2")을 견디도록
    공백을 뺀 뒤 포함관계까지 허용한다."""
    compact_title = compact(title)
    if not compact_title:
        return False
    for name in program_names:
        compact_name = compact(name)
        if compact_name and (compact_title == compact_name
                             or compact_name in compact_title
                             or compact_title in compact_name):
            return True
    return False


def select_program_slots(entries, program_names, start_hm=None, end_hm=None) -> dict:
    """하루 편성(entries)에서 이 프로그램의 방송 회차를 전부 골라낸다.

    entries: [(시작 'HH:MM', 종료 'HH:MM', 방송제목, payload)]
    반환: {시작시각: {"end": 종료시각, "items": [payload, ...]}}

    같은 날 2회 방송(예: 2026-09-08 오감쇼 08:15 / 19:30)이 있어도 전부 잡히게
    '방송 제목'으로 고른다. pgm-comm은 가장 가까운 회차 1개만 알려줘서,
    그 시간대만 보던 예전 방식으로는 저녁 회차(신세계푸드 원육)가 통째로
    누락됐다.

    제목으로 한 건도 못 고르면(편성표에 프로그램명이 안 붙은 경우)
    예전처럼 pgm-comm이 알려준 시간대로 폴백한다."""
    slots = {}
    for start, end, title, payload in entries:
        if not start or not title_matches(title, program_names):
            continue
        slot = slots.setdefault(start, {"end": end, "items": []})
        if end and not slot["end"]:
            slot["end"] = end
        slot["items"].append(payload)
    if slots:
        return slots

    if not start_hm:
        return {}
    for start, end, _title, payload in entries:
        if not start:
            continue
        in_window = (start_hm <= start < end_hm) if end_hm else (start == start_hm)
        if not in_window:
            continue
        slot = slots.setdefault(start, {"end": end or end_hm, "items": []})
        slot["items"].append(payload)
    return slots


HD_LIVE_DIR = os.path.join("homeshopping", "HD_live")


def load_local_day_entries(brod_date) -> list:
    """hd_scraper.py가 만든 로컬 편성(homeshopping/HD_live/{YYYY-MM}.json)에서
    brod_date 하루치를 select_program_slots용 엔트리로 반환한다.
      [(시작, 종료, 프로그램명(pgm), normalize_item과 같은 스키마의 상품)]

    tv-list 실시간 조회가 라인업 공개 전이라 일부만 반환했을 때의 보충 소스.
    파일이 없거나 날짜가 없으면 빈 리스트."""
    path = os.path.join(HD_LIVE_DIR, f"{brod_date.strftime('%Y-%m')}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            days = json.load(f).get("days", {})
    except Exception as e:
        print(f"    -> [경고] 로컬 편성({path}) 읽기 실패: {e}")
        return []

    entries = []
    for p in days.get(brod_date.strftime("%Y-%m-%d"), []):
        strt = p.get("start") or ""
        if not strt:
            continue
        link = p.get("link") or ""
        m = re.search(r"slitmCd=(\d+)", link)
        code = m.group(1) if m else None
        product = {
            "broadcast_date_label": None,  # 호출부에서 정확한 방송시각 라벨로 채움
            "brand": p.get("brand") or "",
            "name": p.get("product"),
            "price": p.get("price") or None,
            # 상품 이미지는 로컬 편성에 없어서 기존 데이터와 같은
            # image.hmall.com/{상품코드}_0.jpg 규칙으로 구성한다.
            "image": to_image_url(f"{code}_0.jpg") if code else None,
            "link": link or None,
            "_code": code,
        }
        entries.append((strt, p.get("end") or "", p.get("pgm") or "", product))
    return entries


def extract_next_data(html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("__NEXT_DATA__ 없음")
    return json.loads(m.group(1))


def fetch_list_page_map() -> dict:
    """searchSpexSectItem에서 spexSectNm -> {schedule_raw, itemList} 매핑."""
    try:
        resp = scrape_guard.get(
            LIST_PAGE_URL,
            headers={"User-Agent": UA_DESKTOP, "Referer": "https://www.hmall.com/"},
            timeout=15,
            label="HD 셀럽PGM 목록페이지",
        )
        next_data = extract_next_data(resp.text)
        pgm_list = next_data["props"]["pageProps"]["data"]["holiInfo"]["pgmShowList"]
        return {
            p.get("spexSectNm"): {
                "sect_id": str(p.get("spexSectId") or "") or None,
                "schedule_raw": p.get("sectLbl", ""),
                "itemList": p.get("itemList", []) or [],
            }
            for p in pgm_list
        }
    except Exception as e:
        print(f"[경고] 목록 API(searchSpexSectItem) 실패: {e}")
        return {}


def find_list_info(list_map: dict, spex_sect_nm: str) -> dict:
    """목록 API 매핑에서 프로그램명으로 항목을 찾는다. 완전일치가 우선이고,
    표기 흔들림("최은경쇼" vs "최은경 쇼")에 대비해 공백을 뺀 부분일치까지
    허용한다. 못 찾으면 빈 dict."""
    if spex_sect_nm in list_map:
        return list_map[spex_sect_nm]
    target = re.sub(r"\s+", "", spex_sect_nm or "")
    if not target:
        return {}
    for name, info in list_map.items():
        compact = re.sub(r"\s+", "", name or "")
        if compact and (compact == target or target in compact or compact in target):
            return info
    return {}


def normalize_item(item: dict, date_label: str) -> dict:
    def first_present(cands):
        for f in cands:
            if item.get(f) not in (None, ""):
                return item[f]
        return None

    code = first_present(CODE_FIELD_CANDIDATES)
    link = f"https://www.hmall.com/md/pda/itemPtc?slitmCd={code}" if code else item.get("link")
    image_raw = first_present(IMAGE_FIELD_CANDIDATES)
    brand_raw = item.get("brandNm") or item.get("repBrandNm") or item.get("brndNm")

    return {
        "broadcast_date_label": date_label,
        "brand": brand_raw.strip() if isinstance(brand_raw, str) else "",
        "name": first_present(NAME_FIELD_CANDIDATES),
        "price": parse_price(first_present(PRICE_FIELD_CANDIDATES)),
        "image": to_image_url(image_raw) if image_raw else None,
        "link": link,
        "_code": code,  # 중복제거용, 최종 출력 전에 제거됨
    }


def capture_pgm_comm_responses(page, detail_link: str, sect_id: str, timeout_ms: int = 15000):
    """pgm-comm(JSON, 대표상품 1개)와 pgm-comm-html(스와이퍼 마크업 추정)
    두 응답을 모두 가로챈다."""
    captured = {}

    def on_response(response):
        url = response.url
        if PGM_COMM_HTML_API_PATTERN.search(url) and f"sectId={sect_id}" in url:
            try:
                captured["html_data"] = response.json()
            except Exception:
                try:
                    captured["html_text"] = response.text()
                except Exception as e:
                    captured["html_error"] = str(e)
            captured["html_url"] = url
        elif PGM_COMM_API_PATTERN.search(url) and f"sectId={sect_id}" in url:
            try:
                captured["data"] = response.json()
            except Exception as e:
                captured["error"] = str(e)
            captured["url"] = url

    page.on("response", on_response)
    page.goto(detail_link, wait_until="domcontentloaded", timeout=timeout_ms)

    waited = 0
    step = 300
    # pgm-comm과 pgm-comm-html 둘 다(또는 타임아웃까지) 기다린다.
    while ("data" not in captured or ("html_data" not in captured and "html_text" not in captured)) \
            and "error" not in captured and waited < timeout_ms:
        page.wait_for_timeout(step)
        waited += step

    page.remove_listener("response", on_response)
    return captured


def find_swiper_html(obj, depth: int = 0):
    """중첩된 JSON 어디에 들어있든 스와이퍼 마크업 문자열을 찾아낸다.

    기존에는 respData 바로 아래 한 겹만 뒤졌는데, 응답 구조가 바뀌면서
    (2026-08 기준 HD 3개 프로그램 전부) 마크업을 못 찾고 있다. 한 단계
    스키마 변경 정도는 견디도록 재귀로 훑는다."""
    if depth > 6:
        return None
    if isinstance(obj, str):
        return obj if ("swiper-slide" in obj or "data-slitm-cd" in obj) else None
    values = obj.values() if isinstance(obj, dict) else (obj if isinstance(obj, list) else [])
    for v in values:
        found = find_swiper_html(v, depth + 1)
        if found:
            return found
    return None


def parse_swiper_items_from_html(html: str, date_label: str) -> list:
    """pgm-comm-html 응답(또는 그 안의 html 필드)에서 swiper-slide 카드들을 파싱.
    DOM에서 확인된 구조: <div class="swiper-slide ..."><img alt="상품명" src="...">
    ...<div title="상품명">상품명</div><div>날짜 방송</div>...
    <a data-slitm-cd="코드" ...></a></div>
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for slide in soup.select(".swiper-slide"):
        alrim_btn = slide.select_one("[data-slitm-cd]")
        code = alrim_btn.get("data-slitm-cd") if alrim_btn else None

        img = slide.select_one("img")
        name = img.get("alt") if img and img.get("alt") else None
        image = img.get("src") if img else None

        date_div_text = None
        for div in slide.find_all("div"):
            text = div.get_text(strip=True)
            if "방송" in text and re.search(r"\d{2}/\d{2}", text):
                date_div_text = text
                break

        if not code and not name:
            continue

        link = f"https://www.hmall.com/md/pda/itemPtc?slitmCd={code}" if code else None
        items.append({
            "broadcast_date_label": date_div_text or date_label,
            "brand": "",
            "name": name,
            "price": None,  # 이 카드엔 가격이 없음 (알리미 캐러셀로 추정)
            "image": image if (image and image.startswith("http")) else to_image_url(image),
            "link": link,
            "_code": code,
        })
    return items


def collect_lineup_products(brod_date, program_names, brod_start=None, brod_end=None) -> list:
    """방송일(brod_date) 하루치 편성표를 훑어 이 프로그램의 모든 회차 상품을 모은다.

    ("가까운 방송 >" 페이지가 보여주는 것과 같은 데이터. 여기가 메인 소스)
    pgm-comm은 '가장 가까운 회차' 하나만 알려주기 때문에, 그 시간대만 보면
    같은 날 2회 방송하는 날의 나머지 회차가 통째로 빠진다
    (2026-09-08 오감쇼: 08:15 세포랩은 수집됐는데 19:30 신세계푸드 원육 누락).
    그래서 하루치 편성을 다 받아 방송 제목으로 이 프로그램 회차를 고른다."""
    products = []

    api_entries = [
        (it.get("brodStrtDtm") or "", it.get("brodEndDtm") or "",
         it.get("brodTitl") or "", it)
        for it in fetch_day_items(brod_date.strftime("%Y%m%d"))
    ]
    api_slots = select_program_slots(api_entries, program_names, brod_start, brod_end)
    print(f"    -> tv-list {brod_date} 편성에서 '{program_names[0]}' 회차 "
          f"{len(api_slots)}개 선택: {', '.join(sorted(api_slots)) or '없음'}")
    for start_hm in sorted(api_slots):
        exact_label = make_exact_label(brod_date, start_hm)
        slot_items = api_slots[start_hm]["items"]
        print(f"    -> [{exact_label}] 라인업 {len(slot_items)}개")
        for it in slot_items:
            products.append(normalize_item(it, exact_label))
            print(f"         · [{it.get('brndNm') or ''}] {(it.get('slitmNm') or '')[:40]}")
    if not api_slots:
        print(f"    -> [경고] tv-list에서 이 프로그램 방송을 못 찾음 (편성 미공개일 수 있음)")

    # 로컬 편성(HD_live)으로 보충 - 새벽 실행 시점엔 tv-list에 라인업이
    # 다 공개되기 전이라 일부만 잡히는 경우가 있다. 여기도 하루치를 훑는다.
    local_entries = load_local_day_entries(brod_date)
    local_slots = select_program_slots(local_entries, program_names, brod_start, brod_end)
    for start_hm in sorted(local_slots):
        exact_label = make_exact_label(brod_date, start_hm)
        slot_items = local_slots[start_hm]["items"]
        print(f"    -> 로컬 편성(HD_live) [{exact_label}] {len(slot_items)}개")
        for it in slot_items:
            it["broadcast_date_label"] = exact_label
            products.append(it)
            print(f"         · [{it['brand']}] {(it['name'] or '')[:40]}")

    return products


def merge_sources(lineup_products, pgm_comm_products,
                  itemlist_products, swiper_products) -> list:
    """소스 간 같은 상품(같은 slitmCd)이 중복으로 오므로 병합한다.
    우선순위: 라인업(전체+브랜드/가격) > pgm-comm(정확 라벨) > itemList > 스와이퍼.
    앞 소스 항목이 기준이 되고, 뒤 소스의 값은 비어있는 필드만 채운다
    (예: 라인업엔 이미지가 없을 수 있는데 itemList 이미지로 보충).

    주의: 같은 상품이 하루에 두 번 편성되는 날이 있다(2026-09-08 오감쇼의
    세포랩 - 08:15/19:30 양쪽). 그래서 라인업은 상품코드가 아니라
    (방송회차 라벨, 상품코드)로 중복을 제거해 회차별로 남긴다."""

    def fill_empty(base: dict, other: dict):
        for field in ("brand", "name", "price", "image", "link"):
            if not base.get(field) and other.get(field):
                base[field] = other[field]
        # 폴백 라벨('... 방송상품')만 있는 항목에 정확한 방송시각 라벨이 오면 교체
        if "방송상품" in (base.get("broadcast_date_label") or "") \
                and re.search(r"\d{1,2}:\d{2}", other.get("broadcast_date_label") or ""):
            base["broadcast_date_label"] = other["broadcast_date_label"]

    def identity(p: dict) -> str:
        return str(p.get("_code") or p.get("name"))

    merged = {}
    order = []
    by_identity = {}

    def put(p: dict):
        key = ((p.get("broadcast_date_label") or ""), identity(p))
        if key in merged:
            fill_empty(merged[key], p)
            return
        merged[key] = p
        order.append(key)
        by_identity.setdefault(identity(p), []).append(p)

    for p in lineup_products:
        put(p)

    # 라인업 외 소스는 회차 정보가 없거나(itemList/스와이퍼) 대표 회차 하나뿐
    # (pgm-comm)이라, 이미 라인업에 있는 상품이면 회차별 항목에 값만 채우고
    # 새 항목으로는 넣지 않는다.
    for p in pgm_comm_products + itemlist_products + swiper_products:
        existing = by_identity.get(identity(p))
        if existing:
            for base in existing:
                fill_empty(base, p)
            continue
        put(p)

    deduped = [merged[k] for k in order]
    for p in deduped:
        p.pop("_code", None)
    return deduped


def crawl_hd_program(page, config: dict, list_map: dict):
    tab_name = config["tab_name"]

    list_info = find_list_info(list_map, config["spex_sect_nm"])
    # sect_id는 설정값 우선, 없으면 목록 API가 알려준 spexSectId를 쓴다
    # (신규 프로그램은 ID를 미리 알 수 없으므로 이름으로 찾아온다).
    sect_id = config.get("sect_id") or list_info.get("sect_id")
    if not sect_id:
        print(f"\n===== [{tab_name}] sectId를 찾지 못해 건너뜀 "
              f"(목록 API에 '{config['spex_sect_nm']}' 없음) =====")
        return None

    detail_link = f"https://www.hmall.com/md/dpa/pgmComm?sectId={sect_id}"
    schedule_raw = list_info.get("schedule_raw", "")
    fallback_label = compute_this_week_date_label(schedule_raw) if schedule_raw else "방송상품"

    print(f"\n===== [{tab_name}] (sectId={sect_id}) 수집 시작 =====")

    # 소스별로 모았다가 우선순위 순(라인업 -> pgm-comm -> itemList -> 스와이퍼)으로
    # 병합한다. 앞 소스가 기준이 되고, 뒤 소스는 빈 필드만 채운다.
    lineup_products = []
    pgm_comm_products = []
    itemlist_products = []
    swiper_products = []

    # searchSpexSectItem.itemList (최대 2개, 날짜 라벨 없음 -> 폴백 라벨 사용)
    for item in list_info.get("itemList", []):
        itemlist_products.append(normalize_item(item, fallback_label))

    # pgm-comm(대표상품 1개, JSON) + pgm-comm-html(스와이퍼 마크업 추정) 둘 다 캡처.
    captured = capture_pgm_comm_responses(page, detail_link, sect_id)

    # --- pgm-comm-html: 스와이퍼 카드 파싱 시도 ---
    html_content = None
    if "html_text" in captured:
        html_content = captured["html_text"]
    elif "html_data" in captured:
        # 응답 어디에 박혀 있든(respData 바로 아래든 더 깊은 곳이든) 찾아낸다
        html_content = find_swiper_html(captured["html_data"])

    if html_content:
        debug_html_path = os.path.join(OUTPUT_DIR, f"_debug_pgm_comm_html_{sect_id}.html")
        with open(debug_html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"    -> [디버그] pgm-comm-html 원본 저장: {debug_html_path}")

        swiper_items = parse_swiper_items_from_html(html_content, fallback_label)
        print(f"    -> pgm-comm-html 스와이퍼에서 {len(swiper_items)}개 카드 파싱됨")
        for it in swiper_items:
            print(f"         · [{it['broadcast_date_label']}] {it['name']}")
        swiper_products.extend(swiper_items)
    elif "html_url" in captured:
        print(f"    -> [경고] pgm-comm-html 응답은 잡았는데 파싱 가능한 HTML을 못 찾음")
        print(f"       캡처된 키: {list(captured.keys())}")
        # 응답 구조가 바뀌었다는 뜻이라, 원본을 남겨야 다음에 고칠 수 있다.
        # (경고만 찍고 끝내면 실제 응답이 어떻게 생겼는지 영영 알 수 없다)
        dump_path = os.path.join(OUTPUT_DIR, f"_debug_pgm_comm_html_{sect_id}.json")
        try:
            with open(dump_path, "w", encoding="utf-8") as f:
                json.dump(captured.get("html_data"), f, ensure_ascii=False, indent=2)
            print(f"       -> [디버그] 원본 응답 저장: {dump_path}")
        except (OSError, TypeError, ValueError) as e:
            print(f"       -> [경고] 원본 응답 저장 실패: {e}")
    else:
        print(f"    -> [경고] pgm-comm-html 응답을 못 잡음")

    # --- pgm-comm: 대표상품 1개 (JSON) + 정확한 방송일시 확보 ---
    brod_date = None      # datetime.date: 다음 방송 날짜
    brod_start = None     # "HH:MM"
    brod_end = None       # "HH:MM"

    if "error" in captured:
        print(f"    -> [경고] pgm-comm JSON 파싱 실패: {captured['error']}")
    elif "data" not in captured:
        print(f"    -> [경고] pgm-comm 응답을 못 잡음 (타임아웃)")
    else:
        debug_path = os.path.join(OUTPUT_DIR, f"_debug_pgm_comm_{sect_id}.json")
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(captured["data"], f, ensure_ascii=False, indent=2)
        print(f"    -> [디버그] pgm-comm 원본 응답 저장: {debug_path}")

        resp_data = (captured["data"] or {}).get("respData") or {}

        pgm_view_item = resp_data.get("pgmViewItem")
        if isinstance(pgm_view_item, dict) and pgm_view_item.get("slitmNm"):
            date_label = pgm_view_item.get("brodDispNm") or fallback_label
            pgm_comm_products.append(normalize_item(pgm_view_item, date_label))
            print(f"    -> pgm-comm 대표상품 1개 확보: [{date_label}] {pgm_view_item.get('slitmNm')[:25]}...")

            # 정확한 방송일시: brodDt="20260721", brodStrtDtm="19:30", brodEndDtm="21:45"
            brod_dt_raw = str(pgm_view_item.get("brodDt") or "")
            if re.fullmatch(r"\d{8}", brod_dt_raw):
                brod_date = datetime.strptime(brod_dt_raw, "%Y%m%d").date()
            hm = re.compile(r"^\d{2}:\d{2}$")
            if hm.match(str(pgm_view_item.get("brodStrtDtm") or "")):
                brod_start = pgm_view_item["brodStrtDtm"]
            if hm.match(str(pgm_view_item.get("brodEndDtm") or "")):
                brod_end = pgm_view_item["brodEndDtm"]
        else:
            print(f"    -> [경고] pgm-comm 응답에 pgmViewItem이 없음")

    # pgm-comm이 실패했으면 schedule_raw에서 다음 방송 날짜/시각을 계산해 폴백
    if brod_date is None or brod_start is None:
        fb_date, fb_start = compute_next_broadcast(schedule_raw)
        brod_date = brod_date or fb_date
        brod_start = brod_start or fb_start

    # --- 편성표(tv-list + 로컬 HD_live)에서 그 날 이 프로그램 회차를 전부 수집 ---
    if brod_date:
        lineup_products = collect_lineup_products(
            brod_date, [config["spex_sect_nm"], tab_name], brod_start, brod_end)
    else:
        print(f"    -> [경고] 방송일을 알 수 없어 편성표 라인업 조회 생략")

    deduped = merge_sources(lineup_products, pgm_comm_products,
                            itemlist_products, swiper_products)

    print(f"    -> 최종 상품 {len(deduped)}개 (병합/중복 제거 후)")

    return {
        "company": "HD",
        "tab_name": tab_name,
        "program_title": config["spex_sect_nm"],
        "schedule_raw": schedule_raw,
        "detail_link": detail_link,
        "products": deduped,
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("[HD] 목록 API(searchSpexSectItem) 수집 중...")
    list_map = fetch_list_page_map()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**p.devices["iPhone 13"])
        page = context.new_page()

        for config in PROGRAMS:
            result = crawl_hd_program(page, config, list_map)
            if result is None:
                # sectId 미확인(아직 목록에 안 올라온 신규 PGM 등).
                # 기존 파일이 있으면 덮어쓰지 않고 그대로 둔다.
                continue

            output_path = os.path.join(OUTPUT_DIR, config["output_file"])
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            print(f"[성공] [{config['tab_name']}] 저장 완료: {output_path}")
            print(f"  - 총 수집된 상품 수: {len(result['products'])}개")

        browser.close()


if __name__ == "__main__":
    main()