# -*- coding: utf-8 -*-
"""
프로모션(카드할인) 일정 수집기 - 홈쇼핑 4사 (HD/GS/CJ/LT)

각 사 사이트의 "카드 혜택" 영역에서 날짜별 카드할인 일정(카드사/할인유형/할인율)을
수집해 하나의 JSON으로 저장한다. 프론트 "프로모션" 탭의 카드할인 서브탭이 참조한다.

== 저장 구조 ==
homeshopping/promotions/card_discounts.json
{
  "collected_at": "2026-08-01T06:40:00+09:00",
  "companies": {
    "HD": {
      "name": "현대홈쇼핑",
      "source": "https://www.hmall.com/md/dpl/index",
      "status": "ok",                      # ok | failed
      "error": "",                          # failed일 때 원인
      "last_success": "2026-08-01T06:40:00+09:00",
      "days": {
        "2026-08-02": [{"card": "현대카드", "type": "즉시할인", "rate": 5}]
      }
    },
    "GS": {...}, "CJ": {...}, "LT": {...}
  }
}

== 수집 정책 ==
- 과거 날짜는 이미 기록돼 있으면 보존 (이력 누적).
- 오늘 이후 날짜는 이번 수집 결과로 통째로 교체 (사이트가 미래 일정을 바꾸면 반영).
- 수집 실패한 회사는 기존 days를 그대로 유지하고 status="failed"로만 표시.

== 회사별 수집 방식 ==
| 회사 | 방식 | 상태 |
|---|---|---|
| HD | www.hmall.com/md/dpl/index 의 "한눈에 보는 카드 혜택" 스와이퍼.
|    | requests(SSR 포함 시) -> 실패하면 Playwright 렌더링. 구조 확정됨. | 확정 |
| LT | www.lotteimall.com 메인의 "카드 청구할인" ul.cardbox.
|    | Vue 렌더링이라 Playwright 필요할 가능성 높음. 구조 확정됨. | 확정 |
| CJ | display.cjonstyle.com 혜택 홈탭(H00009). 구조 미확정이라 렌더링 후
|    | 텍스트 휴리스틱(카드사명+할인율%)으로 추출. 실패 시 스냅샷 저장. | 실험 |
| GS | event.gsshop.com/event/gs-pay-tip. gsshop은 클라우드 IP를 차단하는
|    | 전력이 있고(README 참고) 이벤트 페이지가 이미지 위주일 수 있음.
|    | CJ와 같은 휴리스틱 + 실패 시 스냅샷 저장. | 실험 |

구조 미확정 회사(CJ/GS)는 파싱 실패 시 렌더링된 HTML을
homeshopping/promotions/_debug_{사}.html 로 저장한다. Actions 실행 후 이 스냅샷을
보고 파서를 확정하는 식으로 개선한다. (data/_debug_fail_*.html 과 같은 패턴)

== 회사 확장 ==
COMPANIES 리스트에 (코드, 이름, 소스URL, 수집함수)를 추가하면 된다.
NS(카드혜택 캘린더 table), 공영/NT/SK(이벤트 페이지) 등은 구조 확인 후 추가 예정.
KT(K쇼핑)는 혜택 영역이 이미지뿐이라 텍스트 파싱 불가 - 보류.

== 사용법 ==
  pip install requests beautifulsoup4 playwright
  playwright install chromium
  python promotion_scraper.py
"""

import os
import re
import json
import time
import traceback
from datetime import datetime, date, timedelta, timezone

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
OUTPUT_DIR = os.path.join("homeshopping", "promotions")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "card_discounts.json")

UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def now_kst():
    return datetime.now(KST)


def today_kst():
    return now_kst().date()


def infer_date(month, day, base=None):
    """'08.02'처럼 연도 없는 날짜의 연도를 추정한다.
    후보(작년/올해/내년) 중 오늘과 가장 가까운 날짜를 선택 (연말/연초 경계 대응)."""
    base = base or today_kst()
    candidates = []
    for year in (base.year - 1, base.year, base.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda d: abs((d - base).days))


def parse_rate(text):
    """텍스트에서 할인율 숫자만 추출 ('5 %', '7%', '최대 5%' -> 5, 7, 5)."""
    m = re.search(r"(\d{1,2}(?:\.\d)?)\s*%", text)
    if not m:
        return None
    rate = float(m.group(1))
    if rate <= 0 or rate > 50:  # 카드할인율 범위를 벗어나면 오탐으로 간주
        return None
    return int(rate) if rate == int(rate) else rate


def dedup_entries(entries):
    seen = set()
    out = []
    for e in entries:
        key = (e["card"], e["type"], e["rate"])
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


# ============ Playwright (필요할 때만 브라우저 기동) ============

_playwright = None
_browser = None


def _ensure_browser():
    global _playwright, _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
    return _browser


def close_browser():
    global _playwright, _browser
    if _browser is not None:
        _browser.close()
        _browser = None
    if _playwright is not None:
        _playwright.stop()
        _playwright = None


def _sel_count(page, selector):
    try:
        return page.locator(selector).count()
    except Exception:
        return 0


def drive_infinite_scroll(page, wait_selector=None, anchor_selector=None,
                          max_rounds=25, settle_ms=600, stale_limit=3,
                          max_seconds=75):
    """무한 스크롤(react-infinite-scroll-component) 페이지를 목표 섹션이
    DOM에 붙을 때까지 끝까지 내린다.

    hmall 모바일 메인이 2026-08 말 개편되면서 섹션들이 한 번에 렌더되지 않고
    스크롤에 따라 순차적으로 추가되는 구조로 바뀌었다. 이때
    window.scrollBy 만 반복하면 문서 바닥에 닿는 순간 더 이상 scroll 이벤트가
    발생하지 않아(스크롤 위치가 변하지 않으므로) 다음 배치가 영영 로드되지
    않는다. 그래서 바닥으로 내린 뒤 살짝 위로 올렸다 다시 내려 scroll 이벤트를
    한 번 더 강제로 발생시키고, 문서 높이가 더 이상 자라지 않을 때까지 반복한다.

    Actions 잡 전체 타임아웃(20분)을 지키려고 회차 수와 총 시간을 함께 제한한다.
    목표 셀렉터를 찾았으면 True, 못 찾고 끝났으면 False."""
    deadline = time.monotonic() + max_seconds
    stale = 0
    for _ in range(max_rounds):
        if time.monotonic() > deadline:
            break
        if wait_selector and _sel_count(page, wait_selector):
            return True
        # 섹션 컨테이너만 먼저 붙고 내용물이 lazy인 경우: 그 위치로 끌어당긴다
        if anchor_selector and _sel_count(page, anchor_selector):
            try:
                page.locator(anchor_selector).first.scroll_into_view_if_needed(timeout=3000)
                page.wait_for_timeout(1200)
            except Exception:
                pass
            if wait_selector and _sel_count(page, wait_selector):
                return True

        try:
            before = page.evaluate("document.documentElement.scrollHeight")
            page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(settle_ms)
            # 바닥에서 한 번 튕겨 scroll 이벤트를 다시 발생시킨다
            page.evaluate("window.scrollBy(0, -400)")
            page.wait_for_timeout(200)
            page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(settle_ms)
            after = page.evaluate("document.documentElement.scrollHeight")
        except Exception:
            break

        stale = stale + 1 if after <= before else 0
        if stale >= stale_limit:
            break
    return bool(wait_selector and _sel_count(page, wait_selector))


def rendered_anchors(html):
    """렌더된 섹션 앵커 목록 (실패 원인 진단용)."""
    found = set(re.findall(r'data-(?:scroll-)?anchor="([^"]+)"', html))
    return sorted(found)


def fetch_rendered(url, wait_selector=None, mobile=False, timeout_ms=30000,
                   scroll_steps=8, anchor_selector=None, click_selector=None,
                   infinite=False):
    """Playwright로 페이지를 렌더링해 HTML을 반환한다.
    카드혜택 영역이 lazy-load(스크롤로 화면에 가까워져야 렌더)인 경우가 많아
    화면 높이 단위로 단계적으로 스크롤한다. anchor_selector가 주어지면
    (섹션 컨테이너는 DOM에 있는데 내용물이 lazy인 케이스) 해당 요소를
    scroll_into_view로 강제 노출시킨다.
    click_selector가 주어지면 로딩 후 그 요소를 먼저 클릭한다(탭 전환).
    infinite=True면 단순 스크롤 대신 무한 스크롤 드라이버를 쓴다.
    wait_selector가 끝내 안 나타나도 예외를 던지지 않고 현재 HTML을 반환한다
    (호출부의 파서가 빈 결과를 내면 그때 실패 처리)."""
    browser = _ensure_browser()
    context = browser.new_context(
        user_agent=UA_MOBILE if mobile else UA_DESKTOP,
        viewport={"width": 390, "height": 844} if mobile else {"width": 1440, "height": 900},
        locale="ko-KR",
        # hmall '오늘' 슬라이드의 날짜 라벨은 브라우저 로컬 시간으로 계산된다.
        # Actions 러너는 UTC라 KST 새벽에 하루 전 날짜로 렌더링되므로 KST로 고정.
        timezone_id="Asia/Seoul",
    )
    try:
        page = context.new_page()
        # 무거운 페이지(롯데 메인 등)는 domcontentloaded가 타임아웃까지 안 오는
        # 경우가 있다. 네비게이션 자체는 시작됐을 수 있으므로 중단하지 않고
        # 이어서 셀렉터 대기/스크롤로 진행한다 (파싱이 비면 호출부에서 실패 처리).
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as e:
            print(f"    -> [경고] goto 타임아웃/오류({type(e).__name__}) - 로딩된 상태로 계속 진행")
        page.wait_for_timeout(2000)
        if click_selector:
            try:
                page.locator(click_selector).first.click(timeout=5000)
                page.wait_for_timeout(2500)
            except Exception as e:
                print(f"    -> [경고] 탭 클릭 실패({type(e).__name__}) - 현재 페이지로 계속 진행")
        if infinite:
            drive_infinite_scroll(page, wait_selector=wait_selector,
                                  anchor_selector=anchor_selector,
                                  max_rounds=scroll_steps)
        else:
            for i in range(scroll_steps):
                if wait_selector:
                    try:
                        if page.locator(wait_selector).count() > 0:
                            break
                    except Exception:
                        pass
                # 앵커 컨테이너가 이미 DOM에 있으면 그 위치로 바로 스크롤
                if anchor_selector:
                    try:
                        loc = page.locator(anchor_selector)
                        if loc.count() > 0:
                            loc.first.scroll_into_view_if_needed(timeout=3000)
                            page.wait_for_timeout(1200)
                            continue
                    except Exception:
                        pass
                page.evaluate("window.scrollBy(0, window.innerHeight * 0.9)")
                page.wait_for_timeout(600)
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=5000)
            except Exception:
                pass
        page.wait_for_timeout(1000)
        return page.content()
    finally:
        context.close()


def fetch_static(url, referer=None, mobile=False, timeout=15):
    headers = {"User-Agent": UA_MOBILE if mobile else UA_DESKTOP}
    if referer:
        headers["Referer"] = referer
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text


def save_debug(company, html):
    path = os.path.join(OUTPUT_DIR, f"_debug_{company}.html")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"    -> [디버그] 렌더링 HTML 저장: {path}")
    except Exception as e:
        print(f"    -> [디버그] 스냅샷 저장 실패: {e}")


# PROMO_DEBUG=true 면 파싱 성공 여부와 무관하게 렌더링 스냅샷을 저장한다.
# (CJ/GS처럼 휴리스틱으로 돌아가는 회사의 실제 DOM을 확보해 파서를 확정하는 용도.
#  promotion.yml의 workflow_dispatch 입력으로 켤 수 있다)
DEBUG_SNAPSHOTS = os.environ.get("PROMO_DEBUG", "").lower() in ("1", "true", "yes")


# ============ HD (현대홈쇼핑) ============
# www.hmall.com/md/dpl/index 의 "한눈에 보는 카드 혜택" 스와이퍼.
# 슬라이드 순서 = 날짜 순서이고, 같은 날짜에 카드가 2개 이상이면 두 번째 슬라이드부터는
# 날짜 라벨이 없다 (예: 08.04 카카오페이현대 + 하나 -> 하나 슬라이드엔 날짜 없음).
# 그래서 슬라이드를 순서대로 돌며 "직전 날짜"를 이어받는다.
# 해시 클래스명(a15pwr* 등)은 배포마다 바뀔 수 있어 쓰지 않고,
# data-anchor 속성과 태그 구조(p 2개 = 카드명/할인유형, 텍스트의 'N %')로만 파싱한다.

HD_URL = "https://www.hmall.com/md/dpl/index"
# 상단 탭 '혜택'(mainDispSeq=7 / mblMainTmplGbcd=07). 예전에는 /md/dpl/index가 이 탭을
# 바로 열어줘서 evnt_card_new 앵커가 정적으로 잡혔는데, 2026-08 말 개편 이후
# 기본 탭이 '현대홈쇼핑'(홈)으로 바뀌었다.
HD_BENEFIT_URL = "https://www.hmall.com/md/dpl/index?mainDispSeq=7&mblMainTmplGbcd=07"
HD_BENEFIT_TAB_SELECTOR = 'a[data-rel="혜택"], a[data-disptrtynmcd="event"]'

HD_WAIT_SELECTOR = ('[data-anchor="evnt_card_new"] .swiper-slide, '
                    '[data-scroll-anchor="evnt_card_new"] .swiper-slide, '
                    '[data-anchor="home_card"] .swiper-slide, '
                    '[data-scroll-anchor="home_card"] .swiper-slide')
HD_ANCHOR_SELECTOR = ('[data-anchor="evnt_card_new"], [data-scroll-anchor="evnt_card_new"], '
                      '[data-anchor="home_card"], [data-scroll-anchor="home_card"]')


def parse_hd(html):
    soup = BeautifulSoup(html, "html.parser")
    # 같은 스와이퍼가 페이지에 따라 다른 앵커명으로 붙는다:
    #  - /md/dpl/index (혜택 페이지): evnt_card_new
    #  - 모바일 메인:               home_card  (data-scroll-anchor만 있는 경우도 있음)
    container = None
    for name in ("evnt_card_new", "home_card", "event_card", "evnt_card"):
        for sel in (f'[data-anchor="{name}"]', f'[data-scroll-anchor="{name}"]'):
            container = soup.select_one(sel)
            if container is not None:
                break
        if container is not None:
            break
    if container is None:
        # 앵커명이 또 바뀌었을 때의 폴백: 섹션 제목 텍스트로 역추적
        # (개편으로 제목이 '한눈에 보는 카드 혜택' -> '카드 즉시할인' 등으로 바뀔 수 있다)
        title = soup.find(string=re.compile(r"한눈에 보는 카드 혜택|카드 즉시할인|카드혜택|카드 혜택"))
        if title:
            node = title.parent
            for _ in range(6):
                if node is None:
                    break
                if node.select_one(".swiper-slide"):
                    container = node
                    break
                node = node.parent
    if container is None:
        return {}

    days = {}
    cur_date = None
    for slide in container.select(".swiper-slide"):
        text = slide.get_text(" ", strip=True)

        # '오늘' 슬라이드의 날짜 라벨은 클라이언트 로컬 시간으로 계산돼
        # UTC 환경에선 하루 전으로 찍힌다 -> 라벨 대신 KST 오늘을 신뢰
        if "오늘" in text:
            cur_date = today_kst()
        else:
            m = re.search(r"(\d{1,2})\.(\d{1,2})\s*\(", text)
            if m:
                d = infer_date(int(m.group(1)), int(m.group(2)))
                if d:
                    cur_date = d
        if cur_date is None:
            continue

        # 마크업이 p 태그가 아닌 경우(개편 후 div/span)도 있어, p가 없으면
        # 슬라이드 안의 짧은 텍스트 줄들을 순서대로 훑어 카드명/할인유형을 잡는다.
        ps = [p.get_text(strip=True) for p in slide.find_all("p")]
        ps = [t for t in ps if t]
        if not ps:
            ps = [t.strip() for t in slide.stripped_strings]
            # 날짜 라벨('08.04(월)', '오늘')과 버튼 텍스트는 카드명이 아니다
            ps = [t for t in ps
                  if t and len(t) <= 20
                  and "오늘" not in t
                  and not re.match(r"^\d{1,2}\.\d{1,2}", t)
                  and not re.fullmatch(r"[\d.%\s]+", t)
                  and "알림" not in t]
        if not ps:
            continue
        card = ps[0]
        dc_type = next((t for t in ps[1:] if "할인" in t), "할인")
        # '알림신청' 버튼 텍스트 등이 섞여도 %가 붙은 숫자만 할인율로 인정
        rate = parse_rate(text)
        if not card or rate is None:
            continue

        key = cur_date.isoformat()
        days.setdefault(key, []).append({"card": card, "type": dc_type, "rate": rate})

    return {k: dedup_entries(v) for k, v in days.items()}


def scrape_hd():
    for url in (HD_BENEFIT_URL, HD_URL):
        try:
            html = fetch_static(url, referer="https://www.hmall.com/", mobile=True)
            days = parse_hd(html)
            if days:
                return days
        except Exception as e:
            print(f"    [HD] 정적 요청 실패({url}: {e})")
    print("    [HD] 정적 HTML에 카드혜택 영역 없음 -> Playwright 렌더링 시도")

    # /md/* 는 모바일 웹이라 모바일 컨텍스트로 연다 (rehd.py와 동일 접근).
    # 카드혜택 섹션은 스크롤로 화면에 가까워져야 렌더링되는 lazy 섹션이다.
    # 2026-08 말 개편으로 메인이 무한 스크롤(react-infinite-scroll-component)로 바뀌어
    # 섹션들이 스크롤에 따라 순차적으로 붙는다. 카드행사(home_card)는 목록 중간쯤이라
    # 바닥까지 반복해서 내려야 DOM에 나타난다 -> infinite=True.
    attempts = (
        ("혜택탭 URL", dict(url=HD_BENEFIT_URL, click_selector=None)),
        ("홈 + 무한스크롤", dict(url=HD_URL, click_selector=None)),
        ("홈 -> 혜택탭 클릭", dict(url=HD_URL, click_selector=HD_BENEFIT_TAB_SELECTOR)),
    )
    html = ""
    for label, kw in attempts:
        print(f"    [HD] 렌더링 시도: {label}")
        html = fetch_rendered(
            kw["url"], mobile=True, scroll_steps=25, infinite=True,
            click_selector=kw["click_selector"],
            wait_selector=HD_WAIT_SELECTOR, anchor_selector=HD_ANCHOR_SELECTOR)
        days = parse_hd(html)
        if days:
            if DEBUG_SNAPSHOTS:
                save_debug("HD", html)
            return days
        print(f"    [HD] {label} 실패 - 렌더된 섹션: {rendered_anchors(html)}")

    save_debug("HD", html)
    raise RuntimeError(
        "카드혜택 스와이퍼 파싱 결과 0건 (구조 변경 의심) - 렌더된 섹션: "
        + ", ".join(rendered_anchors(html)))


# ============ LT (롯데홈쇼핑) ============
# www.lotteimall.com 메인의 "카드 청구할인" 영역 ul.cardbox.
# li.card_list 순서 = 날짜 순서. h1이 '오늘'이거나 '8.3(월)' 형식이고,
# 같은 날짜의 두 번째 카드부터는 h1이 없어서 직전 날짜를 이어받는다.
# 배너 id(f_bnr_card_prom_1_5223439)는 전시번호가 붙어 바뀔 수 있으므로
# id 대신 ul.cardbox 클래스로 찾는다.

LT_URL = "https://www.lotteimall.com/main/viewMain.lotte?dpml_no=1"


def parse_lt(html):
    soup = BeautifulSoup(html, "html.parser")
    days = {}
    for ul in soup.select("ul.cardbox"):
        cur_date = None
        for li in ul.select("li.card_list"):
            h1 = li.find("h1")
            if h1:
                h1_text = h1.get_text(strip=True)
                if "오늘" in h1_text:
                    cur_date = today_kst()
                else:
                    m = re.search(r"(\d{1,2})\.(\d{1,2})", h1_text)
                    if m:
                        d = infer_date(int(m.group(1)), int(m.group(2)))
                        if d:
                            cur_date = d
            if cur_date is None:
                continue

            txts = [t.get_text(strip=True) for t in li.select(".txt1")]
            txts = [t for t in txts if t]
            img = li.find("img")
            card = (img.get("alt", "").strip() if img else "") or (txts[0] if txts else "")
            dc_type = next((t for t in txts if "할인" in t), "할인")
            percent_el = li.select_one(".percent")
            rate = parse_rate(percent_el.get_text(" ", strip=True) if percent_el
                              else li.get_text(" ", strip=True))
            if not card or rate is None:
                continue
            # 이미지 alt가 'KB국민'처럼 '카드' 없이 오는 경우 표기 통일
            if not card.endswith(("카드", "페이")):
                card += "카드"

            key = cur_date.isoformat()
            days.setdefault(key, []).append({"card": card, "type": dc_type, "rate": rate})

    return {k: dedup_entries(v) for k, v in days.items()}


def scrape_lt():
    try:
        html = fetch_static(LT_URL, referer="https://www.lotteimall.com/")
        days = parse_lt(html)
        if days:
            return days
        print("    [LT] 정적 HTML에 cardbox 없음 (Vue 렌더링) -> Playwright 시도")
    except Exception as e:
        print(f"    [LT] 정적 요청 실패({e}) -> Playwright 렌더링 시도")

    # 롯데 메인은 무거워서 로딩이 간헐적으로 매우 느리다 (goto 30초 타임아웃으로
    # 실패한 사례 있음). 타임아웃을 넉넉히 주고 실패 시 잠시 쉬었다 재시도.
    html = ""
    for attempt in range(1, 4):
        html = fetch_rendered(LT_URL, wait_selector="ul.cardbox li.card_list",
                              timeout_ms=45000)
        days = parse_lt(html)
        if days:
            return days
        print(f"    [LT] 렌더링 {attempt}/3회차 실패 (HTML {len(html)}자) -> 재시도")
        time.sleep(5)

    save_debug("LT", html)
    raise RuntimeError("cardbox 파싱 결과 0건 (3회 재시도 실패 - 로딩 지연 반복 또는 구조 변경)")


# ============ CJ (혜택탭 - 텍스트 휴리스틱) ============
# 스냅샷(_debug_CJ.html) 확인 결과 혜택탭에는 '오늘 적용중인' 카드혜택이
# "삼성카드 10% 5만원 이상 즉시할인 일부 행사상품" 같은 텍스트로 나열된다.
# 날짜 정보가 없는 대신 실제로 '오늘' 혜택이 맞아서, 오늘 날짜로 기록하는 게 정확하다.
# 할인유형(즉시/청구할인)이 %(할인율) 뒤에 오는 패턴이라 매칭 후 주변 텍스트에서 찾는다.

CARD_ENTRY_RE = re.compile(
    r"((?:카카오페이|네이버페이|토스페이)\s*"
    r"(?:KB\s*국민|국민|신한|삼성|현대|롯데|하나|우리|비씨|BC|NH\s*농협|농협|씨티)\s*(?:카드)?"
    r"|(?:KB\s*국민|국민|신한|삼성|현대|롯데|하나|우리|비씨|BC|NH\s*농협|농협|씨티)\s*(?:카드)?"
    r"|카카오페이|네이버페이|토스페이)"
    r"[\s]*(?:혜택)?[\s]*(즉시\s*할인|청구\s*할인|할인)?"
    r"[\s~]*(?:최대)?[\s~]*(\d{1,2}(?:\.\d)?)\s*%"
)


def parse_card_text_heuristic(html):
    """렌더링된 페이지 텍스트에서 '카드사 N%' 패턴을 추출한다 (날짜 미상 -> 오늘로 기록).
    - 할인유형이 % 앞/뒤 어디에 있든 매칭 주변(±30자)에서 찾는다
    - '5만원 이상' 같은 조건 문구는 note로 함께 저장
    - 상품 할인율('삼성 갤럭시 50%↓' 등) 오탐을 줄이기 위해 카드/페이 표기가 없으면
      할인 문구가 함께 있을 때만 채택한다"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)

    entries = []
    for m in CARD_ENTRY_RE.finditer(text):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        rate = parse_rate(m.group(3) + "%")
        if rate is None:
            continue

        # 할인유형: 매칭 내부 -> 없으면 매칭 직후 30자에서 탐색 (CJ는 % 뒤에 옴)
        after = text[m.end():m.end() + 30]
        if m.group(2):
            dc_type = re.sub(r"\s+", "", m.group(2))
        elif "즉시할인" in after.replace(" ", ""):
            dc_type = "즉시할인"
        elif "청구할인" in after.replace(" ", ""):
            dc_type = "청구할인"
        else:
            dc_type = ""

        if not re.search(r"(카드|페이)$", name):
            if not dc_type:  # 카드/페이 표기도, 할인 문구도 없으면 오탐 가능성 높음
                continue
            name += "카드"

        entry = {"card": name, "type": dc_type or "할인", "rate": rate}
        note_m = re.search(r"(\d+\s*만\s*원?\s*이상)", after)
        if note_m:
            entry["note"] = re.sub(r"\s+", "", note_m.group(1))
        entries.append(entry)

    entries = dedup_entries(entries)
    # '카카오페이 7%'와 '카카오페이 삼성카드 7%'처럼 같은 혜택이 축약/전체 표기로
    # 두 번 잡히면 더 구체적인 쪽만 남긴다
    entries = [e for e in entries
               if not any(o is not e and e["card"] != o["card"] and e["card"] in o["card"]
                          and e["rate"] == o["rate"] and e["type"] == o["type"]
                          for o in entries)]
    # 패턴이 비정상적으로 많이 잡히면 상품 목록 등을 오탐한 것으로 보고 버린다
    if len(entries) > 12:
        return []
    return entries


CJ_URL = "https://display.cjonstyle.com/p/homeTab/main?hmtabMenuId=H00009"


def parse_cj(html):
    """CJ 혜택탭의 '카드 혜택' 카드 목록. 사용자 제공 HTML로 확정한 구조:
      li.item_card > a.btn_benefit > span.benefit_wrap
        ├ strong.card_name : '삼성카드' | '카카오페이'
        ├ span.notify      : 결합카드 부기 ('카카오페이' 카드의 '삼성카드')
        ├ strong.range     : '~' (최대 할인율 표기)
        ├ strong.number    : '5'  + span.txt '%'
        └ span.txt_benefit : '즉시할인'
    날짜 라벨이 없는 '현재 적용중' 혜택이라 오늘 날짜로 기록한다.
    (쿠폰팩 등 비카드 item_card는 card_name이 없어 자연히 걸러짐)"""
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    for li in soup.select("li.item_card"):
        wrap = li.select_one("span.benefit_wrap")
        if wrap is None:
            continue
        name_el = wrap.select_one("strong.card_name")
        if name_el is None:
            continue
        name = name_el.get_text(strip=True)
        sub_el = wrap.select_one("span.notify")
        sub = sub_el.get_text(strip=True) if sub_el else ""
        if sub:
            name = f"{name} {sub}"

        num_el = wrap.select_one("strong.number")
        rate = parse_rate((num_el.get_text(strip=True) + "%") if num_el
                          else wrap.get_text(" ", strip=True))
        if not name or rate is None:
            continue

        type_el = wrap.select_one(".txt_benefit")
        dc_type = type_el.get_text(strip=True) if type_el else "할인"

        entry = {"card": name, "type": dc_type, "rate": rate}
        range_el = wrap.select_one("strong.range")
        if range_el and "~" in range_el.get_text():
            entry["max"] = True  # '~5%' = 최대 5% (프론트에서 ~ 접두 표시)
        entries.append(entry)

    entries = dedup_entries(entries)
    return {today_kst().isoformat(): entries} if entries else {}


def scrape_cj():
    html = fetch_rendered(CJ_URL, mobile=True)
    days = parse_cj(html)
    if not days:
        # 구조가 바뀌었을 때의 백업: 텍스트 휴리스틱
        entries = parse_card_text_heuristic(html)
        if entries:
            print("    [CJ] item_card 파서 실패 -> 텍스트 휴리스틱으로 백업 수집")
            days = {today_kst().isoformat(): entries}
    if DEBUG_SNAPSHOTS:
        save_debug("CJ", html)
    if not days:
        save_debug("CJ", html)
        raise RuntimeError("혜택탭에서 카드할인 카드를 못 찾음 (스냅샷 저장됨 - 구조 변경 의심)")
    return days


# ============ GS (m.gsshop 메인 "카드 혜택" 슬라이더) ============
# 처음엔 event.gsshop.com/event/gs-pay-tip 을 썼는데, 그 페이지는 월간 카드행사
# '안내' 페이지라 날짜별로 다른 카드가 전부 나열돼 있어 텍스트 휴리스틱이
# 전체를 '오늘' 혜택으로 오탐했다 (실제 오늘은 신한 5%뿐인데 5개 카드가 잡힘).
# m.gsshop.com 모바일 메인에 HD와 같은 날짜 슬라이더("카드 혜택")가 있어 이쪽을 쓴다.
# 사용자 제공 HTML로 확정한 구조:
#   section.card-slider > .x-scroll > ul.card-detail-box > li
#     ├ time.date        : '오늘' | '8.3(월)' | ''(빈값 = 직전 날짜 이어받음)
#     ├ div.card-gs-pay  : 있으면 GS Pay 결합 카드 (수집 제외 - 일반 카드행사만)
#     ├ div.card-name    : '신한카드'
#     ├ p.benefit-num    : '5 %'
#     └ p.benefit-txt    : '즉시할인' | '즉시할인 외'

GS_URL = "https://m.gsshop.com/index.gs"


def parse_gs(html):
    soup = BeautifulSoup(html, "html.parser")
    days = {}
    for ul in soup.select("section.card-slider ul.card-detail-box"):
        cur_date = None
        for li in ul.find_all("li", recursive=False):
            time_el = li.select_one("time.date")
            label = time_el.get_text(strip=True) if time_el else ""
            if label:
                if "오늘" in label:
                    cur_date = today_kst()
                else:
                    m = re.search(r"(\d{1,2})\.(\d{1,2})", label)
                    if m:
                        d = infer_date(int(m.group(1)), int(m.group(2)))
                        if d:
                            cur_date = d
            if cur_date is None:
                continue

            # GS Pay 결합 카드는 표시 대상에서 제외 (일반 카드행사만 수집)
            if li.select_one(".card-gs-pay"):
                continue
            name_el = li.select_one(".card-name")
            name = name_el.get_text(strip=True) if name_el else ""

            num_el = li.select_one(".benefit-num")
            rate = parse_rate(num_el.get_text(" ", strip=True) if num_el
                              else li.get_text(" ", strip=True))
            txt_el = li.select_one(".benefit-txt")
            dc_type = txt_el.get_text(" ", strip=True) if txt_el else "할인"
            if not name or rate is None:
                continue

            key = cur_date.isoformat()
            days.setdefault(key, []).append({"card": name, "type": dc_type, "rate": rate})

    return {k: dedup_entries(v) for k, v in days.items()}


def scrape_gs():
    """m.gsshop은 간헐적으로 1KB 미만의 빈 껍데기 문서('GS이숍의 새이름' 레거시
    타이틀만 있는 스텁)를 내려주는 경우가 있다 (같은 날 4:00 성공 -> 4:28 스텁 확인).
    차단 페이지는 아니고 일시 현상이라, 스텁이 오면 잠시 쉬었다가 재시도한다."""
    try:
        html = fetch_static(GS_URL, referer="https://m.gsshop.com/", mobile=True)
        days = parse_gs(html)
        if days:
            if DEBUG_SNAPSHOTS:
                save_debug("GS", html)
            return days
        print("    [GS] 정적 HTML에서 카드혜택 슬라이더 못 찾음 -> Playwright 렌더링 시도")
    except Exception as e:
        print(f"    [GS] 정적 요청 실패({e}) -> Playwright 렌더링 시도")

    html = ""
    for attempt in range(1, 4):
        html = fetch_rendered(GS_URL, mobile=True, scroll_steps=20)
        days = parse_gs(html)
        if days:
            if DEBUG_SNAPSHOTS:
                save_debug("GS", html)
            return days
        print(f"    [GS] 렌더링 {attempt}/3회차 실패 (HTML {len(html)}자"
              f"{' - 스텁 의심' if len(html) < 5000 else ''}) -> 재시도")
        time.sleep(5)

    save_debug("GS", html)
    raise RuntimeError("m.gsshop 메인에서 카드혜택 슬라이더를 못 찾음 (3회 재시도 실패 - "
                       "스텁 응답 반복 또는 구조 변경, 스냅샷 확인 필요)")


# ============ 회사 목록 (확장 지점) ============

COMPANIES = [
    {"code": "HD", "name": "현대홈쇼핑", "source": HD_URL, "fetch": scrape_hd},
    {"code": "GS", "name": "GS샵",      "source": GS_URL, "fetch": scrape_gs},
    {"code": "CJ", "name": "CJ온스타일", "source": CJ_URL, "fetch": scrape_cj},
    {"code": "LT", "name": "롯데홈쇼핑", "source": LT_URL, "fetch": scrape_lt},
    # 확장 후보 (구조 확인 후 파서 추가):
    #  NS : m.nsmall.com/store/atypical/benefit-coupon 의 카드혜택 캘린더(table.calendar-box)
    #  공영: gongyoungshop.kr 이벤트 상세 / NT: shoppingntmall.com/event/eventMain
    #  SK : skstoa.com/event / KT: 이미지 배너뿐이라 텍스트 파싱 불가(보류) / 홈앤·SSG: 소스 미확인
]


def load_existing():
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def merge_days(old_days, new_days, today_str):
    """과거 날짜는 기존 기록 보존, 오늘 이후는 이번 수집 결과로 교체."""
    merged = {d: v for d, v in (old_days or {}).items() if d < today_str}
    for d, v in new_days.items():
        if d < today_str and d in merged:
            continue  # 과거 기록은 덮지 않음
        merged[d] = v
    return dict(sorted(merged.items()))


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    existing = load_existing()
    old_companies = existing.get("companies", {})

    now_iso = now_kst().isoformat(timespec="seconds")
    today_str = today_kst().isoformat()
    result = {"collected_at": now_iso, "companies": {}}

    try:
        for co in COMPANIES:
            code = co["code"]
            old = old_companies.get(code, {})
            print(f"[{code}] {co['name']} 카드할인 수집 중...")
            entry = {
                "name": co["name"],
                "source": co["source"],
                "status": "ok",
                "error": "",
                "last_success": old.get("last_success", ""),
                "days": old.get("days", {}),
            }
            try:
                new_days = co["fetch"]()
                entry["days"] = merge_days(old.get("days", {}), new_days, today_str)
                entry["last_success"] = now_iso
                total = sum(len(v) for v in new_days.values())
                print(f"  -> {len(new_days)}일 / {total}건 수집")
            except Exception as e:
                entry["status"] = "failed"
                entry["error"] = str(e)
                print(f"  -> [실패] {e}")
                traceback.print_exc()
            result["companies"][code] = entry
    finally:
        close_browser()

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n저장: {OUTPUT_PATH}")

    ok = [c for c, v in result["companies"].items() if v["status"] == "ok"]
    fail = [c for c, v in result["companies"].items() if v["status"] != "ok"]
    print(f"성공: {', '.join(ok) or '없음'} / 실패: {', '.join(fail) or '없음'}")


if __name__ == "__main__":
    main()
