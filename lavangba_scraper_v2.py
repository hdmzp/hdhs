"""
라방바(live.ecomm-data.com) 11개사 방송 편성 자동 수집 - ver2 (로그인 없이 수집).

v1(lavangba_scraper.py)과의 차이 - 왜 v2가 필요한가:
  라방바 보안정책이 바뀌면서 로그인해야 볼 수 있던 매출 데이터(hsshow/items의
  sales_amt / sales_amt_rcd)를 더 이상 스크래핑할 수 없게 됐다. v1이 쓰던 우회
  (chrome_profile 로그인 세션 + headless=False 브라우저 안에서 fetch)도 막혔다.
  그래서 v2는 **로그인이 필요한 부분을 통째로 들어냈다.**

    - Playwright / chrome_profile / login_setup.py 불필요 (requests만 사용)
    - 매출액(총주문)과 그 파생값(순주문)은 수집 불가 -> JSON에 "-" 로 기록
    - 분단위 매출 시계열(sales_amt_rcd)이 없어졌으므로 복합 PGM을 상품별로
      쪼갤 수 없다 -> 방송 1건 = 1행(대표 단일코드)으로만 뽑는다
    - 판매가/브랜드/카테고리/상품링크는 각 사 편성표(GitHub)에서 그대로 가져온다
      (로그인 무관)

데이터 소스 (둘 다 로그인 불필요):
  1) https://live.ecomm-data.com/api/schedule/list_hs
     - 채널별 방송 목록, 방송 시작/종료시각, 방송 제목, 라방바 대분류
  2) hdmzp/hdhs 저장소 homeshopping/{코드}_live/{YYYY-MM}.json (각 사 자체 편성표)
     - 상품명 / 브랜드 / 판매가 / 상품링크 / 카테고리

출력:
  data/{YYYYMM}.json - v1과 **같은 구조/필드명**({"YYYYMMDD": [행...]}).
  index.html이 그대로 읽을 수 있게 필드를 유지하고, 이제 못 긁는 값만 "-"로 채운다.
  (목표 target 필드는 더 이상 안 넣는다 - pgmsales 연동 제거)

  index.html은 sales_amt/price가 숫자인 것을 전제로 했었기 때문에, "-"가 그대로
  '-'로 표시되도록 lvFmtAmt()/lvFmtPrice()/총주문 합계에 숫자 변환을 넣어뒀다
  (공개 저장소 index.html에 적용 완료).

사용법:
    python lavangba_scraper_v2.py                    # 최근 30일 중 아직 없는 날짜만 자동 수집
    python lavangba_scraper_v2.py 20260819           # 특정 날짜 하루
    python lavangba_scraper_v2.py 20260801 20260819  # 기간 지정

필요 패키지: requests (playwright / cryptography 불필요)

이 파일은 비공개 저장소(hdmzp/hdhs_private)와 공개 저장소(hdmzp/hdhs)에 같은 내용으로
둔다. 저장 경로(data/ vs lavangba/data/)와 편성표 위치(로컬 homeshopping/ vs GitHub raw)는
실행 위치를 보고 자동으로 고른다.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import requests

# 콘솔/리다이렉트 인코딩이 cp949일 때 상품명의 특수문자(∙ 등)로 print가 죽는 것 방지
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

KST = timezone(timedelta(hours=9))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _pick_dir(*candidates):
    """존재하는 첫 경로. 하나도 없으면 마지막 후보(기본값)를 쓴다.
    이 스크립트를 비공개 저장소(hdhs_private)와 공개 저장소(hdhs) 양쪽에
    같은 내용으로 두고 쓰기 위한 레이아웃 자동 인식."""
    for c in candidates:
        if os.path.isdir(c):
            return c
    return candidates[-1]


# 공개 저장소(hdmzp/hdhs)는 lavangba/data/, 비공개 저장소는 data/ 에 결과를 쌓는다.
DATA_DIR = _pick_dir(os.path.join(BASE_DIR, "lavangba", "data"), os.path.join(BASE_DIR, "data"))
# 각 사 편성표. 공개 저장소 안에서 돌 때는 같은 저장소의 파일을 그대로 읽고
# (같은 워크플로우에서 방금 갱신된 최신본을 쓰게 됨), 없으면 GitHub raw로 받는다.
HOMESHOPPING_DIR = os.path.join(BASE_DIR, "homeshopping")

# 보안정책 변경으로 더 이상 수집할 수 없는 값(총주문 등)을 표시하는 값.
# 필드 자체는 v1과 동일하게 유지하고 값만 이걸로 채운다.
MISSING = "-"

# platform_id -> hdmzp/hdhs 저장소 homeshopping 폴더 코드
GITHUB_CODE = {
    "hs_gsshop": "GS",
    "hs_cjonstyle": "CJ",
    "hs_hmall": "HD",
    "hs_lotteimall": "LT",
    "hs_nsmall": "NS",
    "hs_gongyoung": "PUBLIC",
    "hs_shinsegae": "SHINSEGAE",
    "hs_shopntmall": "SHOPPINGNT",
    "hs_skstoa": "SKSTOA",
    "hs_hnsmall": "HNS",
    "hs_kshop": "KTALPHA",
}
# 제외: hs_hmallplus / hs_gsshopmyshop / hs_lotteimallonetv / hs_nsmallshopplus /
#       hs_cjonstyleplus (TV 11개사 외 데이터홈쇼핑/플러스 채널 - 필요하면 위 맵에 추가)

API_HEADERS = {"content-type": "application/json", "domain": "ecomm-data.com"}

# 인자 없이 실행할 때 "빠진 날짜"를 찾아볼 기간(어제 기준 과거 며칠).
# 하루 실패해서 생긴 구멍도 메우되, 한 번에 너무 긴 기간을 긁지 않도록 상한 역할도 한다.
BACKFILL_LOOKBACK_DAYS = 30

HTTP_TIMEOUT_SEC = 20
HTTP_RETRIES = 4
HTTP_RETRY_SLEEP_SEC = 3


# ============================== HTTP ==============================

def _request_with_retry(method, url, **kwargs):
    """네트워크 오류/5xx면 재시도하고, 다 소진하면 마지막 예외를 던진다."""
    kwargs.setdefault("timeout", HTTP_TIMEOUT_SEC)
    last_err = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            res = requests.request(method, url, **kwargs)
            if res.status_code >= 500:
                raise RuntimeError(f"HTTP {res.status_code}")
            res.raise_for_status()
            return res
        except Exception as e:
            last_err = e
            if attempt < HTTP_RETRIES:
                print(f"  [재시도 {attempt}/{HTTP_RETRIES}] {url.rsplit('/', 1)[-1]} - {e}")
                time.sleep(HTTP_RETRY_SLEEP_SEC)
    raise last_err


def fetch_list_hs(date_str):
    """그날의 방송 목록. 로그인 불필요(공개 스케줄) - 매출 필드는 어차피 안 씀."""
    res = _request_with_retry(
        "POST",
        "https://live.ecomm-data.com/api/schedule/list_hs",
        headers=API_HEADERS,
        json={"date": date_str[2:]},
    )
    data = res.json()
    return [x for x in data.get("list", []) if x.get("platform_id") in GITHUB_CODE]


_github_cache = {}


def fetch_github_month(code, yyyy_mm):
    """각 사 편성표 {코드}_live/{YYYY-MM}.json.
    같은 저장소 안(공개 저장소 hdmzp/hdhs)에 파일이 있으면 그걸 읽고, 없으면 GitHub raw로 받는다."""
    key = f"{code}|{yyyy_mm}"
    if key in _github_cache:
        return _github_cache[key]

    data = None
    local_path = os.path.join(HOMESHOPPING_DIR, f"{code}_live", f"{yyyy_mm}.json")
    if os.path.exists(local_path):
        try:
            with open(local_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  [편성표] 로컬 {code} {yyyy_mm} 읽기 실패({e}) - GitHub raw로 재시도")

    if data is None:
        url = f"https://raw.githubusercontent.com/hdmzp/hdhs/main/homeshopping/{code}_live/{yyyy_mm}.json"
        try:
            res = requests.get(url, timeout=HTTP_TIMEOUT_SEC)
            if res.ok:
                data = res.json()
        except requests.RequestException:
            pass

    _github_cache[key] = data
    return data


# ============================== 시간/문자열 유틸 ==============================

def hs_to_datetime(s):
    return datetime(int(s[0:4]), int(s[4:6]), int(s[6:8]), int(s[8:10]), int(s[10:12]), tzinfo=KST)


def gh_entry_datetimes(date_hyphen, entry):
    y, m, d = (int(x) for x in date_hyphen.split("-"))
    sh, sm = (int(x) for x in entry["start"].split(":"))
    eh, em = (int(x) for x in entry["end"].split(":"))
    start = datetime(y, m, d, sh, sm, tzinfo=KST)
    end = datetime(y, m, d, eh, em, tzinfo=KST)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def ymd_to_hyphen(date_str):
    return f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"


def kst_today():
    return datetime.now(KST).date()


def date_range(start_ymd, end_ymd):
    start = datetime.strptime(start_ymd, "%Y%m%d").date()
    end = datetime.strptime(end_ymd, "%Y%m%d").date()
    out, cur = [], start
    while cur <= end:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def format_price(v):
    if not isinstance(v, (int, float)) or not v:
        return MISSING
    return f"{int(v):,}원"


_BRACKET_RE = re.compile(r"\[([^\[\]]*)\]|\(([^()]*)\)")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")


def extract_brand(name):
    """상품명/방송제목에서 브랜드로 추정되는 어절 하나를 뽑는다(편성표 brand가 빈 경우용).
    대괄호/소괄호 마케팅 카피("[방송에서만]" 등)를 걷어낸 본문 첫 어절을 쓰되, 그게
    1글자거나 숫자뿐이면(가격/모델명) 괄호 안 첫 어절을 대신 쓴다."""
    raw = (name or "").strip()
    if not raw:
        return ""

    bracket_contents = [(g1 or g2).strip() for g1, g2 in _BRACKET_RE.findall(raw)]
    bracket_contents = [c for c in bracket_contents if c]

    body = _BRACKET_RE.sub(" ", raw)
    body = _MULTI_SPACE_RE.sub(" ", body).strip().lstrip("+").strip()

    def first_word(s):
        parts = s.split()
        return parts[0] if parts else ""

    candidates = [w for w in [first_word(body)] + [first_word(c) for c in bracket_contents] if w]
    if not candidates:
        return ""
    brand = candidates[0]
    if (len(brand) <= 1 or brand.isdigit()) and len(candidates) > 1:
        return candidates[1]
    return brand


_NON_ALNUM_RE = re.compile(r"[^0-9a-z가-힣]+")


def _norm_match_text(s):
    """제목 매칭용 정규화: 소문자화 후 괄호/특수문자를 공백으로 치환."""
    return _NON_ALNUM_RE.sub(" ", (s or "").lower()).strip()


def title_entry_similarity(title, entry):
    """라방바 방송 제목과 편성표 항목(product/brand)의 유사도(0~1).
    같은 시간대에 방송 2개가 병행 편성됐을 때 어느 편성 항목이 어느 방송 것인지
    가려내고, 복합 방송에서 대표 상품을 고르는 데 쓴다.
    brand 매칭은 0.8점 상한 - 같은 브랜드 방송 2개가 병행 편성되면 브랜드만으로는
    구분이 안 되므로 상품명이 실제로 맞는 쪽이 항상 이기게 한다."""
    nt = _norm_match_text(title)
    nt_ns = nt.replace(" ", "")
    if not nt_ns:
        return 0.0
    best = 0.0
    for cap, cand in ((1.0, entry.get("product") or ""), (0.8, entry.get("brand") or "")):
        nc = _norm_match_text(cand)
        nc_ns = nc.replace(" ", "")
        if len(nc_ns) < 2:
            continue
        if nc_ns in nt_ns or nt_ns in nc_ns:
            score = 1.0
        else:
            seq = SequenceMatcher(None, nt_ns, nc_ns).ratio()
            ta, tb = set(nt.split()), set(nc.split())
            jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
            score = max(seq, jac)
        best = max(best, min(score, cap))
    return best


# ============================== 편성표 <-> 방송 매칭 ==============================

def prepare_shows(list_hs, date_str):
    """방송(hsshow)별 기본 정보 + 해당 채널의 GitHub 편성표를 붙여서 반환.
    편성 항목 배정은 resolve_entry_matches에서 한다."""
    date_hyphen = ymd_to_hyphen(date_str)
    yyyy_mm = f"{date_str[0:4]}-{date_str[4:6]}"
    prepared = []
    for hshow in list_hs:
        code = GITHUB_CODE[hshow["platform_id"]]
        month_data = fetch_github_month(code, yyyy_mm)
        prepared.append({
            "hshow": hshow,
            "code": code,
            "channel_label": hshow.get("platform_name") or code,
            "day_entries": ((month_data or {}).get("days") or {}).get(date_hyphen, []),
            "start": hs_to_datetime(hshow["hsshow_datetime_start"]),
            "end": hs_to_datetime(hshow["hsshow_datetime_end"]),
            "matched": [],
        })
    return prepared


def resolve_entry_matches(prepared, date_hyphen):
    """각 방송(hsshow)에 편성표 항목을 배정한다 (p["matched"]를 채움).

    1) 편성 항목에 hsshow_id가 있으면(라방바 기반 편성표: GS/NS/공영/신세계/SK스토아 등)
       그 값으로 1:1 정확 매칭.
    2) 나머지 항목(HD/CJ/LT 자사몰 편성표, hsshow_id 없는 과거 데이터)은 5분 이상
       겹치는 방송을 후보로 하되, 한 항목을 두 방송이 동시에 가져가지 않도록
       제목 유사도 -> 겹침시간 순으로 한 방송에만 배정한다.
       (같은 시간대에 방송 2개가 병행 편성되는 채널에서 서로 상대 상품의 편성 정보를
        가져가 브랜드/카테고리/가격이 오염되는 것 방지)
    """
    by_code = {}
    for p in prepared:
        by_code.setdefault(p["code"], []).append(p)

    for shows in by_code.values():
        ecs = []
        for e in shows[0]["day_entries"]:
            s, en = gh_entry_datetimes(date_hyphen, e)
            ecs.append({"entry": e, "start": s, "end": en,
                        "hsshow_id": str(e.get("hsshow_id") or "")})
        live_ids = {str(p["hshow"].get("hsshow_id") or "") for p in shows} - {""}

        # 1) hsshow_id 정확 매칭
        for p in shows:
            own_id = str(p["hshow"].get("hsshow_id") or "")
            if own_id:
                p["matched"] = [
                    {"entry": ec["entry"], "start": ec["start"], "end": ec["end"]}
                    for ec in ecs if ec["hsshow_id"] == own_id
                ]

        # 2) id로 매칭 안 된 방송: 겹침 기반 후보 수집 후 항목별로 승자 1명에게만 배정.
        #    다른 방송의 id가 달린 항목은 그 방송 소유이므로 후보에서 제외.
        claims = {}
        fallback_shows = []
        for p in shows:
            if p["matched"]:
                continue
            p["_cands"] = []
            fallback_shows.append(p)
            for idx, ec in enumerate(ecs):
                if ec["hsshow_id"] and ec["hsshow_id"] in live_ids:
                    continue
                overlap_min = (min(ec["end"], p["end"]) - max(ec["start"], p["start"])).total_seconds() / 60
                if overlap_min < 5:
                    continue
                sim = title_entry_similarity(p["hshow"].get("hsshow_title"), ec["entry"])
                claims.setdefault(idx, []).append((round(sim, 3), overlap_min, p))
                p["_cands"].append((round(sim, 3), overlap_min, idx))

        owner = {}
        for idx, claimers in claims.items():
            claimers.sort(key=lambda t: (t[0], t[1]), reverse=True)  # 동점이면 list_hs 순서 유지
            sim, _overlap, winner = claimers[0]
            owner[idx] = winner
            if len(claimers) > 1:
                print(f"  [편성매칭] '{(ecs[idx]['entry'].get('product') or '')[:24]}' -> "
                      f"'{(winner['hshow'].get('hsshow_title') or '')[:24]}' (유사도 {sim:.2f}, 경합 {len(claimers)}건)")

        # 독식 방지: 후보는 있었는데 하나도 못 받은 방송은, 항목을 2개 이상 가져간
        # 방송에게서 자기 유사도가 가장 높은 항목 1개를 넘겨받는다.
        own_count = {}
        for p in owner.values():
            own_count[id(p)] = own_count.get(id(p), 0) + 1
        for p in fallback_shows:
            if own_count.get(id(p)):
                continue
            for sim, _overlap, idx in sorted(p["_cands"], reverse=True):
                cur = owner[idx]
                if own_count.get(id(cur), 0) >= 2:
                    owner[idx] = p
                    own_count[id(cur)] -= 1
                    own_count[id(p)] = 1
                    print(f"  [편성매칭] '{(ecs[idx]['entry'].get('product') or '')[:24]}' 재배정 -> "
                          f"'{(p['hshow'].get('hsshow_title') or '')[:24]}' (유사도 {sim:.2f})")
                    break

        for idx, p in owner.items():
            p["matched"].append({"entry": ecs[idx]["entry"], "start": ecs[idx]["start"], "end": ecs[idx]["end"]})
        for p in fallback_shows:
            p.pop("_cands", None)

        for p in shows:
            p["matched"].sort(key=lambda m: m["start"])


# ============================== 행 생성 ==============================

def entry_brand(entry):
    """편성 항목의 브랜드. brand 필드가 비어있으면(NS/SK스토아 등) 상품명에서 추정."""
    return (entry.get("brand") or "").strip() or extract_brand(entry.get("product") or "")


def pick_representative(matched, title):
    """방송의 대표 편성 항목(= 단일코드) 하나를 고른다.

    v1은 복합 방송을 분단위 매출 시계열로 상품별 분리했지만, v2는 매출을 못 받으므로
    분리 근거가 없다. 대신 편성 노출시간이 가장 긴 항목을, 시간이 같으면(HD/CJ/LT
    자사몰 편성표는 복합 방송의 모든 항목을 방송 전체 시간으로 기재) 방송 제목과 가장
    비슷한 항목을 대표로 뽑는다."""
    if not matched:
        return None
    return max(
        matched,
        key=lambda m: (m["end"] - m["start"], title_entry_similarity(title, m["entry"])),
    )


def build_row(prep, date_str):
    """방송 1건 -> 행 1개. 매출 관련 필드는 MISSING("-")으로 채운다."""
    hshow = prep["hshow"]
    matched = prep["matched"]
    title = hshow.get("hsshow_title") or ""
    cat_name = (hshow.get("cat") or {}).get("cat_name", "")

    hshow_start = prep["start"]
    hshow_end = prep["end"]

    # 라방바 종료시각(hsshow_datetime_end)이 실제 편성표보다 1분쯤 늦게 찍히는 경우가
    # 잦다. 편성표 마지막 상품 종료시각과 5분 이내로만 차이나면 편성표 쪽을 신뢰해서
    # 보정한다(차이가 크면 편성 매칭 자체가 틀렸을 수 있으니 라방바 값 유지).
    if matched:
        gh_end = max(m["end"] for m in matched)
        if gh_end > hshow_start and abs((gh_end - hshow_end).total_seconds()) <= 5 * 60:
            hshow_end = gh_end

    start_label = hshow_start.strftime("%H:%M")
    end_label = hshow_end.strftime("%H:%M")
    duration_min = round((hshow_end - hshow_start).total_seconds() / 60)

    # 단순/복합 판정: 편성 항목의 브랜드가 2종 이상이면 복합.
    # (v1은 SKU 상품명 브랜드까지 봤지만 그건 로그인 API라 v2에선 못 씀)
    entry_brands = {entry_brand(m["entry"]) for m in matched} - {""}
    is_simple = len(entry_brands) <= 1

    rep = pick_representative(matched, title)
    rep_entry = rep["entry"] if rep else {}

    # 복합이면 대표 상품명(단일코드)을, 단순이면 v1과 동일하게 방송 제목을 상품명으로 쓴다.
    item_name = title if is_simple else (rep_entry.get("product") or title)
    brand = (
        entry_brand(rep_entry) if rep_entry else ""
    ) or (next(iter(entry_brands)) if len(entry_brands) == 1 else "") or extract_brand(title)

    price = rep_entry.get("price")
    price = price if isinstance(price, (int, float)) and price else MISSING

    # link 열은 라방바 방송 상세 페이지(공개 페이지), product_link는 상품 구매 페이지.
    lavangba_link = f"https://live.ecomm-data.com/report/hsshow/{hshow['hsshow_id']}"

    return {
        "channel": prep["channel_label"],
        "date": date_str,
        "broadcast_start": start_label,
        "broadcast_end": end_label,
        "duration_min": duration_min,
        "pgm_title": title,
        "brand": brand,
        "item_name": item_name,
        "type": "단순" if is_simple else "복합",
        # 방송 1건 = 1행이므로 상품 노출구간 = 방송 전체 구간.
        "item_start": start_label,
        "item_end": end_label,
        "item_duration_min": duration_min,
        # 로그인 필요 -> 수집 불가 (총주문, index.html의 순주문도 이 값에서 파생)
        "sales_amt": MISSING,
        "category": rep_entry.get("category", "") if rep_entry else "",
        "lavangba_category": (rep_entry.get("lavangba_category") or cat_name) if rep_entry else cat_name,
        "price": price,
        "link": lavangba_link,
        "product_link": rep_entry.get("link") if rep_entry else None,
    }


# ============================== 수집 ==============================

def scrape_dates(target_dates):
    rows = []
    hshow_count = 0

    for date_str in target_dates:
        print(f"[list_hs] {date_str} 조회 중...")
        try:
            list_hs = fetch_list_hs(date_str)
        except Exception as e:
            print(f"[오류] {date_str} 방송 목록 조회 실패 - {e}")
            continue
        print(f"  -> 대상 11개사 방송 {len(list_hs)}건")
        if not list_hs:
            continue

        prepared = prepare_shows(list_hs, date_str)
        resolve_entry_matches(prepared, ymd_to_hyphen(date_str))

        date_rows = []
        for prep in prepared:
            hshow_count += 1
            row = build_row(prep, date_str)
            date_rows.append(row)
            print(f"  {row['type']} | {row['channel']} {row['item_start']} | "
                  f"{row['item_name'][:24]} | 판매가 {format_price(row['price'])}")

        # 날짜 하나가 끝날 때마다 바로 저장 (길게 돌리다 중간에 죽어도 완료분 보존)
        if date_rows:
            rows.extend(date_rows)
            save_rows(date_rows)

    print(f"완료! 총 {len(rows)}행 ({hshow_count}개 방송)")
    return rows


def save_rows(rows):
    """월별 파일(data/YYYYMM.json)에 {"YYYYMMDD": [행...]} 구조로 저장.
    기존 월 파일이 있으면 이번에 수집한 날짜만 갈아끼우고 나머지 날짜는 보존한다
    (v1과 완전히 같은 형식 - index.html이 그대로 읽는다)."""
    os.makedirs(DATA_DIR, exist_ok=True)

    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)

    by_month = {}
    for date_str, date_rows in by_date.items():
        by_month.setdefault(date_str[:6], {})[date_str] = date_rows

    for month, month_updates in sorted(by_month.items()):
        json_path = os.path.join(DATA_DIR, f"{month}.json")
        month_data = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, encoding="utf-8") as f:
                    month_data = json.load(f)
            except Exception:
                month_data = {}
        month_data.update(month_updates)
        month_data = {k: month_data[k] for k in sorted(month_data)}

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(month_data, f, ensure_ascii=False, separators=(",", ":"))

        updated = ", ".join(f"{d}({len(v)}행)" for d, v in sorted(month_updates.items()))
        print(f"저장됨: {json_path} <- {updated}")


def saved_dates():
    """data/에 이미 저장돼 있는 날짜(YYYYMMDD) 집합.
    월별 파일(YYYYMM.json)의 날짜 키와 예전 일별 파일(YYYYMMDD.json) 파일명을 모두 인식하고,
    행이 0개인 날짜 키는 수집 실패로 보고 "저장 안 됨"으로 취급한다."""
    dates = set()
    if not os.path.isdir(DATA_DIR):
        return dates
    for fname in os.listdir(DATA_DIR):
        if re.fullmatch(r"\d{8}\.json", fname):
            dates.add(fname[:8])
            continue
        if not re.fullmatch(r"\d{6}\.json", fname):
            continue
        try:
            with open(os.path.join(DATA_DIR, fname), encoding="utf-8") as f:
                month_data = json.load(f)
        except Exception:
            continue
        dates.update(k for k, v in month_data.items() if re.fullmatch(r"\d{8}", k) and v)
    return dates


def missing_dates(lookback_days=BACKFILL_LOOKBACK_DAYS):
    """어제부터 과거 lookback_days일 사이에서 아직 수집 안 된 날짜들을 오름차순으로 반환.

    "마지막 저장일 다음날부터"가 아니라 "빠진 날짜 전부"를 보기 때문에, 중간에 하루
    실패해서 구멍이 난 경우(예: 워크플로우가 실패한 날)도 다음 실행 때 알아서 메운다.
    이미 있는 날짜는 건너뛰고, 수집한 날짜만 월별 JSON에 덧씌운다."""
    yesterday = kst_today() - timedelta(days=1)
    have = saved_dates()
    out = []
    for i in range(lookback_days, 0, -1):
        d = (yesterday - timedelta(days=i - 1)).strftime("%Y%m%d")
        if d not in have:
            out.append(d)
    return out


def main():
    args = sys.argv[1:]
    if args:
        if not all(re.fullmatch(r"\d{8}", a) for a in args) or len(args) > 2:
            print("사용법: python lavangba_scraper_v2.py [시작일YYYYMMDD] [종료일YYYYMMDD]")
            sys.exit(1)
        start, end = args[0], args[-1]
        if start > end:
            print(f"시작일({start})이 종료일({end})보다 늦습니다.")
            sys.exit(1)
        target_dates = date_range(start, end)
    else:
        # 인자가 없으면 data/의 월별 파일을 훑어서 "아직 없는 날짜"만 고른다.
        target_dates = missing_dates()
        if not target_dates:
            print(f"최근 {BACKFILL_LOOKBACK_DAYS}일 내 빠진 날짜 없음 - 수집할 날짜 없음")
            return
        print(f"빠진 날짜 {len(target_dates)}일: {', '.join(target_dates)}")
    print(f"수집 대상: {target_dates[0]} ~ {target_dates[-1]} ({len(target_dates)}일)")

    rows = scrape_dates(target_dates)
    if not rows:
        print("[경고] 수집된 행이 없습니다.")
        sys.exit(1)  # 스케줄러가 다음 회차에 재시도


if __name__ == "__main__":
    main()
