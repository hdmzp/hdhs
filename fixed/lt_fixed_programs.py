# -*- coding: utf-8 -*-
"""
lt_fixed_programs.py
롯데홈쇼핑 고정 편성 프로그램(진행자 쇼) 목록을 자동으로 발견하며 수집한다.

(업데이트 - requests 기반으로 전면 재작성)
이전 버전은 상세페이지가 동적 렌더링(Vue CSR)이라 Selenium으로 페이지를
직접 렌더링해서 파싱했다. 하지만 실제로는 Vue 컴포넌트가 내부적으로
호출하는 JSON API 2개만 알면 Selenium 없이 requests로 동일한 데이터를
훨씬 빠르고 안정적으로 얻을 수 있다는 게 확인되어 이 방식으로 변경한다.

== 데이터 소스 (Vue pgm_container 컴포넌트가 호출하는 API) ==

1) 기본 정보 + 메인 썸네일
   GET /contstmpl/getContsTmplBaseInfo.lotte?conts_no={conts_no}
   body[0].data.contsInfo:
     - contsMainTit : 프로그램명
     - imgUrl       : 메인 썸네일 (1200x430)
     - logoImgUrl   : 로고 이미지
     - contsBdctTime: 편성 텍스트 (예: "매주 일요일 10시")
     - linkUrl      : 2단계 API 경로 (disp_no 포함, 상대경로)

2) 상세 콘텐츠 (소개상품 + 추천 프로그램)
   GET /contstmpl/{linkUrl from step 1}
   body[*].meta.sid 로 섹션 구분:
     - conts_tmpl_live_pre_info       : 다가오는 방송 소개상품 (우리가 원하는 upcoming_products)
     - conts_tmpl_bdct_past_goods_info: 지난 방송 상품 (참고용, 미사용)
     - conts_tmpl_recomm_info         : 추천 프로그램 목록
         data.dataList[].linkUrl 에 "/contstmpl/viewContsTmplDetail.lotte?conts_no=NNNN"
         형태로 다른 프로그램의 conts_no가 "직접" 들어있어서, 이미지 URL 패턴으로
         conts_no를 추론하던 예전 방식이 필요 없어졌다.

존재하지 않는 conts_no로 호출하면 HTTP 200 + HTML 에러페이지가 내려오므로
(JSON이 아님) JSONDecodeError를 "유효하지 않은 conts_no"로 처리한다.
"""

import os
import re
import sys
import json
import time
import requests
from collections import deque
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

KST = timezone(timedelta(hours=9))

# 시작 프로그램 번호
SEED_CONTS_NO = "417"

BASE_DOMAIN = "https://www.lotteimall.com"
BASE_INFO_URL = BASE_DOMAIN + "/contstmpl/getContsTmplBaseInfo.lotte?conts_no={conts_no}"
DETAIL_PAGE_URL = BASE_DOMAIN + "/contstmpl/viewContsTmplDetail.lotte?conts_no={conts_no}"

OUTPUT_DIR = os.path.join("homeshopping", "fixed_programs")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "LT.json")

REQUEST_DELAY_SEC = 0.4
REQUEST_TIMEOUT_SEC = 10
MAX_PAGES = 60  # 무한 크롤링 방지용 안전장치
MAX_RETRIES = 2

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

CONTS_NO_RE = re.compile(r'conts_no=(\d+)')


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Referer": BASE_DOMAIN + "/",
        "Accept": "application/json, text/plain, */*",
    })
    return session


def parse_price(text) -> int:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    cleaned = re.sub(r'[^\d]', '', str(text))
    return int(cleaned) if cleaned else None


def fetch_json(session: requests.Session, url: str):
    """JSON 응답이면 dict를, 에러페이지(HTML) 등 JSON이 아니면 None을 반환."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError):
            if attempt < MAX_RETRIES:
                time.sleep(0.5)
                continue
            return None
    return None


def extract_goods_list(section_data: dict) -> list:
    """live_pre_info / bdct_past_goods_info 공통 구조에서 상품 목록 추출."""
    products = []
    if not section_data:
        return products
    for bdct in section_data.get("dataList", []) or []:
        for g in bdct.get("goodsList", []) or []:
            info = g.get("goodsInfo", {}) or {}
            if not info:
                continue
            products.append({
                "name": info.get("goodsNm"),
                "price": parse_price(info.get("benefitPrc") or info.get("normalPrc")),
                "image": info.get("goodsImgUrl"),
                "link": urljoin(BASE_DOMAIN, info["goodsUrl"]) if info.get("goodsUrl") else None,
            })
    return products


def crawl_program(session: requests.Session, conts_no: str) -> dict:
    """conts_no 하나에 대한 프로그램 정보 + 추천 프로그램(conts_no 목록)을 가져온다.
    유효하지 않은 conts_no면 None을 반환한다."""

    base_json = fetch_json(session, BASE_INFO_URL.format(conts_no=conts_no))
    if not base_json:
        return None

    try:
        conts_info = base_json["body"][0]["data"]["contsInfo"]
    except (KeyError, IndexError, TypeError):
        return None

    title = (conts_info.get("contsMainTit") or "").strip()
    if not title:
        return None

    thumbnail = conts_info.get("imgUrl") or ""
    logo = conts_info.get("logoImgUrl") or ""
    schedule_raw = (conts_info.get("contsBdctTime") or "").strip()
    detail_link = DETAIL_PAGE_URL.format(conts_no=conts_no)

    upcoming_products = []
    recommended_conts_nos = []
    recommend_titles = {}

    rel_link = conts_info.get("linkUrl")
    if rel_link:
        detail_json = fetch_json(session, urljoin(BASE_DOMAIN + "/contstmpl/", rel_link))
        if detail_json:
            for item in detail_json.get("body", []) or []:
                sid = item.get("meta", {}).get("sid")

                if sid == "conts_tmpl_live_pre_info":
                    upcoming_products = extract_goods_list(item.get("data"))

                elif sid == "conts_tmpl_recomm_info":
                    for rec in (item.get("data") or {}).get("dataList", []) or []:
                        rec_link = rec.get("linkUrl") or ""
                        m = CONTS_NO_RE.search(rec_link)
                        if not m:
                            continue
                        rec_conts_no = m.group(1)
                        recommended_conts_nos.append(rec_conts_no)
                        rec_title = (rec.get("contsMainTit") or "").strip()
                        if rec_title:
                            recommend_titles[rec_conts_no] = rec_title

    return {
        "conts_no": conts_no,
        "title": title,
        "schedule_raw": schedule_raw,
        "thumbnail": thumbnail,
        "logo": logo,
        "detail_link": detail_link,
        "upcoming_products": upcoming_products,
        "_recommended_conts_nos": recommended_conts_nos,
        "_recommend_titles": recommend_titles,
    }


def discover_all_programs(seed: str) -> list:
    visited = set()
    queue = deque([seed])
    discovered_from = {}
    title_hints = {}
    raw_results = {}

    session = make_session()

    while queue and len(visited) < MAX_PAGES:
        conts_no = queue.popleft()
        if conts_no in visited:
            continue
        visited.add(conts_no)

        print(f"  [LT] conts_no={conts_no} 수집 중...")
        try:
            program = crawl_program(session, conts_no)
        except Exception as e:
            print(f"    [실패] conts_no={conts_no}: {e}")
            continue

        if not program:
            print(f"    [건너뜀] conts_no={conts_no}: 유효하지 않거나 제목 없음")
            continue

        raw_results[conts_no] = program
        recommend_titles = program.pop("_recommend_titles", {})
        recommended = program.pop("_recommended_conts_nos", [])

        for next_no in recommended:
            seen_title = recommend_titles.get(next_no)
            if next_no not in title_hints and seen_title:
                title_hints[next_no] = seen_title
            if next_no not in visited:
                discovered_from.setdefault(next_no, set()).add(conts_no)
                queue.append(next_no)

        time.sleep(REQUEST_DELAY_SEC)

    results = []
    for conts_no, program in raw_results.items():
        if not program["title"]:
            program["title"] = title_hints.get(conts_no, "")
        program["discovered_from"] = sorted(discovered_from.get(conts_no, []))
        results.append(program)

    return results


def main():
    seed = sys.argv[1] if len(sys.argv) > 1 else SEED_CONTS_NO
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"[LT] conts_no={seed} 에서 출발해 프로그램 그래프 탐색 시작...")
    programs = discover_all_programs(seed)

    payload = {
        "company": "LT",
        "collectedAt": datetime.now(KST).isoformat(),
        "seed_conts_no": seed,
        "programs": programs,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n총 {len(programs)}개 프로그램 발견, 저장: {OUTPUT_PATH}")
    for p in programs:
        print(f"  conts_no={p['conts_no']} - {p['title']} | {p['schedule_raw']} "
              f"(소개상품 {len(p['upcoming_products'])}개)")


if __name__ == "__main__":
    main()
