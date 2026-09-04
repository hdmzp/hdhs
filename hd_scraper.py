# -*- coding: utf-8 -*-
"""
현대홈쇼핑(HD) 편성표 수집기

라이브방송(TV쇼핑) / 데이터방송(TV+샵) 편성을 공통 스키마로 변환해 저장한다.

== 저장 구조 ==
homeshopping/
├── HD_live/{YYYY-MM}.json   라이브(TV쇼핑)
└── HD_data/{YYYY-MM}.json   데이터방송(TV+샵)

== 공통 스키마 (월 파일 안에 날짜별 누적) ==
{
  "company": "HD", "broadcast": "live", "month": "2026-06",
  "days": {
    "2026-06-22": [
      {"start":"08:00","end":"09:59","brand":"미우미우","product":"하프문 숄더백",
       "price":39000,"link":"https://..."}
    ]
  }
}

== 수집 정책 ==
오늘 기준 -1일 ~ +5일(7일)을 매번 수집.
과거(오늘 이전) 날짜가 이미 기록돼 있으면 다시 안 건드리고 보존, 오늘+미래만 갱신.
오늘/미래 날짜도 "수집 실패"와 "정말 편성이 없음"을 구분해, 실패한 날은
기존 데이터를 그대로 둔다 (아래 장애 이력 참고).

== 장애 이력 ==
2026-08-27: tv-list API가 HTTP 200 + 비-JSON 본문(WAF 차단 페이지로 추정)을
돌려주기 시작해 8개 페이지 전부 `Expecting value: line 1 column 1 (char 0)`로
깨졌다. 그런데 옛 코드는 그 결과(빈 리스트)를 그대로 days[날짜]에 덮어써서,
이미 잘 받아둔 8/28~9/2 편성까지 한 번에 0건으로 날아갔다.
  -> 대책 3가지를 넣었다.
     (1) 세션/쿠키 + AJAX 헤더로 요청해 WAF에 XHR로 인식되게 한다
     (2) 캐시 래퍼(/md/api/cache)가 비-JSON을 주면 원본 엔드포인트로 폴백,
         재시도는 tools/scrape_guard(지수 백오프)에 맡긴다
     (3) 수집 실패한 날짜는 절대 덮어쓰지 않는다 (데이터 보존이 최우선)

== 사용법 ==
  pip install requests
  python hd_scraper.py
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta, timezone
from categorize import classify_batch
from clean_product import clean_product_name

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools import scrape_guard

KST = timezone(timedelta(hours=9))
OUTPUT_DIR = "homeshopping"
REQUEST_DELAY = 0.4
DAYS_RANGE = range(-1, 6)  # 어제 ~ +5일

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

HOME_URL = "https://www.hmall.com/"

# tv-list는 브라우저에선 XHR로만 불리는 엔드포인트다. Accept가 */*이고
# X-Requested-With도 없는 요청은 WAF가 봇으로 보기 쉬워, 브라우저 XHR과
# 같은 헤더를 실어 보낸다 (2026-08-27 장애 대책).
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.hmall.com/md/dpa/tvSchedule",
    "Origin": "https://www.hmall.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "X-Requested-With": "XMLHttpRequest",
}

# 1순위는 지금까지 쓰던 캐시 래퍼, 2순위는 래퍼가 감싸고 있는 원본 API.
# 래퍼만 차단당하는 경우가 있어 폴백 경로를 둔다.
TV_LIST_URLS = (
    "https://www.hmall.com/md/api/cache?url=/api/hf/dp/v1/main-tv-new/tv-list"
    "&brodDt={date}&brodPrrgPage={page}&brodType={broad}&deviceInfo=pc",
    "https://www.hmall.com/api/hf/dp/v1/main-tv-new/tv-list"
    "?brodDt={date}&brodPrrgPage={page}&brodType={broad}&deviceInfo=pc",
)

DEBUG_DIR = os.path.join(OUTPUT_DIR, "_debug")

# 페이지당 재시도 횟수. API가 통째로 죽으면 (날짜 14개 x URL 2개)만큼
# 백오프가 쌓이므로, 워크플로우 timeout-minutes(30) 안에 들어오게 묶어둔다.
# 페이지 0이 실패하면 그 날짜는 바로 포기하므로 8배로 불어나지는 않는다.
PAGE_RETRIES = 2


def today_kst():
    return datetime.now(KST)


def parse_price(v):
    """가격을 원 단위 정수로 정규화. '69,900' / 69900 / None 등 처리."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).replace(",", "").strip()
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def add_categories(programs):
    """
    1) 원본 상품명으로 카테고리 분류 (분류 모델은 원본 패턴으로 학습됨)
    2) 분류가 끝난 뒤 product 필드를 화면 표시용으로 정제
    """
    if not programs:
        return programs
    pairs = [(p["brand"], p["product"]) for p in programs]
    categories = classify_batch(pairs)
    for p, cat in zip(programs, categories):
        p["category"] = cat
        p["product"] = clean_product_name(p["product"])
    return programs


def keep_known_pgm(new_programs, old_programs):
    """새 수집분에 프로그램명(pgm)이 비어 있으면 기존 값을 물려준다.

    tv-list의 brodTitl은 시점에 따라 통째로 비어서 온다 (2026-09-04 낮
    수집에서 9/8 편성 전체의 brodTitl이 사라졌다). 셀럽PGM 수집이 이
    이름으로 방송 회차를 찾기 때문에, 한 번이라도 확인된 이름은 지킨다.
    (같은 시간대의 이름을 쓰므로 상품이 바뀌어도 방송 자체는 같다)"""
    known = {}
    for p in old_programs or []:
        if p.get("pgm") and p.get("start"):
            known.setdefault(p["start"], p["pgm"])
    if not known:
        return new_programs
    for p in new_programs:
        if not p.get("pgm") and known.get(p.get("start")):
            p["pgm"] = known[p["start"]]
    return new_programs


def new_session():
    """쿠키를 물고 다니는 세션. 먼저 메인 페이지를 한 번 열어 WAF가 발급하는
    세션 쿠키를 받아둔다 - 쿠키 없는 생요청만 계속 날리면 tv-list가 JSON 대신
    차단 페이지(HTTP 200 + HTML)를 돌려준다."""
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(HOME_URL, timeout=12)
    except requests.RequestException as e:
        # 워밍업 실패는 치명적이지 않다 (쿠키 없이도 되던 시기가 있었다).
        print(f"    [HD] 세션 워밍업 실패(무시하고 진행): {e}")
    return s


_diagnosed = False


def diagnose_once(session, url):
    """차단 응답의 실체를 실행당 딱 한 번만 남긴다.

    scrape_guard는 비-JSON을 재시도 대상으로만 보고 예외 메시지에
    'Expecting value: line 1 column 1' 밖에 안 남긴다. 그것만으론 WAF 차단인지
    엔드포인트 폐지인지 구분이 안 돼 2026-08-27 장애 때 원인 파악이 늦었다.
    러너는 일회용이라 파일로 남겨봐야 못 보므로 로그로 찍는 게 핵심이다."""
    global _diagnosed
    if _diagnosed:
        return
    _diagnosed = True
    try:
        resp = session.get(url, timeout=12)
    except requests.RequestException as e:
        print(f"    [HD] 진단 요청 실패: {e}")
        return
    save_debug_body(resp, "blocked")


def save_debug_body(resp, tag):
    """JSON이 아닌 응답을 스냅샷으로 남긴다. 다음 실패 때 '무엇을 받았는지'를
    로그만 보고 알 수 있게 하려는 것 (다른 스크래퍼의 _debug_* 와 같은 관례)."""
    preview = " ".join(resp.text[:300].split())
    print(f"    [HD] 비-JSON 응답 (HTTP {resp.status_code}, "
          f"content-type={resp.headers.get('Content-Type', '?')}): {preview}")
    os.makedirs(DEBUG_DIR, exist_ok=True)
    path = os.path.join(DEBUG_DIR, f"_debug_hd_{tag}.html")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(resp.text[:20000])
        print(f"    [HD] 비정상 응답 스냅샷 저장: {path}")
    except OSError as e:
        print(f"    [HD] 스냅샷 저장 실패: {e}")


def fetch_page(session, date_compact, broad_param, page):
    """tv-list 한 페이지. 캐시 래퍼 -> 원본 API 순으로 시도하고, 각 URL은
    scrape_guard가 지수 백오프로 재시도한다(비-JSON 응답도 재시도 대상).
    성공하면 아이템 리스트, 모든 경로가 실패하면 None을 돌려준다.
    (빈 리스트 = 편성 없음 / None = 수집 실패 - 이 구분이 핵심이다)"""
    for idx, template in enumerate(TV_LIST_URLS):
        url = template.format(date=date_compact, page=page, broad=broad_param)
        label = f"HD tv-list {broad_param} {date_compact} p{page}" + ("(폴백)" if idx else "")
        try:
            resp = scrape_guard.get(url, session=session, timeout=12,
                                    retries=PAGE_RETRIES, expect_json=True,
                                    label=label)
        except scrape_guard.FetchError as e:
            print(f"    [HD] {label} 실패: {e}")
            diagnose_once(session, url)
            continue
        try:
            return resp.json().get("respData", {}).get("broadItemList", []) or []
        except ValueError:
            # scrape_guard가 이미 걸렀어야 하는 경우라 여기 오면 응답을 남긴다.
            save_debug_body(resp, f"{broad_param}_{date_compact}_p{page}")
    return None


def item_to_program(it, fallback_start="", fallback_end="", pgm=""):
    """tv-list 아이템(대표상품 또는 withItemList 서브상품)을 공통 스키마로 변환.
    pgm: 고정PGM 방송이면 프로그램명(brodTitl) - 프론트 뱃지 표시용."""
    slitm = it.get("slitmCd")
    prog = {
        "start": it.get("brodStrtDtm") or fallback_start,
        "end": it.get("brodEndDtm") or fallback_end,
        "brand": it.get("brndNm", "") or "",
        "product": it.get("convertedSlitmNm") or it.get("slitmNm") or "",
        "price": parse_price(it.get("sellPrc")),
        "link": f"https://www.hmall.com/md/pda/itemPtc?slitmCd={slitm}&preview=true" if slitm else "",
    }
    if pgm:
        prog["pgm"] = pgm
    return prog


def fetch_hyundai(session, date_compact, broad_param):
    """
    date_compact: 'YYYYMMDD'
    broad_param: 'etv'(라이브/TV쇼핑) | 'dtv'(데이터/TV+샵)
    페이지 0~7을 순회 후 (시작시각, 상품코드)로 중복 제거.

    같은 시간대에 함께 방송되는 상품들은 편성표에 대표상품 1개만 나오고
    나머지는 tv-list 응답의 withItemList 필드에 들어있다.
    - 고정PGM(왕영은의 톡 투게더, 오감쇼, 황정민쇼 등 brodTitl이 있는 방송):
      상품이 수십 개라 브랜드별 대표상품 1개씩만 편성에 추가한다
      (예: 왕영은 3시간 방송 = 로버츠베리에 + 휘슬러 + 심플휴먼 3줄).
    - 일반 방송: "함께 방송하는 상품"이 1~2개 수준이므로 전부 편성에 추가한다
      (예: 망고수박 + 불고기한판이 한 방송에 같이 나오는 경우).

    반환값: (편성 리스트, 전 페이지 성공 여부).
      - (None, False)  : 수집 실패 (페이지 0을 못 받음 = API 장애)
      - (리스트, False): 일부 페이지만 실패 - 누락됐을 수 있으니 호출부가 판단
      - (리스트, True) : 정상
    """
    seen = {}
    complete = True
    for page in range(0, 8):
        items = fetch_page(session, date_compact, broad_param, page)
        if items is None:
            # 페이지 0이 안 되면 나머지 페이지도 같은 이유로 안 된다.
            # 8배로 재시도를 쌓지 말고 이 날짜는 바로 포기한다.
            if page == 0:
                return None, False
            complete = False
            continue
        for it in items:
            key = (it.get("brodStrtDtm"), it.get("slitmCd"))
            if key[0] is None:
                continue
            pgm = (it.get("brodTitl") or "").strip()
            seen[key] = item_to_program(it, pgm=pgm)

            # withItemList: 고정PGM은 새 브랜드의 첫 상품만, 일반 방송은 전부 추가
            slot_brands = {it.get("brndNm") or ""}
            for sub in it.get("withItemList") or []:
                if not sub.get("slitmCd"):
                    continue
                brand = sub.get("brndNm") or ""
                if pgm:
                    if brand in slot_brands:
                        continue
                    slot_brands.add(brand)
                sub_key = (sub.get("brodStrtDtm") or key[0], sub.get("slitmCd"))
                seen[sub_key] = item_to_program(
                    sub, fallback_start=it.get("brodStrtDtm", ""),
                    fallback_end=it.get("brodEndDtm", ""), pgm=pgm)
        time.sleep(0.15)

    programs = sorted(seen.values(), key=lambda x: x["start"])

    # 데이터방송(dtv)은 API가 종료시각을 "다음 방송 시작 1분 전"으로 줘서
    # 끊김이 생긴다(예: 01:00-01:19, 01:20-01:39). 다음 방송의 시작시각을
    # 현재 방송의 종료시각으로 보정해 시간이 끊김없이 이어지게 한다.
    # (라이브방송 etv는 원본부터 이미 끊김없이 맞아 있어 보정 불필요)
    # 같은 시각에 여러 상품이 편성될 수 있으므로(고정PGM 확장 등)
    # "다음 시작시각"은 지금보다 늦은 첫 시작시각으로 잡는다.
    if broad_param == "dtv":
        for i in range(len(programs) - 1):
            nxt = next((q["start"] for q in programs[i + 1:] if q["start"] > programs[i]["start"]), None)
            if nxt:
                programs[i]["end"] = nxt

    return programs, complete


BROADCASTS = [
    ("live", "etv"),
    ("data", "dtv"),
]


def load_month(sub_dir, ym):
    path = os.path.join(sub_dir, f"{ym}.json")
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8")).get("days", {})
        except Exception:
            return {}
    return {}


def main():
    base = today_kst()
    today_str = base.strftime("%Y-%m-%d")
    session = new_session()
    failed, collected = [], 0

    for broadcast, broad_param in BROADCASTS:
        sub_dir = os.path.join(OUTPUT_DIR, f"HD_{broadcast}")
        os.makedirs(sub_dir, exist_ok=True)
        month_data = {}

        for offset in DAYS_RANGE:
            d = base + timedelta(days=offset)
            date_compact = d.strftime("%Y%m%d")
            date_dash = d.strftime("%Y-%m-%d")
            ym = d.strftime("%Y-%m")
            if ym not in month_data:
                month_data[ym] = load_month(sub_dir, ym)
            days = month_data[ym]

            is_past = date_dash < today_str
            if is_past and days.get(date_dash):
                print(f"[HD_{broadcast}] {date_dash}: 이미 기록됨, 건너뜀")
                continue

            print(f"[HD_{broadcast}] {date_dash} 수집 중...")
            programs, complete = fetch_hyundai(session, date_compact, broad_param)
            have = len(days.get(date_dash) or [])

            # 수집 실패(None)와 편성 없음([])은 다르다. 실패한 날은 손대지 않고
            # 기존 값을 그대로 둔다 - 2026-08-27에 이 구분이 없어서 이미 받아둔
            # 나흘치 편성이 0건으로 덮어씌워졌다.
            if programs is None:
                print(f"  -> 수집 실패 (기존 {have}개 유지)")
                failed.append(f"HD_{broadcast} {date_dash}")
                time.sleep(REQUEST_DELAY)
                continue

            # 응답은 왔는데 기존보다 줄어든 경우. 편성이 실제로 줄기도 하지만,
            # 페이지가 일부 실패했다면 그건 누락이지 축소가 아니다.
            if not complete and len(programs) < have:
                print(f"  -> {len(programs)}개 (일부 페이지 실패, 기존 {have}개 유지)")
                failed.append(f"HD_{broadcast} {date_dash}(페이지 누락)")
                time.sleep(REQUEST_DELAY)
                continue

            if not programs and have:
                # 전 페이지 정상인데 0건이고 이미 데이터가 있는 날. 편성이 통째로
                # 사라지는 경우는 사실상 없으므로 기존 값을 신뢰한다.
                print(f"  -> 0개 응답, 기존 {have}개 유지")
                failed.append(f"HD_{broadcast} {date_dash}(0건 응답)")
                time.sleep(REQUEST_DELAY)
                continue

            days[date_dash] = add_categories(
                keep_known_pgm(programs, days.get(date_dash)))
            collected += len(programs)
            print(f"  -> {len(programs)}개 편성")
            time.sleep(REQUEST_DELAY)

        for ym, days in month_data.items():
            if not days:
                continue
            out_path = os.path.join(sub_dir, f"{ym}.json")
            sorted_days = {k: days[k] for k in sorted(days)}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({
                    "company": "HD", "broadcast": broadcast,
                    "month": ym, "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "days": sorted_days,
                }, f, ensure_ascii=False, indent=2)
            print(f"  저장: {out_path} ({len(sorted_days)}일)")

    if failed:
        print(f"\n[HD] 수집하지 못한 날짜 {len(failed)}건: {', '.join(failed)}")
    if collected == 0:
        # 한 건도 못 받았으면 API가 통째로 막힌 상황이다. 조용히 초록불로
        # 끝나면 또 며칠을 모르고 지나가므로 종료코드로 드러낸다.
        print("[HD] 두 채널 모두 한 건도 수집하지 못했습니다 - tv-list API 확인 필요")
        sys.exit(1)

    print("\n완료.")


if __name__ == "__main__":
    main()
