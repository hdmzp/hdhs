# -*- coding: utf-8 -*-
"""
4사 동일 브랜드 가격 추적 - 추적 대상 선정기

hmall(HD)·gsshop(GS)·cjonstyle(CJ)·lotteimall(LT) 편성 데이터에서
"여러 사에서 동시에 운영 중인 브랜드"를 자동 선정하고, 브랜드×회사별로
최근 방송된 상품(상품코드·링크·방송가)을 추려 tracked.json으로 저장한다.
네트워크 요청 없음 - homeshopping/ 의 기존 편성 JSON만 읽는다.

== 저장 구조 ==
pricewatch/tracked.json
{
  "generated_at": "2026-07-30T10:40:00+09:00",
  "window_days": 60, "min_companies": 3, "retain_days": 90,
  "brands": {
    "이지듀": {
      "category": "미용",                  # 브랜드 전체 편성 카테고리 최빈값
      "companies": ["HD", "GS", "CJ", "LT"],
      "products": {
        "HD": [{"pid": "2244486431", "name": "...", "link": "https://...",
                "category": "미용", "last_seen": "2026-07-28",
                "last_broadcast_price": 59800}],
        ...
      }
    }
  }
}

== 선정 정책 ==
- 최근 WINDOW_DAYS일 편성(4사 live+data)에서 브랜드별 (회사, 상품코드) 수집
- 이전 tracked.json의 상품은 last_seen이 RETAIN_DAYS 이내면 편성에서
  빠졌어도 계속 추적 유지 (방송 종료 후 상시가 인하를 잡기 위함)
- MIN_COMPANIES사 이상에 상품이 있는 브랜드만 채택
- 회사별 상품은 last_seen 최신순 MAX_PRODUCTS_PER_CELL개 컷 (요청량 상한)

== 사용법 ==
  python pricewatch_tracker.py
"""

import os
import glob
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
OUTPUT_PATH = os.path.join("pricewatch", "tracked.json")
SCHEDULE_DIR = "homeshopping"

WINDOW_DAYS = 60           # 편성 스캔 범위 (오늘-60일 ~ 오늘)
MIN_COMPANIES = 3          # 이 수 이상 회사에 편성된 브랜드만 추적
RETAIN_DAYS = 90           # 편성 이탈 후에도 추적을 유지하는 기간
MAX_PRODUCTS_PER_CELL = 3  # 브랜드×회사당 추적 상품 수 상한

COMPANIES = ["HD", "GS", "CJ", "LT"]
BROADCASTS = ["live", "data"]

# 표기가 다른 동일 브랜드 수동 별칭: {"표기변형": "대표표기"}
ALIAS = {}

# 링크 → 상품코드 추출 패턴
PID_PATTERNS = {
    "HD": re.compile(r"slitmCd=(\d+)"),
    "GS": re.compile(r"prdid=(\d+)"),
    "CJ": re.compile(r"/p/item/(\d+)"),
    "LT": re.compile(r"goods_no=(\d+)"),
}


def today_kst():
    return datetime.now(KST)


def extract_pid(co, link):
    """편성 레코드의 상품 링크에서 회사별 상품코드를 추출한다."""
    if not link:
        return None
    m = PID_PATTERNS[co].search(link)
    return m.group(1) if m else None


def months_in_window(start, end):
    """start~end 날짜 구간에 걸치는 YYYY-MM 목록."""
    months = []
    cur = start.replace(day=1)
    while cur <= end:
        months.append(cur.strftime("%Y-%m"))
        cur = (cur + timedelta(days=32)).replace(day=1)
    return months


def scan_schedule(window_start_str):
    """
    최근 편성에서 {brand: {co: {pid: 상품정보}}}를 만든다.
    같은 상품이 여러 번 편성됐으면 가장 최근 방송의 이름/가격/링크를 쓴다.
    """
    base = today_kst()
    window_start = datetime.strptime(window_start_str, "%Y-%m-%d")
    brands = {}

    for co in COMPANIES:
        for bc in BROADCASTS:
            for ym in months_in_window(window_start, base.replace(tzinfo=None)):
                path = os.path.join(SCHEDULE_DIR, f"{co}_{bc}", f"{ym}.json")
                if not os.path.exists(path):
                    continue
                try:
                    days = json.load(open(path, encoding="utf-8")).get("days", {})
                except Exception as e:
                    print(f"[tracker] {path} 읽기 실패: {e}")
                    continue
                for date_str, programs in days.items():
                    if date_str < window_start_str:
                        continue
                    for p in programs or []:
                        brand = (p.get("brand") or "").strip()
                        if not brand:
                            continue
                        brand = ALIAS.get(brand, brand)
                        pid = extract_pid(co, p.get("link") or "")
                        if not pid:
                            continue
                        entry = brands.setdefault(brand, {}).setdefault(co, {})
                        prev = entry.get(pid)
                        if prev and prev["last_seen"] >= date_str:
                            continue
                        entry[pid] = {
                            "pid": pid,
                            "name": p.get("product") or "",
                            "link": p.get("link") or "",
                            "category": p.get("category") or "",
                            "last_seen": date_str,
                            "last_broadcast_price": int(p.get("price") or 0),
                        }
    return brands


def load_prev_tracked():
    if not os.path.exists(OUTPUT_PATH):
        return {}
    try:
        return json.load(open(OUTPUT_PATH, encoding="utf-8")).get("brands", {})
    except Exception:
        return {}


def merge_with_retention(fresh, prev, retain_start_str):
    """
    이전 tracked의 상품 중 last_seen이 RETAIN_DAYS 이내인 것은
    이번 편성 창에 안 보여도 유지한다. 같은 상품이면 fresh가 우선.
    """
    for brand, companies in prev.items():
        for co, products in (companies.get("products") or {}).items():
            for item in products:
                if item.get("last_seen", "") < retain_start_str:
                    continue
                entry = fresh.setdefault(brand, {}).setdefault(co, {})
                if item.get("pid") and item["pid"] not in entry:
                    entry[item["pid"]] = item
    return fresh


def build_tracked(merged):
    """MIN_COMPANIES 필터 + 회사별 최신 상품 컷 + 대표 카테고리 계산."""
    out = {}
    for brand in sorted(merged):
        companies = merged[brand]
        cos = [co for co in COMPANIES if companies.get(co)]
        if len(cos) < MIN_COMPANIES:
            continue
        cat_counter = Counter()
        products = {}
        for co in cos:
            items = sorted(companies[co].values(),
                           key=lambda x: x["last_seen"], reverse=True)
            items = items[:MAX_PRODUCTS_PER_CELL]
            products[co] = items
            for it in items:
                if it.get("category"):
                    cat_counter[it["category"]] += 1
        out[brand] = {
            "category": cat_counter.most_common(1)[0][0] if cat_counter else "기타",
            "companies": cos,
            "products": products,
        }
    return out


def main():
    base = today_kst()
    window_start = (base - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    retain_start = (base - timedelta(days=RETAIN_DAYS)).strftime("%Y-%m-%d")

    print(f"[tracker] 편성 스캔: {window_start} ~ {base.strftime('%Y-%m-%d')}")
    fresh = scan_schedule(window_start)
    prev = load_prev_tracked()
    merged = merge_with_retention(fresh, prev, retain_start)
    brands = build_tracked(merged)

    n_products = sum(len(items) for b in brands.values()
                     for items in b["products"].values())
    n_four = sum(1 for b in brands.values() if len(b["companies"]) == 4)
    print(f"[tracker] {MIN_COMPANIES}사 이상 브랜드 {len(brands)}개 "
          f"(4사 공통 {n_four}개), 추적 상품 {n_products}개")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": base.isoformat(timespec="seconds"),
            "window_days": WINDOW_DAYS,
            "min_companies": MIN_COMPANIES,
            "retain_days": RETAIN_DAYS,
            "brands": brands,
        }, f, ensure_ascii=False, indent=2)
    print(f"[tracker] 저장: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
