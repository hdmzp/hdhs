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


def fetch_rendered(url, wait_selector=None, mobile=False, timeout_ms=30000, scroll_steps=8):
    """Playwright로 페이지를 렌더링해 HTML을 반환한다.
    카드혜택 영역이 lazy-load(스크롤 시 렌더)인 경우가 많아 단계적으로 스크롤한다.
    wait_selector가 끝내 안 나타나도 예외를 던지지 않고 현재 HTML을 반환한다
    (호출부의 파서가 빈 결과를 내면 그때 실패 처리)."""
    browser = _ensure_browser()
    context = browser.new_context(
        user_agent=UA_MOBILE if mobile else UA_DESKTOP,
        viewport={"width": 390, "height": 844} if mobile else {"width": 1440, "height": 900},
        locale="ko-KR",
    )
    try:
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2000)
        for i in range(scroll_steps):
            if wait_selector:
                try:
                    if page.locator(wait_selector).count() > 0:
                        break
                except Exception:
                    pass
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(700)
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


# ============ HD (현대홈쇼핑) ============
# www.hmall.com/md/dpl/index 의 "한눈에 보는 카드 혜택" 스와이퍼.
# 슬라이드 순서 = 날짜 순서이고, 같은 날짜에 카드가 2개 이상이면 두 번째 슬라이드부터는
# 날짜 라벨이 없다 (예: 08.04 카카오페이현대 + 하나 -> 하나 슬라이드엔 날짜 없음).
# 그래서 슬라이드를 순서대로 돌며 "직전 날짜"를 이어받는다.
# 해시 클래스명(a15pwr* 등)은 배포마다 바뀔 수 있어 쓰지 않고,
# data-anchor 속성과 태그 구조(p 2개 = 카드명/할인유형, 텍스트의 'N %')로만 파싱한다.

HD_URL = "https://www.hmall.com/md/dpl/index"


def parse_hd(html):
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one('[data-anchor="evnt_card_new"]')
    if container is None:
        # 앵커명이 바뀌었을 때의 폴백: 섹션 제목 텍스트로 역추적
        title = soup.find(string=re.compile("한눈에 보는 카드 혜택"))
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

        m = re.search(r"(\d{1,2})\.(\d{1,2})\s*\(", text)
        if m:
            d = infer_date(int(m.group(1)), int(m.group(2)))
            if d:
                cur_date = d
        elif "오늘" in text and cur_date is None:
            cur_date = today_kst()
        if cur_date is None:
            continue

        ps = [p.get_text(strip=True) for p in slide.find_all("p")]
        ps = [t for t in ps if t]
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
    try:
        html = fetch_static(HD_URL, referer="https://www.hmall.com/")
        days = parse_hd(html)
        if days:
            return days
        print("    [HD] 정적 HTML에 카드혜택 영역 없음 -> Playwright 렌더링 시도")
    except Exception as e:
        print(f"    [HD] 정적 요청 실패({e}) -> Playwright 렌더링 시도")

    html = fetch_rendered(HD_URL, wait_selector='[data-anchor="evnt_card_new"] .swiper-slide')
    days = parse_hd(html)
    if not days:
        save_debug("HD", html)
        raise RuntimeError("카드혜택 스와이퍼 파싱 결과 0건 (구조 변경 의심)")
    return days


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

    html = fetch_rendered(LT_URL, wait_selector="ul.cardbox li.card_list")
    days = parse_lt(html)
    if not days:
        save_debug("LT", html)
        raise RuntimeError("cardbox 파싱 결과 0건 (구조 변경 의심)")
    return days


# ============ CJ / GS (구조 미확정 - 텍스트 휴리스틱) ============
# 페이지 구조를 아직 못 봐서(스냅샷 미확보), 렌더링된 텍스트에서
# "카드사명 (+할인유형) N%" 패턴을 찾는 휴리스틱으로 시작한다.
# 날짜 정보를 함께 못 잡으므로 일단 '오늘' 혜택으로만 기록한다.
# 첫 Actions 실행에서 _debug_{사}.html 스냅샷이 남으면 그걸 보고 파서를 확정할 것.

CARD_ENTRY_RE = re.compile(
    r"((?:카카오페이\s*현대|카카오페이|네이버페이|토스페이|"
    r"(?:KB\s*)?국민|신한|삼성|현대|롯데|하나|우리|비씨|BC|NH\s*농협|농협|씨티)"
    r"\s*(?:카드|페이)?)"
    r"[\s]*(즉시\s*할인|청구\s*할인|할인)?"
    r"[\s]*(?:최대)?[\s]*(\d{1,2}(?:\.\d)?)\s*%"
)


def parse_card_text_heuristic(html):
    """렌더링된 페이지 텍스트에서 '카드사 N%' 패턴을 추출한다 (날짜 미상 -> 오늘로 기록).
    상품 할인율('삼성 갤럭시 50%↓' 등) 오탐을 줄이기 위해
    카드/페이 표기가 없으면 '할인' 문구가 함께 있을 때만 채택한다."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)

    entries = []
    for m in CARD_ENTRY_RE.finditer(text):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        dc_type = re.sub(r"\s+", "", m.group(2)) if m.group(2) else ""
        rate = parse_rate(m.group(3) + "%")
        if rate is None:
            continue
        if not re.search(r"(카드|페이)$", name):
            if not dc_type:  # 카드/페이 표기도, 할인 문구도 없으면 오탐 가능성 높음
                continue
            name += "카드"
        entries.append({"card": name, "type": dc_type or "할인", "rate": rate})

    entries = dedup_entries(entries)
    # 패턴이 비정상적으로 많이 잡히면 상품 목록 등을 오탐한 것으로 보고 버린다
    if len(entries) > 12:
        return []
    return entries


CJ_URL = "https://display.cjonstyle.com/p/homeTab/main?hmtabMenuId=H00009"


def scrape_cj():
    html = fetch_rendered(CJ_URL, mobile=True)
    entries = parse_card_text_heuristic(html)
    if not entries:
        save_debug("CJ", html)
        raise RuntimeError("혜택탭에서 카드할인 패턴을 못 찾음 (스냅샷 저장됨 - 파서 확정 필요)")
    return {today_kst().isoformat(): entries}


GS_URL = "https://event.gsshop.com/event/gs-pay-tip"


def scrape_gs():
    html = None
    try:
        html = fetch_static(GS_URL, referer="https://www.gsshop.com/", mobile=True)
        entries = parse_card_text_heuristic(html)
        if entries:
            return {today_kst().isoformat(): entries}
        print("    [GS] 정적 HTML에서 패턴 못 찾음 -> Playwright 렌더링 시도")
    except Exception as e:
        print(f"    [GS] 정적 요청 실패({e}) -> Playwright 렌더링 시도")

    html = fetch_rendered(GS_URL, mobile=True)
    entries = parse_card_text_heuristic(html)
    if not entries:
        save_debug("GS", html)
        raise RuntimeError("이벤트 페이지에서 카드할인 패턴을 못 찾음 "
                           "(클라우드 IP 차단 또는 이미지 구성 - 스냅샷 확인 필요)")
    return {today_kst().isoformat(): entries}


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
