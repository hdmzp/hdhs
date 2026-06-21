# -*- coding: utf-8 -*-
"""
네이버 검색 'TV 편성표' 위젯에서 지상파+종편 8개 채널의
주간(7일) 시간대별 프로그램을 크롤링하는 파서.

== 알아낸 구조 (네이버가 두 가지 마크업을 번갈아 보여줌) ==

[버전 A: weekly-full] (더 풍부한 데이터, 한 시간대에 여러 프로그램 다 표시)
  - div.time_list > li.item (24개, 0~23시)
      - div.time_box: 시
      - div.ind_program.col1~col7 (7일치, 각 col = 한 날짜)
          - div.inner (1개 이상! 한 시간대 안의 여러 프로그램)
              - div.time_min: 시작 분
              - .pr_title: 프로그램명
  - ul._date_list 의 li.col1~col7 텍스트로 각 col의 실제 날짜 확인

[버전 B: weekly-simple] (시간대별 대표 프로그램 1개만)
  - div._timeline_page00 ~ _timeline_page06 (7일치, 각 page = 한 날짜)
      - li.item (24개, 0~23시)
          - div.time_box: 시
          - div.ind_program (1개, inner 없이 바로 time_min+pr_title)
  - div.date_select 의 li._li 순서로 각 page의 실제 날짜 확인

같은 쿼리로 요청해도 어느 버전이 뜨는지 랜덤하게 바뀌는 것으로 보임.
파서는 두 버전을 모두 인식해서 처리한다. 버전 A가 뜨면 그걸 우선 사용
(데이터가 더 풍부하므로), 버전 B만 있으면 그걸로 처리.

== 공통 한계 ==
종료시각이 없어서 "다음 프로그램 시작시간 = 이전 프로그램 종료시간"으로 역산.
버전 B(simple)는 한 시간대 대표 1개만 주므로 그 안의 다른 프로그램은 누락됨.

== 사용법 ==
    pip install requests beautifulsoup4
    python naver_schedule_scraper.py

결과: ./output/{YYYY-MM-DD}.json (날짜별 1파일, 8개 채널 다 포함)
"""

import requests
import re
import json
import time
import os
from datetime import datetime
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://www.naver.com/",
}

BASE_URL = "https://search.naver.com/search.naver"

CHANNELS = {
    "KBS1": "kbs1",
    "KBS2": "kbs2",
    "MBC": "mbc",
    "SBS": "sbs",
    "JTBC": "jtbc",
    "MBN": "mbn",
    "TV조선": "tv조선",
    "채널A": "채널a",
}

OUTPUT_DIR = "data"
REQUEST_DELAY_SEC = 1.5


def fetch_channel_html(channel_query: str) -> str:
    """채널명으로 검색해서 편성표 위젯이 포함된 HTML 전체를 반환.

    참고: 검색어에 날짜 문구('06월24일' 등)를 넣어봐도 결과는 항상
    동일하게 '오늘 기준 -1일~+5일'의 7일 구간이 반환됨이 확인됨.
    따라서 날짜 문구 없이 '{채널명} 편성표'로만 요청한다.
    """
    params = {"where": "nexearch", "sm": "tab_etc", "qvt": "0", "query": f"{channel_query} 편성표"}
    resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.text


def naver_date_to_iso(date_str: str, ref_year: int) -> str | None:
    """'06.20.(토)' -> '2026-06-20' 변환."""
    m = re.match(r"(\d{2})\.(\d{2})\.", date_str)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    return f"{ref_year:04d}-{month:02d}-{day:02d}"


def extract_title(inner):
    """inner div에서 (제목, 링크여부) 추출. 데이터 없으면 None."""
    title_tag = inner.find(class_="pr_title")
    if not title_tag:
        return None
    title = title_tag.get_text(strip=True)
    if not title:
        return None
    has_link = title_tag.name == "a"
    return title, has_link


def extract_minute(inner) -> int:
    time_min_tag = inner.find("div", class_="time_min")
    if not time_min_tag:
        return 0
    m = re.match(r"(\d+)", time_min_tag.get_text(strip=True))
    return int(m.group(1)) if m else 0


# ---------- 버전 A: weekly-full (col1~col7, 각 col에 inner 여러개) ----------

def try_parse_version_a(soup: BeautifulSoup, ref_year: int):
    date_list_ul = soup.find("ul", class_="_date_list")
    time_list = soup.find("div", class_="time_list")
    if not date_list_ul or not time_list:
        return None

    date_lis = date_list_ul.find_all("li")
    col_to_date = {}  # "col1" -> iso date
    for li in date_lis:
        col_class = next((c for c in li.get("class", []) if c.startswith("col")), None)
        if not col_class:
            continue
        date_text = li.get_text(" ", strip=True).replace("오늘", "").strip()
        iso = naver_date_to_iso(date_text, ref_year)
        if iso:
            col_to_date[col_class] = iso

    if not col_to_date:
        return None

    slots_by_date = {iso: [] for iso in col_to_date.values()}

    items = time_list.find_all("li", class_="item")
    for li in items:
        time_box = li.find("div", class_=lambda c: c and "time_box" in c)
        if not time_box:
            continue
        hour_m = re.match(r"(\d+)시", time_box.get_text(strip=True))
        if not hour_m:
            continue
        hour = int(hour_m.group(1))

        for col_class, iso in col_to_date.items():
            col_div = li.find("div", class_=lambda c: c and "ind_program" in c and col_class in c)
            if not col_div:
                continue
            inners = col_div.find_all("div", class_="inner")
            for inner in inners:
                extracted = extract_title(inner)
                if not extracted:
                    continue
                title, has_link = extracted
                minute = extract_minute(inner)
                slots_by_date[iso].append({
                    "hour": hour, "minute": minute, "title": title, "has_link": has_link,
                })

    for iso in slots_by_date:
        slots_by_date[iso].sort(key=lambda s: (s["hour"], s["minute"]))

    return slots_by_date


# ---------- 버전 B: weekly-simple (_timeline_page00~06, 슬롯당 1개) ----------

def try_parse_version_b(soup: BeautifulSoup, ref_year: int):
    date_select = soup.find("div", class_="date_select")
    if not date_select:
        return None
    lis = date_select.find_all("li", class_="_li")
    if not lis:
        return None

    date_list = [li.get("data-value", "").strip() for li in lis]
    slots_by_date = {}

    for idx, date_str in enumerate(date_list):
        page_div = soup.find("div", class_=f"_timeline_page{idx:02d}")
        if not page_div:
            continue
        iso = naver_date_to_iso(date_str, ref_year)
        if not iso:
            continue

        slots = []
        items = page_div.find_all("li", class_="item")
        for li in items:
            time_box = li.find("div", class_=lambda c: c and "time_box" in c)
            if not time_box:
                continue
            hour_m = re.match(r"(\d+)시", time_box.get_text(strip=True))
            if not hour_m:
                continue
            hour = int(hour_m.group(1))

            ind_program = li.find("div", class_=lambda c: c and "ind_program" in c)
            if not ind_program:
                continue
            extracted = extract_title(ind_program)
            if not extracted:
                continue
            title, has_link = extracted
            minute = extract_minute(ind_program)
            slots.append({"hour": hour, "minute": minute, "title": title, "has_link": has_link})

        slots_by_date[iso] = slots

    return slots_by_date if slots_by_date else None


def compute_end_times(slots):
    """슬롯 리스트(시작시각만 있음)를 받아 종료시각을 채운다.
    규칙: 다음 프로그램 시작시간 = 이전 프로그램 종료시간.
    마지막 프로그램의 종료시간은 24:00으로 처리.
    """
    programs = []
    for i, slot in enumerate(slots):
        start_h, start_m = slot["hour"], slot["minute"]
        if i + 1 < len(slots):
            end_h, end_m = slots[i + 1]["hour"], slots[i + 1]["minute"]
            end_str = f"{end_h:02d}:{end_m:02d}"
        else:
            end_str = "24:00"

        programs.append({
            "start": f"{start_h:02d}:{start_m:02d}",
            "end": end_str,
            "title": slot["title"],
        })
    return programs


def scrape_channel(channel_name: str, channel_query: str, ref_year: int):
    """한 채널의 7일치 편성표를 받아서 (by_date, version) 형태로 반환."""
    html = fetch_channel_html(channel_query)
    soup = BeautifulSoup(html, "html.parser")

    slots_by_date = try_parse_version_a(soup, ref_year)
    version = "A"
    if not slots_by_date:
        slots_by_date = try_parse_version_b(soup, ref_year)
        version = "B"

    if not slots_by_date:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        debug_path = os.path.join(OUTPUT_DIR, f"_debug_fail_{channel_name}.html")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(html)
        return {}, "FAIL"

    by_date = {iso: compute_end_times(slots) for iso, slots in slots_by_date.items()}
    return by_date, version


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ref_year = datetime.now().year

    all_data = {}

    for channel_name, channel_query in CHANNELS.items():
        print(f"[수집] {channel_name} ...")
        try:
            by_date, version = scrape_channel(channel_name, channel_query, ref_year)
        except Exception as e:
            print(f"  [실패] {channel_name}: {e}")
            by_date, version = {}, "ERROR"

        if version == "FAIL":
            print(f"  [경고] {channel_name}: 두 버전 모두 매칭 실패. 디버그 HTML 저장됨.")
        else:
            print(f"  [버전 {version}] {channel_name}")
            for iso_date, programs in sorted(by_date.items()):
                all_data.setdefault(iso_date, {})[channel_name] = programs
                print(f"    {iso_date}: {len(programs)}개 프로그램")

        time.sleep(REQUEST_DELAY_SEC)

    for iso_date, channels_data in sorted(all_data.items()):
        out_path = os.path.join(OUTPUT_DIR, f"{iso_date}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(channels_data, f, ensure_ascii=False, indent=2)
        print(f"[저장] {out_path}")

    print("\n완료.")


if __name__ == "__main__":
    main()
