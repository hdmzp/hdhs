"""
asiangames_scraper.py
2026 나고야(아이치) 아시안게임 날짜별 경기 일정 수집

수집 대상
  https://m.sports.naver.com/asiangames2026/schedule?type=date&date=YYYY-MM-DD...

이 페이지는 클라이언트에서 api-gw.sports.naver.com 을 호출해 일정을 그리는
구조다. 그래서 HTML만 받아와서는 표가 비어 있고, Playwright로 띄운 뒤
네트워크 응답(XHR)을 가로채는 방식이 가장 안정적이다.

응답 JSON의 정확한 스키마는 대회마다 바뀌므로(항저우 때와 키 이름이 다름),
파서는 특정 키 경로에 의존하지 않는다. 대신 JSON 트리를 재귀적으로 훑어서
"경기 한 건처럼 생긴 dict"(시작시각 + 종목 + 경기명/팀을 가진 객체)를 모으고,
키 이름은 후보 목록으로 느슨하게 매칭한다. 스키마가 또 바뀌어도 후보 목록만
늘리면 된다.

--probe 를 주면 수집 대신 정찰만 한다: 어떤 API가 호출됐고 응답 JSON이
어떤 모양인지(키 경로, 샘플 레코드)를 로그로 찍어준다. 파서가 못 알아먹는
스키마로 바뀌었을 때 이걸로 먼저 확인하고 후보 목록을 고친다.

사용법
    pip install playwright && python -m playwright install --with-deps chromium
    python asiangames_scraper.py --out-dir ../data/asiangames
    python asiangames_scraper.py --probe --dates 2026-09-20
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, date as date_cls, timedelta, timezone

from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))

# 대회 기간 (개회식 9/19 ~ 폐회식 10/4)
GAMES_START = date_cls(2026, 9, 19)
GAMES_END = date_cls(2026, 10, 4)

SCHEDULE_URL = (
    "https://m.sports.naver.com/asiangames2026/schedule"
    "?type=date&date={date}&disciplineId=&isKorean=N&isMedal=N&isScheduledTv=N"
)

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

KOREA_NAMES = {"대한민국", "한국", "KOR", "Korea", "Republic of Korea", "South Korea"}

# 경기를 클릭했을 때 열리는 네이버 스포츠 경기 페이지.
# gameId가 없는 레코드는 그 날짜의 일정 페이지로 보낸다(항상 유효).
GAME_URL = "https://m.sports.naver.com/game/{game_id}"


def game_link(game_id, day):
    if game_id:
        return GAME_URL.format(game_id=game_id)
    return SCHEDULE_URL.format(date=day)

# 경기 레코드에서 각 필드를 찾을 때 쓸 키 후보들. 네이버가 대회마다 키를
# 바꾸기 때문에 하나로 못 박지 않고 순서대로 시도한다(앞쪽이 우선).
KEYS_TIME = ["startTime", "gameStartTime", "startDateTime", "gameDateTime",
             "startHm", "time", "gameTime", "startDate"]
KEYS_DISCIPLINE = ["disciplineName", "categoryName", "sportName", "disciplineKoName",
                   "discipline", "categoryKoName", "sports"]
KEYS_EVENT = ["eventName", "gameName", "roundName", "subCategoryName", "title",
              "eventKoName", "phaseName", "gameSubName"]
KEYS_VENUE = ["venueName", "stadium", "placeName", "venue", "stadiumName"]
KEYS_MEDAL = ["medalYn", "isMedal", "hasMedal", "medal"]
KEYS_TV = ["scheduledTvYn", "isScheduledTv", "scheduledTv", "broadcast", "tvYn", "onAir"]
# 개인 종목은 출전국가 목록이 비어 있고, 한국 선수 출전 여부만 이 플래그로 온다.
KEYS_KOREA = ["koreaPlayer", "isKorean", "koreanYn", "hasKorean"]

# 출전국/팀 목록이 들어있을 법한 키
KEYS_TEAMS = ["teams", "countries", "participants", "nations", "competitors",
              "athletes", "teamList", "countryList"]
# 팀 객체 안에서 표시할 이름을 고를 때
KEYS_TEAM_NAME = ["nationName", "countryName", "teamName", "name", "nationKoName",
                  "countryKoName", "athleteName", "nation", "country"]
# 홈/원정 2팀 구조일 때
KEYS_HOME = ["homeTeamName", "homeNationName", "homeTeam", "homeName"]
KEYS_AWAY = ["awayTeamName", "awayNationName", "awayTeam", "awayName"]

TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


def log(*a):
    print(*a, flush=True)


def date_range(start: date_cls, end: date_cls):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ---------------------------------------------------------------- 값 추출 헬퍼

def pick(d, keys):
    """dict에서 키 후보를 순서대로 찾아 비어 있지 않은 첫 값을 반환."""
    for k in keys:
        if k in d:
            v = d[k]
            if v not in (None, "", [], {}):
                return v
    return None


def as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().upper() in ("Y", "TRUE", "1")
    if isinstance(v, (int, float)):
        return bool(v)
    return False


def to_hhmm(v):
    """'2026-09-20T11:00:00', '1100', '11:00' 등 여러 표기를 hh:mm 으로."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        v = str(int(v))
    s = str(v).strip()
    m = TIME_RE.search(s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"
    if s.isdigit() and len(s) in (3, 4):
        h, mi = int(s[:-2]), int(s[-2:])
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"
    return None


def team_name(t):
    if isinstance(t, str):
        return t.strip() or None
    if isinstance(t, dict):
        v = pick(t, KEYS_TEAM_NAME)
        if isinstance(v, str):
            return v.strip() or None
    return None


def extract_countries(rec):
    """경기 레코드에서 출전국가 목록을 뽑는다."""
    out = []

    for k in KEYS_TEAMS:
        v = rec.get(k)
        if isinstance(v, list) and v:
            for t in v:
                n = team_name(t)
                if n and n not in out:
                    out.append(n)
            if out:
                return out

    home, away = pick(rec, KEYS_HOME), pick(rec, KEYS_AWAY)
    for v in (home, away):
        n = team_name(v)
        if n and n not in out:
            out.append(n)
    return out


def looks_like_game(rec):
    """경기 한 건처럼 생긴 dict인지 판정."""
    if not isinstance(rec, dict):
        return False
    if to_hhmm(pick(rec, KEYS_TIME)) is None:
        return False
    # 종목명이나 경기명 중 하나는 있어야 한다
    return bool(pick(rec, KEYS_DISCIPLINE) or pick(rec, KEYS_EVENT))


def walk_games(node, found, path="$"):
    """JSON 트리를 훑으며 경기 레코드를 전부 긁어모은다."""
    if isinstance(node, dict):
        if looks_like_game(node):
            found.append((path, node))
            return  # 경기 레코드 내부는 더 안 판다
        for k, v in node.items():
            walk_games(v, found, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk_games(v, found, f"{path}[{i}]")


def normalize(rec, day, path=""):
    """네이버 레코드 -> 화면이 쓰는 스키마."""
    countries = extract_countries(rec)
    discipline = pick(rec, KEYS_DISCIPLINE) or ""
    event = pick(rec, KEYS_EVENT) or ""
    if isinstance(discipline, dict):
        discipline = team_name(discipline) or ""
    if isinstance(event, dict):
        event = team_name(event) or ""

    # 종목명이 경기명 앞에 그대로 중복돼 들어오는 경우가 있어 한 번 정리한다
    discipline = str(discipline).strip()
    event = str(event).strip()
    if discipline and event.startswith(discipline):
        event = event[len(discipline):].strip(" -·:")

    # 개·폐회식은 종목명이 '개·폐회식', 경기명이 '개회식'으로 따로 온다.
    # 화면에도 검색어에도 '개·폐회식 개회식'은 어색하므로 '개회식' 하나로 만든다.
    for word in ("개회식", "폐회식"):
        if word in discipline or word in event:
            discipline, event = word, ""
            break

    # 한국 출전 판정: 단체전은 출전국가 목록에 '대한민국'이 들어오고,
    # 개인 종목은 목록이 비어 있는 대신 koreaPlayer 플래그가 켜진다.
    has_korea = any(c in KOREA_NAMES for c in countries) or as_bool(pick(rec, KEYS_KOREA))

    # 개·폐회식은 특정 국가의 '출전 경기'가 아니다. 한국 경기 수에 섞이지
    # 않도록 hasKorea를 끄고, 출전국가 칸은 '전 참가국'으로 채운다.
    # (화면에서는 '대한민국 출전 경기만' 필터와 무관하게 항상 노출된다)
    if discipline in ("개회식", "폐회식"):
        return {
            "time": to_hhmm(pick(rec, KEYS_TIME)),
            "discipline": discipline,
            "event": "",
            "countries": ["전 참가국"],
            "venue": pick(rec, KEYS_VENUE) if isinstance(pick(rec, KEYS_VENUE), str) else "",
            "hasKorea": False,
            "medal": False,
            "tv": as_bool(pick(rec, KEYS_TV)),
            "cancelled": False,
            "gameId": rec.get("gameId") or "",
            "link": game_link(rec.get("gameId"), day),
            "_path": path,
        }
    # 개인 종목이라 국가 목록이 비었는데 한국 선수가 출전하면, 화면의
    # '출전국가' 칸이 비지 않도록 대한민국을 채워준다.
    if not countries and has_korea:
        countries = ["대한민국"]

    venue = pick(rec, KEYS_VENUE)

    return {
        "time": to_hhmm(pick(rec, KEYS_TIME)),
        "discipline": discipline,
        "event": event,
        "countries": countries,
        "venue": venue if isinstance(venue, str) else "",
        "hasKorea": has_korea,
        "medal": as_bool(pick(rec, KEYS_MEDAL)),
        "tv": as_bool(pick(rec, KEYS_TV)),
        "cancelled": as_bool(rec.get("cancel")) or as_bool(rec.get("suspended")),
        "gameId": rec.get("gameId") or "",
        "link": game_link(rec.get("gameId"), day),
        "_path": path,
    }


def dedupe_sort(games):
    """같은 응답이 여러 번 잡히므로 중복을 걷어낸다. gameId가 있으면 그걸 쓴다."""
    seen, out = set(), []
    for g in games:
        if g.get("cancelled"):
            continue
        key = g.get("gameId") or (g["time"], g["discipline"], g["event"], tuple(g["countries"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(g)
    out.sort(key=lambda g: (g["time"] or "99:99", g["discipline"], g["event"]))
    for g in out:
        g.pop("_path", None)
        g.pop("cancelled", None)
        g.pop("gameId", None)
    return out


# ---------------------------------------------------------------- 수집

def fetch_day(page, day: str, probe=False):
    """하루치 페이지를 열고, 가로챈 API 응답에서 경기 목록을 뽑는다."""
    payloads = []

    def on_response(resp):
        url = resp.url
        # 같은 페이지에서 광고 SSP(veta) 응답도 쏟아지는데 경기와 무관하다.
        if "sports.naver.com" not in url or "veta.naver.com" in url:
            return
        ctype = (resp.headers or {}).get("content-type", "")
        if "json" not in ctype.lower():
            return
        try:
            payloads.append((url, resp.json()))
        except Exception:
            pass

    page.on("response", on_response)
    try:
        page.goto(SCHEDULE_URL.format(date=day), wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(1500)
    except Exception as e:
        log(f"  [{day}] 페이지 로드 경고: {e}")
    finally:
        page.remove_listener("response", on_response)

    games = []
    raw_hits = 0
    for url, data in payloads:
        found = []
        walk_games(data, found)
        raw_hits += len(found)
        for pth, rec in found:
            games.append(normalize(rec, day, pth))
    games = dedupe_sort(games)

    if probe:
        log(f"  [{day}] JSON 응답 {len(payloads)}건 / 경기 후보 {raw_hits}건 / 중복 제거 후 {len(games)}건")
        if games:
            log("  --- 파싱 결과 샘플 (앞 10건) ---")
            for g in games[:10]:
                mark = "KR" if g["hasKorea"] else "  "
                flags = ("메달" if g["medal"] else "") + ("/중계" if g["tv"] else "")
                log(f"   {mark} {g['time']}  {g['discipline']} {g['event']}"
                    f"  | {', '.join(g['countries']) or '-'}  | {g['venue']} {flags}")
            kr = [g for g in games if g["hasKorea"]]
            log(f"  --- 대한민국 출전 {len(kr)}건 ---")
            for g in kr[:15]:
                log(f"      {g['time']}  {g['discipline']} {g['event']}  | {', '.join(g['countries'])}")
            # 경기 페이지 링크가 실제로 열리는지 한 건만 확인한다.
            # 네이버가 URL 형식을 바꾸면 여기서 404/리다이렉트로 드러난다.
            game_prefix = GAME_URL.split("{")[0]
            sample = next((g for g in games if g.get("link", "").startswith(game_prefix)), None)
            if sample:
                log(f"  --- 경기 페이지 링크 확인: {sample['link']} ---")
                try:
                    r = page.goto(sample["link"], wait_until="domcontentloaded", timeout=30_000)
                    log(f"      HTTP {r.status if r else '?'} -> 최종 URL {page.url}")
                except Exception as e:
                    log(f"      열기 실패: {e}")
        else:
            # 한 건도 못 뽑았으면 구조 파악용으로 응답 본문을 보여준다
            for url, data in payloads:
                log(f"    - {url[:160]}")
                log(f"      본문(앞 700자): {json.dumps(data, ensure_ascii=False)[:700]}")

    return games, len(payloads)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="../data/asiangames")
    ap.add_argument("--dates", default="", help="쉼표로 구분한 YYYY-MM-DD 목록 (생략 시 대회 전체 기간)")
    ap.add_argument("--probe", action="store_true", help="수집 대신 응답 구조만 정찰")
    args = ap.parse_args()

    if args.dates:
        days = [d.strip() for d in args.dates.split(",") if d.strip()]
    else:
        days = [d.isoformat() for d in date_range(GAMES_START, GAMES_END)]

    log(f"대상 날짜 {len(days)}일: {days[0]} ~ {days[-1]}")
    if args.probe:
        log("=== PROBE 모드: 파일을 쓰지 않고 응답 구조만 확인합니다 ===")
    else:
        os.makedirs(args.out_dir, exist_ok=True)

    collected_at = datetime.now(KST).isoformat(timespec="seconds")
    manifest_dates = {}
    total_games = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(user_agent=UA, viewport={"width": 420, "height": 900},
                                  locale="ko-KR")
        page = ctx.new_page()

        for day in days:
            games, n_payload = fetch_day(page, day, probe=args.probe)
            kr = sum(1 for g in games if g["hasKorea"])
            log(f"[{day}] 응답 {n_payload}건 -> 경기 {len(games)}건 (대한민국 {kr}건)")
            total_games += len(games)

            if args.probe:
                continue

            out = {
                "date": day,
                "collectedAt": collected_at,
                "games": games,
            }
            path = os.path.join(args.out_dir, f"{day}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
            manifest_dates[day] = {"total": len(games), "korea": kr}

        browser.close()

    if args.probe:
        log(f"=== PROBE 종료: 경기 후보 총 {total_games}건 ===")
        return 0

    manifest = {
        "collectedAt": collected_at,
        "start": days[0],
        "end": days[-1],
        "totalGames": total_games,
        "dates": manifest_dates,
    }
    with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    log(f"완료: 총 {total_games}건 -> {args.out_dir}")

    # 한 건도 못 받았으면 워크플로우가 실패로 보이게 해서 바로 알아차리게 한다
    if total_games == 0:
        log("경고: 수집된 경기가 0건입니다. 네이버 응답 스키마가 바뀌었을 수 있습니다 "
            "(--probe 로 확인하세요).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
