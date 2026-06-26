# -*- coding: utf-8 -*-
"""
cj_fixed_programs.py
CJ온스타일 모바일 메인페이지의 "대표프로그램" 13개 슬롯을 Playwright로 직접
클릭하며 각 슬롯의 (이름/편성텍스트/썸네일/pgmShop 링크)를 전부 수집하고,
schedule API에서 모은 pgmCd별 (요일,시각) 출현 패턴과 대조해 pgmCd까지
역으로 매칭한다.

(업데이트 - 2026-06)
CJ온스타일 사이트가 리뉴얼되어 메인페이지가 완전 SPA(__cjos_config__ +
page.cjmall.mobile.homeTab.min.js)로 바뀌었다. 정적 HTML에는
".pgm_tab_section" 같은 마크업이 더 이상 없고, 콘텐츠 영역
(<div id="moduleArea"></div>)이 비어 있는 채로 내려온 뒤 JS가 내부 API를
호출해 채운다. 따라서 requests로는 더 이상 데이터를 가져올 수 없어
Playwright로 실제 렌더링한 뒤 DOM을 읽는 방식으로 전환한다.

== 동작 전략 ==
1. Playwright(headless Chromium, 모바일 에뮬레이션)로 메인페이지 접속.
   페이지가 호출하는 모든 네트워크 응답을 가로채서, JSON이면서
   URL에 후보 키워드(pgm/tab/home/major 등)가 포함된 것들을 모두 기록한다.
   -> 이 중에서 실제로 13개 슬롯 데이터를 담고 있는 응답을 자동으로 찾아
      "메인페이지 슬롯 정보"로 우선 사용한다 (마크업이 또 바뀌어도 견고).
2. 위 방식으로 못 찾으면, 화면에 렌더링된 DOM에서 대표프로그램 슬롯으로
   추정되는 탭/슬라이드 요소들을 후보 셀렉터 목록으로 순차 시도해 찾고,
   하나씩 클릭해 펼쳐지는 패널의 텍스트와 링크를 직접 긁는다 (폴백).
3. 메인페이지에서 얻은 슬롯별 "이름 + 편성텍스트(요일/시각 패턴)"를
   schedule API(최근 N일치)에서 모은 pgmCd별 (요일,시각) 출현 패턴과
   대조해, 정확히 일치하거나 가장 가까운 pgmCd를 매칭한다.

DOM 구조나 모듈 클래스명이 또 바뀌면 SLOT_SELECTOR_CANDIDATES /
PANEL_SELECTOR_CANDIDATES 목록에 새 셀렉터를 추가하면 된다. 이 스크립트는
"못 찾으면 빈 배열을 반환"하는 식으로 동작하므로, 실패해도 워크플로
전체가 죽지 않고(continue-on-error) candidate_fixed_pgm_codes(스케줄 기반
추정치)는 항상 함께 저장된다.

== 출력 ==
homeshopping/fixed_programs/CJ.json
{
  "company": "CJ",
  "collectedAt": "...",
  "matched_programs": [
    {
      "slot_title": "동가게",
      "pgm_cd": "563049",
      "schedule_text": "목20:45 / 토10:20",
      "occurrences": ["목 20:45", "토 10:20"],
      "thumbnail": "https://...",
      "pgmshop_link": "https://display.cjonstyle.com/m/pgmShop/100013",
      "match_type": "exact" | "partial" | "unmatched"
    },
    ...
  ],
  "slot_titles": ["동가게", "더 지완스 1부", "탑쇼", ...],
  "candidate_fixed_pgm_codes": {
    "563049": ["목 20:45", "토 10:20"],
    ...
  }
}

== 사용법 ==
  pip install playwright requests
  python -m playwright install --with-deps chromium
  python cj_fixed_programs.py
"""

import os
import re
import json
import time
import requests
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
WEEKDAYS_KR = ["월", "화", "수", "목", "금", "토", "일"]

MAIN_PAGE_URL = "https://display.cjonstyle.com/m/homeTab/main?hmtabMenuId=004389"
SCHEDULE_API_URL = "https://display-frontapi.cjonstyle.com/polling/broadcast/schedule"

OUTPUT_DIR = os.path.join("homeshopping", "fixed_programs")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "CJ.json")

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
)

SCHEDULE_LOOKBACK_DAYS = 14
REQUEST_DELAY_SEC = 0.4
MIN_DURATION_MIN_FOR_CANDIDATE = 30

# 네트워크 응답 중 "이게 대표프로그램 슬롯 데이터일 것 같다" 판단용 키워드.
# URL이나 JSON 키 이름에 이 중 하나라도 포함되면 후보로 수집한다.
API_URL_HINTS = ["pgm", "major", "homeTab", "representative", "tab"]

# DOM 폴백용 셀렉터 후보들 (사이트가 또 바뀌면 여기에 추가).
# (탭/슬라이드 컨테이너 선택자, 그 안의 클릭 가능한 개별 슬롯 선택자)
SLOT_SELECTOR_CANDIDATES = [
    ("ul.tab_pgm", "img[id^='majorPgm']"),
    ("[class*='pgm_tab']", "[class*='tab_item'], img"),
    ("[class*='major']", "[class*='item'], img"),
]

EXPECTED_SLOT_COUNT = 13


def fetch_schedule(bd_dt: str) -> list:
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": MAIN_PAGE_URL,
        "User-Agent": MOBILE_UA,
        "Origin": "https://display.cjonstyle.com",
    }
    params = {"bdDt": bd_dt}
    resp = requests.get(SCHEDULE_API_URL, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", {}).get("broadcastList", []) or []


def collect_schedule_occurrences(days: int) -> dict:
    """최근 days일치 schedule을 모아 pgmCd -> ["요일 HH:MM", ...] 누적."""
    occurrences = defaultdict(set)
    today = datetime.now(KST).date()

    for i in range(days):
        target_date = today - timedelta(days=i)
        bd_dt = target_date.strftime("%Y%m%d")
        print(f"  [CJ] schedule {bd_dt} 수집 중...")
        try:
            broadcasts = fetch_schedule(bd_dt)
        except Exception as e:
            print(f"    [실패] {bd_dt}: {e}")
            continue

        for b in broadcasts:
            if b.get("broadType") != "TV":
                continue
            try:
                start_dt = datetime.fromisoformat(b["bdStrDtm"])
                end_dt = datetime.fromisoformat(b["bdEndDtm"])
            except (KeyError, ValueError):
                continue

            duration_min = (end_dt - start_dt).total_seconds() / 60
            if duration_min < MIN_DURATION_MIN_FOR_CANDIDATE:
                continue

            weekday = WEEKDAYS_KR[start_dt.weekday()]
            time_str = f"{weekday} {start_dt.strftime('%H:%M')}"
            occurrences[b["pgmCd"]].add(time_str)

        time.sleep(REQUEST_DELAY_SEC)

    return {pgm_cd: sorted(times) for pgm_cd, times in occurrences.items()}


def parse_schedule_text(text: str) -> set:
    """'목20:45 / 토10:20' -> {'목 20:45', '토 10:20'}"""
    result = set()
    if not text:
        return result
    parts = re.split(r'[/|,]', text)
    for part in parts:
        m = re.search(r'([월화수목금토일])\s*(\d{1,2}):?(\d{2})', part)
        if m:
            day, hh, mm = m.group(1), m.group(2), m.group(3)
            result.add(f"{day} {int(hh):02d}:{mm}")
    return result


def looks_like_slot_payload(obj) -> bool:
    """JSON 응답이 13개 안팎의 프로그램 슬롯 리스트처럼 생겼는지 휴리스틱 판정."""
    candidates = []
    if isinstance(obj, list):
        candidates = obj
    elif isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list) and 3 <= len(v) <= 30:
                candidates = v
                break
    if not (3 <= len(candidates) <= 30):
        return False

    sample = [c for c in candidates if isinstance(c, dict)][:5]
    if not sample:
        return False

    key_hint_hits = 0
    for item in sample:
        keys = " ".join(item.keys()).lower()
        if any(h in keys for h in ["pgm", "title", "nm", "schedule", "sect"]):
            key_hint_hits += 1
    return key_hint_hits >= len(sample) // 2 + 1


def collect_main_page_slots_via_network(playwright) -> tuple:
    """Playwright로 메인페이지를 열고, 네트워크 응답 중 슬롯 데이터로 보이는
    JSON을 자동으로 찾는다. 못 찾으면 DOM 폴백을 시도한다.
    반환: (slots: list[dict(title, schedule_text, thumbnail, link)], debug_info: dict)
    """
    captured_payloads = []

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=MOBILE_UA,
        viewport={"width": 390, "height": 844},
        locale="ko-KR",
    )
    page = context.new_page()

    def on_response(response):
        try:
            url = response.url
            if not any(h.lower() in url.lower() for h in API_URL_HINTS):
                return
            ctype = response.headers.get("content-type", "")
            if "json" not in ctype:
                return
            body = response.json()
            captured_payloads.append({"url": url, "body": body})
        except Exception:
            pass

    page.on("response", on_response)

    print(f"  [CJ] Playwright로 메인페이지 접속: {MAIN_PAGE_URL}")
    page.goto(MAIN_PAGE_URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)

    slots = []

    # 1) 네트워크 응답에서 슬롯 데이터 자동 탐지
    for payload in captured_payloads:
        body = payload["body"]
        targets = []
        if isinstance(body, dict):
            for v in body.values():
                if isinstance(v, list):
                    targets.append(v)
                elif isinstance(v, dict):
                    for v2 in v.values():
                        if isinstance(v2, list):
                            targets.append(v2)
        elif isinstance(body, list):
            targets.append(body)

        for t in targets:
            if looks_like_slot_payload(t):
                print(f"  [CJ] 후보 슬롯 응답 발견: {payload['url']} ({len(t)}개 항목)")
                slots = t
                break
        if slots:
            break

    browser.close()
    return slots, {"captured_count": len(captured_payloads)}


def collect_main_page_slots_via_dom(playwright) -> list:
    """DOM 폴백: 화면에 렌더링된 슬롯 탭들을 순서대로 클릭하며
    펼쳐지는 패널에서 (제목, 편성텍스트, 썸네일, 링크)를 긁는다."""
    slots = []

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=MOBILE_UA,
        viewport={"width": 390, "height": 844},
        locale="ko-KR",
    )
    page = context.new_page()

    print(f"  [CJ] (DOM 폴백) 메인페이지 접속: {MAIN_PAGE_URL}")
    page.goto(MAIN_PAGE_URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)

    tab_locator = None
    item_selector = None
    for tab_sel, item_sel in SLOT_SELECTOR_CANDIDATES:
        loc = page.locator(f"{tab_sel} {item_sel}")
        try:
            count = loc.count()
        except Exception:
            count = 0
        if count >= 3:
            tab_locator = loc
            item_selector = item_sel
            print(f"  [CJ] DOM 셀렉터 매칭: '{tab_sel} {item_sel}' ({count}개)")
            break

    if not tab_locator:
        print("  [CJ] [경고] DOM에서 슬롯 탭을 찾지 못했습니다. 셀렉터 후보를 갱신해야 합니다.")
        browser.close()
        return slots

    count = min(tab_locator.count(), EXPECTED_SLOT_COUNT + 5)
    for i in range(count):
        try:
            item = tab_locator.nth(i)
            title = (item.get_attribute("alt") or item.inner_text() or "").strip()
            item.click(timeout=3000)
            page.wait_for_timeout(800)

            # 펼쳐진 패널에서 편성텍스트/링크/썸네일을 최대한 일반적인 방식으로 탐색
            schedule_text = ""
            thumbnail = ""
            link = ""

            for sel in ["[class*='schedule']", "[class*='time']", "sup", ".txt"]:
                loc2 = page.locator(sel).first
                if loc2.count() and loc2.is_visible():
                    txt = loc2.inner_text().strip()
                    if re.search(r'[월화수목금토일]', txt):
                        schedule_text = txt
                        break

            for sel in ["a[href*='pgmShop']", "a.btn_banner", "a[class*='link']"]:
                loc2 = page.locator(sel).first
                if loc2.count():
                    href = loc2.get_attribute("href")
                    if href:
                        link = href
                        break

            for sel in ["figure img", "[class*='ban'] img", "img"]:
                loc2 = page.locator(sel).first
                if loc2.count():
                    src = loc2.get_attribute("src")
                    if src:
                        thumbnail = src
                        break

            if title:
                slots.append({
                    "title": title,
                    "schedule_text": schedule_text,
                    "thumbnail": thumbnail,
                    "link": link,
                })
        except Exception as e:
            print(f"    [경고] 슬롯 {i} 처리 실패: {e}")
            continue

    browser.close()
    return slots


def normalize_slot(raw_slot) -> dict:
    """네트워크 캡처 슬롯(키 이름 불확실)을 표준 형태로 정규화."""
    if not isinstance(raw_slot, dict):
        return None

    def pick(*keys):
        for k in keys:
            if k in raw_slot and raw_slot[k]:
                return raw_slot[k]
        return ""

    title = pick("title", "pgmNm", "sectNm", "name", "alt")
    schedule_text = pick("scheduleText", "sectLbl", "broadcastTime", "timeText")
    thumbnail = pick("thumbnail", "imgUrl", "bannerImgUrl", "imageUrl")
    link = pick("link", "linkUrl", "pgmShopUrl", "href")

    if not title:
        return None

    return {
        "title": str(title).strip(),
        "schedule_text": str(schedule_text).strip(),
        "thumbnail": str(thumbnail).strip(),
        "link": str(link).strip(),
    }


def match_slots_to_pgm_cd(slots: list, occurrences: dict) -> list:
    """각 슬롯의 편성텍스트(요일/시각 집합)를 schedule occurrences와 대조해
    pgmCd를 매칭한다. 정확히 같은 집합이면 exact, 부분집합/교집합이 있으면
    partial, 없으면 unmatched."""
    matched = []

    for slot in slots:
        target = parse_schedule_text(slot.get("schedule_text") or "")
        result = {
            "slot_title": slot.get("title"),
            "pgm_cd": None,
            "schedule_text": slot.get("schedule_text"),
            "occurrences": [],
            "thumbnail": slot.get("thumbnail"),
            "pgmshop_link": slot.get("link"),
            "match_type": "unmatched",
        }

        if not target:
            matched.append(result)
            continue

        exact_match = None
        best_partial = None
        best_overlap = 0

        for pgm_cd, times in occurrences.items():
            times_set = set(times)
            if times_set == target:
                exact_match = pgm_cd
                break
            overlap = len(times_set & target)
            if overlap > best_overlap:
                best_overlap = overlap
                best_partial = pgm_cd

        if exact_match:
            result["pgm_cd"] = exact_match
            result["occurrences"] = occurrences.get(exact_match, [])
            result["match_type"] = "exact"
        elif best_partial and best_overlap > 0:
            result["pgm_cd"] = best_partial
            result["occurrences"] = occurrences.get(best_partial, [])
            result["match_type"] = "partial"

        matched.append(result)

    return matched


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("[CJ] 메인페이지에서 대표프로그램 슬롯 13개 수집 (Playwright)...")
    raw_slots = []
    debug_info = {}
    try:
        with sync_playwright() as p:
            raw_slots, debug_info = collect_main_page_slots_via_network(p)
            if not raw_slots:
                print("  [CJ] 네트워크 응답에서 슬롯을 찾지 못해 DOM 폴백을 시도합니다...")
                with sync_playwright() as p2:
                    raw_slots = collect_main_page_slots_via_dom(p2)
    except Exception as e:
        print(f"  [실패] Playwright 슬롯 수집: {e}")

    slots = [s for s in (normalize_slot(rs) for rs in raw_slots) if s]
    print(f"  [CJ] 정규화된 슬롯 {len(slots)}개 (기대값 {EXPECTED_SLOT_COUNT}개)")

    print(f"\n[CJ] 최근 {SCHEDULE_LOOKBACK_DAYS}일치 편성표 수집 (고정 프로그램 패턴 추출용)...")
    occurrences = collect_schedule_occurrences(SCHEDULE_LOOKBACK_DAYS)

    matched_programs = match_slots_to_pgm_cd(slots, occurrences)

    candidate_fixed = {
        pgm_cd: times for pgm_cd, times in occurrences.items() if len(times) >= 2
    }

    payload = {
        "company": "CJ",
        "collectedAt": datetime.now(KST).isoformat(),
        "matched_programs": matched_programs,
        "slot_titles": [s["title"] for s in slots],
        "candidate_fixed_pgm_codes": candidate_fixed,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n저장 완료: {OUTPUT_PATH}")
    print(f"  슬롯 {len(slots)}개 이름: {payload['slot_titles']}")
    exact = sum(1 for m in matched_programs if m["match_type"] == "exact")
    partial = sum(1 for m in matched_programs if m["match_type"] == "partial")
    print(f"  매칭 결과: exact={exact}, partial={partial}, unmatched={len(matched_programs)-exact-partial}")
    print(f"  고정 편성 후보 pgmCd {len(candidate_fixed)}개 발견")


if __name__ == "__main__":
    main()
