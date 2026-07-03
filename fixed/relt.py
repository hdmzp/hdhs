# -*- coding: utf-8 -*-
"""
lt_representative_programs.py
롯데홈쇼핑 대표 PGM(최유라쇼, 강주은쇼, 이유리 등)의 '방송예정' 상품을
방송일시 단위로 수집한다.

fixed/lt_fixed_programs.py에서 이미 밝혀낸 API 구조를 그대로 재사용한다
(Selenium 불필요, requests만으로 충분):

1) 기본 정보:
   GET /contstmpl/getContsTmplBaseInfo.lotte?conts_no={conts_no}
   body[0].data.contsInfo:
     - contsMainTit  : 프로그램명
     - contsBdctTime : 편성 텍스트 (예: "매주 목 20시 45분/ 토 08시 20분")
     - linkUrl       : 2단계 API 경로

2) 상세 콘텐츠:
   GET /contstmpl/{linkUrl}
   body[*].meta.sid 로 섹션 구분:
     - conts_tmpl_live_pre_info        : 방송예정 상품 (우리가 원하는 것)
     - conts_tmpl_bdct_past_goods_info : 지난 방송 상품 (sid 자체가 달라서
                                          live_pre_info만 읽으면 자동으로 제외됨.
                                          GS처럼 DOM에서 지난방송을 걷어내는
                                          방어 로직이 필요 없음)
   conts_tmpl_live_pre_info.dataList[] 가 방송 1타임 단위:
     - bdctDate   : "07/04 토요일 08:20" 같은 방송일시 텍스트 (그룹 전체에 적용)
     - goodsList[].goodsInfo:
         - goodsNm  : 상품명
         - brandNm  : 브랜드명 (진짜 브랜드 필드, GS처럼 대괄호 파싱 안 해도 됨)
         - benefitPrc / normalPrc : 가격
         - goodsImgUrl : 이미지
         - goodsUrl : 상세 링크 (상대경로)
"""

import os
import re
import json
import time
import requests
from urllib.parse import urljoin

OUTPUT_DIR = os.path.join("homeshopping", "representative_programs")
BASE_DOMAIN = "https://www.lotteimall.com"
BASE_INFO_URL = BASE_DOMAIN + "/contstmpl/getContsTmplBaseInfo.lotte?conts_no={conts_no}"
DETAIL_PAGE_URL = BASE_DOMAIN + "/contstmpl/viewContsTmplDetail.lotte?conts_no={conts_no}"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SEC = 10
REQUEST_DELAY_SEC = 0.3

# ============ 여기에 프로그램 추가 ============
# conts_no는 https://www.lotteimall.com/contstmpl/viewContsTmplDetail.lotte?conts_no=XX 의 XX
PROGRAMS = [
    {
        "tab_name": "최유라",
        "conts_no": "73",
        "output_file": "LT_CYR.json",
    },
    # TODO: 강주은, 이유리 conts_no 확인되면 여기에 추가
    # {"tab_name": "강주은", "conts_no": "???", "output_file": "LT_KJE.json"},
    # {"tab_name": "이유리", "conts_no": "417", "output_file": "LT_LYR.json"},  # 참고: 417은 요즘쇼핑 유리네(고정PGM에서 이미 확인된 번호)
]
# ==============================================


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
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT_SEC)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def extract_upcoming_products(section_data: dict) -> list:
    """conts_tmpl_live_pre_info 구조에서 방송일시(bdctDate) 단위로 상품을 추출한다."""
    products = []
    if not section_data:
        return products

    for bdct in section_data.get("dataList", []) or []:
        bdct_date = (bdct.get("bdctDate") or "").strip()

        for g in bdct.get("goodsList", []) or []:
            info = g.get("goodsInfo", {}) or {}
            if not info:
                continue
            products.append({
                "broadcast_date_label": bdct_date,
                "brand": (info.get("brandNm") or "").strip(),
                "name": info.get("goodsNm"),
                "price": parse_price(info.get("benefitPrc") or info.get("normalPrc")),
                "image": info.get("goodsImgUrl"),
                "link": urljoin(BASE_DOMAIN, info["goodsUrl"]) if info.get("goodsUrl") else None,
            })
    return products


def crawl_lt_program(config: dict):
    tab_name = config["tab_name"]
    conts_no = config["conts_no"]

    print(f"\n===== [{tab_name}] (conts_no={conts_no}) 수집 시작 =====")

    session = make_session()

    base_json = fetch_json(session, BASE_INFO_URL.format(conts_no=conts_no))
    if not base_json:
        print(f"[실패] [{tab_name}] 기본 정보 API 응답 없음/JSON 아님")
        return None

    try:
        conts_info = base_json["body"][0]["data"]["contsInfo"]
    except (KeyError, IndexError, TypeError):
        print(f"[실패] [{tab_name}] contsInfo 구조를 못 찾음")
        return None

    program_title = (conts_info.get("contsMainTit") or "").strip()
    schedule_raw = (conts_info.get("contsBdctTime") or "").strip()
    detail_link = DETAIL_PAGE_URL.format(conts_no=conts_no)
    print(f"    -> 프로그램명: {program_title} / 편성: {schedule_raw}")

    rel_link = conts_info.get("linkUrl")
    if not rel_link:
        print(f"[실패] [{tab_name}] linkUrl 없음")
        return None

    time.sleep(REQUEST_DELAY_SEC)
    detail_json = fetch_json(session, urljoin(BASE_DOMAIN + "/contstmpl/", rel_link))
    if not detail_json:
        print(f"[실패] [{tab_name}] 상세 API 응답 없음/JSON 아님")
        return None

    upcoming_products = []
    for item in detail_json.get("body", []) or []:
        sid = item.get("meta", {}).get("sid")
        if sid == "conts_tmpl_live_pre_info":
            upcoming_products = extract_upcoming_products(item.get("data"))
            break

    print(f"    -> 방송예정 상품 {len(upcoming_products)}개 수집됨")
    for p in upcoming_products:
        print(f"       [{p['broadcast_date_label']}] [{p['brand']}] {p['name'][:20] if p['name'] else ''}...")

    return {
        "company": "LT",
        "tab_name": tab_name,
        "program_title": program_title,
        "schedule_raw": schedule_raw,
        "detail_link": detail_link,
        "products": upcoming_products,
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for config in PROGRAMS:
        result = crawl_lt_program(config)
        if not result:
            continue

        output_path = os.path.join(OUTPUT_DIR, config["output_file"])
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"[성공] [{config['tab_name']}] 저장 완료: {output_path}")
        print(f"  - 총 수집된 예고 상품 수: {len(result['products'])}개")


if __name__ == "__main__":
    main()