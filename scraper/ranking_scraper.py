# -*- coding: utf-8 -*-
"""
홈쇼핑 인기 랭킹(주간) 수집기 - 홈쇼핑모아 DataHub(datahub.hsmoa.com) 경유

== 실행 주기 / 순위변동 추적 ==
매일 1회 실행한다. 저장 파일은 항상 하나(data/ranking/latest.json)이고,
매번 그 파일을 덮어쓴다(예전처럼 주(월요일) 단위로 파일을 계속 새로 쌓지
않는다 - 그러면 repo 용량이 매주 계속 불어나기 때문). 덮어쓰기 전에 기존
latest.json을 먼저 읽어서 직전 실행 시점의 pdid별 순위를 기억해두고, 새로
수집한 순위와 비교해 rank_change를 계산한다.
  rank_change > 0 : 순위 상승 (숫자가 작아짐, 예: 5위 -> 2위 => +3)
  rank_change < 0 : 순위 하락
  rank_change = null : 이번이 첫 실행이거나(latest.json이 아직 없음),
                       직전 실행에는 그 카테고리에 없던 신규 진입 상품
아주 처음 실행할 때는 latest.json이 없어 전부 null(NEW)로 뜨는 게 정상이다.

== 1단계: 랭킹 목록 수집 ==
datahub.hsmoa.com의 내부 API를 그대로 사용한다(브라우저 Network 탭에서
캡처 확인됨, 로그인 없이도 200 응답):

  GET https://datahub.hsmoa.com/next-api/insights/ranking?limit=100&time_range=week[&category1=카테고리명]

- "전체" 카테고리는 category1 파라미터를 생략한다.
- 나머지 17개 카테고리는 category1에 한글 카테고리명을 그대로 넣는다
  (requests가 알아서 URL 인코딩).
- revenue/sales_count 등은 로그인(유료 플랜) 전용이라 응답에서 항상 null로
  온다 - 저장하지 않는다. rank/product/broadcast/tier/badge/sales_ratio만
  사용한다.
- time_range=week가 정확히 "고정 캘린더 주"인지 "최근 7일 롤링"인지는
  hsmoa 쪽 공식 문서가 없어 단정할 수 없다. 매일 실행해도 문제없도록
  설계했으니 어느 쪽이든 상관없다.
- 특정 카테고리(예: 전체, 가전) 호출이 그날따라 응답이 비어 items가
  0개로 오는 경우가 실제로 있었다(레이트리밋/일시적 API 이슈로 추정).
  이 경우도 정상 실행으로 처리하고 빈 리스트로 저장한다 - 다음 실행 때
  다시 채워지는지 지켜봐야 한다.

== 2단계: 바로가기(구매) 링크 enrichment ==
랭킹 API 응답에는 실제 구매 링크가 없다. 대신 상품 상세페이지
  GET https://datahub.hsmoa.com/product/{pdid}
가 SSR이라, 응답 HTML 안에 target="_blank" 달린 <a> 태그로 실제 채널
구매 링크가 박혀 있다(관련상품 카드도 같은 target="_blank" 속성을 쓰지만
href가 "/product/{다른pdid}" 같은 내부 상대경로라 절대 URL인지로 구분).
채널마다(cjmall -> display.cjonstyle.com 등) 링크 형식이 완전히 달라서
패턴을 코드로 암기하지 않고, 매번 실제 상세페이지를 요청해서 그대로
긁어온다.

같은 상품(pdid)은 여러 카테고리 랭킹에 중복 등장하거나 날짜가 바뀌어도
계속 인기 상품일 수 있으므로, link_cache.json에 한번 조회한 pdid->link를
계속 누적 캐싱해서 불필요한 재요청을 줄인다. 매일 실행해도 캐시 덕분에
신규 진입 상품의 pdid만 새로 조회하면 되므로 갈수록 빨라진다.

== 저장 구조 ==
data/ranking/
├── latest.json             현재 랭킹 스냅샷 1개, 매번 덮어씀
│   {
│     "weekStart": "2026-06-29", "weekEnd": "2026-07-05",  (참고용 메타데이터,
│                                                            더 이상 파일명이나
│                                                            주차 탐색에는 안 쓰임)
│     "collectedAt": "2026-07-02T15:50:49+09:00",
│     "categories": {
│       "전체": [ {rank, pdid, tier, badge, sales_ratio, rank_change,
│                  product:{name,brand,image,price,sale_price,
│                           channel,category1,category2,category3},
│                  broadcast:{channel_name,start_time,end_time},
│                  link:"실제 구매 링크"}, ... ],
│       "의류": [...], "식품": [...], ...
│     }
│   }
└── link_cache.json         {pdid: link, ...} 누적 캐시

예전에는 드라마/예능 탭처럼 주(월요일) 단위 스냅샷 파일을 계속 쌓는
방식이었으나, repo 용량이 매주 불어나는 문제 때문에 latest.json 하나만
덮어쓰는 방식으로 바꿨다. 과거 이력이 필요해지면 이 latest.json을 주기적
으로 별도 아카이브에 백업하는 방식을 추가하면 된다(지금은 미포함).

== 사용법 ==
  pip install requests
  python ranking_scraper.py
"""

import os
import re
import json
import time
import requests
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
OUTPUT_DIR = "data/ranking"
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "latest.json")  # 항상 이 파일 하나만 덮어쓴다
LINK_CACHE_PATH = os.path.join(OUTPUT_DIR, "link_cache.json")

RANKING_URL = "https://datahub.hsmoa.com/next-api/insights/ranking"
PRODUCT_URL_TMPL = "https://datahub.hsmoa.com/product/{pdid}"
LIMIT = 100
TIME_RANGE = "week"

CATEGORY_REQUEST_DELAY = 0.6   # 카테고리(18개) 호출 사이 대기
DETAIL_REQUEST_DELAY = 0.4     # 상품 상세페이지(바로가기 링크) 호출 사이 대기

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.5 Safari/605.1.15")

RANKING_HEADERS = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://datahub.hsmoa.com/ranking",
}

DETAIL_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://datahub.hsmoa.com/ranking",
}

# 랭킹 페이지 카테고리 필터 버튼 순서 그대로 (전체 포함 18개)
CATEGORIES = [
    "전체", "의류", "식품", "잡화", "뷰티", "스포츠/레저", "생필품/주방",
    "가구/인테리어", "가전", "건강", "서비스/금융", "반려동물", "출산/육아",
    "자동차/공구", "취미", "문화/컨텐츠", "디지털", "컴퓨터",
]

# 상세페이지 <a> 태그의 속성 순서가 채널/카드마다 제각각이라(예: href가
# target보다 먼저 오는 경우가 실제로 훨씬 많았음), 속성 순서를 가정하지 않고
# 태그 하나를 통째로 잡은 뒤 그 안에서 target="_blank" 여부와 href를 따로 검사한다.
TAG_RE = re.compile(r'<a\b([^>]*)>')
HREF_ATTR_RE = re.compile(r'href="([^"]+)"')

# 2026-11경 hsmoa가 상세페이지 사이드바에 자기 광고센터 링크(target="_blank" 절대 URL)를
# 넣었다. 이게 진짜 구매 버튼보다 먼저 나와서 랭킹 링크 대부분이 광고 페이지로 저장됐다.
#   https://ad.hsmoa.com/?utm_source=datahub&utm_medium=sidebar&utm_campaign=ad_center
#   https://hsmoa-ad.web.app/?referrer=datahub
# 호스트에 hsmoa 가 들어가거나 web.app 인 링크는 구매 링크가 아니다.
AD_HOST_RE = re.compile(r'^https?://[^/?#]*(?:hsmoa|\.web\.app)', re.I)

# 상세페이지에서 구매 링크를 못 찾으면 pdid('{채널}_{상품번호}')로 채널 상품페이지를 직접 만든다.
# (link_cache.json 의 정상 링크 전부 - GS 보험 8건 제외 - 가 pdid 상품번호와 일치함을 확인)
DIRECT_LINK_TMPL = {
    "hmall":      "https://www.hmall.com/md/pda/itemPtc?slitmCd={id}",
    "cjmall":     "https://display.cjonstyle.com/p/item/{id}",
    "gsshop":     "https://www.gsshop.com/prd/prd.gs?prdid={id}",
    "lotteimall": "https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no={id}",
    "bshop":      "https://www.skstoa.com/display/goods/{id}",
    "hnsmall":    "https://m.hnsmall.com/goods/view/{id}",
    "immall":     "https://www.gongyoungshop.kr/goods/selectGoodsDetail.do?prdId={id}",
    "kshop":      "https://www.kshop.co.kr/display/ec/product/{id}",
    "nsmall":     "https://m.nsmall.com/goods/{id}",
    "shopnt":     "https://m.shoppingntmall.com/display/goods/{id}",
    "ssgshop":    "https://www.shinsegaetvshopping.com/display/detail/{id}",
    "wshop":      "https://m.w-shopping.co.kr/display/goods/{id}",
}


# 채널별 구매 링크로 인정하는 도메인. 상세페이지엔 hsmoa 광고(ad.hsmoa.com,
# hsmoa-ad.web.app)뿐 아니라 제휴 광고(fitcl.ai 등, 2026-11-24 확인)도 계속 새로 붙어서,
# 광고 주소를 막는 방식 대신 '그 상품 채널의 도메인'인 링크만 받는다.
CHANNEL_DOMAINS = {
    "hmall": ("hmall.com",),
    "cjmall": ("cjonstyle.com", "cjmall.com"),
    "gsshop": ("gsshop.com",),
    "lotteimall": ("lotteimall.com",),
    "bshop": ("skstoa.com",),
    "hnsmall": ("hnsmall.com",),
    "immall": ("gongyoungshop.kr", "immall.co.kr"),
    "kshop": ("kshop.co.kr",),
    "nsmall": ("nsmall.com",),
    "shopnt": ("shoppingntmall.com",),
    "ssgshop": ("shinsegaetvshopping.com",),
    "wshop": ("w-shopping.co.kr",),
}
HOST_RE = re.compile(r'^https?://([^/?#:]+)', re.I)


def is_channel_link(pdid: str, href: str) -> bool:
    """href 가 pdid 채널의 쇼핑몰 도메인이면 True."""
    m = HOST_RE.match(href or "")
    if not m:
        return False
    host = m.group(1).lower()
    domains = CHANNEL_DOMAINS.get((pdid or "").rpartition("_")[0], ())
    return any(host == d or host.endswith("." + d) for d in domains)


def is_ad_link(href: str) -> bool:
    return bool(href) and bool(AD_HOST_RE.match(href))


def direct_link(pdid: str) -> str:
    """pdid로 채널 상품페이지 URL을 만든다. 모르는 채널이면 빈 문자열."""
    ch, _, num = (pdid or "").rpartition("_")
    tmpl = DIRECT_LINK_TMPL.get(ch)
    return tmpl.format(id=num) if tmpl and num.isdigit() else ""


def now_kst() -> datetime:
    return datetime.now(KST)


def to_date_str(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def monday_of(d: datetime) -> datetime:
    return d - timedelta(days=d.weekday())


def fetch_ranking(category: str) -> list:
    """카테고리 1개의 주간 랭킹 top100을 가져온다. 실패 시 빈 리스트."""
    params = {"limit": LIMIT, "time_range": TIME_RANGE}
    if category != "전체":
        params["category1"] = category

    try:
        r = requests.get(RANKING_URL, params=params, headers=RANKING_HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"    [ranking:{category}] HTTP {r.status_code} 오류")
            return []
        data = r.json()
        items = data.get("items", []) or []

        cleaned = []
        for it in items:
            product = it.get("product") or {}
            broadcast = it.get("broadcast") or {}
            if not product.get("name"):
                # 상품 정보가 비어있는 항목(가끔 등장)은 스킵
                continue
            cleaned.append({
                "rank": it.get("rank"),
                "pdid": it.get("pdid", ""),
                "tier": it.get("tier", ""),
                "badge": it.get("badge", ""),
                "sales_ratio": it.get("sales_ratio"),
                "product": {
                    "name": product.get("name", ""),
                    "brand": product.get("brand", ""),
                    "image": product.get("image", ""),
                    "price": product.get("price"),
                    "sale_price": product.get("sale_price"),
                    "channel": product.get("channel", ""),
                    "category1": product.get("category1", ""),
                    "category2": product.get("category2", ""),
                    "category3": product.get("category3", ""),
                },
                "broadcast": {
                    "channel_name": broadcast.get("channel_name", ""),
                    "start_time": broadcast.get("start_time", ""),
                    "end_time": broadcast.get("end_time", ""),
                },
                "link": "",  # 2단계에서 채움
            })
        return cleaned

    except Exception as e:
        print(f"    [ranking:{category}] 오류: {e}")
        return []


HSMOA_PRODUCT_URL_TMPL = "https://hsmoa.com/product/{pdid}"
# hsmoa.com 상품페이지 안 JSON 의 "url":"..." 값 (\u0026 등 이스케이프 포함)
JSON_URL_RE = re.compile(r'\\?"url\\?"\s*:\s*\\?"(https?:(?:[^"\\]|\\u[0-9a-fA-F]{4}|\\/)*)')
IMAGE_URL_RE = re.compile(r'\.(?:jpe?g|png|gif|webp|svg)(?:[?#]|$)|^https?://(?:img|image|itemimage|cdn)[^/]*/', re.I)
USERID_PARAM_RE = re.compile(r'[?&]hsmoa_userid=[^&]*')


def _clean_link(href: str) -> str:
    """이스케이프를 풀고, 접속자별 추적값(hsmoa_userid)을 뗀다."""
    href = href.replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")
    href = USERID_PARAM_RE.sub(lambda m: m.group(0)[0] if m.group(0)[0] == "?" else "", href)
    return href.replace("?&", "?").rstrip("?&")


def _pick_channel_link(pdid: str, candidates) -> str:
    """후보 중 pdid 채널 쇼핑몰 도메인이면서 상품번호가 들어간 첫 링크."""
    num = (pdid or "").rpartition("_")[2]
    num_re = re.compile(r"(?<!\d)" + re.escape(num) + r"(?!\d)")
    for href in candidates:
        href = _clean_link(href)
        if IMAGE_URL_RE.search(href):
            continue  # 상품 이미지 URL (W쇼핑 img.w-shopping.co.kr/... 등)
        if is_channel_link(pdid, href) and num_re.search(href):
            return href
    return ""


def fetch_outbound_link(pdid: str) -> str:
    """랭킹 상품의 실제 구매(쇼핑몰 상품페이지) 링크. 못 찾으면 빈 문자열.

    2026-11 hsmoa 개편 이후 datahub 상세페이지(datahub.hsmoa.com/product/{pdid})에는
    쇼핑몰 링크가 없고, '홈쇼핑모아에서 구매하기' 버튼이 hsmoa.com/product/{pdid} 로 간다.
    그 페이지 내장 JSON 의 "url" 값들 중(관련상품 링크도 섞여 있음) 이 상품 채널 도메인이고
    상품번호가 들어간 링크가 구매 링크다. 예전 구조 대비로 datahub 페이지도 먼저 본다.
    광고(ad.hsmoa.com, hsmoa-ad.web.app, fitcl.ai ...)는 채널 도메인이 아니라 자연히 걸러진다."""
    for tmpl, extract in (
        (HSMOA_PRODUCT_URL_TMPL, lambda t: JSON_URL_RE.findall(t)),
        (PRODUCT_URL_TMPL, lambda t: HREF_ATTR_RE.findall(t)),
    ):
        try:
            r = requests.get(tmpl.format(pdid=pdid), headers=DETAIL_HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            link = _pick_channel_link(pdid, extract(r.text))
            if link:
                return link
        except Exception as e:
            print(f"      [link] {pdid} 오류: {e}")
    return ""


def load_previous_snapshot() -> dict:
    """오늘 덮어쓰기 전, latest.json이 이미 있으면 카테고리별 데이터를 읽어온다.
    (직전 실행 결과 - 순위변동 계산용)"""
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                return json.load(f).get("categories", {})
        except Exception:
            return {}
    return {}


def build_rank_lookup(prev_categories: dict) -> dict:
    """{카테고리: {pdid: 순위}} 형태로 변환 (비교하기 쉽게)."""
    lookup = {}
    for cat, items in prev_categories.items():
        lookup[cat] = {it.get("pdid"): it.get("rank") for it in items if it.get("pdid")}
    return lookup


def apply_rank_change(categories_data: dict, prev_lookup: dict):
    """직전 순위와 비교해 각 아이템에 rank_change를 채운다.
    양수=순위 상승, 음수=순위 하락, None=이 주 첫 등장(또는 그 주 첫 실행)."""
    for cat, items in categories_data.items():
        cat_prev = prev_lookup.get(cat, {})
        for it in items:
            prev_rank = cat_prev.get(it["pdid"])
            if prev_rank is not None and it["rank"] is not None:
                it["rank_change"] = prev_rank - it["rank"]
            else:
                it["rank_change"] = None


def load_link_cache() -> dict:
    if os.path.exists(LINK_CACHE_PATH):
        try:
            with open(LINK_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_link_cache(cache: dict):
    with open(LINK_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    base = now_kst()
    week_start_dt = monday_of(base)
    week_end_dt = week_start_dt + timedelta(days=6)
    week_start = to_date_str(week_start_dt)
    week_end = to_date_str(week_end_dt)

    print(f"[랭킹] {week_start} ~ {week_end} 주간 수집 시작 (저장: latest.json 덮어쓰기)")

    prev_lookup = build_rank_lookup(load_previous_snapshot())

    categories_data = {}
    all_pdids = set()

    for cat in CATEGORIES:
        print(f"  [ranking] {cat} 수집 중...")
        items = fetch_ranking(cat)
        categories_data[cat] = items
        for it in items:
            if it["pdid"]:
                all_pdids.add(it["pdid"])
        print(f"    -> {len(items)}개")
        time.sleep(CATEGORY_REQUEST_DELAY)

    # 2단계: 바로가기 링크 enrichment (캐시 활용)
    link_cache = load_link_cache()
    # 실제 페이지에서 찾은 구매 링크가 아닌 캐시는 지우고 다시 조회한다:
    # 빈 값, 광고 링크(2026-11 hsmoa 광고 오수집), 채널 도메인이 아니거나 상품번호가 없는 링크,
    # pdid로 만든 임시 상품페이지 링크(direct_link).
    for pdid, link in list(link_cache.items()):
        if not link or link == direct_link(pdid) or not _pick_channel_link(pdid, [link]):
            del link_cache[pdid]
    new_pdids = [p for p in sorted(all_pdids) if p not in link_cache]
    print(f"  [link] 전체 고유 상품 {len(all_pdids)}개 / 신규 조회 {len(new_pdids)}개")

    for i, pdid in enumerate(new_pdids, 1):
        link = fetch_outbound_link(pdid)
        if link:
            link_cache[pdid] = link   # 실제 페이지에서 찾은 링크만 캐시 (못 찾으면 다음 실행에 재시도)
        if i % 20 == 0:
            print(f"    ... {i}/{len(new_pdids)}")
        time.sleep(DETAIL_REQUEST_DELAY)

    save_link_cache(link_cache)

    for cat, items in categories_data.items():
        for it in items:
            # 페이지에서 구매 링크를 못 찾은 상품은 pdid로 만든 상품페이지 링크 (캐시 안 함)
            it["link"] = link_cache.get(it["pdid"]) or direct_link(it["pdid"])

    apply_rank_change(categories_data, prev_lookup)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "weekStart": week_start,
            "weekEnd": week_end,
            "collectedAt": base.isoformat(),
            "categories": categories_data,
        }, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in categories_data.values())
    print(f"\n완료. 저장: {OUTPUT_PATH} (카테고리 {len(categories_data)}개, 총 {total}건)")


if __name__ == "__main__":
    main()