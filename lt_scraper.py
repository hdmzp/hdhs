# -*- coding: utf-8 -*-
"""
롯데홈쇼핑(LT) 편성표 수집기

라이브TV(라이브방송) / 원TV(데이터방송) 편성을 공통 스키마로 변환해 저장한다.

== 저장 구조 ==
homeshopping/
├── LT_live/{YYYY-MM}.json   라이브TV
└── LT_data/{YYYY-MM}.json   원TV(데이터방송)

== 공통 스키마 (월 파일 안에 날짜별 누적) ==
{
  "company": "LT", "broadcast": "live", "month": "2026-06",
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

== 날짜 처리 주의 ==
롯데 API는 bdDate로 요청해도 전날 밤~당일 새벽, 당일 밤~다음날 새벽 방송까지
함께 내려준다. 응답의 stime(시작시각)만으로는 어느 날짜에 속하는지 알 수 없으므로,
각 항목의 sdate(방송 시작일) 필드를 실제 방송일로 사용해 요청한 날짜와 일치하는
것만 채택한다. 이를 무시하면 같은 시간대 방송이 인접한 두 날짜에 중복으로 잡힌다.

== 사용법 ==
  pip install requests
  python lt_scraper.py
"""

import os
import re
import json
import time
import requests
from datetime import datetime, timedelta, timezone
from categorize import classify_batch
from clean_product import clean_product_name
from infer_brand import pick_brand_from_prefix, is_marketing_copy

_BRACKET_RE = re.compile(r"\[[^\[\]]*\]|\([^()]*\)")

KST = timezone(timedelta(hours=9))
OUTPUT_DIR = "homeshopping"
REQUEST_DELAY = 0.4
DAYS_RANGE = range(-1, 6)  # 어제 ~ +5일

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def today_kst():
    return datetime.now(KST)


def parse_price(v):
    """가격을 원 단위 정수로 정규화. '69,900' / 69900 / None 등 처리."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).replace(",", "").strip()
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def resolve_brand(raw_brand, product_name):
    """편성 데이터의 브랜드 값을 정제한다.

    브랜드 필드에 "정상가 247,000원", "SALE" 같은 안내문이 들어오는 경우가 있어,
    마케팅 카피면 버리고 상품명 접두어/본문에서 다시 판별한다.
    """
    brand = str(raw_brand or "").strip()
    if brand and not is_marketing_copy(brand):
        return brand
    return pick_brand_from_prefix(product_name or "")


def product_key(name):
    """상품 중복 판단용 키: 괄호 안내문/공백 제거 후 소문자화.
    "[화이트] 무선 전동 그라인더 2.0" 와 "[레드] 무선 전동 그라인더 2.0" 을
    같은 상품으로 본다."""
    text = _BRACKET_RE.sub(" ", str(name or ""))
    return re.sub(r"\s+", "", text).lower()


def add_categories(programs):
    """
    1) 원본 상품명으로 카테고리 분류 (분류 모델은 원본 패턴으로 학습됨)
    2) 분류가 끝난 뒤 product 필드를 화면 표시용으로 정제
    """
    if not programs:
        return programs
    pairs = [(p["brand"], p["product"]) for p in programs]
    categories = classify_batch(pairs)
    for p, cat in zip(programs, categories):
        p["category"] = cat
        p["product"] = clean_product_name(p["product"])
    return programs


def fetch_lotte(date_compact, date_dash, endpoint):
    """
    endpoint: 'scheduleLive'(라이브TV) | 'scheduleOne'(원TV/데이터방송)

    주의: 롯데 API는 bdDate로 요청해도 응답에 전날 밤 ~ 당일 새벽 방송까지
    함께 내려준다 (예: bdDate=20260627 요청 시 06-26 22:40 방송도 포함되고,
    반대로 06-27 22:30~01:00처럼 다음날로 넘어가는 방송도 포함됨).
    각 항목의 실제 방송 날짜는 stime이 아니라 sdate(방송 시작일) 필드로
    판단해야 한다. 여기서는 sdate가 요청한 날짜(date_dash)와 일치하는
    항목만 추려서 반환한다. stime만 보고 날짜를 추정하면 전날/다음날
    방송이 같은 날짜로 잘못 합쳐져 중복이 발생한다.
    """
    headers = {"User-Agent": UA, "Referer": "https://www.lotteimall.com/main/viewMain.lotte"}
    url = f"https://www.lotteimall.com/main/{endpoint}.lotte?bdDate={date_compact}&date={date_dash}"
    programs = []
    try:
        r = requests.get(url, headers=headers, timeout=12)
        r.raise_for_status()
        prods = r.json().get("body", {}).get("prod", []) or []
        for p in prods:
            sdate = (p.get("sdate") or "")[:10]
            actual_date = sdate or date_dash  # sdate 없으면 방어적으로 요청일 간주
            if actual_date != date_dash:
                # 전날 밤 방송이거나 다음날로 넘어가는 항목 -> 이 날짜 버킷 아님
                continue

            link_info = p.get("linkInfo", "") or ""
            link = f"https://www.lotteimall.com{link_info}" if link_info.startswith("/") else link_info
            prgm_id = p.get("bdPrgmId")
            uid = str(prgm_id) if prgm_id else f"{sdate}_{p.get('stime','')}_{p.get('name','')}"
            # 고정PGM 방송이면 pgmMap.titNm에 프로그램명이 있다 (프론트 뱃지용)
            pgm = ((p.get("pgmMap") or {}).get("titNm") or "").strip()
            entry = {
                "uid": uid,
                "start": p.get("stime", ""),
                "end": p.get("etime", ""),
                "brand": resolve_brand(p.get("brand"), p.get("name")),
                "product": p.get("name", "") or "",
                "price": parse_price(p.get("price_disc")),
                "link": link,
            }
            if pgm:
                entry["pgm"] = pgm
            programs.append(entry)

            # 고정PGM(최유라쇼 등 pgmMap이 있는 방송)은 편성표에 대표상품 1개만
            # 나오고 나머지 방송상품이 related("함께 방송하는 상품") 필드에 숨어있다.
            # (relatedAdd는 "함께사면 좋은 상품" 단품 구성이라 방송상품이 아님 -> 제외)
            # 브랜드는 related에 별도 필드가 없어 상품명 앞 [브랜드] 접두어로 판별하고,
            # 새 브랜드의 첫 상품만 추가한다 (브랜드별 대표상품).
            # 접두어에는 브랜드 대신 가격/구성/색상 안내문이 들어오는 경우가 많아
            # (예: "[백화점가 106만원][포트메리온] 뉴베리에이션 4인조 홈세트")
            # pick_brand_from_prefix로 안내문 접두어는 건너뛰고 판별한다.
            if p.get("pgmMap"):
                slot_brands = {entry["brand"]}
                main_brand = pick_brand_from_prefix(p.get("name") or "")
                if main_brand:
                    slot_brands.add(main_brand)
                slot_names = {product_key(p.get("name") or "")}
                for rel in p.get("related") or []:
                    rel_name = rel.get("name") or ""
                    rel_brand = pick_brand_from_prefix(rel_name)
                    rel_goods = rel.get("goodsNo")
                    if not rel_goods:
                        continue
                    if rel_brand and rel_brand in slot_brands:
                        continue
                    # 브랜드를 못 잡은 상품은 브랜드 없이 담되, 같은 상품이
                    # 색상/옵션 접두어만 바꿔 여러 번 들어오는 걸 막는다
                    # ("[화이트]/[레드]/[블랙] 무선 전동 그라인더 2.0 풀세트")
                    rel_key = product_key(rel_name)
                    if not rel_brand and (not rel_key or rel_key in slot_names):
                        continue
                    if rel_brand:
                        slot_brands.add(rel_brand)
                    slot_names.add(rel_key)
                    rel_link_info = rel.get("linkInfo", "") or ""
                    rel_entry = {
                        "uid": f"{uid}_{rel_goods}",
                        "start": p.get("stime", ""),
                        "end": p.get("etime", ""),
                        "brand": rel_brand,
                        "product": rel_name,
                        "price": parse_price(rel.get("price_disc")),
                        "link": (f"https://www.lotteimall.com{rel_link_info}"
                                 if rel_link_info.startswith("/") else rel_link_info),
                    }
                    if pgm:
                        rel_entry["pgm"] = pgm
                    programs.append(rel_entry)
    except Exception as e:
        print(f"    [LT] 오류: {e}")

    # 같은 응답 안에서도 항목이 중복으로 잡힐 가능성에 대한 방어적 dedup
    seen = set()
    deduped = []
    for prog in programs:
        if prog["uid"] in seen:
            continue
        seen.add(prog["uid"])
        deduped.append(prog)
    deduped.sort(key=lambda x: x["start"])
    for prog in deduped:
        del prog["uid"]
    return deduped


BROADCASTS = [
    ("live", "scheduleLive"),
    ("data", "scheduleOne"),
]


def load_month(sub_dir, ym):
    path = os.path.join(sub_dir, f"{ym}.json")
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8")).get("days", {})
        except Exception:
            return {}
    return {}


def main():
    base = today_kst()
    today_str = base.strftime("%Y-%m-%d")

    for broadcast, endpoint in BROADCASTS:
        sub_dir = os.path.join(OUTPUT_DIR, f"LT_{broadcast}")
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
                print(f"[LT_{broadcast}] {date_dash}: 이미 기록됨, 건너뜀")
                continue

            print(f"[LT_{broadcast}] {date_dash} 수집 중...")
            programs = fetch_lotte(date_compact, date_dash, endpoint)
            programs = add_categories(programs)

            if is_past and not programs:
                print(f"  -> 0개 (과거, 기존값 유지)")
                time.sleep(REQUEST_DELAY)
                continue

            days[date_dash] = programs
            print(f"  -> {len(programs)}개 편성")
            time.sleep(REQUEST_DELAY)

        for ym, days in month_data.items():
            if not days:
                continue
            out_path = os.path.join(sub_dir, f"{ym}.json")
            sorted_days = {k: days[k] for k in sorted(days)}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({
                    "company": "LT", "broadcast": broadcast,
                    "month": ym, "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "days": sorted_days,
                }, f, ensure_ascii=False, indent=2)
            print(f"  저장: {out_path} ({len(sorted_days)}일)")

    print("\n완료.")


if __name__ == "__main__":
    main()
