# -*- coding: utf-8 -*-
"""
건강 프로그램 회차 단서 수집기 (PPL 소재 추정용)

지상파·종편·케이블 편성(data/{날짜}.json)에서 건강 프로그램 본방 회차를 골라,
네이버 검색으로 그 회차가 다룬 내용을 짐작할 수 있는 '단서'를 모은다.

== 단서 출처 ==
1) 공식 회차정보: '{프로그램명}' 검색 시 방송정보 위젯의 회차 목록 (li._item)
   - "714 회 2026.10.06(화) 환절기 마른 기침 ..." 형태. 일부 프로그램만 제공, 최근 6회분.
   - 프로그램 하나 검색으로 여러 회차가 한꺼번에 채워진다.
2) 검색 결과 제목: '{프로그램명} {N}회' 검색 결과 중 프로그램명과 'N회'가 함께 들어간 제목
   - 블로그·뉴스·예고영상 제목에 PPL 성분이 그대로 나오는 경우가 많다
     (예: '건강한 집2 다이어트 유산균 K2 효능').

== 저장 ==
data/episode_clues/{YYYY-MM}.json (방송일 기준 월별)
  { "<채널>|<프로그램>|<회차 또는 날짜>": {
      "ch", "series", "epi", "date", "start",
      "official": "공식 회차 소개" | "",
      "clues": ["검색 결과 제목", ...],
      "tries": 시도 횟수, "updated": ISO시각 } }

== 사용법 ==
    python episode_clue_scraper.py              # 최근 4일(오늘 포함) 방영분
    python episode_clue_scraper.py --days 30    # 지난 30일 채우기 (요청 상한 MAX_REQUESTS)
    python episode_clue_scraper.py --dry-run    # 저장 없이 결과만 출력
"""

import argparse
import glob
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://www.naver.com/",
}
BASE_URL = "https://search.naver.com/search.naver"
SCHEDULE_DIR = "data"
OUT_DIR = os.path.join("data", "episode_clues")
REQUEST_DELAY_SEC = 3.0
MAX_REQUESTS = 80         # 한 번 실행에 네이버 요청 상한 (차단 방지). 남은 건 다음 실행에서 이어서.
BLOCK_WAIT_SEC = 60       # 403/429(차단)를 받으면 이만큼 쉬고 한 번만 다시 시도, 또 막히면 이번 실행은 중단
MAX_TRIES = 3             # 단서를 못 찾은 회차는 며칠에 걸쳐 최대 이만큼 다시 시도
MAX_CLUES = 6
KST = timezone(timedelta(hours=9))

# ---- 건강 프로그램 판정: index.html 건강프로그램 추적(IN_TRK_*)과 같은 규칙 ----
KEYWORD = re.compile(r"건강|몸신|명의|닥터|의사|병원|질병|혈당|혈압|관절|면역|노화|장수|백세|영양|한의|한방|약초|보약|다이어트|웰빙|유레카|천기누설|엄지의\s*제왕|처방|수명|몸")
KEYWORD_EXCL = re.compile(r"드라마|영화|뮤지컬|시네마|무비|극장|월드컵|프로야구|뉴스")
DRAMA_EXCL = ["닥터x", "하얀마피아"]
TITLES = [
    "기분좋은날", "굿모닝대한민국", "히든카드", "시크릿코드", "세개의시선", "트루맨쇼",
    "인생2막", "알콩달콩", "인생의연장전", "퍼펙트라이프", "중독자들", "쌀롱하우스",
    "지킬박사", "생존의비밀", "생존의단서", "아모르바디", "가화만사성", "엄마를부탁해",
    "편스토랑", "바디인사이트", "아픈사이", "슈퍼푸드", "나비효과",
]
# 재방·편집본은 새 회차가 아니므로 검색하지 않는다
RERUN = re.compile(r"스페셜|특별판|베스트|하이라이트|다시보기|재방")

# 채널 키 -> 네이버 방송정보 '편성' 표기 (공식 회차정보가 엉뚱한 동명 프로그램이 아닌지 확인용)
CH_ALIASES = {
    "TV조선": ["TV조선", "TV CHOSUN"], "채널A": ["채널A"], "MBC every1": ["MBC every1", "에브리원"],
    "SBS Plus": ["SBS Plus", "SBS플러스"],
}

norm = lambda s: re.sub(r"[^0-9A-Za-z가-힣]", "", str(s or "")).lower()


def is_health(title: str) -> bool:
    n = norm(title)
    if any(t in n for t in DRAMA_EXCL):
        return False
    return (bool(KEYWORD.search(title)) and not KEYWORD_EXCL.search(title)) or any(t in n for t in TITLES)


def split_title(title: str):
    """'건강한 집2(119회)' -> ('건강한 집2', '119'). 회차 없으면 (제목, '')."""
    m = re.search(r"\(\s*(\d+)\s*회\s*\)\s*$", title)
    if m:
        return title[:m.start()].strip(), m.group(1)
    return title.strip(), ""


class Blocked(Exception):
    """네이버가 요청을 막음(403/429). 이어서 요청해도 소용없으므로 실행을 멈춘다."""


def fetch(query: str) -> str:
    for attempt in range(2):
        resp = requests.get(BASE_URL, params={"where": "nexearch", "query": query}, headers=HEADERS, timeout=15)
        if resp.status_code in (403, 429):
            if attempt == 0:
                print(f"  [차단 {resp.status_code}] {BLOCK_WAIT_SEC}초 쉬고 다시 시도")
                time.sleep(BLOCK_WAIT_SEC)
                continue
            raise Blocked(f"{resp.status_code} {query}")
        resp.raise_for_status()
        return resp.text


def clean(t: str) -> str:
    t = re.sub(r"새 창 열림|Keep에 저장|Keep에 바로가기", " ", t)
    t = re.sub(r"^\d+\s*(분|시간|일|주)\s*전\s*", "", t.strip())  # '1일 전 ...' 같은 게시 시각 접두어
    return re.sub(r"\s+", " ", t).strip()


# 단서로 쓸 수 없는 결과: 불법 다운로드·파일공유 제목, 편성표 목록 텍스트
JUNK = re.compile(r"다시보기|다운로드|스트리밍|1080p|720p|WANNA|파일쿠키|filekuki|토렌트|torrent|\.E\s?\d+|\bE\s?\d{2,}\b|›|www\.", re.I)


def parse_official(html: str, ch: str):
    """방송정보 위젯의 회차 목록 -> {'714': ('2026-10-06', '소개'), ...}. 다른 채널 프로그램이면 빈 dict."""
    soup = BeautifulSoup(html, "html.parser")
    info = soup.find(class_="detail_info") or soup.find(class_="cm_info_box")
    if info is not None:
        txt = info.get_text(" ", strip=True)
        aliases = CH_ALIASES.get(ch, [ch])
        if not any(a.lower() in txt.lower() for a in aliases):
            return {}
    out = {}
    for li in soup.find_all("li", class_="_item"):
        t = clean(li.get_text(" ", strip=True))
        m = re.match(r"^(\d+)\s*회\s*(\d{4})\.(\d{2})\.(\d{2})\.?\s*\(.\)\s*(.*)$", t)
        if m:
            out[m.group(1)] = (f"{m.group(2)}-{m.group(3)}-{m.group(4)}", m.group(5)[:400])
    return out


def parse_clues(html: str, series: str, epi: str, date: str):
    """검색 결과에서 '프로그램명' + 'N회'(또는 방송 날짜)가 함께 들어간 제목만 단서로 모은다."""
    soup = BeautifulSoup(html, "html.parser")
    ns = norm(series)
    md = datetime.strptime(date, "%Y-%m-%d")
    date_pats = [f"{md.month}월 {md.day}일", f"{md.month}월{md.day}일", md.strftime("%y%m%d"), md.strftime("%Y.%m.%d")]
    clues, seen = [], set()
    for a in soup.find_all("a"):
        t = clean(a.get_text(" ", strip=True))
        if not (8 <= len(t) <= 160):
            continue
        if ns not in norm(t):
            continue
        if epi:
            if not re.search(rf"(?<!\d){epi}\s*(회|화)", t):
                continue
        elif not any(p in t for p in date_pats):
            continue
        if JUNK.search(t) or len(re.findall(r"\d{1,2}:\d{2}", t)) >= 2:
            continue
        # 다른 해 방송분 ('굿모닝대한민국 2012년 10월 5일')
        years = re.findall(r"(?<!\d)(20\d{2})\s*년", t)
        if years and str(md.year) not in years:
            continue
        # 프로그램명·회차·채널·날짜 말고 내용이 거의 없는 제목 ('엄지의 제왕 714회')은 버린다
        rest = norm(t).replace(ns, "", 1)
        rest = re.sub(rf"{epi}(회|화)" if epi else "", "", rest)
        rest = re.sub(r"\d+|tv조선|tvchosun|채널a|mbn|jtbc|kbs\d?|mbc|sbs|tvn|ena|방송|예고|회차|본방", "", rest)
        if len(rest) < 6:
            continue
        k = norm(t)
        if k in seen or any(k in s or s in k for s in seen):
            continue
        seen.add(k)
        clues.append(t)
        if len(clues) >= MAX_CLUES:
            break
    return clues


def load_month(ym: str) -> dict:
    p = os.path.join(OUT_DIR, f"{ym}.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="오늘 포함 며칠 전 방영분까지 볼지 (이미 단서를 찾은 회차는 건너뜀)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = datetime.now(KST).date()
    window = {(today - timedelta(days=i)).isoformat() for i in range(args.days)}

    # 1) 대상 회차 목록 - 본방만.
    #    재방송도 '(151회)'처럼 회차가 붙어 나오므로, 수집된 편성 전체를 날짜순으로 훑으며
    #    그 프로그램이 앞서 방영한 회차 번호보다 큰 번호가 처음 나온 날만 본방으로 본다.
    #    회차 번호가 없는 프로그램(매일 생방 등)은 날짜별로 하나씩.
    targets = []  # (key, ch, series, epi, date, start)
    seen_keys = set()
    max_epi = {}  # (ch, series) -> 이전 날짜까지 방영한 최대 회차
    for p in sorted(glob.glob(os.path.join(SCHEDULE_DIR, "????-??-??.json"))):
        d = os.path.basename(p)[:10]
        if d > today.isoformat():
            break
        with open(p, encoding="utf-8") as f:
            sched = json.load(f)
        day_max = {}
        for ch, progs in sched.items():
            for prog in progs:
                title = prog.get("title", "")
                if not is_health(title) or RERUN.search(title):
                    continue
                series, epi = split_title(title)
                if epi:
                    n = int(epi)
                    if n <= max_epi.get((ch, series), 0):
                        continue  # 예전 회차 재방송
                    day_max[(ch, series)] = max(day_max.get((ch, series), 0), n)
                if d not in window:
                    continue
                key = f"{ch}|{series}|{epi or d}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                targets.append((key, ch, series, epi, d, prog.get("start", "")))
        for k, n in day_max.items():
            max_epi[k] = max(max_epi.get(k, 0), n)

    months = {}
    def store_for(d):
        ym = d[:7]
        if ym not in months:
            months[ym] = load_month(ym)
        return months[ym]

    requests_used = 0
    official_cache = {}  # (ch, series) -> {epi: (date, desc)}
    done = skipped = 0
    for key, ch, series, epi, d, start in targets:
        store = store_for(d)
        rec = store.get(key) or {"ch": ch, "series": series, "epi": epi, "date": d, "start": start,
                                 "official": "", "clues": [], "tries": 0}
        if rec.get("official") or rec.get("clues") or rec.get("tries", 0) >= MAX_TRIES:
            skipped += 1
            continue
        if requests_used >= MAX_REQUESTS:
            print(f"[중단] 요청 상한 {MAX_REQUESTS}회 도달 - 남은 회차는 다음 실행에서")
            break

        try:
            # 공식 회차정보 (프로그램당 1번)
            if (ch, series) not in official_cache:
                official_cache[(ch, series)] = parse_official(fetch(series), ch)
                requests_used += 1
                time.sleep(REQUEST_DELAY_SEC)
            off = official_cache[(ch, series)]
            if epi and epi in off:
                rec["official"] = off[epi][1]
            elif not epi:
                same_day = [v for v in off.values() if v[0] == d]
                if same_day:
                    rec["official"] = same_day[0][1]

            # 검색 결과 제목 단서
            q = f"{series} {epi}회" if epi else f"{series} {int(d[5:7])}월 {int(d[8:10])}일"
            rec["clues"] = parse_clues(fetch(q), series, epi, d)
            requests_used += 1
            time.sleep(REQUEST_DELAY_SEC)
        except Blocked as e:
            # 실패한 회차는 시도 횟수를 올리지 않고 저장도 하지 않는다 (다음 실행에서 처음부터 다시)
            print(f"[중단] 네이버 차단: {e} - 지금까지 받은 것만 저장")
            break
        except Exception as e:
            print(f"  [실패] {key}: {e}")
            continue

        rec["tries"] = rec.get("tries", 0) + 1
        rec["updated"] = datetime.now(KST).isoformat(timespec="seconds")
        store[key] = rec
        done += 1
        print(f"[{d} {start}] {ch} {series} {epi and epi + '회'}")
        if rec["official"]:
            print(f"    공식: {rec['official'][:120]}")
        for c in rec["clues"]:
            print(f"    단서: {c[:120]}")

    print(f"\n대상 {len(targets)}개 · 이번에 검색 {done}개 · 이미 있음/포기 {skipped}개 · 네이버 요청 {requests_used}회")

    if args.dry_run:
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    for ym, store in months.items():
        with open(os.path.join(OUT_DIR, f"{ym}.json"), "w", encoding="utf-8") as f:
            json.dump(dict(sorted(store.items(), key=lambda kv: (kv[1]["date"], kv[1]["start"]))),
                      f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
