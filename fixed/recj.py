# -*- coding: utf-8 -*-
"""
cj_representative_programs.py
CJ온스타일 대표 PGM(강주은 굿라이프, 최화정쇼, 더 김창옥 라이브)의
'방송 라인업'을 방송일시 단위로 수집한다.

== API 구조 (devtools Network 캡처로 확인됨) ==

1단계 - 프로그램 기본정보 + tabId 획득
  GET https://display-frontapi.cjonstyle.com/pgmShop/??? (URL 확인 필요 - TODO)
  ?pgmCd={pgmCd}
  응답:
    result.pgmShopInfo   : 프로그램명(pgmNm), 편성텍스트(bdTmCntsList[].bdTmCnts) 등
    result.tabList[]     : [{"tabId": "P00241", "tabNm": "홈", ...}]  <- 2단계에 필요한 tabId

2단계 - 실제 상품/방송 라인업
  GET https://display-frontapi.cjonstyle.com/pgmShop/moduleList
      ?tabId={tabId}&pmType=M&isEmployee=false&employeeDiscountRate=0
  응답:
    result.moduleList[] 중 moduleBaseInfo.repModulTpCd == "MSRT06"
    ("방송 라인업 전체보기") 모듈의 contentList[0].srttbList[] 가 방송 1타임 단위:
      - srttbId  : "20260707193500" 같은 방송일시 키 (지난방송은 "00000000000000")
      - srttbNm  : "07/07(화) 19:35" 같은 사람이 읽는 라벨 ("지난방송상품"도 있음)
      - itemInfoList : 상품 목록 (지난방송 항목은 null이라 자동으로 걸러짐)
          itemBaseInfo.itemCd / displayItemName / repBrandNm(진짜 브랜드) /
          salePrice / imgUrlList[0] / itemLink

주의: 김창옥 라이브는 "방송일시가 자주 바뀐다"고 함 -> schedule_raw를
하드코딩하지 말고 매번 pgmShopInfo.bdTmCntsList에서 새로 읽어올 것.

== 아직 안 채워진 것 ==
- 1단계 API의 정확한 URL/path (devtools에서 pgmShopInfo가 담긴 응답의
  Request URL을 확인해서 STEP1_URL_TEMPLATE에 채워넣어야 함)
- 강주은(100009), 최화정(100027)의 pgmCd -> tabId 매핑 (형식이 P00241처럼
  프로그램마다 다른 tabId를 쓰는 것으로 보여서, 1단계 API를 먼저 호출해서
  알아내야 함. pgmShop 페이지 URL의 숫자(100009 등)가 pgmCd인지 아니면
  또 다른 식별자인지도 확인 필요 - 김창옥의 실제 pgmCd는 URL의 563907이
  아니라 응답 안의 pgmCd 563907로 재확인됨(동일해서 다행), 100009/100027도
  URL 숫자 = pgmCd로 우선 가정함)
"""

import os
import re
import json
import time
import requests

OUTPUT_DIR = os.path.join("homeshopping", "representative_programs")

STEP1_URL_TEMPLATE = (
    "https://display-frontapi.cjonstyle.com/pgmShop"
    "?pgmCd={pgm_cd}&pmType=M&includeOpnPreplnYn=Y&isEmployee=false"
)

STEP2_MODULE_LIST_URL = "https://display-frontapi.cjonstyle.com/pgmShop/moduleList"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://display.cjonstyle.com",
}

TARGET_MODULE_CODE = "MSRT06"

# ============ 여기에 프로그램 추가 ============
PROGRAMS = [
    {"tab_name": "강주은", "program_title": "강주은 굿라이프", "pgm_cd": "100009", "output_file": "CJ_KJE.json"},
    {"tab_name": "최화정", "program_title": "최화정쇼", "pgm_cd": "100027", "output_file": "CJ_CHJ.json"},
    {"tab_name": "김창옥", "program_title": "더 김창옥 라이브", "pgm_cd": "563907", "output_file": "CJ_KCO.json"},
]
# ==============================================


def fetch_json(session: requests.Session, url: str, params: dict = None):
    try:
        resp = session.get(url, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"    -> [실패] {url} : {e}")
        return None


def to_https(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    return url


def get_tab_id(session: requests.Session, pgm_cd: str):
    """1단계: pgmShop 기본정보 API에서 tabId + 편성텍스트를 얻는다."""
    url = STEP1_URL_TEMPLATE.format(pgm_cd=pgm_cd)
    data = fetch_json(session, url)
    if not data:
        return None, None, None

    result = data.get("result", {})
    pgm_shop_info = result.get("pgmShopInfo", {}) or {}
    program_title = pgm_shop_info.get("pgmNm", "")
    schedule_parts = [
        t.get("bdTmCnts", "") for t in (pgm_shop_info.get("bdTmCntsList") or [])
    ]
    schedule_raw = " / ".join(p for p in schedule_parts if p)

    tab_list = result.get("tabList") or []
    main_tab = next((t for t in tab_list if t.get("mainTabYn") == "Y"), None) or (tab_list[0] if tab_list else None)
    tab_id = main_tab.get("tabId") if main_tab else None

    return tab_id, program_title, schedule_raw


def crawl_cj_program(session: requests.Session, config: dict):
    tab_name = config["tab_name"]
    pgm_cd = config["pgm_cd"]

    print(f"\n===== [{tab_name}] (pgmCd={pgm_cd}) 수집 시작 =====")

    tab_id, program_title, schedule_raw = get_tab_id(session, pgm_cd)
    if not tab_id:
        print(f"[실패] [{tab_name}] tabId를 못 얻음 (1단계 URL이 아직 안 채워졌을 수 있음)")
        return None

    print(f"    -> tabId={tab_id} / 프로그램명: {program_title} / 편성: {schedule_raw}")

    time.sleep(0.3)
    module_data = fetch_json(session, STEP2_MODULE_LIST_URL, params={
        "tabId": tab_id,
        "pmType": "M",
        "isEmployee": "false",
        "employeeDiscountRate": "0",
    })
    if not module_data:
        print(f"[실패] [{tab_name}] moduleList 응답 없음")
        return None

    modules = module_data.get("result", {}).get("moduleList", []) or []
    target_module = next(
        (m for m in modules if m.get("moduleBaseInfo", {}).get("repModulTpCd") == TARGET_MODULE_CODE),
        None
    )
    if not target_module:
        print(f"[경고] [{tab_name}] '{TARGET_MODULE_CODE}'(방송 라인업) 모듈을 못 찾음")
        return {
            "company": "CJ",
            "tab_name": tab_name,
            "program_title": program_title or config["program_title"],
            "schedule_raw": schedule_raw,
            "detail_link": f"https://display.cjonstyle.com/m/pgmShop/{pgm_cd}",
            "products": [],
        }

    products = []
    content_list = target_module.get("contentList") or []
    for content in content_list:
        srttb_list = content.get("srttbList") or []
        for srttb in srttb_list:
            date_label = srttb.get("srttbNm", "")
            item_list = srttb.get("itemInfoList")
            if not item_list:
                # "지난방송상품" 등 -> itemInfoList가 null
                continue

            print(f"    -> [방송 타임 진입]: {date_label} - 상품 {len(item_list)}개")

            for it in item_list:
                base = it.get("itemBaseInfo", {}) or {}
                if not base:
                    continue
                img_list = base.get("imgUrlList") or []
                products.append({
                    "broadcast_date_label": date_label,
                    "brand": base.get("repBrandNm", ""),
                    "name": base.get("displayItemName") or base.get("itemNm"),
                    "price": base.get("salePrice"),
                    "image": to_https(img_list[0]) if img_list else "",
                    "link": base.get("itemLink"),
                })

    print(f"[{tab_name}] 수집 완료. 방송예정 상품 총 {len(products)}개")

    return {
        "company": "CJ",
        "tab_name": tab_name,
        "program_title": program_title or config["program_title"],
        "schedule_raw": schedule_raw,
        "detail_link": f"https://display.cjonstyle.com/m/pgmShop/{pgm_cd}",
        "products": products,
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = requests.Session()

    for config in PROGRAMS:
        result = crawl_cj_program(session, config)
        if not result:
            continue

        output_path = os.path.join(OUTPUT_DIR, config["output_file"])
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"[성공] [{config['tab_name']}] 저장 완료: {output_path}")
        print(f"  - 총 수집된 상품 수: {len(result['products'])}개")


if __name__ == "__main__":
    main()