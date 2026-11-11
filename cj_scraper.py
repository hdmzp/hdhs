# -*- coding: utf-8 -*-
"""
CJ온스타일(CJ) 편성표 수집기

TV LIVE(라이브방송) / TV+(데이터방송) 편성을 공통 스키마로 변환해 저장한다.

== 저장 구조 ==
homeshopping/
├── CJ_live/{YYYY-MM}.json   TV LIVE
└── CJ_data/{YYYY-MM}.json   TV+(데이터방송)

== 공통 스키마 (월 파일 안에 날짜별 누적) ==
{
  "company": "CJ", "broadcast": "live", "month": "2026-06",
  "days": {
    "2026-06-22": [
      {"start":"08:00","end":"09:59","brand":"미우미우","product":"하프문 숄더백",
       "price":39000,"link":"https://..."}
    ]
  }
}

== 수집 정책 ==
오늘 기준 -1일 ~ +5일(7일)을 매번 수집.
과거(오늘 이전) 날짜가 이미 기록돼 있으면 다시 안 건드리고 보존, 오늘+미래만 갱신.

== 브랜드 추출 ==
CJ API(tvSchedule)의 brandName은 거의 항상 비어있고(None), 상품 상세페이지(/p/item/{itemCd})는
JS로 렌더링되는 SPA라 requests로는 화면에 보이는 브랜드를 직접 파싱할 수 없다.
대신 itemCd 기준으로 대표 브랜드를 조회하는 별도 REST API가 있다:
  GET https://display-frontapi.cjonstyle.com/itemDetails/{itemCd}/repBrandTag
  -> {"result": {"repBrandCd": "00009621", "repBrandNm": "데이즈온", ...}, "status": 200}
브랜드가 없는(노브랜드) 상품은 result가 null로 내려온다(정상 동작, 화면에도 브랜드 미표시).
fetch_cj()에서 itemCd가 있을 때마다 이 API를 호출해 repBrandNm을 brand로 채우고,
호출 실패/null/조회 안 됨인 경우에만 add_categories()의 상품명(itemNm) 기반 추론
(categorize.resolve_display_brand_batch)으로 백업 추정한다.
두 방식 모두 실패하면 brand는 빈 문자열로 저장되고, 프론트엔드는 빈 브랜드를
표시하지 않는다(HD/LT와 동일 처리 방식).

== 사용법 ==
  pip install requests
  python cj_scraper.py
"""
import os
import json
import time
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from categorize import classify_batch, resolve_display_brand_batch
from clean_product import clean_product_name
from tools.lavangba_gapfill import fill_gaps  # 자사 API가 빼고 주는 방송(여행 등)을 라방바로 보충

# 설정값
KST = timezone(timedelta(hours=9))
OUTPUT_DIR = "homeshopping"
REQUEST_DELAY = 0.8
REPBRAND_WORKERS = 6  # repBrandTag 동시 조회 수 
DAYS_RANGE = range(-1, 6)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def today_kst():
    return datetime.now(KST)

def parse_price(v):
    if v is None: return 0
    if isinstance(v, (int, float)): return int(v)
    s = str(v).replace(",", "").strip()
    if not s: return 0
    try: return int(float(s))
    except ValueError: return 0

def add_categories(programs):
    """
    1) 브랜드 보강: brand가 비어있으면 product에서 추론해 화면 표시용 brand로 채움
       (HD/LT처럼 브랜드가 별도로 보이도록 - resolve_display_brand가 추론 실패시
        빈 문자열을 반환하므로, 추론 안 되는 진짜 노브랜드 상품은 그대로 빈 값 유지)
    2) 분류: 원본 상품명 + (보강된) 브랜드로 카테고리 예측
       (분류 모델은 원본 패턴으로 학습됐으므로 정제 전 텍스트를 사용)
    3) 분류가 끝난 뒤 product 필드를 화면 표시용으로 정제
    """
    if not programs: return programs

    raw_pairs = [(p["brand"], p["product"]) for p in programs]

    # 1) 화면 표시용 브랜드 보강 (브랜드 없으면 상품명에서 추론)
    display_brands = resolve_display_brand_batch(raw_pairs)
    for p, db in zip(programs, display_brands):
        if not p["brand"] and db:
            p["brand"] = db

    # 2) 분류 (보강된 brand + 원본 product 사용 - 추론된 브랜드가 있으면 분류 정확도 향상)
    pairs_for_model = [(p["brand"], p["product"]) for p in programs]
    categories = classify_batch(pairs_for_model)

    # 3) 정제
    for p, cat in zip(programs, categories):
        p["category"] = cat
        p["product"] = clean_product_name(p["product"])
    return programs

def fetch_repbrand_batch(item_cds):
    """
    itemCd 목록에 대해 대표 브랜드명을 조회한다.
    GET https://display-frontapi.cjonstyle.com/itemDetails/{itemCd}/repBrandTag
    -> {"result": {"repBrandNm": "데이즈온", ...}} 또는 {"result": null} (노브랜드 상품, 정상)
    실패/타임아웃 시 해당 itemCd는 결과에서 빠지며, 호출부에서 빈 문자열로 처리한다
    (add_categories()의 상품명 기반 추론이 백업으로 작동).
    중복 itemCd는 한 번만 조회(같은 상품이 같은 날 여러 회 편성되는 경우 대비).
    """
    headers = {"User-Agent": UA, "Referer": "https://item.cjonstyle.com/nfront/item/"}

    def one(item_cd):
        url = f"https://display-frontapi.cjonstyle.com/itemDetails/{item_cd}/repBrandTag"
        try:
            r = requests.get(url, headers=headers, timeout=8)
            r.raise_for_status()
            result = r.json().get("result")
            return item_cd, (result or {}).get("repBrandNm") or ""
        except Exception as e:
            print(f"    [CJ] repBrandTag 오류 (itemCd={item_cd}): {e}")
            return item_cd, ""

    # 2026-11-11 방송상품 전부를 넣으면서 조회 건수가 몇 배로 늘어(하루 100~300개)
    # 순차 조회(0.3초 간격)로는 CJ 단계가 10분을 넘겨 동시 6건으로 나눠 조회한다.
    cds = list(dict.fromkeys(c for c in item_cds if c))  # 중복 제거, 순서 유지
    brand_map = {}
    with ThreadPoolExecutor(max_workers=REPBRAND_WORKERS) as pool:
        for item_cd, brand in pool.map(one, cds):
            if brand:
                brand_map[item_cd] = brand
    return brand_map

# 고정PGM(셀럽/네임드 쇼) 판별 키워드. CJ는 모든 편성에 pgmNm이 있어서
# (예: "건강식품 1부", "Weekly Best") HD처럼 제목 유무로는 못 거르고,
# 이름 있는 쇼만 골라 itemList의 나머지 방송상품을 브랜드별로 추가한다.
# 새 PGM이 생기면 여기에 키워드만 추가하면 됨.
# 셀럽PGM 수집기(fixed/recj.py)가 등록한 프로그램은 반드시 여기에도 있어야 한다.
# 편성표에 pgm이 안 붙으면 뱃지가 안 나올 뿐 아니라, celeb_day_sweep이 pgm으로
# 회차를 찾으므로 셀럽PGM 안전망까지 같이 죽는다 (2026-09-20 소이현의 겟잇스타일
# 20:30 회차 - pgmShop이 그날 회차를 아예 안 줬는데 편성표 보강도 못 돌아서
# 홈쇼핑 탭 뱃지와 셀럽PGM 탭 회차가 통째로 비었다).
# tools/test_cj_pgm_keywords.py가 이 짝을 지킨다.
CJ_PGM_KEYWORDS = (
    "최화정", "굿 라이프", "굿라이프", "강주은", "김창옥",
    "이승연", "조윤주", "지완스", "스튜디오B", "탑쇼",
    "김신영",
    # 소이현의 겟잇스타일: 격주 금요일 21:45가 기본이지만 실제로는 요일·시간이
    # 수시로 바뀐다 (2026-09 실측: 04일 금 22:45 / 15일 화 20:00 / 17일 목 20:00 /
    # 18일 금 22:45 / 20일 일 20:30). 편성표 pgmNm은 "소이현의 겟잇스타일".
    "소이현", "겟잇스타일",
    # index.html의 HS_PGM_BADGE_RULES에는 뱃지가 이미 있었는데 키워드만 빠져 있던
    # CJ 고정PGM (서아랑의 아쇼라 = 목 08:15, 동가게 = 목 20:45 / 토 10:20).
    "아쇼라", "서아랑", "동가게",
    # 2026-10 신규 CJ PGM (pgmShop 100082)
    "림잇티드",
    # 2026-10-28 모니터링 편입 (pgmShop 100033)
    "바이림",
)


def item_to_program(item, start_str, end_str, brand="", pgm=""):
    """tvSchedule itemList 항목을 공통 스키마로 변환.
    pgm: 고정PGM 방송이면 프로그램명(pgmNm) - 프론트 뱃지 표시용."""
    item_cd = item.get("itemCd", "")
    chn_cd = item.get("chnCd", "")
    link = (f"https://display.cjonstyle.com/p/item/{item_cd}?channelCode={chn_cd}"
            if item_cd else "")
    prog = {
        "start": start_str,
        "end": end_str,
        "brand": brand or item.get("brandName") or "",
        "product": item.get("itemNm", "") or "",
        "price": parse_price(item.get("salePrice")),
        "link": link,
        "_item_cd": item_cd,  # repBrandTag 조회용 임시 필드, 마지막에 제거
    }
    if pgm:
        prog["pgm"] = pgm
    return prog


def fetch_cj(date_compact, broad_param):
    """CJ tvSchedule 1일치. 방송(program)마다 itemList의 방송상품을 전부 편성 항목으로 넣는다
    (2026-11-11 변경: 예전엔 대표상품 1개 + 고정PGM만 '새 브랜드' 첫 상품을 추가해서,
     같은 브랜드 상품만 있는 바이림(락포트 3종) 같은 방송이 1개로 보였다).
    같은 방송 안에서 상품명이 같은 항목은 한 번만 넣는다. 화면은 같은 시간대 상품을 접어서 보여준다."""
    headers = {"User-Agent": UA, "Referer": "https://display.cjonstyle.com/p/tv/tvSchedule"}
    url = (f"https://display.cjonstyle.com/c/rest/tv/tvSchedule"
           f"?bdDt={date_compact}&isMobile=false&broadType={broad_param}&isEmployee=false")
    programs = []
    try:
        r = requests.get(url, headers=headers, timeout=12)
        r.raise_for_status()
        prog_list = r.json().get("result", {}).get("programList", []) or []
        for pg in prog_list:
            start_ms = pg.get("bdStrDtm")
            end_ms = pg.get("bdEndDtm")
            if not start_ms or not end_ms: continue

            start_str = datetime.fromtimestamp(start_ms / 1000, tz=KST).strftime("%H:%M")
            end_str = datetime.fromtimestamp(end_ms / 1000, tz=KST).strftime("%H:%M")

            # CJ는 모든 편성에 pgmNm이 있으므로(건강식품 1부 등) 네임드 쇼
            # 키워드에 걸리는 고정PGM만 pgm 필드를 남긴다 (프론트 뱃지용)
            pgm_nm = (pg.get("pgmNm") or "").strip()
            pgm = pgm_nm if any(k in pgm_nm for k in CJ_PGM_KEYWORDS) else ""

            items = pg.get("itemList", []) or [{}]
            seen_names = set()
            for item in items:
                name = (item.get("itemNm") or "").strip()
                if name and name in seen_names:
                    continue
                seen_names.add(name)
                programs.append(item_to_program(item, start_str, end_str, pgm=pgm))
    except Exception as e:
        print(f"    [CJ] 오류: {e}")

    # 브랜드명이 비어 있는 상품만 대표 브랜드 조회 (실행 동안 itemCd별 캐시)
    if programs:
        need = [p["_item_cd"] for p in programs if not p["brand"] and p["_item_cd"] not in _REPBRAND_CACHE]
        _REPBRAND_CACHE.update({cd: "" for cd in need})
        _REPBRAND_CACHE.update(fetch_repbrand_batch(need))
        for p in programs:
            if not p["brand"]:
                p["brand"] = _REPBRAND_CACHE.get(p["_item_cd"], "")

    for p in programs:
        p.pop("_item_cd", None)

    programs.sort(key=lambda x: x["start"])
    return programs


_REPBRAND_CACHE = {}  # itemCd -> 대표 브랜드 (같은 상품이 여러 날·여러 방송에 나와도 한 번만 조회)

BROADCASTS = [("live", "live"), ("data", "plus")]
# 라방바 채널 ID - CJ tvSchedule API가 빼고 주는 방송(일요일 저녁 여행상품 등)을 채우는 데 쓴다
LAVANGBA_PLATFORM = {"live": "hs_cjonstyle", "data": "hs_cjonstyleplus"}

def load_month(sub_dir, ym):
    path = os.path.join(sub_dir, f"{ym}.json")
    if os.path.exists(path):
        try: return json.load(open(path, encoding="utf-8")).get("days", {})
        except: return {}
    return {}

def main():
    base = today_kst()
    today_str = base.strftime("%Y-%m-%d")

    for broadcast, broad_param in BROADCASTS:
        sub_dir = os.path.join(OUTPUT_DIR, f"CJ_{broadcast}")
        os.makedirs(sub_dir, exist_ok=True)
        month_data = {}

        for offset in DAYS_RANGE:
            d = base + timedelta(days=offset)
            date_compact = d.strftime("%Y%m%d")
            date_dash = d.strftime("%Y-%m-%d")
            ym = d.strftime("%Y-%m")
            if ym not in month_data:
                month_data[ym] = load_month(sub_dir, ym)
            days = month_data[ym]

            is_past = date_dash < today_str
            if is_past and days.get(date_dash):
                print(f"[CJ_{broadcast}] {date_dash}: 이미 기록됨, 건너뜀")
                continue

            print(f"[CJ_{broadcast}] {date_dash} 수집 중...")
            programs = fetch_cj(date_compact, broad_param)
            if programs:
                fill_gaps(programs, d, LAVANGBA_PLATFORM[broadcast], f"CJ_{broadcast}")
            programs = add_categories(programs)

            if is_past and not programs:
                print(f"  -> 0개 (과거, 기존값 유지)")
                time.sleep(REQUEST_DELAY)
                continue

            days[date_dash] = programs
            print(f"  -> {len(programs)}개 편성")
            time.sleep(REQUEST_DELAY)

        for ym, days in month_data.items():
            if not days: continue
            out_path = os.path.join(sub_dir, f"{ym}.json")
            sorted_days = {k: days[k] for k in sorted(days)}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({"company": "CJ", "broadcast": broadcast, "month": ym,
                           "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
                           "days": sorted_days},
                          f, ensure_ascii=False, indent=2)
            print(f"  저장: {out_path}")
    print("\n완료.")

if __name__ == "__main__":
    main()
