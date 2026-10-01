# -*- coding: utf-8 -*-
"""닐슨코리아 일일 시청률 순위로 드라마/예능 주차 파일의 빈 요일을 보강한다.

네이버 '방영중' 위젯은 프로그램당 최신 회차 시청률 하나만 보여준다. 하루
4회 수집 사이에 위젯이 갱신되지 않거나 페이징이 흔들려 그 회차를 못 잡으면,
다음 회차가 뜨는 순간 값이 덮여 그 요일은 영영 비게 된다(2026-09-21 주차
일일드라마가 통째로 빈 사고). 네이버가 보여주는 숫자의 원본인 닐슨코리아
공개 순위(지상파 상위 20 / 종편·케이블 상위 10, 전국 가구 기준)를 날짜별로
조회해, 주차 파일에 이미 있는 프로그램의 비어 있는 방영 요일만 채운다.

범위를 '파일에 있는 프로그램의 빈 요일'로 한정하는 이유: 어떤 프로그램을
싣는지(편성 요일·시간, 링크, 표/기준 미만 구분)는 계속 네이버가 정하고,
닐슨은 그 안의 구멍만 메운다. 기준 미만 수치도 채운다 — 표에는 안 실리지만
(프론트가 요일별로 조회 기준을 적용) 다음 주 전주 대비 증감 계산에 쓰인다.
순위 밖(지상파 20위, 종편·케이블 10위 밖) 수치는 공개되지 않으므로 메우지
못하는 날도 있다.

표준 라이브러리만 쓴다(브라우저 불필요). 단독 실행도 가능:
  python nielsen_backfill.py --out-dir ../data/dramavariety --dry-run
"""

import argparse
import glob
import json
import os
import re
import urllib.request
from datetime import datetime, date as date_cls, timedelta, timezone

KST = timezone(timedelta(hours=9))
DAY_ORDER = ["월", "화", "수", "목", "금", "토", "일"]   # scrape_naver.DAY_ORDER와 동일
WEEK_FILE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\.json$')

NIELSEN_URL = ("https://www.nielsenkorea.co.kr/tv_terrestrial_day.asp"
               "?menu=Tit_1&sub_menu={sub_menu}&area=00&begin_date={ymd}")
PLATFORM_SUB_MENU = {"terrestrial": "1_1", "jongpyun": "2_1", "cable": "3_1"}
PLATFORM_LABEL = {"terrestrial": "지상파", "jongpyun": "종편", "cable": "케이블"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
FETCH_TIMEOUT_S = 20
# 이번 주와 지난주만 본다. 일요일 회차는 다음 주 월요일에 집계되므로 지난주까지는
# 봐야 하고, 그보다 오래된 주차는 네이버 수집이 끝난 확정 기록이라 건드리지 않는다.
LOOKBACK_DAYS = 14

TERRESTRIAL_CHANNELS = {"kbs1", "kbs2", "mbc", "sbs", "ebs1", "ebs2"}
JONGPYUN_CHANNELS = {"jtbc", "채널a", "tv조선", "mbn"}
# 닐슨 표기 → 우리 데이터(네이버) 표기. 비교 전에 공백을 지우고 소문자로 맞춘다.
CHANNEL_ALIASES = {"tvchosun": "tv조선"}

_TABLE_RE = re.compile(r'<table[^>]*class="ranking_tb"[^>]*>(.*?)</table>', re.S)
_ROW_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
_CELL_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
# 제목 칸 끝의 <본>(본방송)/<재>(재방송) 표기. 태그처럼 생겼지만 그냥 글자다.
_AIR_MARK_RE = re.compile(r'<(본|재)>')


def monday_of(d):
    return d - timedelta(days=d.weekday())


def normalize_channel(name):
    key = re.sub(r'\s+', '', (name or '')).lower()
    return CHANNEL_ALIASES.get(key, key)


def platform_for_channel(name):
    key = normalize_channel(name)
    if key in TERRESTRIAL_CHANNELS:
        return "terrestrial"
    if key in JONGPYUN_CHANNELS:
        return "jongpyun"
    return "cable"


def normalize_title(title):
    """닐슨은 띄어쓰기 없이 'KBS1일일드라마(엄마가미쳤어요)'처럼 적는다.
    글자·숫자만 남기고 소문자로 맞춰 '포함' 비교가 되게 한다."""
    s = _AIR_MARK_RE.sub('', title or '')
    return re.sub(r'[^0-9a-z가-힣]', '', s.lower())


def parse_ranking_html(html):
    """순위 페이지에서 시청률 표(첫 번째 ranking_tb)만 읽는다.
    두 번째 ranking_tb는 시청자 수(천 명, '1,036' 형태)라 건너뛴다."""
    m = _TABLE_RE.search(html or '')
    if not m:
        return []
    rows = []
    for tr in _ROW_RE.findall(m.group(1)):
        cells = _CELL_RE.findall(tr)
        if len(cells) < 4:
            continue
        rank_txt, channel_txt, title_txt, rating_txt = (c.strip() for c in cells[:4])
        try:
            rank = int(re.sub(r'<[^>]+>', '', rank_txt).strip())
            rating = float(re.sub(r'<[^>]+>', '', rating_txt).strip())
        except ValueError:
            continue
        mark = _AIR_MARK_RE.search(title_txt)
        title = re.sub(r'<[^>]+>', '', _AIR_MARK_RE.sub('', title_txt)).strip()
        rows.append({
            "rank": rank,
            "channel": re.sub(r'<[^>]+>', '', channel_txt).strip(),
            "title": title,
            "rerun": bool(mark and mark.group(1) == '재'),
            "rating": rating,
        })
    return rows


def find_rating(rows, channel, title):
    """같은 채널에서 제목이 맞는 행의 시청률. 없으면 None.
    닐슨 제목에 우리 제목이 포함되면 맞는 것으로 본다('티빙오리지널로또1등도출근합니다'
    ⊃ '로또1등도출근합니다'). 완전히 같은 제목 > 포함, 본방 > 재방, 그다음 높은 값 순."""
    want_ch = normalize_channel(channel)
    want = normalize_title(title)
    if len(want) < 2:
        return None
    best = None
    for r in rows:
        if normalize_channel(r["channel"]) != want_ch:
            continue
        have = normalize_title(r["title"])
        if have == want:
            score = 2
        elif want in have:
            score = 1
        else:
            continue
        key = (score, not r["rerun"], r["rating"])
        if best is None or key > best[0]:
            best = (key, r)
    return best[1]["rating"] if best else None


def fetch_ranking_html(platform, d):
    url = NIELSEN_URL.format(sub_menu=PLATFORM_SUB_MENU[platform], ymd=d.strftime("%Y%m%d"))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _md(d):
    return f"{d.month:02d}.{d.day:02d}"


def backfill_week_file(path, today, get_rows, dry_run=False):
    """한 주차 파일의 표(programs)와 기준 미만 기록(newBelowCutoff)에서 비어 있는
    방영 요일을 채운다. 채운 건수 반환.

    get_rows(platform, date) -> parse_ranking_html 결과(실패 시 빈 리스트)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        week_start = date_cls.fromisoformat(data["weekStart"])
    except Exception:
        return 0

    week_dates = {day: week_start + timedelta(days=i) for i, day in enumerate(DAY_ORDER)}
    filled = 0
    for p in data.get("programs", []) + data.get("newBelowCutoff", []):
        by_day = p.get("ratingByDay")
        if not isinstance(by_day, dict):
            by_day = {}
        platform = platform_for_channel(p.get("channel"))
        for day in DAY_ORDER:
            if day not in (p.get("days") or []) or day in by_day:
                continue
            d = week_dates[day]
            if d >= today:
                continue   # 시청률은 다음 날 집계된다
            rating = find_rating(get_rows(platform, d), p.get("channel"), p.get("title"))
            if rating is None:
                continue
            rating = round(rating, 1)   # 닐슨 소수 셋째 자리 → 네이버 표기와 같은 첫째 자리
            by_day[day] = {"rating": rating, "ratingDate": _md(d), "source": "nielsen"}
            p["ratingByDay"] = by_day
            # 대표 시청률(rating/ratingDate)은 '가장 최근 회차' 값이므로, 채운 날이
            # 지금 대표 값의 날짜보다 뒤면 교체한다.
            rep_date = next((wd for wd in week_dates.values() if _md(wd) == p.get("ratingDate")), None)
            if rep_date is None or d > rep_date:
                p["rating"], p["ratingDate"] = rating, _md(d)
            filled += 1
            print(f"  [닐슨 보강] {os.path.basename(path)} '{p.get('title')}' ({p.get('channel')})"
                  f" {day} {rating}% ← {PLATFORM_LABEL[platform]} {d.isoformat()}"
                  f"{' (미리보기)' if dry_run else ''}")

    if filled and not dry_run:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return filled


def backfill_recent_weeks(out_dir, today, fetch_html=None, dry_run=False):
    """이번 주·지난주 주차 파일을 보강한다. (날짜, 플랫폼)별 조회는 한 번만 한다.
    조회 실패는 그 날짜만 건너뛰고 전체 수집을 멈추지 않는다."""
    fetch_html = fetch_html or fetch_ranking_html
    since = monday_of(today) - timedelta(days=LOOKBACK_DAYS)
    cache = {}

    def get_rows(platform, d):
        key = (platform, d)
        if key not in cache:
            try:
                cache[key] = parse_ranking_html(fetch_html(platform, d))
            except Exception as e:
                print(f"  [닐슨 보강] {PLATFORM_LABEL[platform]} {d.isoformat()} 조회 실패(건너뜀): {e}")
                cache[key] = []
        return cache[key]

    total = 0
    for path in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
        name = os.path.basename(path)
        if not WEEK_FILE_RE.match(name):
            continue
        try:
            week_start = date_cls.fromisoformat(name[:-5])
        except ValueError:
            continue
        if week_start < since:
            continue
        total += backfill_week_file(path, today, get_rows, dry_run=dry_run)
    print(f"  [닐슨 보강] 총 {total}건 채움" + (" (미리보기, 저장 안 함)" if dry_run else ""))
    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="../data/dramavariety")
    parser.add_argument("--dry-run", action="store_true", help="채울 내용만 출력하고 저장하지 않음")
    args = parser.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = args.out_dir if os.path.isabs(args.out_dir) else os.path.normpath(os.path.join(here, args.out_dir))
    backfill_recent_weeks(out_dir, datetime.now(KST).date(), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
