# -*- coding: utf-8 -*-
"""
lavangba_gapfill.py
자사 편성표에 빠진 방송을 라방바 편성으로 채우는 공통 유틸 (2026-11-11 수립).

== 왜 필요한가 ==
홈쇼핑사 자사 편성 API가 일부 방송을 아예 안 준다. 예: CJ tvSchedule API는
일요일 저녁 여행상품 방송(11/1 17:40~19:25 동유럽/이탈리아 9일, 11/8 18:30~19:25
동유럽 3국 9일 등)을 빼고 줘서 화면에 'Hit & Best' 빈칸이 생겼다.
라방바는 이 방송을 갖고 있으므로, 자사 편성과 시간이 겹치지 않는 라방바 방송만 골라 넣는다.

== 규칙 ==
- 자사 편성이 기본. 라방바 방송이 자사 편성 어느 항목과든 MIN_OVERLAP_MIN분 이상 겹치면 넣지 않는다.
- MIN_DURATION_MIN분보다 짧은 라방바 기록(새벽 5분 재방송 조각 등)은 넣지 않는다.
- 넣는 방송만 라방바 상세 페이지에서 link/price를 받는다 (호출 최소화).
- 라방바 요청 실패 시 아무것도 넣지 않는다 (자사 편성은 그대로).

== 사용법 ==
    from tools.lavangba_gapfill import fill_gaps
    added = fill_gaps(programs, date_obj, "hs_cjonstyle", "CJ_live")
"""

import json
import re
import time

from tools import scrape_guard
from tools.lavangba_relay import session as lavangba

LIST_URL = "https://live.ecomm-data.com/api/schedule/list_hs"
DETAIL_URL_TMPL = "https://live.ecomm-data.com/report/hsshow/{hsshow_id}"
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Content-Type": "application/json",
    "Origin": "https://live.ecomm-data.com",
    "Referer": "https://live.ecomm-data.com/schedule/hs",
}
LIST_RETRY_STATUS = scrape_guard.RETRY_STATUS | {403, 405}
MIN_OVERLAP_MIN = 10
# 이보다 짧은 라방바 방송은 넣지 않는다. 라방바는 데이터홈쇼핑 새벽 재방송을 5분 단위로
# 쪼개 기록하고(CJ 플러스 01:45~04:45), 1분짜리 기록도 있어 화면만 어지럽힌다.
MIN_DURATION_MIN = 20
DETAIL_DELAY = 0.4


def _hhmm(raw: str) -> str:
    return f"{raw[8:10]}:{raw[10:12]}" if raw and len(raw) >= 12 else ""


def _span(start: str, end: str):
    """'HH:MM' 두 개 -> (시작분, 종료분). 자정을 넘기면 종료를 24시 이후로."""
    s = int(start[:2]) * 60 + int(start[3:5])
    e = int(end[:2]) * 60 + int(end[3:5])
    if e <= s:
        e += 24 * 60
    return s, e


def fetch_list(date_obj, platform_id: str, label: str):
    """라방바 list_hs 1일치 (해당 채널만). 실패 시 None."""
    payload = {"date": date_obj.strftime("%y%m%d"), "type": None,
               "platform": [platform_id], "cid": None}
    try:
        r = scrape_guard.request("POST", LIST_URL, json=payload, headers=HEADERS, timeout=15,
                                 retries=3, retry_status=LIST_RETRY_STATUS, session=lavangba,
                                 expect_json=True, label=f"{label} 라방바 list_hs")
    except scrape_guard.FetchError as e:
        print(f"    [{label}] 라방바 보충 실패: {e}")
        return None
    out = []
    for item in r.json().get("list", []) or []:
        if item.get("platform_id") and item.get("platform_id") != platform_id:
            continue
        start = _hhmm(item.get("hsshow_datetime_start", ""))
        end = _hhmm(item.get("hsshow_datetime_end", ""))
        if start and end:
            out.append({"start": start, "end": end,
                        "product": item.get("hsshow_title", "") or "",
                        "lavangba_category": (item.get("cat") or {}).get("cat_name", "") or "",
                        "hsshow_id": str(item.get("hsshow_id") or "")})
    return out


def fetch_detail(hsshow_id: str):
    """라방바 방송 상세에서 대표 상품 (link, price). 실패 시 ("", 0)."""
    try:
        r = lavangba.get(DETAIL_URL_TMPL.format(hsshow_id=hsshow_id),
                         headers={"User-Agent": UA, "Referer": HEADERS["Referer"]}, timeout=12)
        m = NEXT_DATA_RE.search(r.text) if r.status_code == 200 else None
        if not m:
            return "", 0
        items = (json.loads(m.group(1)).get("props", {}).get("pageProps", {})
                 .get("ss_data", {}).get("products", {}).get("items") or [])
        if not items:
            return "", 0
        price = items[0].get("price") or items[0].get("price_sales") or 0
        return items[0].get("item_url", "") or "", int(price) if price else 0
    except Exception as e:
        print(f"      [lavangba_detail] {hsshow_id} 오류: {e}")
        return "", 0


def fill_gaps(programs: list, date_obj, platform_id: str, label: str) -> list:
    """programs(자사 편성, 공통 스키마)에 시간이 겹치지 않는 라방바 방송을 추가한다.
    programs를 직접 수정(추가 후 시작시각 정렬)하고, 추가한 항목 리스트를 반환한다.
    추가 항목은 brand가 비어 있으므로 호출부의 add_categories(브랜드 추론+분류) 전에 부를 것."""
    lav = fetch_list(date_obj, platform_id, label)
    if not lav:
        return []
    spans = [_span(p["start"], p["end"]) for p in programs
             if len(p.get("start") or "") == 5 and len(p.get("end") or "") == 5]
    added = []
    for lp in lav:
        s, e = _span(lp["start"], lp["end"])
        if e - s < MIN_DURATION_MIN:
            continue
        if any(min(e, pe) - max(s, ps) >= MIN_OVERLAP_MIN for ps, pe in spans):
            continue
        link, price = fetch_detail(lp["hsshow_id"]) if lp["hsshow_id"] else ("", 0)
        time.sleep(DETAIL_DELAY)
        prog = {"start": lp["start"], "end": lp["end"], "brand": "", "product": lp["product"],
                "price": price, "link": link, "category": "",
                "lavangba_category": lp["lavangba_category"], "hsshow_id": lp["hsshow_id"]}
        added.append(prog)
        spans.append((s, e))
    if added:
        programs.extend(added)
        programs.sort(key=lambda x: x["start"])
        print(f"    [{label}] 자사 편성에 없는 방송 {len(added)}개 라방바로 보충: "
              + ", ".join(f"{p['start']}~{p['end']} {p['product'][:20]}" for p in added))
    return added
