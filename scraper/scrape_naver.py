"""
scrape_naver.py
네이버 "방영중한국드라마" / "방영예능" 검색 위젯 수집
- '전체' 탭 URL 강제 추출 및 다이렉트 접속 (클릭 씹힘 완벽 방어)
- 드라마/예능 모두 다중 페이지(페이징) 끝까지 수집
- 수집된 모든 데이터를 무조건 '이번 주' 파일에 덮어쓰기/누적
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, date as date_cls, timedelta, timezone
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

DRAMA_URL = "https://search.naver.com/search.naver?where=nexearch&sm=top_hty&fbm=0&ie=utf8&query=%EB%B0%A9%EC%98%81%EC%A4%91%ED%95%9C%EA%B5%AD%EB%93%9C%EB%9D%BC%EB%A7%88"
# 기존 URL에는 1회성 세션 토큰(tqi, ackey)이 하드코딩돼 있어, 매 실행마다
# 만료된/낯선 세션으로 요청하게 되는 문제가 있었다. 이 경우 네이버가 풀
# 위젯('전체' 탭 포함) 대신 "오늘의 예능"류의 축약 스냅샷만 내려줘서,
# 실행한 요일의 프로그램만 수집되는 증상(예: 매번 그날 하루치만 잡힘)으로
# 이어졌다. DRAMA_URL과 동일하게 세션 토큰 없는 깨끗한 쿼리로 통일한다.
VARIETY_URL = "https://search.naver.com/search.naver?where=nexearch&sm=top_hty&fbm=0&ie=utf8&query=%EB%B0%A9%EC%98%81%EC%98%88%EB%8A%A5"

MIN_RATING_DRAMA = 5.0
MIN_RATING_VARIETY = 1.0
KST = timezone(timedelta(hours=9))

DAY_ORDER = ["월", "화", "수", "목", "금", "토", "일"]
DAY_INDEX = {d: i for i, d in enumerate(DAY_ORDER)}
DEBUG = False

# 회차(N회) 추출용. 네이버 카드가 "1회", "제 1회", "12회차" 등 어떤 표기로
# 내려주더라도 잡히도록 느슨하게 매칭한다. 네이버가 회차를 아예 안 주는
# 레이아웃일 수도 있어서(카드 구성이 수시로 바뀜) 못 찾으면 None을 반환하고,
# 신규 판별은 이력 기반 폴백(recompute_new_flags)이 대신 처리한다.
EPISODE_RE = re.compile(r'(?:제\s*)?(\d{1,4})\s*회(?:차)?')
# "첫 방송"류 문구는 회차 숫자가 없어도 1회로 간주한다.
FIRST_AIR_RE = re.compile(r'첫\s*방송|첫방송|첫방|새\s*드라마|신규\s*편성')

WEEK_FILE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\.json$')

# 방영 기간 조회: 각 카드의 link는 그 프로그램의 네이버 정보 페이지로
# 연결되고, 거기에 방영 기간이 표기된다. 방영 중이면 "2026.07.03. ~",
# 종영했으면 "2026.07.03. ~ 2026.09.20." 처럼 끝나는 날짜가 붙는다.
# 앞 날짜는 신규(New), 뒤 날짜는 종영 판정의 근거로 쓴다.
#
# 왜 "이력에 처음 등장" 방식(firstSeen)을 쓰지 않는가: 시청률 컷오프
# (드라마 5%/예능 1%) 미달로 초반 몇 주 데이터에 안 잡히다가 뒤늦게
# 컷오프를 넘은 프로그램(예: 전현무계획4 — 1회는 7/3인데 7/17에야 1.3%로
# 처음 등장)이 신규로 오탐되기 때문. 첫 방송일은 이 문제가 없다.
FIRST_AIR_CACHE_FILE = "first_air_dates.json"
FIRST_AIR_RANGE_RE = re.compile(r'(20\d{2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?\s*~')
# 종영일: '~' 바로 뒤에 오는 날짜. 방영 중인 프로그램은 '~' 뒤가 비어 있거나
# 다른 채널명이 이어지므로(예: "2026.07.03.~, 채널S") 날짜가 안 잡힌다.
AIR_END_RE = re.compile(r'~\s*(20\d{2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})')
FIRST_AIR_LOOKUP_MAX = 60   # 한 번의 실행에서 상세 페이지를 방문할 최대 건수
FIRST_AIR_RETRY_DAYS = 7    # 조회 실패(날짜 못 찾음)한 프로그램의 재시도 간격
# 아직 종영일이 없는(=방영 중인) 프로그램을 다시 확인하는 간격. 방영 중이던
# 프로그램은 언제든 종영할 수 있으므로 주기적으로 다시 봐야 한다.
AIR_END_RECHECK_DAYS = 7


def monday_of(date_obj):
    return date_obj - timedelta(days=date_obj.weekday())


def resolve_rating_date(rating_date_str: str, today):
    """네이버가 주는 ratingDate("6.27" 형태, 연도 없음)를 실제 date로 추정한다.
    오늘(today) 기준으로 같은 해/작년 두 후보를 만들어, 오늘보다 미래가 아니면서
    가장 가까운(=가장 최근) 과거 날짜를 채택한다. 연말연초 경계(예: 1월에 12월
    데이터가 들어오는 경우)를 안전하게 처리하기 위함이다.
    파싱에 실패하면 None을 반환하고, 호출 쪽에서 오늘 날짜로 폴백한다."""
    if not rating_date_str:
        return None
    m = re.match(r'^(\d{1,2})\.(\d{1,2})$', rating_date_str.strip())
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))

    candidates = []
    for year in (today.year, today.year - 1):
        try:
            candidates.append(date_cls(year, month, day))
        except ValueError:
            continue

    # 오늘보다 미래인 후보는 제외 (시청률은 과거 방송 기준이므로 미래일 수 없음)
    past_candidates = [d for d in candidates if d <= today]
    if not past_candidates:
        # 전부 미래로 계산되면(예: 시계 오차) 그래도 가장 가까운 후보를 채택
        if not candidates:
            return None
        return min(candidates, key=lambda d: abs((d - today).days))

    return max(past_candidates)


# ==========================================
#              파서 및 병합 로직
# ==========================================

def expand_days(day_token: str):
    days = []
    clean_token = day_token.replace(" ", "").strip()
    for part in [p.strip() for p in clean_token.split(",")]:
        if not part:
            continue
        if "~" in part:
            try:
                start, end = [p.strip() for p in part.split("~")]
                si, ei = DAY_INDEX[start], DAY_INDEX[end]
                days.extend(DAY_ORDER[si:ei + 1])
            except KeyError:
                continue
        else:
            if part in DAY_INDEX:
                days.append(part)
    return days


def parse_schedule_text(schedule_text: str):
    groups = re.findall(r'\(([^)]+)\)\s*((?:오전|오후)\s*\d{1,2}:\d{2})', schedule_text)
    results = []
    for day_token, time_token in groups:
        expanded = expand_days(day_token)
        if expanded:
            results.append({"days": expanded, "time": time_token.strip()})
    return results


def parse_episode(li, title: str = ""):
    """카드에서 회차(N회)를 뽑아낸다. 못 찾으면 None.

    제목 안의 숫자(예: "슈퍼맨이 돌아왔다 500회 특집" 같은 표기)에 걸려
    엉뚱한 회차가 잡히지 않도록, 제목 텍스트는 검색 대상에서 제외한다."""
    texts = []
    for sel in ('div.sub_info', 'div.main_info', 'span.info_txt', 'em.mark', 'span.tag', 'span.badge'):
        for el in li.select(sel):
            texts.append(el.get_text(" ", strip=True))
    blob = " ".join(t for t in texts if t)
    if title:
        blob = blob.replace(title, " ")

    m = EPISODE_RE.search(blob)
    if m:
        try:
            ep = int(m.group(1))
        except ValueError:
            ep = None
        if ep is not None and 1 <= ep <= 3000:
            return ep

    if FIRST_AIR_RE.search(blob):
        return 1
    return None


def parse_card(li, category: str, base_url: str = "", today=None):
    title_tag = li.select_one('strong.title a')
    if not title_tag:
        return []
    title = title_tag.get_text(strip=True)
    link = title_tag.get('href', '')
    if base_url and link:
        link = urljoin(base_url, link)

    info_txt = li.select_one('div.main_info span.info_txt')
    if not info_txt:
        return []
    broadcaster_tag = info_txt.select_one('a.broadcaster')
    channel = broadcaster_tag.get_text(strip=True) if broadcaster_tag else ""
    full = info_txt.get_text(strip=True)
    schedule_text = full.replace(channel, "", 1).strip()
    slots = parse_schedule_text(schedule_text)
    if not slots:
        return []

    sub_info = li.select_one('div.sub_info span.info_txt')
    rating = None
    rating_date = None
    if sub_info:
        num_txt = sub_info.select_one('span.num_txt')
        if num_txt:
            try:
                rating = float(num_txt.get_text(strip=True).replace('%', ''))
            except ValueError:
                rating = None
        m = re.search(r'\(([\d.]+)\)', sub_info.get_text(strip=True))
        if m:
            rating_date = m.group(1).rstrip('.')

    if rating is None:
        return []

    episode = parse_episode(li, title=title)

    # 네이버 카드는 (월~목) 처럼 여러 요일을 한 줄로 묶어서 보여주지만,
    # 시청률/ratingDate는 그 그룹 전체가 아니라 "가장 최근 방영된 회차"
    # 딱 하나에 대한 값이다. 여기서 ratingDate를 실제 날짜로 환산해
    # 요일을 역산하고, 그 요일에만 시청률을 붙인다(ratingByDay). 매일
    # 스크래핑이 도니까 각 요일은 자기 방영일에 자연스럽게 채워지고,
    # 아직 그 회차가 안 온 다른 요일은 이번 실행에서 건드리지 않는다
    # (병합 단계에서 기존에 저장된 값을 그대로 보존).
    matched_weekday = None
    if today is not None and rating_date:
        resolved = resolve_rating_date(rating_date, today)
        if resolved is not None:
            matched_weekday = DAY_ORDER[resolved.weekday()]

    programs = []
    for slot in slots:
        # 식별자(ID)에는 title을 포함한다. 채널+시간대만으로는 같은
        # 채널/시간에 요일마다 전혀 다른 프로그램이 편성되는 경우를
        # 구분할 수 없다(예: KBS2 오후 10시는 요일마다 다른 예능).
        # 말줄임("...")으로 갈리는 제목 중복 문제는 아래 dedupe 단계에서
        # 같은 (category, channel, days, time) 그룹 내 제목을 정규화해
        # 별도로 처리한다.
        rating_by_day = {}
        if matched_weekday and matched_weekday in slot["days"]:
            # 회차도 시청률과 똑같이 "가장 최근 방영된 회차" 하나에 대한 값이라
            # 요일별 버킷에 함께 담아둔다. 이렇게 해두면 1회가 어느 요일에
            # 방영됐는지까지 남아, 나중에 회차가 올라가도(2회, 3회...) 그 주에
            # 1회가 있었다는 사실이 사라지지 않는다.
            entry = {"rating": rating, "ratingDate": rating_date}
            if episode is not None:
                entry["episode"] = episode
            rating_by_day[matched_weekday] = entry

        program = {
            "id": f"{category}_{title}_{channel}_{slot['time']}",
            "category": category,
            "channel": channel,
            "title": title,
            "days": slot["days"],
            "time": slot["time"],
            "rating": rating,
            "ratingDate": rating_date,
            "ratingByDay": rating_by_day,
            "link": link,
        }
        if episode is not None:
            program["episode"] = episode
        programs.append(program)
    return programs


def normalize_truncated_titles(programs: list):
    """네이버 위젯은 카드 레이아웃 상태에 따라 같은 프로그램의 제목을
    풀텍스트로 줄 때와, CSS 말줄임으로 끝을 "..."으로 잘라서 줄 때가
    섞여 있다(예: "콩콩팜팜 (...동물농장)" vs "콩콩팜팜 (...동...").
    title이 id에 포함되어 있어 이 차이만으로 같은 편성이 두 건으로
    갈라져 중복 표시되는 문제가 있었으므로, 같은 (category, channel,
    days, time) 조합 안에서는 말줄임 제목을 그 그룹의 가장 긴(풀)
    제목으로 통일한다."""
    groups = {}
    for p in programs:
        key = (p["category"], p["channel"], tuple(p["days"]), p["time"])
        groups.setdefault(key, []).append(p)

    for key, group in groups.items():
        if len(group) <= 1:
            continue
        titles = [p["title"] for p in group]
        # "..."으로 끝나는(말줄임된) 제목들을 후보에서 제외하고,
        # 남은 것 중 가장 긴 제목을 그 그룹의 대표 제목으로 삼는다.
        full_candidates = [t for t in titles if not t.endswith("...")]
        if not full_candidates:
            continue
        canonical = max(full_candidates, key=len)
        # 말줄임 제목이 대표 제목의 접두사일 때만(=정말 같은 프로그램이
        # 잘려서 생긴 텍스트일 때만) 치환한다. 우연히 같은 시간/채널에
        # 편성된 서로 다른 프로그램까지 잘못 합치지 않기 위한 방어.
        truncated_prefix_len = len(canonical) - 3
        for p in group:
            t = p["title"]
            if t == canonical or not t.endswith("..."):
                continue
            if truncated_prefix_len > 0 and canonical.startswith(t[:-3]):
                p["title"] = canonical


def dedupe_programs(programs: list):
    """동일한 프로그램의 쪼개진 요일 카드들을 하나로 합칩니다.

    주의: 네이버 위젯이 페이징 도중 같은 (category,title,channel,time) 조합의
    카드를 ratingDate가 다른 채로 중복 노출하는 경우가 있다(예: 갱신 중인
    스냅샷 차이). 단순히 "먼저 만난 카드"를 채택하면 더 오래된 ratingDate가
    살아남아 최신 시청률이 영구히 반영되지 않는 문제가 생긴다. 그래서 같은
    id가 다시 나타나면 ratingDate를 비교해 더 최신(과거가 아닌) 쪽을 채택한다."""
    normalize_truncated_titles(programs)
    # 제목을 정규화했으므로 id도 그에 맞춰 다시 계산한다.
    for p in programs:
        p["id"] = f"{p['category']}_{p['title']}_{p['channel']}_{p['time']}"

    today = datetime.now(KST).date()
    merged = {}
    for p in programs:
        key = p["id"]
        if key not in merged:
            merged[key] = p
            merged[key].setdefault("ratingByDay", {})
            continue

        existing_days = set(merged[key]["days"])
        existing_days.update(p["days"])
        merged[key]["days"] = [d for d in DAY_ORDER if d in existing_days]

        # 요일별 시청률(ratingByDay)은 서로 다른 요일 키를 담고 있을 뿐이니
        # 그냥 합치면 된다(같은 요일 키가 겹치면 나중 값으로 덮어써도 무방 —
        # 한 번의 실행 안에서는 같은 날짜 데이터라 차이가 없다).
        merged[key].setdefault("ratingByDay", {})
        merged[key]["ratingByDay"].update(p.get("ratingByDay", {}))

        # ratingDate가 더 최신인 카드로 rating/ratingDate/episode를 교체한다.
        existing_resolved = resolve_rating_date(merged[key].get("ratingDate"), today)
        new_resolved = resolve_rating_date(p.get("ratingDate"), today)
        if new_resolved and (existing_resolved is None or new_resolved > existing_resolved):
            merged[key]["rating"] = p["rating"]
            merged[key]["ratingDate"] = p["ratingDate"]
            if p.get("episode") is not None:
                merged[key]["episode"] = p["episode"]
        elif merged[key].get("episode") is None and p.get("episode") is not None:
            merged[key]["episode"] = p["episode"]

    return list(merged.values())


def parse_cards_from_html(html: str, category: str, min_rating: float = 5.0, base_url: str = "", today=None):
    if today is None:
        today = datetime.now(KST).date()
    soup = BeautifulSoup(html, 'lxml')
    results = []
    for li in soup.select('li.info_box'):
        for p in parse_card(li, category, base_url=base_url, today=today):
            if p["rating"] >= min_rating:
                results.append(p)
    return results


# ==========================================
#            페이지 전환 감지 헬퍼
# ==========================================

VISIBLE_SIG_JS = """
    () => Array.from(document.querySelectorAll('li.info_box'))
        .filter(el => el.offsetParent !== null)
        .map(el => {
            const titleEl = el.querySelector('strong.title');
            return titleEl ? titleEl.innerText.trim() : '';
        })
        .filter(t => t.length > 0)
        .join('|')
"""

PAGING_TEXT_JS = """
    () => {
        const el = document.querySelector('.cm_paging_area._kgs_page')
            || document.querySelector('.cm_paging_area')
            || document.querySelector('[class*="paging"]');
        return el ? el.innerText.replace(/\\s+/g, ' ').trim() : null;
    }
"""

def visible_signature(page):
    return page.evaluate(VISIBLE_SIG_JS)

def read_paging_text(page):
    try:
        return page.evaluate(PAGING_TEXT_JS)
    except Exception:
        return None

def parse_current_total(paging_text):
    if not paging_text:
        return None, None
    m = re.search(r'현재\s*(\d+)\s*전체\s*(\d+)', paging_text)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))

def click_next_and_wait(page, before_paging_text, before_visible_sig, timeout_s=12):
    next_btn = page.query_selector("a.pg_next._next")
    if not next_btn:
        return False

    aria_disabled = next_btn.get_attribute("aria-disabled")
    classes = next_btn.get_attribute("class") or ""
    if aria_disabled == "true" or "on" not in classes.split():
        return False

    try:
        next_btn.scroll_into_view_if_needed(timeout=3000)
        page.wait_for_timeout(200)
        next_btn.evaluate("node => node.click()")
    except Exception:
        return False

    before_cur, before_tot = parse_current_total(before_paging_text)

    steps = int(timeout_s / 0.5)
    for _ in range(steps):
        page.wait_for_timeout(500)
        after_paging_text = read_paging_text(page)
        cur, tot = parse_current_total(after_paging_text)

        # 페이지 번호(현재/전체)를 읽을 수 있으면 이걸 최우선 판정 기준으로 삼습니다.
        # 시그니처(카드 제목 목록)는 일부만 갱신된 과渡 상태에서도 "달라짐"으로
        # 오판해 페이지 전환이 끝나기 전에 HTML을 읽어버리는 원인이었습니다.
        if before_cur is not None:
            if cur is not None and cur != before_cur:
                # 전환 확인 후에도 잠깐 더 기다려 렌더링이 끝난 뒤 읽도록 합니다.
                page.wait_for_timeout(400)
                return True
            # 숫자를 신뢰할 수 있는 상황이면 시그니처만으로는 전환 완료로 보지 않습니다.
            continue

        after_visible_sig = visible_signature(page)
        if after_visible_sig and before_visible_sig and after_visible_sig != before_visible_sig:
            page.wait_for_timeout(400)
            return True
    return False


def click_all_days_tab(page, category):
    """
    파이썬(Playwright)의 마우스 클릭을 쓰지 않고, 
    브라우저 내부 JS로 직접 침투해 숨김 요소 에러(Not visible)를 원천 차단합니다.
    """
    try:
        page.wait_for_selector(".cm_tap_area", timeout=10000)

        js_code = """
            () => {
                const links = Array.from(document.querySelectorAll('.cm_tap_area ul li a'));
                for (const a of links) {
                    const text = a.innerText || a.textContent || '';
                    if (text.trim().includes('전체')) {
                        const href = a.getAttribute('href');
                        if (href && href !== '#' && href.trim() !== '') {
                            return { type: 'url', value: href };
                        }
                        a.click();
                        return { type: 'click', value: 'clicked' };
                    }
                }
                return null;
            }
        """
        result = page.evaluate(js_code)

        if result:
            if result['type'] == 'url':
                target_url = urljoin(page.url, result['value'])
                print(f"  [{category}] 🚀 '전체' 탭 주소 강제 추출 성공! 다이렉트 접속합니다.")
                safe_goto(page, target_url)
                page.wait_for_timeout(2000)
                return True
            elif result['type'] == 'click':
                print(f"  [{category}] 🚀 '전체' 탭 JS 강제 클릭 성공! (숨김 요소 무시)")
                page.wait_for_timeout(2500)
                return True
        else:
            print(f"  [{category}] '전체' 탭을 찾을 수 없습니다. (기본 화면 진행)")

    except Exception as e:
        print(f"  [{category}] '전체' 탭 이동 중 예외 발생: {e}")
    return False


# ---------- 네비게이션 헬퍼 (networkidle 타임아웃 방어) ----------

def safe_goto(page, url: str, retries: int = 3, timeout: int = 30000):
    """page.goto를 안전하게 수행한다.

    'networkidle'은 네이버 검색 페이지처럼 백그라운드에서 분석/광고 요청이
    계속 발생하는 페이지에서는 네트워크가 절대 idle 상태로 떨어지지 않아
    타임아웃이 자주 발생한다(실제로는 페이지가 정상 렌더링됐어도). 그래서
    'domcontentloaded'로 빠르게 진입한 뒤, 실제 콘텐츠(.cm_tap_area)가
    뜨는지를 명시적으로 기다리는 방식으로 바꾼다. 그래도 실패하면(러너
    IP 일시 차단/네트워크 불안정 등 진짜 장애) 잠깐 쉬었다가 재시도한다."""
    from playwright.sync_api import TimeoutError as PWTimeoutError

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            try:
                page.wait_for_selector(".cm_tap_area", timeout=15000)
            except PWTimeoutError:
                # 탭 영역이 끝내 안 뜨면 페이지 구조가 다르거나 비정상일 수
                # 있으니, 재시도 루프가 다시 판단하도록 예외를 올린다.
                raise
            return
        except PWTimeoutError as e:
            last_err = e
            print(f"  [safe_goto] 시도 {attempt}/{retries} 실패: {e}")
            if attempt < retries:
                page.wait_for_timeout(5000)
    raise last_err


# ---------- 데이터 수집 함수 ----------

def _collect_drama_pages(page, max_pages: int):
    """현재 로드된 drama 페이지에서 페이징을 끝까지 따라가며 카드를 수집한다."""
    all_programs = []
    page_num = 1
    max_retries_per_page = 3

    while page_num <= max_pages:
        page.wait_for_timeout(800)
        paging_text = read_paging_text(page)
        cur, tot = parse_current_total(paging_text)
        html = page.content()

        # 컷오프 미만 카드도 일단 모두 수집한다(신규 프로그램 안내용).
        # 컷오프 분리는 fetch_drama 마지막의 _split_by_cutoff에서 수행.
        programs = parse_cards_from_html(html, "drama", min_rating=0.0, base_url=DRAMA_URL)
        all_programs.extend(programs)

        if cur is not None and tot is not None:
            print(f"  [drama] page {page_num} (네이버 표시: 현재{cur}/전체{tot}) 수집 중...")
        else:
            print(f"  [drama] page {page_num} 수집 중...")

        if cur is not None and tot is not None and cur >= tot:
            break

        if cur is None and tot is None:
            break

        before_paging_text = paging_text
        before_visible_sig = visible_signature(page)

        advanced = False
        for attempt in range(1, max_retries_per_page + 1):
            advanced = click_next_and_wait(page, before_paging_text, before_visible_sig)
            if advanced:
                break
            page.wait_for_timeout(1000)

        if not advanced:
            break
        page_num += 1

    return all_programs


def recent_collection_baseline(out_dir: str, category: str, weeks: int = 4):
    """최근 '완료된' 주차들의 카테고리별 프로그램 수 중앙값을 낸다.

    수집 실패 감지의 기준선으로 쓴다. 위젯은 언제 조회하든 '현재 방영 중인
    프로그램 전체'를 보여주므로, 정상 수집이면 카테고리별 건수가 주차마다
    크게 튀지 않는다(실측: 예능 59~69, 드라마 5~9). 이번 수집이 이 기준선에
    한참 못 미치면 페이징 유실 등 부분 수집을 의심할 수 있다.

    진행 중인 주차는 아직 한 주치가 다 안 쌓여 건수가 적으므로 제외한다.
    중앙값을 쓰는 이유는, 과거에 부분 수집으로 유난히 적게 기록된 주차가
    섞여 있어도 기준선이 끌려 내려가지 않게 하기 위함이다."""
    try:
        this_monday = monday_of(datetime.now(KST).date()).isoformat()
    except Exception:
        this_monday = "9999-12-31"

    counts = []
    for name in reversed(_week_files(out_dir)):
        if name[:-5] >= this_monday:
            continue  # 진행 중인 주차 제외
        try:
            with open(os.path.join(out_dir, name), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        counts.append(sum(1 for p in d.get("programs", []) if p.get("category") == category))
        if len(counts) >= weeks:
            break

    if not counts:
        return None
    counts.sort()
    mid = len(counts) // 2
    return counts[mid] if len(counts) % 2 else (counts[mid - 1] + counts[mid]) / 2


# 이번 수집이 기준선의 이 비율보다 적으면 부분 수집을 의심해 재시도한다.
COLLECTION_MIN_RATIO = 0.75


def warn_if_short(category: str, count: int, baseline: float):
    """재시도까지 했는데도 기준선에 못 미치면 눈에 띄게 경고를 남긴다.

    이 경고가 뜬 주차는 그 주에만 잡히는 시청률을 놓쳤을 수 있고, 지난 뒤에는
    복구가 불가능하다(네이버는 프로그램당 최신 회차만 보여준다). 그러니
    Actions 로그에서 바로 눈에 띄어야 한다."""
    if not baseline:
        return
    if count < baseline * COLLECTION_MIN_RATIO:
        print(f"  [⚠️ 수집 부족] {category}: {count}건 — 최근 주차 기준선 {baseline:g}건의 "
              f"{count / baseline:.0%}에 그침. 재시도 후에도 회복되지 않았으므로 이번 실행은"
              f" 부분 수집일 가능성이 높습니다(그 주에만 잡히는 시청률을 놓쳤을 수 있음).")
    else:
        print(f"  [수집 점검] {category}: {count}건 (기준선 {baseline:g}건 대비 {count / baseline:.0%}) — 정상")


def _split_by_cutoff(programs: list, min_rating: float):
    """수집된 카드를 컷오프 이상(main)/미만(below)으로 나눈다.
    below는 표(그리드)에는 안 실리지만, 신규(1회차) 프로그램이 컷오프
    미만이라 표에서 안 보이는 경우를 표 아래에 따로 알려주기 위해 유지한다."""
    main = [p for p in programs if p["rating"] >= min_rating]
    below = [p for p in programs if p["rating"] < min_rating]
    return main, below


def fetch_drama(page, max_pages: int = 30, baseline: float = None):
    safe_goto(page, DRAMA_URL)
    click_all_days_tab(page, "drama")
    # '전체' 탭 클릭 직후 실제 위젯(페이징 포함)이 완전히 다시 렌더링되기 전에
    # 첫 페이징 텍스트를 읽으면 과도기 상태값(예: "현재1/전체1")을 그대로
    # 믿어버려 대부분의 카드를 놓치는 문제가 있었다. 첫 수집 전 별도로
    # 충분히 대기해 이 레이스 컨디션을 방지한다.
    page.wait_for_timeout(2000)
    all_programs = _collect_drama_pages(page, max_pages)
    main_programs, below_programs = _split_by_cutoff(all_programs, MIN_RATING_DRAMA)

    # variety와 동일한 이유로 drama도 부분/오늘자 스냅샷만 잡히는 경우를
    # 방어한다. drama는 보통 요일 여러 개(주 5회, 매일 등)에 걸쳐 편성되므로,
    # 카드가 어느 정도 쌓였는데도 요일이 1~2개로만 몰려있으면 '전체' 탭이
    # 아니라 부분 스냅샷일 가능성이 높다고 보고 재시도한다. 또한 카드 수
    # 자체가 비정상적으로 적은 경우(예: 페이징이 "1/1"로 잘못 읽혀 1페이지만
    # 수집된 경우)도 별도로 재시도 대상으로 잡는다 — 이 경우는 요일 조건과
    # 무관하게 절대적인 카드 수가 너무 적다는 것 자체가 신호이기 때문이다.
    # (판정은 기존과 동일하게 컷오프 이상 카드 기준으로 한다)
    distinct_days = {d for p in main_programs for d in p["days"]}
    too_few = len(main_programs) < 10
    narrow_days = len(main_programs) >= 5 and len(distinct_days) <= 2
    below_baseline = baseline and len(main_programs) < baseline * COLLECTION_MIN_RATIO
    if too_few or narrow_days or below_baseline:
        if too_few:
            reason = "카드 수가 비정상적으로 적음"
        elif narrow_days:
            reason = f"요일 {sorted(distinct_days)}에만 몰려있음"
        else:
            reason = f"최근 주차 기준선({baseline:g}건)의 {COLLECTION_MIN_RATIO:.0%}에 미달"
        print(f"  [drama] ⚠️ 수집된 {len(main_programs)}건 — {reason} "
              f"— 부분 수집으로 의심됨. 페이지를 다시 로드해 재시도합니다.")
        safe_goto(page, DRAMA_URL)
        page.wait_for_timeout(2500)
        click_all_days_tab(page, "drama")
        page.wait_for_timeout(2000)
        retry_programs = _collect_drama_pages(page, max_pages)
        retry_main, retry_below = _split_by_cutoff(retry_programs, MIN_RATING_DRAMA)
        if len(retry_main) > len(main_programs):
            print(f"  [drama] ✅ 재시도 후 {len(retry_main)}건으로 증가 — 재시도 결과를 채택합니다.")
            main_programs, below_programs = retry_main, retry_below
        else:
            print(f"  [drama] ⚠️ 재시도해도 {len(retry_main)}건으로 동일/더 적음 — "
                  f"원래 결과를 그대로 사용하되 데이터가 불완전할 수 있음에 유의.")

    warn_if_short("drama", len(main_programs), baseline)
    return dedupe_programs(main_programs), dedupe_programs(below_programs)


def _collect_variety_pages(page, max_pages: int):
    """현재 로드된 variety 페이지에서 페이징을 끝까지 따라가며 카드를 수집한다."""
    all_programs = []
    page_num = 1
    max_retries_per_page = 3

    while page_num <= max_pages:
        page.wait_for_timeout(800)
        paging_text = read_paging_text(page)
        cur, tot = parse_current_total(paging_text)
        html = page.content()

        # 컷오프 미만 카드도 일단 모두 수집한다(신규 프로그램 안내용).
        programs = parse_cards_from_html(html, "variety", min_rating=0.0, base_url=VARIETY_URL)
        all_programs.extend(programs)

        if cur is not None and tot is not None:
            print(f"  [variety] page {page_num} (네이버 표시: 현재{cur}/전체{tot}) 수집 중...")
        else:
            print(f"  [variety] page {page_num} 수집 중...")

        if cur is not None and tot is not None and cur >= tot:
            break

        before_paging_text = paging_text
        before_visible_sig = visible_signature(page)

        advanced = False
        for attempt in range(1, max_retries_per_page + 1):
            advanced = click_next_and_wait(page, before_paging_text, before_visible_sig)
            if advanced:
                break
            page.wait_for_timeout(1000)

        if not advanced:
            break
        page_num += 1

    return all_programs


def fetch_variety(page, max_pages: int = 30, baseline: float = None):
    safe_goto(page, VARIETY_URL)
    click_all_days_tab(page, "variety")
    all_programs = _collect_variety_pages(page, max_pages)
    main_programs, below_programs = _split_by_cutoff(all_programs, MIN_RATING_VARIETY)

    # 클릭 성공 여부(return값)만으로는 실제 데이터가 정상인지 알 수 없다
    # (클릭은 '성공'으로 찍혀도 위젯이 그날 하루치 축약 스냅샷을 보여주는
    # 경우가 있었다). 그래서 실제로 모인 데이터의 요일 분포를 직접
    # 점검한다 — variety는 정상이어도 프로그램별로 보통 1~2개 요일에만
    # 편성되지만, 정상 수집이라면 한 주 전체에 걸쳐 다양한 요일이 섞여
    # 나와야 한다. 거의 모든 카드가 단 하루(혹은 이틀 이하)에 몰려있으면
    # '전체' 탭이 아니라 '오늘' 스냅샷만 긁힌 것으로 간주하고 재시도한다.
    # (판정은 기존과 동일하게 컷오프 이상 카드 기준으로 한다)
    distinct_days = {d for p in main_programs for d in p["days"]}
    narrow_days = len(main_programs) >= 5 and len(distinct_days) <= 2
    below_baseline = baseline and len(main_programs) < baseline * COLLECTION_MIN_RATIO
    if narrow_days or below_baseline:
        cause = (f"요일 {sorted(distinct_days)}에만 몰려있음 — '오늘' 스냅샷만 잡힌 것으로 의심됨"
                 if narrow_days
                 else f"최근 주차 기준선({baseline:g}건)의 {COLLECTION_MIN_RATIO:.0%}에 미달 — 부분 수집 의심")
        print(f"  [variety] ⚠️ 수집된 {len(main_programs)}건이 {cause}. 페이지를 다시 로드해 재시도합니다.")
        safe_goto(page, VARIETY_URL)
        page.wait_for_timeout(1500)
        click_all_days_tab(page, "variety")
        retry_programs = _collect_variety_pages(page, max_pages)
        retry_main, retry_below = _split_by_cutoff(retry_programs, MIN_RATING_VARIETY)
        retry_days = {d for p in retry_main for d in p["days"]}
        if len(retry_days) > len(distinct_days) or len(retry_main) > len(main_programs):
            print(f"  [variety] ✅ 재시도 후 {len(retry_main)}건/요일 {sorted(retry_days)} — 재시도 결과를 채택합니다.")
            main_programs, below_programs = retry_main, retry_below
        else:
            print(f"  [variety] ⚠️ 재시도해도 {len(retry_main)}건/요일 {sorted(retry_days)}로 나아지지 않음 — "
                  f"원래 결과를 그대로 사용하되 데이터가 불완전할 수 있음에 유의.")

    warn_if_short("variety", len(main_programs), baseline)
    return dedupe_programs(main_programs), dedupe_programs(below_programs)


# ---------- 저장 로직 (ratingDate 기준으로 해당 주차 파일에 분배) ----------

def _validate_week_membership(monday_date, programs, today):
    """programs 중 ratingDate가 monday_date 주차(월~일) 범위 밖인 항목을 걸러낸다.
    weekStart/weekEnd와 안 맞는 program은 잘못된 경로(과거 로직의 잔존 오염,
    수동 편집 실수 등)로 그 파일에 끼어든 것이므로 보관하지 않는다.
    반환값: (정상 program 리스트, 제외된 program 리스트)"""
    week_start = monday_date
    week_end = monday_date + timedelta(days=6)

    valid, invalid = [], []
    for p in programs:
        resolved = resolve_rating_date(p.get("ratingDate"), today)
        if resolved is None or (week_start <= resolved <= week_end):
            # ratingDate가 없거나 파싱 불가하면 보수적으로 그대로 유지
            # (잘못 지우는 것보다 안전하게 두는 쪽을 택함)
            valid.append(p)
        else:
            invalid.append(p)
    return valid, invalid


def _merge_rating_by_day(existing_map: dict, new_map: dict):
    """요일별 값(ratingByDay)을 요일 단위가 아니라 '필드 단위'로 병합한다.

    dict.update로 요일 항목을 통째로 갈아끼우면, 예전 수집에서 잡아둔
    회차(episode)가 이번 수집(회차 없이 시청률만 잡힌 경우)에 의해 통째로
    지워진다. 1회 방영 사실이 그 주에 딱 한 번만 관측되는 값이라 이렇게
    날아가면 New 배지가 사라지므로, 같은 요일이면 필드를 덮어쓰되 새 값에
    없는 필드는 기존 값을 남긴다."""
    merged = {}
    for day, entry in (existing_map or {}).items():
        merged[day] = dict(entry) if isinstance(entry, dict) else entry
    for day, entry in (new_map or {}).items():
        if isinstance(entry, dict) and isinstance(merged.get(day), dict):
            merged[day].update(entry)
        else:
            merged[day] = dict(entry) if isinstance(entry, dict) else entry
    return merged


def collapse_time_variants(programs: list, fresh_ids: set = None):
    """같은 프로그램이 '시간 표기만 다르게' 두 건으로 갈린 것을 하나로 합친다.

    id가 (분류, 제목, 채널, 시간)이라서, 네이버가 주중에 편성 시간 표기를
    바꾸면(예: KBS2 개그콘서트가 오후 10:35 → 오후 09:20으로 바뀜) 같은 회차가
    서로 다른 id로 저장돼 그리드에 카드 두 장이 나란히 뜬다. 실제로
    2026-08-03 주차에서 개그콘서트(일)·살림하는 남자들 시즌2(토)가 각각
    시청률·측정일까지 똑같은 카드 두 장으로 표시됐다.

    요일 구성이 완전히 같을 때만 합친다. 아파트(JTBC)처럼 토 오후 10:40 /
    일 오후 10:30으로 요일마다 다른 시간에 편성되는 경우는 별개 슬롯이므로
    건드리지 않는다. 대표로 남길 항목은 이번 수집에서 확인된 시간(fresh_ids)을
    우선하고, 그다음 요일별 데이터가 더 풍부한 쪽을 택한다."""
    fresh_ids = fresh_ids or set()
    groups = {}
    for p in programs:
        key = (p["category"], p["title"], p["channel"], tuple(sorted(p["days"])))
        groups.setdefault(key, []).append(p)

    out = []
    for key, group in groups.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        # 대표 선정: 이번 수집에서 본 시간 > 요일별 데이터가 많은 쪽
        winner = max(group, key=lambda p: (p["id"] in fresh_ids, len(p.get("ratingByDay") or {})))
        for p in group:
            if p is winner:
                continue
            _carry_over_history(p, winner)
            print(f"  [시간표기 통합] '{p['title']}' ({p['channel']}) {p['time']} → {winner['time']}"
                  f" — 같은 요일({'/'.join(key[3])}) 중복 카드 병합")
        out.append(winner)
    return out


def _carry_over_history(existing_entry: dict, p: dict):
    """같은 id의 기존 저장분(existing_entry)이 갖고 있던 이력을 새 항목(p)에 옮긴다."""
    existing_days = set(existing_entry.get("days", []))
    existing_days.update(p.get("days", []))
    p["days"] = [d for d in DAY_ORDER if d in existing_days]
    p["ratingByDay"] = _merge_rating_by_day(existing_entry.get("ratingByDay"), p.get("ratingByDay"))
    if p.get("episode") is None and existing_entry.get("episode") is not None:
        p["episode"] = existing_entry["episode"]


def _merge_programs_into_file(out_dir: str, monday_date, programs: list):
    """programs를 monday_date가 속한 주차 파일에 머지 저장한다.
    (기존 dispatch_to_current_week의 병합 로직을 그대로 사용, 대상 주차만 인자로 받음)"""
    file_date = monday_date.isoformat()
    week_end = (monday_date + timedelta(days=6)).isoformat()
    file_path = os.path.join(out_dir, f"{file_date}.json")
    today = datetime.now(KST).date()

    existing_below = None
    if os.path.exists(file_path):
        try:
            with open(file_path, encoding="utf-8") as f:
                existing_data = json.load(f)
            existing_programs = existing_data.get("programs", [])
            existing_below = existing_data.get("newBelowCutoff")
        except Exception:
            existing_programs = []
    else:
        existing_programs = []

    # 정합성 체크: 기존에 저장돼있던 program 중 이 주차(weekStart~weekEnd)에
    # 속하지 않는 ratingDate를 가진 게 있으면 제거한다. 과거 로직(오늘 날짜
    # 기준으로 무조건 같은 파일에 쓰던 시절)의 잔존 오염이나, 다른 경로로
    # 잘못 들어온 데이터가 영구히 박혀있는 것을 막기 위함이다.
    existing_programs, contaminated = _validate_week_membership(monday_date, existing_programs, today)
    for p in contaminated:
        print(f"  [정합성 정리] {file_date}.json 에서 '{p['title']}'"
              f" ({p['channel']}, ratingDate={p.get('ratingDate')}) 제거 — 이 주차 범위 밖의 날짜")

    by_id = {p["id"]: p for p in existing_programs}
    for p in programs:
        if p["id"] in by_id:
            # 요일별 시청률은 "이번에 새로 들어온 요일"만 덮어쓰고, 나머지
            # 요일은 기존에 저장돼 있던 값을 그대로 보존한다. 이렇게 해야
            # 월화수목 같은 그룹에서 매일 실행할 때마다 그날 요일만 갱신되고
            # 나머지 요일이 그날 값으로 덮어써지지 않는다.
            _carry_over_history(by_id[p["id"]], p)
        by_id[p["id"]] = p

    # 기존에 누적 저장된 데이터(과거 회차에 말줄임으로 박혀있을 수 있음)와
    # 이번에 새로 수집한 데이터를 합친 전체 집합에 대해 다시 한 번
    # 말줄임 정규화 + id 재계산을 적용해, 주 단위로 쌓이는 과정에서도
    # 같은 프로그램이 풀제목/말줄임제목으로 갈려 중복되지 않게 한다.
    all_merged = list(by_id.values())
    normalize_truncated_titles(all_merged)
    by_id = {}
    for p in all_merged:
        p["id"] = f"{p['category']}_{p['title']}_{p['channel']}_{p['time']}"
        if p["id"] in by_id:
            _carry_over_history(by_id[p["id"]], p)
        by_id[p["id"]] = p

    # 네이버가 편성 시간 표기를 바꿔 같은 회차가 두 장의 카드로 갈린 경우 통합
    fresh_ids = {p["id"] for p in programs}
    final_programs = collapse_time_variants(list(by_id.values()), fresh_ids)

    merged_payload = {
        "weekStart": file_date,
        "weekEnd": week_end,
        "collectedAt": datetime.now(KST).isoformat(),
        "programs": final_programs,
    }
    # 컷오프 미만 신규 후보 목록(dispatch_below_cutoff가 관리)은 보존한다
    if existing_below is not None:
        merged_payload["newBelowCutoff"] = existing_below

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(merged_payload, f, ensure_ascii=False, indent=2)

    print(f"  [Merge Success] {file_date}.json 에 {len(merged_payload['programs'])}개 데이터 안착 완료!")


def dispatch_by_rating_date(out_dir: str, programs: list):
    """각 program을 ratingDate(실제 방송/시청률 측정 날짜) 기준으로
    소속 주차를 계산해 그 주차 파일에 분배 저장한다.

    배경: 토/일(주말) 시청률은 네이버 집계가 며칠 늦게 올라온다. 기존에는
    스크래핑 '실행 시점'(오늘)의 주차 파일에 무조건 몰아넣었기 때문에,
    예를 들어 6/29(월)에 수집된 6/27,28(토,일) 데이터가 6/22 주차가 아니라
    6/29 주차 파일로 잘못 들어가서:
      - 6/22 주차 파일은 토일 데이터가 영구히 비어 보임(직전 주 값이 잔류)
      - 6/29 주차 파일에는 아직 끝나지 않은 이번 주에 지난주 토일 데이터가 섞임
    이 문제가 있었다.

    수정 후에는 ratingDate를 파싱해 실제 방송 날짜를 구하고, 그 날짜가 속한
    주의 월요일 파일에 정확히 귀속시킨다. ratingDate가 없거나 파싱 실패하면
    안전하게 '오늘' 기준 주차로 폴백한다.
    """
    today = datetime.now(KST).date()
    today_monday = monday_of(today)

    buckets = {}  # monday_date -> [program, ...]
    for p in programs:
        resolved = resolve_rating_date(p.get("ratingDate"), today)
        target_monday = monday_of(resolved) if resolved else today_monday
        buckets.setdefault(target_monday, []).append(p)

    for target_monday, bucket_programs in sorted(buckets.items()):
        tag = "이번 주" if target_monday == today_monday else "과거 주차(소급 반영)"
        print(f"  [{tag}] {target_monday.isoformat()} 주차에 {len(bucket_programs)}개 분배")
        _merge_programs_into_file(out_dir, target_monday, bucket_programs)




def dispatch_below_cutoff(out_dir: str, programs: list):
    """컷오프 미만 프로그램을 ratingDate 기준 주차 파일의 newBelowCutoff에 저장한다.

    용도: 신규(1회차) 프로그램인데 시청률이 컷오프(드라마 5%/예능 1%) 미만이라
    표(programs)에 안 실리는 경우, 표 아래에 "New" 목록으로 알려주기 위함.
    저장 시점에는 첫 방송일을 모를 수 있어 일단 다 담아두고, 신규가 아닌 것으로
    판명된 항목(첫 방송일이 그 주 밖)은 recompute_new_flags가 정리한다."""
    today = datetime.now(KST).date()
    today_monday = monday_of(today)

    buckets = {}
    for p in programs:
        resolved = resolve_rating_date(p.get("ratingDate"), today)
        target_monday = monday_of(resolved) if resolved else today_monday
        buckets.setdefault(target_monday, []).append(p)

    for target_monday, bucket in sorted(buckets.items()):
        file_date = target_monday.isoformat()
        file_path = os.path.join(out_dir, f"{file_date}.json")
        if os.path.exists(file_path):
            try:
                with open(file_path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
        else:
            # 그 주차 파일이 아직 없으면(모든 카드가 컷오프 미만인 극단적 경우)
            # 빈 뼈대를 만들어서라도 신규 후보를 기록해둔다.
            data = {
                "weekStart": file_date,
                "weekEnd": (target_monday + timedelta(days=6)).isoformat(),
                "collectedAt": datetime.now(KST).isoformat(),
                "programs": [],
            }

        existing = data.get("newBelowCutoff", [])
        by_id = {p["id"]: p for p in existing}
        for p in bucket:
            if p["id"] in by_id:
                _carry_over_history(by_id[p["id"]], p)
                # 시청률은 더 최신 회차 값으로 유지
                old = by_id[p["id"]]
                old_resolved = resolve_rating_date(old.get("ratingDate"), today)
                new_resolved = resolve_rating_date(p.get("ratingDate"), today)
                if new_resolved and old_resolved and new_resolved < old_resolved:
                    p["rating"], p["ratingDate"] = old["rating"], old["ratingDate"]
            by_id[p["id"]] = p

        # 말줄임 제목 정규화 + id 재계산 (programs 병합과 동일한 이유)
        merged = list(by_id.values())
        normalize_truncated_titles(merged)
        by_id = {}
        for p in merged:
            p["id"] = f"{p['category']}_{p['title']}_{p['channel']}_{p['time']}"
            if p["id"] in by_id:
                _carry_over_history(by_id[p["id"]], p)
            by_id[p["id"]] = p

        data["newBelowCutoff"] = collapse_time_variants(
            list(by_id.values()), {p["id"] for p in bucket})
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  [컷오프 미만] {file_date}.json newBelowCutoff에 {len(data['newBelowCutoff'])}건 유지")


def prune_dropped_programs(out_dir: str, collected_ids: set, today):
    """직전 주차 파일에서 '그 주의 실측 근거가 없는' 항목만 정리한다.

    ⚠️ 과거에는 "이번 스크래핑에서 안 보이면 = 컷오프 미달로 떨어진 것"으로
    보고 직전 주차 파일에서 삭제했는데, 이게 데이터를 영구 소실시키는
    버그였다. 실제 사고 사례(2026-07-26):
      7/13 주차 파일에서 김부장(23.1%), 결혼의 완성(6.4%), 아파트(6.0%),
      오싹한 연애(5.3%), 사랑을 처방해 드립니다(14.5%)가 삭제됨.
      전부 그 뒤로도 계속 방영된 드라마고, 7/18~19(토·일) 시청률이 통째로
      사라져 그 주차 주말 칸이 빈 상태로 남았다.

    원인: 네이버 위젯 수집은 페이징이 불안정해서 한 번의 실행에서 일부
    카드를 놓치는 일이 흔하다(이 파일의 재시도 로직들이 그 방증). "한 번의
    수집에 안 보임"은 컷오프 하락이 아니라 그냥 수집 누락일 때가 많은데,
    30% 안전장치는 한꺼번에 대량 삭제되는 경우만 막아줄 뿐 매 실행 몇 건씩
    조금씩 지워지는 이 패턴은 통과시켜 버렸다.

    더 근본적으로, 주차 파일에 들어있는 항목은 "그 주에 이 시청률로 방영됐다"는
    과거의 사실 기록이다. 그 프로그램의 '현재' 시청률이 나중에 컷오프 밑으로
    떨어졌다고 해서 지난주 기록을 소급해 지울 이유가 없다.

    그래서 지금은 수집 여부(collected_ids)로는 아무것도 지우지 않고, 그 주의
    실측 근거(주차 범위 안의 ratingDate 또는 ratingByDay)가 전혀 없는 항목만
    정리한다. 주차 범위를 벗어난 오염 데이터는 _validate_week_membership이
    저장 시점에 이미 걸러낸다."""
    today_monday = monday_of(today)
    target_monday = today_monday - timedelta(days=7)
    target_week_end = target_monday + timedelta(days=6)

    file_path = os.path.join(out_dir, f"{target_monday.isoformat()}.json")
    if not os.path.exists(file_path):
        return
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return

    def has_week_evidence(p):
        resolved = resolve_rating_date(p.get("ratingDate"), today)
        if resolved and target_monday <= resolved <= target_week_end:
            return True
        for entry in (p.get("ratingByDay") or {}).values():
            if not isinstance(entry, dict):
                continue
            d = resolve_rating_date(entry.get("ratingDate"), today)
            if d and target_monday <= d <= target_week_end:
                return True
        # ratingDate를 아예 못 읽는 항목은 보수적으로 유지(잘못 지우는 것보다 안전)
        return resolve_rating_date(p.get("ratingDate"), today) is None

    existing_programs = data.get("programs", [])
    kept = [p for p in existing_programs if has_week_evidence(p)]
    dropped = [p for p in existing_programs if not has_week_evidence(p)]

    if not dropped:
        return

    for p in dropped:
        print(f"  [정리] {target_monday.isoformat()} 주차에서 '{p['title']}'"
              f" ({p['channel']}, ratingDate={p.get('ratingDate')}) 제거 — 이 주차의 실측 근거 없음")

    data["programs"] = kept
    data["collectedAt"] = datetime.now(KST).isoformat()
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- 신규(New) 프로그램 판정 ----------

def _clean_title(title: str):
    """제목 비교용 정규화. 말줄임("...")은 잘라내고 공백을 정리한다."""
    t = (title or "").strip()
    if t.endswith("..."):
        t = t[:-3].strip()
    return re.sub(r'\s+', ' ', t)


def _first_air_key(p: dict):
    return f"{p.get('category','')}|{_clean_title(p.get('title'))}|{p.get('channel','')}"


def has_first_episode(p: dict):
    """이 프로그램이 '1회'로 관측된 적이 있는지."""
    if p.get("episode") == 1:
        return True
    for entry in (p.get("ratingByDay") or {}).values():
        if isinstance(entry, dict) and entry.get("episode") == 1:
            return True
    return False


def _week_files(out_dir: str):
    names = [os.path.basename(p) for p in glob.glob(os.path.join(out_dir, "*.json"))]
    return sorted(n for n in names if WEEK_FILE_RE.match(n))


# ---------- 첫 방송일 조회 (네이버 프로그램 정보 페이지) ----------

def _first_air_cache_path(out_dir: str):
    return os.path.join(out_dir, FIRST_AIR_CACHE_FILE)


def load_first_air_cache(out_dir: str):
    path = _first_air_cache_path(out_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_first_air_cache(out_dir: str, cache: dict):
    with open(_first_air_cache_path(out_dir), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)


def parse_air_period_from_html(html: str, today=None):
    """프로그램 정보 페이지 HTML에서 방영 기간을 뽑아 (첫방송일, 종영일)로 반환.

    방영 중이면 "2026.07.03. ~" 라 종영일은 None이고, 종영했으면
    "2026.07.03. ~ 2026.09.20." 처럼 뒤 날짜가 붙는다.

    페이지 다른 곳의 날짜 범위(광고, 다른 카드)에 걸리지 않도록 프로그램
    정보 모듈 안쪽부터 좁게 찾고, 없으면 점점 넓힌다. 첫방송일이 미래거나
    말도 안 되는 과거(2000년 이전)면 버린다."""
    if today is None:
        today = datetime.now(KST).date()
    soup = BeautifulSoup(html, 'lxml')
    scopes = []
    for sel in ('.cs_common_module', '.detail_info', '.cm_content_wrap'):
        el = soup.select_one(sel)
        if el:
            scopes.append(el.get_text(" ", strip=True))
    scopes.append(soup.get_text(" ", strip=True))

    for text in scopes:
        m = FIRST_AIR_RANGE_RE.search(text)
        if not m:
            continue
        try:
            start = date_cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if not (date_cls(2000, 1, 1) <= start <= today):
            continue

        end = None
        me = AIR_END_RE.search(text, m.end() - 1)
        if me:
            try:
                cand = date_cls(int(me.group(1)), int(me.group(2)), int(me.group(3)))
            except ValueError:
                cand = None
            # 종영일은 첫방송일보다 뒤여야 한다(엉뚱한 날짜 매칭 방어)
            if cand and cand >= start:
                end = cand
        return start, end
    return None, None


def _stored_program_links(out_dir: str, since: str = "2026-06-29"):
    """주차 파일에 저장된 프로그램들의 link를 모은다.

    종영한 프로그램은 네이버 '방영중' 위젯에서 빠지기 때문에 이번 수집
    대상에는 절대 안 들어온다. 그런데 우리가 종영일을 알아야 하는 대상이
    바로 그 프로그램들이다. 그래서 조회 후보를 이번 수집분만이 아니라
    저장된 과거 주차 데이터에서도 끌어온다(최신 주차부터)."""
    links = {}
    for name in reversed(_week_files(out_dir)):
        if name[:-5] < since:
            break
        try:
            with open(os.path.join(out_dir, name), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        for p in d.get("programs", []) + d.get("newBelowCutoff", []):
            key = _first_air_key(p)
            if key not in links and p.get("link"):
                links[key] = p["link"]
    return links


def lookup_air_periods(page, programs: list, out_dir: str):
    """프로그램들의 방영 기간(첫방송일/종영일)을 상세 페이지에서 조회해
    캐시(first_air_dates.json)에 채운다.

    조회 대상 우선순위:
      1) 첫방송일조차 모르는 프로그램 (신규 판정에 바로 필요)
      2) 방영 중으로 알고 있는데 마지막 확인이 오래된 프로그램 (종영 여부 확인)
    종영일까지 확보한 프로그램은 더 이상 조회하지 않는다."""
    cache = load_first_air_cache(out_dir)
    today = datetime.now(KST).date()
    now_iso = datetime.now(KST).isoformat()

    # 같은 프로그램이 슬롯별로 여러 항목일 수 있으니 키 기준으로 유니크하게.
    # 이번 수집분을 먼저 넣고, 저장된 과거 주차 데이터로 보충한다(종영작 포함).
    targets = {}
    for p in programs:
        key = _first_air_key(p)
        if key not in targets and p.get("link"):
            targets[key] = p["link"]
    for key, link in _stored_program_links(out_dir).items():
        targets.setdefault(key, link)

    def days_since_check(ent):
        try:
            return (today - datetime.fromisoformat(ent.get("checkedAt", "")).date()).days
        except ValueError:
            return 10 ** 6

    need_start, need_end = [], []
    for key, link in targets.items():
        ent = cache.get(key)
        if not ent:
            need_start.append((key, link))
            continue
        if ent.get("endDate"):
            continue  # 종영일까지 확보 — 더 볼 것 없음
        if not ent.get("date"):
            if days_since_check(ent) >= FIRST_AIR_RETRY_DAYS:
                need_start.append((key, link))
            continue
        # 첫방송일은 아는데 종영 여부를 모르는 상태 — 주기적으로 다시 확인
        if days_since_check(ent) >= AIR_END_RECHECK_DAYS:
            need_end.append((key, link))

    queue = need_start + need_end
    if len(queue) > FIRST_AIR_LOOKUP_MAX:
        print(f"  [방영기간] 조회 대기 {len(queue)}건 중 이번 실행은 {FIRST_AIR_LOOKUP_MAX}건만 "
              f"— 나머지는 다음 실행에서 계속 (신규 판정 우선)")
        queue = queue[:FIRST_AIR_LOOKUP_MAX]

    looked = 0
    for key, link in queue:
        looked += 1
        start = end = None
        try:
            page.goto(link, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(800)
            start, end = parse_air_period_from_html(page.content(), today)
        except Exception as e:
            print(f"  [방영기간] '{key}' 조회 실패: {e}")
        prev = cache.get(key) or {}
        cache[key] = {
            "date": start.isoformat() if start else prev.get("date"),
            "endDate": end.isoformat() if end else None,
            "checkedAt": now_iso,
        }
        print(f"  [방영기간] {key} -> 첫방송 {cache[key]['date'] or '못 찾음'}"
              f"{' / 종영 ' + cache[key]['endDate'] if cache[key]['endDate'] else ''}")

    if looked:
        save_first_air_cache(out_dir, cache)
    known = sum(1 for v in cache.values() if v.get("date"))
    ended = sum(1 for v in cache.values() if v.get("endDate"))
    print(f"  [방영기간] 캐시 현황: 첫방송 {known}건 / 종영 {ended}건 / 전체 {len(cache)}건"
          f" (이번 실행 {looked}건 조회, 대기 {max(0, len(need_start)+len(need_end)-looked)}건)")
    return cache


def recompute_new_flags(out_dir: str):
    """주차 파일 전체를 훑어 각 프로그램에 신규(isNew)·종영(isEnded)을 붙인다.

    신규 판정 우선순위:
      1) episode == 1 — 네이버 카드에 '1회'가 표기돼 있으면 그게 가장 확실한
         신규 신호다(newReason="episode").
      2) 첫 방송일이 그 주(월~일) 안 — 상세 페이지에서 조회해 캐시해둔
         firstAirDate 기준(newReason="firstAir"). 컷오프 미달로 초반 몇 주
         데이터에 없다가 뒤늦게 등장한 프로그램은 첫 방송일이 이미 지난
         주차라 신규로 찍히지 않는다.

    종영 판정: 캐시된 종영일이 그 주(월~일) 안이면 isEnded.

    매 실행마다 전체를 다시 계산하므로 결과는 멱등이고, 실제로 값이
    바뀐 파일만 다시 쓴다."""
    files = _week_files(out_dir)
    if not files:
        return

    cache = load_first_air_cache(out_dir)

    # 교차검증용: 각 프로그램이 데이터에 처음 등장한 주차(월요일).
    # 상세 페이지에서 뽑은 첫 방송일이 가끔 엉뚱한 값일 때가 있다
    # (예: 가요무대 — 수십 년 된 프로그램인데 '다음 방송' 날짜로 추정되는
    # 2026.08.11.~ 이 잡힘). 첫 방송일보다 이전 주차 데이터에 그 프로그램이
    # 이미 존재한다면 그 첫 방송일은 모순이므로 신규 판정에 쓰지 않는다.
    earliest_week = {}
    for name in files:
        try:
            with open(os.path.join(out_dir, name), encoding="utf-8") as f:
                d = json.load(f)
            ws = date_cls.fromisoformat(name[:-5])
        except Exception:
            continue
        for p in d.get("programs", []) + d.get("newBelowCutoff", []):
            key = _first_air_key(p)
            if key not in earliest_week or ws < earliest_week[key]:
                earliest_week[key] = ws

    for name in files:
        path = os.path.join(out_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        try:
            week_start = date_cls.fromisoformat(name[:-5])
        except ValueError:
            continue
        week_end = week_start + timedelta(days=6)

        programs = data.get("programs", [])
        changed = False
        new_count = 0

        def judge_ended(p):
            """종영일이 이 주(월~일) 안이면 종영 주차로 본다."""
            ent = cache.get(_first_air_key(p)) or {}
            if not ent.get("endDate"):
                return False, None
            try:
                end = date_cls.fromisoformat(ent["endDate"])
            except ValueError:
                return False, None
            return (week_start <= end <= week_end), end.isoformat()

        def judge(p):
            """(is_new, reason, first_air_known) 판정. episode==1 또는
            첫 방송일이 이 주(월~일) 안이면 신규."""
            ent = cache.get(_first_air_key(p)) or {}
            first_air = None
            if ent.get("date"):
                try:
                    first_air = date_cls.fromisoformat(ent["date"])
                except ValueError:
                    first_air = None
            if has_first_episode(p):
                return True, "episode", first_air is not None
            if first_air and week_start <= first_air <= week_end:
                # 모순 검사: 첫 방송일 이전 주차에 이미 등장한 프로그램이면
                # 그 첫 방송일은 잘못 파싱된 값 — 신규 아님으로 처리
                seen_from = earliest_week.get(_first_air_key(p))
                if seen_from is not None and seen_from < monday_of(first_air):
                    return False, None, True
                return True, "firstAir", True
            return False, None, first_air is not None

        def apply_flags(p):
            """isNew/newReason/isEnded/endDate를 다시 계산해 붙인다.
            변경이 있었으면 True를 반환(파일 재기록 판단용)."""
            touched = False
            is_new, reason, _ = judge(p)
            if is_new:
                if p.get("isNew") is not True or p.get("newReason") != reason:
                    touched = True
                p["isNew"] = True
                p["newReason"] = reason
            else:
                if "isNew" in p or "newReason" in p:
                    touched = True
                p.pop("isNew", None)
                p.pop("newReason", None)

            ended, end_iso = judge_ended(p)
            if ended:
                if p.get("isEnded") is not True or p.get("endDate") != end_iso:
                    touched = True
                p["isEnded"] = True
                p["endDate"] = end_iso
            else:
                if "isEnded" in p or "endDate" in p:
                    touched = True
                p.pop("isEnded", None)
                p.pop("endDate", None)
            return touched, is_new, ended

        end_count = 0
        for p in programs:
            touched, is_new, ended = apply_flags(p)
            changed = changed or touched
            new_count += 1 if is_new else 0
            end_count += 1 if ended else 0

        # 컷오프 미만 목록(newBelowCutoff): 같은 판정을 적용하되,
        # - 표(programs)에 이미 있는 프로그램(주중에 컷오프를 넘은 경우)은 제거
        #   → 그리드 카드의 배지가 대신 보여준다
        # - 신규도 종영도 아닌 것으로 확정된 항목은 제거(보여줄 게 없음)
        # - 첫 방송일을 아직 모르는 항목은 나중에 판정할 수 있게 보류(표시는 안 됨)
        below = data.get("newBelowCutoff", [])
        if below:
            main_keys = {_first_air_key(p) for p in programs}
            kept_below = []
            below_new, below_end = 0, 0
            for p in below:
                if _first_air_key(p) in main_keys:
                    changed = True
                    continue
                touched, is_new, ended = apply_flags(p)
                changed = changed or touched
                _, _, known = judge(p)
                if is_new or ended:
                    below_new += 1 if is_new else 0
                    below_end += 1 if ended else 0
                    kept_below.append(p)
                elif known:
                    changed = True  # 신규·종영 아님 확정 → 목록에서 제거
                else:
                    kept_below.append(p)  # 첫 방송일 미확인 — 판정 보류
            data["newBelowCutoff"] = kept_below
            if below_new or below_end:
                print(f"  [판정] {name}: 컷오프 미만 신규 {below_new}건 / 종영 {below_end}건")

        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"  [판정] {name}: New {new_count}건 / End {end_count}건 (총 {len(programs)}건)")


def report_episode_coverage(programs: list):
    """네이버 카드에서 회차(N회)가 실제로 몇 건이나 잡혔는지 로그로 남긴다.
    네이버 위젯 마크업은 수시로 바뀌기 때문에, 어느 날 갑자기 회차가 0건이
    되면(=1회 기반 New 판정이 폴백으로만 동작하게 되면) 이 로그로 바로
    알아챌 수 있게 하기 위함이다."""
    with_ep = [p for p in programs if p.get("episode") is not None]
    firsts = [p for p in programs if has_first_episode(p)]
    print(f"  [회차] 전체 {len(programs)}건 중 {len(with_ep)}건에서 회차 정보 추출"
          f" / 그중 1회(신규) {len(firsts)}건")
    for p in firsts:
        print(f"    - 1회: '{p['title']}' ({p['channel']}, {p.get('ratingDate')})")
    if not with_ep and programs:
        print("    ⚠️ 회차 정보가 하나도 안 잡혔습니다 — 네이버 카드가 회차를 안 주는"
              " 마크업일 수 있습니다(신규 판정은 이력 기반 폴백으로 동작).")


def main():
    global DEBUG
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="../data/dramavariety")
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--headful", action="store_true")
    args = parser.parse_args()
    DEBUG = args.debug

    CURRENT_FILE_DIR = os.path.dirname(os.path.abspath(__file__))
    final_out_dir = os.path.isabs(args.out_dir) and args.out_dir or os.path.normpath(os.path.join(CURRENT_FILE_DIR, args.out_dir))
    os.makedirs(final_out_dir, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headful, args=["--disable-dev-shm-usage"])
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            locale="ko-KR"
        )
        page.set_default_timeout(25000)

        # 최근 완료된 주차들의 수집량을 기준선으로 삼아 부분 수집을 감지한다.
        drama_baseline = recent_collection_baseline(final_out_dir, "drama")
        variety_baseline = recent_collection_baseline(final_out_dir, "variety")
        print(f"수집 기준선(최근 주차 중앙값): 드라마 {drama_baseline or '없음'} / 예능 {variety_baseline or '없음'}")

        print("collecting drama...")
        drama_programs, drama_below = fetch_drama(page, max_pages=args.max_pages, baseline=drama_baseline)

        print("collecting variety...")
        variety_programs, variety_below = fetch_variety(page, max_pages=args.max_pages, baseline=variety_baseline)

        below_raw_programs = drama_below + variety_below

        # 신규(New) 판정용 첫 방송일 조회 — 아직 캐시에 없는 프로그램만
        # 상세 페이지를 방문한다. 실패해도 수집 결과 저장에는 영향 없음.
        # 컷오프 미만 프로그램도 포함(컷오프 이상 우선 조회) — 신규인데
        # 시청률 미달로 표에 안 실리는 경우를 표 아래에 알려주기 위해.
        print("looking up air periods (first air / end dates)...")
        try:
            lookup_air_periods(page, drama_programs + variety_programs + below_raw_programs, final_out_dir)
        except Exception as e:
            print(f"  [방영기간] 조회 단계 전체 실패(무시하고 진행): {e}")

        browser.close()

    all_raw_programs = drama_programs + variety_programs
    report_episode_coverage(all_raw_programs + below_raw_programs)
    dispatch_by_rating_date(final_out_dir, all_raw_programs)
    dispatch_below_cutoff(final_out_dir, below_raw_programs)

    # 이번에 수집된 전체 program id 집합을 기준으로, 직전 주차 파일에서
    # 컷오프 미달로 더 이상 안 보이는 항목을 정리한다.
    collected_ids = {p["id"] for p in all_raw_programs}
    today = datetime.now(KST).date()
    prune_dropped_programs(final_out_dir, collected_ids, today)

    # 저장이 끝난 뒤 전체 주차를 다시 훑어 신규(New) 여부를 갱신한다.
    # 이번 실행에서 과거 주차로 소급 반영된 데이터까지 반영되도록 마지막에 돈다.
    recompute_new_flags(final_out_dir)


if __name__ == "__main__":
    main()
