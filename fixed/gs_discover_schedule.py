# -*- coding: utf-8 -*-
"""
gs_discover_schedule.py  (일회성 탐색 스크립트)

GS샵 '공식' TV 편성표를 찾는다. 현재 홈쇼핑 탭의 GS 데이터는 라방바에서
수집해서 고정PGM(지금 백지연 등) 표시가 없다. GS 공식 편성표에 프로그램명이
있으면, 그걸 시간 매칭으로 라방바 데이터에 입혀 뱃지를 달 수 있다.

1) tvHighlight(접근 확인된 페이지)에서 편성표로 가는 링크를 찾고
2) 후보 편성표 URL들을 requests로 받아 HTML/JSON 덤프
3) Playwright로 편성표 페이지를 열어 실제 XHR API와 응답을 캡처

출력: homeshopping/_debug_gs_discovery/
"""

import os
import re
import json
import datetime
import requests

OUT_DIR = os.path.join("homeshopping", "_debug_gs_discovery")
os.makedirs(OUT_DIR, exist_ok=True)

MOBILE_UA = ("Mozilla/5.0 (Linux; Android 13; SM-S918N) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")
HEADERS = {"User-Agent": MOBILE_UA, "Referer": "https://m.gsshop.com/index.gs",
           "Accept-Language": "ko-KR,ko;q=0.9"}

KST = datetime.timezone(datetime.timedelta(hours=9))
today = datetime.datetime.now(KST).date()
# 지금 백지연(목 20:45) / 소유진쇼(금 20:35)가 걸리는 가장 가까운 목요일
target = today + datetime.timedelta(days=(3 - today.weekday()) % 7)
BROD_DT = os.environ.get("BROD_DT") or target.strftime("%Y%m%d")


def save(name, content, binary=False):
    path = os.path.join(OUT_DIR, name)
    mode = "wb" if binary else "w"
    kwargs = {} if binary else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as f:
        f.write(content)
    print(f"저장: {path} ({os.path.getsize(path)} bytes)")


def step1_find_schedule_links():
    """tvHighlight 페이지에서 편성표 링크 후보를 찾는다."""
    r = requests.get("https://m.gsshop.com/main/tvHighlight?mseq=W00618",
                     headers=HEADERS, timeout=15)
    save("tvHighlight.html", r.text)
    links = set()
    for m in re.finditer(r'href="([^"]*)"', r.text):
        href = m.group(1)
        if re.search(r"schedul|bdSchedule|tvSchedule|편성", href, re.I):
            links.add(href)
    # 텍스트가 '편성표'인 앵커 주변도 수집
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>[^<]*편성[^<]*</a>', r.text):
        links.add(m.group(1))
    print("편성표 링크 후보:", links)
    return links


def step2_fetch_candidates(found_links):
    candidates = set(found_links) | {
        "https://m.gsshop.com/tv/tvScheduleMain?mseq=W00618",
        "https://m.gsshop.com/tv/tvSchedule",
        "https://m.gsshop.com/main/tvSchedule?mseq=W00618",
        f"https://m.gsshop.com/tv/tvScheduleMain?brodDt={BROD_DT}",
        "https://www.gsshop.com/shop/tv/tvScheduleList.gs",
    }
    for i, url in enumerate(sorted(candidates)):
        if url.startswith("/"):
            url = "https://m.gsshop.com" + url
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            body = r.text
            safe = re.sub(r"[^A-Za-z0-9]+", "_", url)[:80]
            save(f"cand_{i}_{r.status_code}_{safe}.html", body[:300000])
        except Exception as e:
            print(f"[실패] {url}: {e}")


def step3_playwright_capture():
    from playwright.sync_api import sync_playwright
    api_calls, api_bodies, log = [], {}, []

    def on_response(resp):
        url = resp.url
        if not re.search(r"gsshop\.com", url):
            return
        api_calls.append(url)
        if re.search(r"schedul|brod|pgm|broadcast", url, re.I):
            try:
                api_bodies[url] = resp.text()[:30000]
            except Exception as e:
                api_bodies[url] = f"_read_error: {e}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(**p.devices["iPhone 13"])
        page = ctx.new_page()
        page.on("response", on_response)
        # 모바일 TV 하이라이트 -> 편성표 링크 클릭 시도
        page.goto("https://m.gsshop.com/main/tvHighlight?mseq=W00618",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
        try:
            loc = page.get_by_text(re.compile("편성표")).first
            if loc.count():
                loc.click(timeout=4000)
                log.append("편성표 클릭 성공")
                page.wait_for_timeout(6000)
                log.append("URL: " + page.url)
                save("schedule_page.html", page.content())
        except Exception as e:
            log.append(f"편성표 클릭 실패: {str(e)[:150]}")
            # 실패 시 후보 URL 직접 진입
            try:
                page.goto("https://m.gsshop.com/tv/tvScheduleMain?mseq=W00618",
                          wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(6000)
                log.append("직접 진입 URL: " + page.url)
                save("schedule_page.html", page.content())
            except Exception as e2:
                log.append(f"직접 진입도 실패: {str(e2)[:150]}")
        browser.close()

    save("api_calls.json", json.dumps(api_calls, ensure_ascii=False, indent=2))
    save("api_bodies.json", json.dumps(api_bodies, ensure_ascii=False, indent=2))
    save("click_log.json", json.dumps(log, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    print(f"타깃 방송일: {BROD_DT}")
    links = step1_find_schedule_links()
    step2_fetch_candidates(links)
    step3_playwright_capture()
