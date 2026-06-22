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

== 사용법 ==
  pip install requests
  python lt_scraper.py
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


def fetch_lotte(date_compact, date_dash, endpoint):
    """
    endpoint: 'scheduleLive'(라이브TV) | 'scheduleOne'(원TV/데이터방송)
    """
    headers = {"User-Agent": UA, "Referer": "https://www.lotteimall.com/main/viewMain.lotte"}
    url = f"https://www.lotteimall.com/main/{endpoint}.lotte?bdDate={date_compact}&date={date_dash}"
    programs = []
    try:
        r = requests.get(url, headers=headers, timeout=12)
        r.raise_for_status()
        prods = r.json().get("body", {}).get("prod", []) or []
        for p in prods:
            link_info = p.get("linkInfo", "") or ""
            link = f"https://www.lotteimall.com{link_info}" if link_info.startswith("/") else link_info
            programs.append({
                "start": p.get("stime", ""),
                "end": p.get("etime", ""),
                "brand": p.get("brand", "") or "",
                "product": p.get("name", "") or "",
                "price": parse_price(p.get("price_disc")),
                "link": link,
            })
    except Exception as e:
        print(f"    [LT] 오류: {e}")
    programs.sort(key=lambda x: x["start"])
    return programs


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
                    "month": ym, "days": sorted_days,
                }, f, ensure_ascii=False, indent=2)
            print(f"  저장: {out_path} ({len(sorted_days)}일)")

    print("\n완료.")


if __name__ == "__main__":
    main()
