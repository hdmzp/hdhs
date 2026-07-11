"""
라방바(live.ecomm-data.com) 11개사 방송 스케줄 + 매출 자동 수집.

login_setup.py로 만들어둔 chrome_profile/ 의 로그인 세션을 재사용한다.

중요: hsshow/items API는 headless 브라우저로 호출하면 로그인 여부와 무관하게
매출액이 마스킹(null)돼서 나온다 (User-Agent에 "HeadlessChrome"이 찍히는 걸
라방바 쪽에서 감지하는 것으로 보임). 그래서 headless=False로 띄우되, 화면
밖(-32000,-32000)에 창을 배치해서 사용자 눈에는 안 보이면서도 "진짜 브라우저"로
인식되게 한다. API 호출은 requests가 아니라 그 페이지 안에서 fetch()를 실행하는
방식(page.evaluate)으로 한다 - 쿠키를 꺼내서 별도 세션으로 쓰면 마스킹이 풀리지 않았음.

- list_hs: 로그인 불필요(항상 마스킹된 공개 스케줄), 방송 목록/시작종료시각/item_cnt 조회 -> requests로 충분
- hsshow/items: 로그인 필요, 실제 매출액(sales_amt)/시계열(sales_amt_rcd) 조회 -> 반드시 브라우저 페이지 경유
- GitHub(hdmzp/hdhs) homeshopping/{코드}_live/{YYYY-MM}.json: 각 회사 자체 편성표
  (상품별 시작/종료시각) 자동 fetch. 복합 PGM(상품 여러개)일 때 라방바 아이템들의
  시계열을 이 편성표 시간대에 매칭해서 상품별 매출을 분리한다.

편성표 항목 <-> 라방바 방송(hsshow) 매칭 규칙:
  1) 편성 항목에 hsshow_id가 저장돼 있으면(gs_scraper/etc_scraper가 라방바 기반으로
     만든 편성표) 그 id로 1:1 정확 매칭한다.
  2) id가 없는 항목(HD/CJ/LT 자사몰 편성표, 과거 데이터)은 기존처럼 "5분 이상
     시간 겹침"으로 후보를 찾되, 한 항목을 여러 방송이 노리면 제목 유사도 ->
     겹침시간 순으로 딱 한 방송에만 배정한다.
  SK스토아/신세계처럼 같은 시간대에 방송 2개가 병행 편성되는 채널에서, 두 방송이
  서로 상대 상품의 편성 정보(브랜드/카테고리/가격/링크)까지 가져가 오염되는 것을
  막기 위함이다.

사용법:
    venv\\Scripts\\python.exe lavangba_scraper.py

로그인 세션이 만료된 상태면(매출액이 마스킹됨) 경고를 남기고 종료코드 1로 끝난다
(작업 스케줄러가 다음 회차에 재시도하도록).
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import requests
from playwright.sync_api import sync_playwright

# 콘솔/리다이렉트 인코딩이 cp949일 때 상품명의 특수문자(∙ 등)로 print가 죽는 것 방지
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

KST = timezone(timedelta(hours=9))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, "chrome_profile")
DATA_DIR = os.path.join(BASE_DIR, "data")

BROWSER_ARGS = [
    "--window-position=-32000,-32000",  # 화면 밖으로 배치 (headless 안 씀 -> 마스킹 회피용)
    "--window-size=1280,900",
    "--disable-blink-features=AutomationControlled",
]

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
    "hs_hmallplus": "HD+",
    "hs_gsshopmyshop":"GS+",
    "hs_lotteimallonetv":"LT+",
    "hs_nsmallshopplus":"NS+",
    "hs_cjonstyleplus":"CJ+"
}
# 제외: hs_hmallplus / hs_gsshopmyshop / hs_lotteimallonetv / hs_nsmallshopplus / hs_cjonstyleplus
# (TV 11개사 외 데이터홈쇼핑/플러스 채널 - 필요하면 위 맵에 추가)

API_HEADERS = {"content-type": "application/json", "domain": "ecomm-data.com"}


def launch_browser():
    if not os.path.isdir(PROFILE_DIR):
        raise RuntimeError("chrome_profile/ 이 없습니다. 먼저 login_setup.py 를 실행해서 로그인하세요.")
    pw = sync_playwright().start()
    try:
        context = pw.chromium.launch_persistent_context(PROFILE_DIR, headless=False, args=BROWSER_ARGS)
    except Exception as e:
        pw.stop()
        raise RuntimeError(f"프로필 실행 실패 (다른 프로세스가 같은 프로필을 쓰고 있을 수 있음): {e}")
    page = context.pages[0] if context.pages else context.new_page()
    for extra in context.pages[1:]:
        extra.close()
    page.goto("https://live.ecomm-data.com/", wait_until="domcontentloaded", timeout=60000)
    return pw, context, page


_FETCH_JS = """async ([url, options]) => {
  const res = await fetch(url, options);
  return {status: res.status, text: await res.text()};
}"""


def page_post_json(page, url, body):
    options = {"method": "POST", "headers": API_HEADERS, "body": json.dumps(body), "credentials": "include"}
    result = page.evaluate(_FETCH_JS, [url, options])
    if result["status"] >= 400:
        raise RuntimeError(f"HTTP {result['status']}: {result['text'][:200]}")
    return json.loads(result["text"])


def fetch_list_hs(date_str):
    yymmdd = date_str[2:]
    res = requests.post(
        "https://live.ecomm-data.com/api/schedule/list_hs",
        headers=API_HEADERS,
        json={"date": yymmdd},
        timeout=15,
    )
    res.raise_for_status()
    data = res.json()
    return [x for x in data.get("list", []) if x.get("platform_id") in GITHUB_CODE]


def fetch_items_all(page, hshow_id, expected_count=None):
    pg = 1
    size = 50
    all_items = []
    while True:
        data = page_post_json(
            page,
            "https://live.ecomm-data.com/api/hsshow/items",
            {"hsshow_id": hshow_id, "page": pg, "size": size, "order": ["sales_amt/desc"], "with_rcd": True},
        )
        items = data.get("items", [])
        all_items.extend(items)
        total_count = data.get("total_count")
        if total_count is None:
            total_count = expected_count or len(all_items)
        if len(items) < size or len(all_items) >= total_count:
            break
        pg += 1
    return all_items


def check_login(page, hshow_id):
    data = page_post_json(
        page,
        "https://live.ecomm-data.com/api/hsshow/items",
        {"hsshow_id": hshow_id, "page": 1, "size": 1, "order": ["sales_amt/desc"], "with_rcd": False},
    )
    items = data.get("items", [])
    return bool(items) and items[0].get("sales_amt") is not None


_github_cache = {}


def fetch_github_month(code, yyyy_mm):
    key = f"{code}|{yyyy_mm}"
    if key in _github_cache:
        return _github_cache[key]
    url = f"https://raw.githubusercontent.com/hdmzp/hdhs/main/homeshopping/{code}_live/{yyyy_mm}.json"
    data = None
    try:
        res = requests.get(url, timeout=15)
        if res.ok:
            data = res.json()
    except requests.RequestException:
        pass
    _github_cache[key] = data
    return data


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


def format_amt(total):
    if total >= 100_000_000:
        return f"{total / 100_000_000:.2f}억"
    return f"{round(total / 10_000)}만"


_BRACKET_RE = re.compile(r"\[([^\[\]]*)\]|\(([^()]*)\)")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")


def extract_brand_candidates(name):
    """상품명에서 브랜드로 추정되는 어절 후보들을 전부 뽑는다.
    대괄호/소괄호 마케팅 카피("[방송에서만]", "(최초가69,900원)" 등)를 제거한
    본문의 첫 어절과, 제거했던 괄호 안 내용들의 첫 어절을 모두 후보로 반환한다.
    ("[아로마티카] 스파 샴푸"처럼 브랜드가 오히려 괄호 안에 있는 경우, 본문 첫
    어절("스파")만으로는 실제 브랜드("아로마티카")를 놓치므로, 최종 판단(둘 중
    무엇이 진짜 브랜드인지)은 호출부에서 알려진 브랜드 목록과 매칭해서 정한다.)
    본문 후보를 우선순위로 앞에 둔다."""
    raw = (name or "").strip()
    if not raw:
        return []

    bracket_contents = [g1 or g2 for g1, g2 in _BRACKET_RE.findall(raw)]
    bracket_contents = [c.strip() for c in bracket_contents if c and c.strip()]

    body = _BRACKET_RE.sub(" ", raw)
    body = _MULTI_SPACE_RE.sub(" ", body).strip().lstrip("+").strip()

    def first_word(s):
        parts = s.split()
        return parts[0] if parts else ""

    candidates = []
    fw = first_word(body)
    if fw:
        candidates.append(fw)
    for bc in bracket_contents:
        fw2 = first_word(bc)
        if fw2 and fw2 not in candidates:
            candidates.append(fw2)
    return candidates


def extract_brand(name):
    """상품명에서 브랜드로 추정되는 어절 하나를 뽑는다 (표시/단순분류용 단일 추정치).
    본문 첫 어절이 너무 짧거나(1글자) 숫자뿐이면(가격/모델명 등) 브랜드로 보기
    어려우므로 괄호 안 내용을 대신 쓴다. 여러 후보 중 어떤 게 진짜 브랜드인지
    확실히 가려야 하는 곳(복합PGM 매칭)에서는 extract_brand_candidates를 써서
    모든 후보를 다 시도해야 한다 - 이 함수처럼 하나만 고르면 브랜드가 괄호 안에
    있는데 본문 첫 어절도 그럴듯한 단어인 경우를 놓칠 수 있다."""
    candidates = extract_brand_candidates(name)
    if not candidates:
        return ""
    brand = candidates[0]
    if (not brand or len(brand) <= 1 or brand.isdigit()) and len(candidates) > 1:
        return candidates[1]
    return brand


_NON_ALNUM_RE = re.compile(r"[^0-9a-z가-힣]+")


def _norm_match_text(s):
    """제목 매칭용 정규화: 소문자화 후 괄호/특수문자를 공백으로 치환."""
    return _NON_ALNUM_RE.sub(" ", (s or "").lower()).strip()


def title_entry_similarity(title, entry):
    """라방바 방송 제목과 편성표 항목(product/brand)의 유사도(0~1).
    같은 시간대에 방송 2개가 병행 편성됐을 때 어느 편성 항목이 어느 방송 것인지
    가려내는 용도. 정규화 후 포함관계면 만점, 아니면 전체 문자열 유사도와
    어절 자카드 유사도 중 큰 값을 쓴다 (편성표 product는 clean_product_name으로
    정제돼 있어서 원 제목과 어절 단위로 어긋나는 경우가 있음).
    brand 매칭은 0.8점 상한 - 같은 브랜드의 방송 2개가 병행 편성되면(예: 플리츠미
    블라우스 방송 + 플리츠미 원피스 방송) 브랜드만으로는 구분이 안 되므로,
    상품명이 실제로 맞는 쪽이 항상 이기게 한다."""
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


def classify_item_to_segment(item, segments):
    """원본 JS의 classifyItemToSegment와 동일한 승자독식 방식:
    SKU의 분단위 매출 시계열(sales_amt_rcd) 활동이 가장 컸던 세그먼트 하나에
    그 SKU의 매출 전액을 배정한다."""
    rcd_raw = item.get("sales_amt_rcd") or ""
    rcd = []
    if rcd_raw:
        for x in rcd_raw.split(","):
            x = x.strip()
            try:
                rcd.append(int(x))
            except ValueError:
                rcd.append(0)
    best_idx, best_sum = None, -1
    for seg in segments:
        s = sum(rcd[i] for i in range(max(0, seg["from"]), min(seg["to"], len(rcd))))
        if s > best_sum:
            best_sum, best_idx = s, seg["idx"]
    return best_idx


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

    1) 편성 항목에 hsshow_id가 있으면(라방바 기반 편성표: GS/공영/신세계/SK스토아 등)
       그 값으로 1:1 정확 매칭.
    2) 나머지 항목(HD/CJ/LT 자사몰 편성표, hsshow_id 없는 과거 데이터)은 기존처럼
       5분 이상 겹치는 방송을 후보로 하되, 한 항목을 두 방송이 동시에 가져가지
       않도록 제목 유사도 -> 겹침시간 순으로 한 방송에만 배정한다.
       (같은 시간대에 방송 2개가 병행 편성되는 채널에서 서로 상대 상품의
        편성 정보를 가져가 브랜드/카테고리/가격이 오염되는 것 방지)
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
        # (제목이 서로 비슷한 병행 방송끼리 한쪽이 다 가져가는 경우 대비.
        #  편성표 항목이 방송 수보다 적어서 정당하게 빈손인 경우는 못 가져감)
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


def scrape_dates(target_dates):
    pw, context, page = launch_browser()

    rows = []
    hshow_count = 0
    login_checked = False

    try:
        for date_str in target_dates:
            print(f"[list_hs] {date_str} 조회 중...")
            list_hs = fetch_list_hs(date_str)
            print(f"  -> 대상 11개사 방송 {len(list_hs)}건")
            if not list_hs:
                continue

            if not login_checked:
                if not check_login(page, list_hs[0]["hsshow_id"]):
                    print("[경고] 로그인 세션이 유효하지 않은 것으로 보입니다 (매출액이 마스킹됨). 이번 회차는 건너뜁니다.")
                    return None
                login_checked = True

            date_hyphen = ymd_to_hyphen(date_str)

            # 1차: 방송별 편성표 로드, 2차: 편성 항목 <-> 방송 배정(id 정확 매칭 + 배타 배정)
            prepared = prepare_shows(list_hs, date_str)
            resolve_entry_matches(prepared, date_hyphen)

            rows_before = len(rows)
            for prep in prepared:
                hshow = prep["hshow"]
                hshow_count += 1
                channel_label = prep["channel_label"]
                matched = prep["matched"]

                hshow_start = prep["start"]
                hshow_end = prep["end"]
                pgm_start_label = hshow_start.strftime("%H:%M")

                # 라방바 자체 종료시각(hsshow_datetime_end)이 실제 편성표보다 정확히 1분
                # 늦게 찍히는 경우가 잦다 (예: 편성표 02:10 종료인데 라방바는 02:11로 기록).
                # 편성표 마지막 상품의 종료시각과 5분 이내로만 차이나면 편성표 쪽을 신뢰해서
                # 보정한다 (차이가 크면 편성 매칭 자체가 잘못됐을 수 있으니 라방바 값 유지).
                if matched:
                    gh_end = max(m["end"] for m in matched)
                    if gh_end > hshow_start and abs((gh_end - hshow_end).total_seconds()) <= 5 * 60:
                        hshow_end = gh_end

                print(f"[{channel_label} {date_str} {pgm_start_label}] item_cnt={hshow.get('item_cnt')} items 조회 중...")
                try:
                    items = fetch_items_all(page, hshow["hsshow_id"], hshow.get("item_cnt"))
                except Exception as e:
                    print(f"[오류] items 조회 실패: {hshow.get('hsshow_title')} - {e}")
                    items = []

                # 단순/복합 판정: 방송 내 모든 상품의 브랜드가 하나면 단순 (SKU 개수가 아님).
                # 브랜드는 SKU 상품명 첫 어절 또는 편성표 brand 필드로 판별.
                sku_brands = {extract_brand(it.get("item_name")) for it in items} - {""}
                gh_real_brands = {(m["entry"].get("brand") or "").strip() for m in matched} - {""}
                # 판정용 항목별 브랜드: brand 필드가 비어있으면 상품명에서 추정해서 채운다.
                # (편성 항목이 2개인데 한쪽만 brand가 비어있을 때 "브랜드 1개 = 단순"으로
                #  오판해서 서로 다른 상품을 한 행으로 뭉치는 것 방지)
                gh_entry_brands = set()
                for m in matched:
                    b = (m["entry"].get("brand") or "").strip() or extract_brand(m["entry"].get("product") or "")
                    if b:
                        gh_entry_brands.add(b)
                is_simple = (
                    len(items) <= 1
                    or len(sku_brands) <= 1
                    or (len(gh_entry_brands) == 1 and len(matched) >= 1)
                )
                cat_name = (hshow.get("cat") or {}).get("cat_name", "")
                pgm_end_label = hshow_end.strftime("%H:%M")
                duration_min = round((hshow_end - hshow_start).total_seconds() / 60)

                def base_row():
                    return {
                        "channel": channel_label,
                        "date": date_str,
                        "broadcast_start": pgm_start_label,
                        "broadcast_end": pgm_end_label,
                        "duration_min": duration_min,
                        "pgm_title": hshow.get("hsshow_title"),
                    }

                # 대표상품 가격/바로가기 링크: 편성표(GitHub) 쪽 정보를 우선 쓰고,
                # 없으면 라방바에서 가장 많이 팔린(=order sales_amt desc 1번) 상품 기준.
                top_item = items[0] if items else None

                # link 열에는 라방바 방송 상세 페이지를 넣는다 (매출/시계열을 바로 확인하는 용도).
                # 상품 구매 페이지는 product_link 열에 별도 보존.
                lavangba_link = f"https://live.ecomm-data.com/report/hsshow/{hshow['hsshow_id']}"

                if is_simple:
                    # 하나의 상품(브랜드)만 파는 방송 -> 라방바가 이미 계산해준 매출액 그대로 사용
                    total = sum(it.get("sales_amt") or 0 for it in items)
                    # 대표 편성 항목: 첫 항목이 아니라 방송 제목과 가장 비슷한 항목을 고른다.
                    # (같은 시간대에 다른 방송의 항목이 섞여 매칭됐던 시절의 오염 방지 +
                    #  동일 방송 내 항목 여러 개일 때도 제목에 맞는 쪽 선택)
                    best_gh = None
                    if matched:
                        _title = hshow.get("hsshow_title") or ""
                        best_gh = max(
                            matched,
                            key=lambda m: (title_entry_similarity(_title, m["entry"]), m["end"] - m["start"]),
                        )["entry"]
                    best_gh_brand = (best_gh.get("brand") or "").strip() if best_gh else ""
                    brand = (
                        best_gh_brand
                        or (next(iter(gh_real_brands)) if len(gh_real_brands) == 1 else "")
                        or (next(iter(sku_brands)) if len(sku_brands) == 1 else "")
                        or extract_brand(hshow.get("hsshow_title"))
                    )
                    price = (best_gh.get("price") if best_gh else None) or (top_item.get("live_price") if top_item else None)
                    product_link = (best_gh.get("link") if best_gh else None) or (top_item.get("item_url") if top_item else None)
                    rows.append({
                        **base_row(),
                        "brand": brand,
                        "item_name": hshow.get("hsshow_title"),
                        "type": "단순",
                        "item_start": pgm_start_label,
                        "item_end": pgm_end_label,
                        "item_duration_min": duration_min,
                        "sales_amt": total,
                        "category": best_gh.get("category", "") if best_gh else "",
                        "lavangba_category": (best_gh.get("lavangba_category") or cat_name) if best_gh else cat_name,
                        "price": price,
                        "link": lavangba_link,
                        "product_link": product_link,
                    })
                    print(f"  단순 | {format_amt(total)}")
                else:
                    # 여러 상품을 파는 방송 -> 시계열(sales_amt_rcd)로 상품별 비중을 계산해서 집계.
                    # GitHub 편성표 세그먼트가 없으면 방송 전체를 세그먼트 1개로 보고 그냥 합산.
                    if len(matched) >= 2:
                        segments = [
                            {
                                "idx": i,
                                "from": max(0, round((m["start"] - hshow_start).total_seconds() / 60)),
                                "to": min(duration_min, round((m["end"] - hshow_start).total_seconds() / 60)),
                                "name": m["entry"].get("product") or hshow.get("hsshow_title") or "",
                                "brand": (m["entry"].get("brand") or "").strip() or extract_brand(m["entry"].get("product") or ""),
                                "category": m["entry"].get("category", ""),
                                "lavangba_category": m["entry"].get("lavangba_category") or cat_name,
                                "price": m["entry"].get("price"),
                                "link": m["entry"].get("link"),
                            }
                            for i, m in enumerate(matched)
                        ]
                    elif len(matched) == 1:
                        # 편성 항목이 1개뿐이면 시간 분할은 못 하지만, 편성표가 가진
                        # 브랜드/카테고리/가격/링크는 그대로 쓴다.
                        e = matched[0]["entry"]
                        segments = [{
                            "idx": 0, "from": 0, "to": duration_min,
                            "price": e.get("price") or (top_item.get("live_price") if top_item else None),
                            "link": e.get("link") or (top_item.get("item_url") if top_item else None),
                            "name": e.get("product") or hshow.get("hsshow_title"),
                            "brand": (e.get("brand") or "").strip(),
                            "category": e.get("category", ""),
                            "lavangba_category": e.get("lavangba_category") or cat_name,
                        }]
                    else:
                        segments = [{
                            "idx": 0, "from": 0, "to": duration_min,
                            "price": top_item.get("live_price") if top_item else None,
                            "link": top_item.get("item_url") if top_item else None,
                            "name": hshow.get("hsshow_title"), "brand": "",
                            "category": "", "lavangba_category": cat_name,
                        }]

                    # 같은 브랜드의 세그먼트는 매출을 합쳐 1행으로.
                    groups = {}
                    for seg in segments:
                        key = seg["brand"] or seg["name"]  # 브랜드 미상이면 상품명별로 유지 (오병합 방지)
                        groups.setdefault(key, []).append(seg)

                    def norm_brand(b):
                        return re.sub(r"\s+", "", (b or "")).lower()

                    group_brand_norm = {
                        key: norm_brand(segs[0]["brand"] or segs[0]["name"])
                        for key, segs in groups.items()
                    }

                    # 1차: 라방바가 이미 SKU 단위로 정확히 나눠준 개별 상품의 매출액을,
                    # 그 SKU 자체 상품명에서 뽑은 브랜드와 세그먼트 그룹의 브랜드를 직접
                    # 매칭해서 그대로 배정한다. 시계열 승자독식 방식보다 훨씬 정확함
                    # (모든 매출이 활동 큰 구간 하나에 몰빵되는 문제 방지).
                    # 상품명 첫 어절 후보 하나만 보지 않고(브랜드가 괄호 안에 있는 경우를
                    # 놓칠 수 있어서) extract_brand_candidates가 뽑아준 후보를 전부
                    # 시도한다. 편성표 브랜드 표기와 실제 상품명 표기가 한두 글자 다른
                    # 경우(예: "비버리힐스폴로클럽" vs "비버리힐즈폴로클럽")를 대비해
                    # 부분일치 실패 시 유사도 기반으로 한 번 더 시도한다.
                    group_totals = {key: 0 for key in groups}
                    unmatched_items = []
                    for it in items:
                        cands = [norm_brand(c) for c in extract_brand_candidates(it.get("item_name"))]
                        cands = [c for c in cands if c]

                        matched_keys = set()
                        for cand in cands:
                            for key, gb in group_brand_norm.items():
                                if gb and (gb in cand or cand in gb):
                                    matched_keys.add(key)
                        matched_keys = list(matched_keys)

                        if len(matched_keys) != 1 and cands:
                            scored = sorted(
                                (
                                    (SequenceMatcher(None, gb, cand).ratio(), key)
                                    for cand in cands
                                    for key, gb in group_brand_norm.items() if gb
                                ),
                                reverse=True,
                            )
                            if scored and scored[0][0] >= 0.75 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.1):
                                matched_keys = [scored[0][1]]

                        if len(matched_keys) == 1:
                            group_totals[matched_keys[0]] += it.get("sales_amt") or 0
                        else:
                            unmatched_items.append(it)

                    # 2차: 브랜드명으로 못 찾은 SKU만 기존 시계열(승자독식) 방식으로 보조 처리
                    for it in unmatched_items:
                        best_idx = classify_item_to_segment(it, segments)
                        if best_idx is None:
                            continue
                        seg = next(s for s in segments if s["idx"] == best_idx)
                        key = seg["brand"] or seg["name"]
                        group_totals[key] += it.get("sales_amt") or 0

                    for key, segs in groups.items():
                        total = round(group_totals.get(key, 0))
                        rep = max(segs, key=lambda s: s["to"] - s["from"])
                        g_from = min(s["from"] for s in segs)
                        g_to = max(s["to"] for s in segs)
                        g_start = hshow_start + timedelta(minutes=g_from)
                        g_end = hshow_start + timedelta(minutes=g_to)
                        rows.append({
                            **base_row(),
                            "brand": rep["brand"] or extract_brand(rep["name"]),
                            "item_name": rep["name"],
                            "price": rep.get("price"),
                            "link": lavangba_link,
                            "product_link": rep.get("link"),
                            "type": "복합",
                            "item_start": g_start.strftime("%H:%M"),
                            "item_end": g_end.strftime("%H:%M"),
                            "item_duration_min": g_to - g_from,
                            "sales_amt": total,
                            "category": rep["category"],
                            "lavangba_category": rep["lavangba_category"],
                        })
                        print(f"  복합(브랜드합산) {rep['name'][:20]} | {format_amt(total)}")

                time.sleep(0.25)

            # 날짜 하나가 끝날 때마다 그 날짜 파일을 바로 저장한다.
            # (여러 날짜를 길게 돌리다 중간에 죽어도 완료된 날짜는 보존)
            if len(rows) > rows_before:
                save_rows(rows[rows_before:])
    finally:
        context.close()
        pw.stop()

    print(f"완료! 총 {len(rows)}행 ({hshow_count}개 방송)")
    return rows


def save_rows(rows, target_dates=None):
    # 여러 날짜를 한 번에 돌려도 날짜별 파일로 나눠서 저장한다 (data/YYYYMMDD.json / .tsv)
    os.makedirs(DATA_DIR, exist_ok=True)
    cols = ["channel", "date", "broadcast_start", "broadcast_end", "duration_min", "pgm_title",
            "brand", "item_name", "type", "item_start", "item_end", "item_duration_min",
            "sales_amt", "category", "lavangba_category", "price", "link", "product_link"]

    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)

    for date_str, date_rows in sorted(by_date.items()):
        json_path = os.path.join(DATA_DIR, f"{date_str}.json")
        tsv_path = os.path.join(DATA_DIR, f"{date_str}.tsv")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(date_rows, f, ensure_ascii=False, indent=2)

        with open(tsv_path, "w", encoding="utf-8-sig") as f:
            f.write("\t".join(cols) + "\n")
            for r in date_rows:
                f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

        print(f"저장됨: {json_path} ({len(date_rows)}행)")


def main():
    yesterday = (kst_today() - timedelta(days=1)).strftime("%Y%m%d")
    target_dates = [yesterday]
    # 특정 날짜: target_dates = ["20260709"]
    # 기간: target_dates = date_range("20260701", "20260709")

    rows = scrape_dates(target_dates)
    if rows is None:
        sys.exit(1)  # 로그인 안 된 상태 -> 스케줄러가 다음 회차에 재시도
    save_rows(rows, target_dates)


if __name__ == "__main__":
    main()
