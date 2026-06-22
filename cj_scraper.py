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

== 참고 ==
CJ는 brandName 필드가 대부분 비어있어, 비어있을 경우 방송 프로그램명(pgmNm)을 대신 사용한다.

== 사용법 ==
  pip install requests
  python cj_scraper.py
"""

import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone

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


def fetch_cj(date_compact, broad_param):
    """
    broad_param: 'live'(TV LIVE) | 'plus'(TV+/데이터방송)
    하나의 프로그램(pgmCd)에 여러 상품이 있어 첫 상품을 대표로 사용.
    시간은 밀리초 Unix timestamp -> KST 변환.
    """
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
            if not start_ms or not end_ms:
                continue
            start_str = datetime.fromtimestamp(start_ms / 1000, tz=KST).strftime("%H:%M")
            end_str = datetime.fromtimestamp(end_ms / 1000, tz=KST).strftime("%H:%M")

            items = pg.get("itemList", []) or []
            first = items[0] if items else {}
            item_cd = first.get("itemCd", "")
            chn_cd = first.get("chnCd", "")
            link = (f"https://display.cjonstyle.com/p/item/{item_cd}?channelCode={chn_cd}"
                    if item_cd else "")
            # CJ는 brandName이 대부분 비어있어 상품명을 대표로 사용
            programs.append({
                "start": start_str,
                "end": end_str,
                "brand": first.get("brandName") or pg.get("pgmNm", "") or "",
                "product": first.get("itemNm", "") or "",
                "price": parse_price(first.get("salePrice")),
                "link": link,
            })
    except Exception as e:
        print(f"    [CJ] 오류: {e}")
    programs.sort(key=lambda x: x["start"])
    return programs


BROADCASTS = [
    ("live", "live"),
    ("data", "plus"),
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
                    "company": "CJ", "broadcast": broadcast,
                    "month": ym, "days": sorted_days,
                }, f, ensure_ascii=False, indent=2)
            print(f"  저장: {out_path} ({len(sorted_days)}일)")

    print("\n완료.")


if __name__ == "__main__":
    main()
