# -*- coding: utf-8 -*-
"""
hd_discover_pgm_items.py  (일회성 탐색 스크립트)

현대홈쇼핑 편성표(https://www.hmall.com/md/dpl/index)에서
"N개 상품 더보기"를 눌렀을 때 어떤 API가 호출되는지,
tv-list 응답 원본에 어떤 필드(bfmtNo, 상품수 등)가 있는지 알아내기 위한
탐색용 스크립트. GitHub Actions(네트워크 제한 없음)에서 workflow_dispatch로
실행하고, 결과 덤프를 레포에 커밋해서 분석한다.

출력: homeshopping/_debug_pgm_discovery/
  tvlist_raw_{날짜}.json      tv-list 응답 원본(모든 필드 그대로)
  api_calls.json              편성표 페이지가 호출한 모든 API URL 목록
  api_bodies.json             tv/brod 관련 API 응답 본문(잘라서)
  page_after_click.html       더보기 클릭 후 페이지 HTML
  click_log.json              클릭 시도 로그
"""

import os
import re
import json
import datetime
import requests

OUT_DIR = os.path.join("homeshopping", "_debug_pgm_discovery")
os.makedirs(OUT_DIR, exist_ok=True)

KST = datetime.timezone(datetime.timedelta(hours=9))
# 왕영은의 톡투게더는 토요일 08:20 방송 -> 오늘 이후 가장 가까운 토요일을 기본 타깃으로
today = datetime.datetime.now(KST).date()
target = today + datetime.timedelta(days=(5 - today.weekday()) % 7)
BROD_DT = os.environ.get("BROD_DT") or target.strftime("%Y%m%d")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def step1_dump_tvlist_raw():
    """tv-list 응답을 필드 하나도 안 버리고 그대로 덤프."""
    headers = {"User-Agent": UA, "Referer": "https://www.hmall.com/"}
    pages = {}
    for page in range(0, 10):
        url = (f"https://www.hmall.com/md/api/cache?url=/api/hf/dp/v1/main-tv-new/tv-list"
               f"&brodDt={BROD_DT}&brodPrrgPage={page}&brodType=etv&deviceInfo=pc")
        try:
            r = requests.get(url, headers=headers, timeout=12)
            pages[str(page)] = r.json() if r.status_code == 200 else {"_status": r.status_code}
        except Exception as e:
            pages[str(page)] = {"_error": str(e)}

    path = os.path.join(OUT_DIR, f"tvlist_raw_{BROD_DT}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False, indent=2)
    print(f"[1] tv-list 원본 저장: {path}")

    # 요약: 아이템별 (시작, 브랜드, 상품명) + 전체 필드명 집합
    all_keys = set()
    for pg in pages.values():
        for it in (pg.get("respData", {}) or {}).get("broadItemList", []) or []:
            all_keys.update(it.keys())
            print(f"    {it.get('brodStrtDtm')} | {it.get('brndNm')} | {(it.get('slitmNm') or '')[:30]}")
    print(f"[1] broadItemList 필드 전체: {sorted(all_keys)}")


def step2_playwright_capture():
    """편성표 페이지를 실제 브라우저로 열고, 모든 API 호출을 기록한 뒤
    '더보기' 류 요소를 클릭해서 추가 API가 뜨는지 본다."""
    from playwright.sync_api import sync_playwright

    api_calls = []       # 모든 요청 URL (시간순)
    api_bodies = {}      # tv/brod/pgm 관련 응답 본문
    click_log = []

    def on_response(resp):
        url = resp.url
        if "/api/" not in url and "api" not in url.split("/")[2]:
            return
        api_calls.append(url)
        if re.search(r"tv|brod|pgm|item", url, re.I):
            try:
                body = resp.text()
                api_bodies[url] = body[:20000]
            except Exception as e:
                api_bodies[url] = f"_read_error: {e}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(**p.devices["iPhone 13"])
        page = ctx.new_page()
        page.on("response", on_response)

        page.goto("https://www.hmall.com/md/dpl/index",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)

        # 타깃 날짜 탭 클릭 시도 (예: '18' 또는 '18토')
        day_num = str(int(BROD_DT[6:8]))
        for sel_text in (day_num, f"{day_num}토", f"{day_num}일"):
            try:
                loc = page.get_by_text(re.compile(rf"^{sel_text}"), exact=False).first
                if loc.count():
                    loc.click(timeout=3000)
                    click_log.append(f"date click ok: {sel_text}")
                    page.wait_for_timeout(4000)
                    break
            except Exception as e:
                click_log.append(f"date click fail({sel_text}): {e}")

        # '더보기' 류 버튼 전부 클릭 시도
        try:
            more_btns = page.locator("text=/더보기/")
            n = more_btns.count()
            click_log.append(f"더보기 후보 {n}개 발견")
            for i in range(min(n, 15)):
                try:
                    more_btns.nth(i).click(timeout=2000)
                    click_log.append(f"더보기 [{i}] 클릭 성공")
                    page.wait_for_timeout(1500)
                except Exception as e:
                    click_log.append(f"더보기 [{i}] 클릭 실패: {str(e)[:120]}")
        except Exception as e:
            click_log.append(f"더보기 탐색 실패: {e}")

        # 방송 슬롯 카드 자체를 눌러 상세 페이지의 API도 캡처 (첫 카드)
        try:
            slot = page.locator("text=/\\d{2}:\\d{2}\\s*[~-]\\s*\\d{2}:\\d{2}/").first
            if slot.count():
                slot.click(timeout=3000)
                click_log.append("슬롯 카드 클릭 성공")
                page.wait_for_timeout(5000)
        except Exception as e:
            click_log.append(f"슬롯 카드 클릭 실패: {str(e)[:120]}")

        with open(os.path.join(OUT_DIR, "page_after_click.html"), "w", encoding="utf-8") as f:
            f.write(page.content())
        browser.close()

    with open(os.path.join(OUT_DIR, "api_calls.json"), "w", encoding="utf-8") as f:
        json.dump(api_calls, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "api_bodies.json"), "w", encoding="utf-8") as f:
        json.dump(api_bodies, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "click_log.json"), "w", encoding="utf-8") as f:
        json.dump(click_log, f, ensure_ascii=False, indent=2)
    print(f"[2] API 호출 {len(api_calls)}건, 본문 {len(api_bodies)}건 저장")
    for line in click_log:
        print("    " + line)


if __name__ == "__main__":
    print(f"타깃 방송일: {BROD_DT}")
    step1_dump_tvlist_raw()
    step2_playwright_capture()
