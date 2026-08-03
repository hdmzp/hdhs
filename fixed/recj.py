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
from datetime import datetime, date, timedelta, timezone

KST = timezone(timedelta(hours=9))
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

# pgmShop 페이지에는 MSRT06(TV 방송상품 모음) 외에도 "라이브쇼 방송상품 모음",
# "라이브 캘린더"(방송 예고 + 대표상품) 같은 방송 구좌가 더 있다 (겟잇스타일에서
# 확인 - 08/10 라이브쇼 상품, 08/21 예고 상품이 MSRT06엔 없고 다른 구좌에만 있음).
# 구좌별 상세 스키마를 다 알 수 없어, MSRT 계열 모듈을 재귀로 훑어
# itemBaseInfo가 있는 항목을 전부 수집한다. 날짜 라벨은 가장 가까운 조상의
# srttbNm(또는 유사 필드)을 쓰고 없으면 '방송상품'으로 둔다.
MODULE_CODE_PREFIX = "MSRT"

LABEL_FIELD_CANDIDATES = ("srttbNm", "bdDtmNm", "bdTmNm", "broadcastDtmNm")


def walk_collect_items(node, current_label, out):
    """모듈 JSON을 재귀 순회하며 (라벨, itemBaseInfo) 수집."""
    if isinstance(node, dict):
        for f in LABEL_FIELD_CANDIDATES:
            v = node.get(f)
            if isinstance(v, str) and v.strip():
                current_label = v.strip()
                break
        base = node.get("itemBaseInfo")
        if isinstance(base, dict) and (base.get("itemCd") or base.get("displayItemName")):
            out.append((current_label, base))
        for v in node.values():
            if isinstance(v, (dict, list)):
                walk_collect_items(v, current_label, out)
    elif isinstance(node, list):
        for v in node:
            walk_collect_items(v, current_label, out)

# ============ 여기에 프로그램 추가 ============
# keywords: 편성표(tvSchedule) 보강 시 같은 시간대 방송이 이 프로그램이 맞는지
#           pgmNm으로 확인하는 키워드 (cj_scraper.CJ_PGM_KEYWORDS와 표기 일치)
PROGRAMS = [
    {"tab_name": "강주은", "program_title": "강주은 굿라이프", "pgm_cd": "100009", "output_file": "CJ_KJE.json",
     "keywords": ("강주은", "굿라이프", "굿 라이프")},
    {"tab_name": "최화정", "program_title": "최화정쇼", "pgm_cd": "100027", "output_file": "CJ_CHJ.json",
     "keywords": ("최화정",)},
    {"tab_name": "김창옥", "program_title": "더 김창옥 라이브", "pgm_cd": "563907", "output_file": "CJ_KCO.json",
     "keywords": ("김창옥",)},
    # pgm_cd는 pgmShop 페이지 URL의 번호 (fixed_programs/CJ.json의 pgmshop_link 참조)
    {"tab_name": "소이현", "program_title": "소이현의 겟잇스타일", "pgm_cd": "100061", "output_file": "CJ_SIH.json",
     "keywords": ("소이현", "겟잇스타일")},
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


# ============ 편성표(tvSchedule) 라인업 보강 ============
# MSRT06(방송 라인업) 모듈이 방송 타임당 대표상품 1개만 주는 프로그램이 있다
# (강주은 굿라이프에서 확인 - 실제 페이지엔 라이프밀/다이슨 등 여러 개).
# cj_scraper.py가 쓰는 공개 편성표 API에는 같은 방송의 itemList 전체가 있으므로,
# MSRT06이 준 방송일시로 편성표를 조회해 나머지 상품을 채운다 (rehd.py의
# tv-list 보강과 같은 접근). 브랜드는 repBrandTag 단건 API로 조회.

TV_SCHEDULE_URL = ("https://display.cjonstyle.com/c/rest/tv/tvSchedule"
                   "?bdDt={bd_dt}&isMobile=false&broadType=live&isEmployee=false")
REPBRAND_URL = "https://display-frontapi.cjonstyle.com/itemDetails/{item_cd}/repBrandTag"


def parse_label_datetime(label: str):
    """'08/03(월) 19:35' -> (date(2026,8,3), '19:35'). 연도는 오늘과 가장
    가까운 해로 추정(연말/연초 경계 대응). 파싱 실패 시 (None, None)."""
    m = re.search(r"(\d{1,2})/(\d{1,2})\s*\([^)]*\)\s*(\d{1,2}):(\d{2})", label or "")
    if not m:
        return None, None
    month, day, hh, mi = map(int, m.groups())
    base = datetime.now(KST).date()
    cands = []
    for y in (base.year - 1, base.year, base.year + 1):
        try:
            cands.append(date(y, month, day))
        except ValueError:
            pass
    if not cands:
        return None, None
    d = min(cands, key=lambda x: abs((x - base).days))
    return d, f"{hh:02d}:{mi:02d}"


def fetch_schedule_lineup(session: requests.Session, bd_dt: str, start_hm: str, keywords):
    """편성표에서 bd_dt(YYYYMMDD) start_hm에 시작하는 방송의 itemList 전체를 반환.
    keywords가 있으면 pgmNm 확인으로 다른 방송 오매칭을 방어한다."""
    data = fetch_json(session, TV_SCHEDULE_URL.format(bd_dt=bd_dt))
    if not data:
        return []
    for pg in data.get("result", {}).get("programList", []) or []:
        start_ms = pg.get("bdStrDtm")
        if not start_ms:
            continue
        s = datetime.fromtimestamp(start_ms / 1000, tz=KST).strftime("%H:%M")
        if s != start_hm:
            continue
        pgm_nm = (pg.get("pgmNm") or "")
        if keywords and not any(k in pgm_nm for k in keywords):
            print(f"    -> [보강] {start_hm} 방송 pgmNm '{pgm_nm}'이 키워드와 불일치 - 건너뜀")
            continue
        return pg.get("itemList") or []
    return []


def fetch_repbrands(session: requests.Session, item_cds):
    """itemCd별 대표 브랜드 조회 (cj_scraper.fetch_repbrand_batch와 동일 API)."""
    out = {}
    for cd in dict.fromkeys(c for c in item_cds if c):
        data = fetch_json(session, REPBRAND_URL.format(item_cd=cd))
        result = (data or {}).get("result")
        if result and result.get("repBrandNm"):
            out[cd] = result["repBrandNm"]
        time.sleep(0.2)
    return out


def supplement_from_schedule(session: requests.Session, config: dict, products: list):
    """MSRT06 결과(products)의 각 방송 타임에 대해 편성표 itemList 전체로 보강."""
    labels = list(dict.fromkeys(p["broadcast_date_label"] for p in products))

    seen_codes = set()
    seen_names = set()
    for p in products:
        m = re.search(r"/item/(\d+)", p.get("link") or "")
        if m:
            seen_codes.add(m.group(1))
        if p.get("name"):
            seen_names.add(p["name"])

    for label in labels:
        bdate, start_hm = parse_label_datetime(label)
        if not bdate or not start_hm:
            continue
        items = fetch_schedule_lineup(session, bdate.strftime("%Y%m%d"), start_hm,
                                      config.get("keywords"))
        new_items = [it for it in items
                     if it.get("itemCd") and str(it["itemCd"]) not in seen_codes
                     and (it.get("itemNm") or "") not in seen_names]
        if not new_items:
            continue

        brand_map = fetch_repbrands(session, [str(it["itemCd"]) for it in new_items])
        for it in new_items:
            cd = str(it["itemCd"])
            seen_codes.add(cd)
            chn = it.get("chnCd", "")
            link = f"https://display.cjonstyle.com/p/item/{cd}"
            if chn:
                link += f"?channelCode={chn}"
            products.append({
                "broadcast_date_label": label,
                "brand": brand_map.get(cd, "") or it.get("brandName") or "",
                "name": it.get("itemNm", ""),
                "price": it.get("salePrice"),
                "image": to_https(it.get("itemImgUrl") or it.get("imgUrl") or ""),
                "link": link,
            })
        print(f"    -> 편성표 보강: {label} +{len(new_items)}개 (전체 {len(products)}개)")
        time.sleep(0.3)


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
    # 모듈 인벤토리 로그 (새 구좌 유형이 생기면 여기서 코드 확인)
    inventory = [m.get("moduleBaseInfo", {}).get("repModulTpCd") for m in modules]
    print(f"    -> 모듈 구성: {inventory}")

    target_module = next(
        (m for m in modules if m.get("moduleBaseInfo", {}).get("repModulTpCd") == TARGET_MODULE_CODE),
        None
    )
    if not target_module:
        print(f"[경고] [{tab_name}] '{TARGET_MODULE_CODE}'(TV 방송상품) 모듈 없음 - 다른 구좌만 수집")

    def item_key(base):
        return str(base.get("itemCd") or base.get("displayItemName") or "")

    products = []
    seen_item_keys = set()

    def add_product(date_label, base):
        key = item_key(base)
        if not key or key in seen_item_keys:
            return False
        seen_item_keys.add(key)
        img_list = base.get("imgUrlList") or []
        products.append({
            "broadcast_date_label": date_label or "방송상품",
            "brand": base.get("repBrandNm", ""),
            "name": base.get("displayItemName") or base.get("itemNm"),
            "price": base.get("salePrice"),
            "image": to_https(img_list[0]) if img_list else "",
            "link": base.get("itemLink"),
        })
        return True

    # 1) TV 방송상품 모음(MSRT06): srttbNm이 정확한 방송일시 라벨
    content_list = (target_module.get("contentList") or []) if target_module else []
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
                if base:
                    add_product(date_label, base)

    # 2) 나머지 방송 구좌(라이브쇼 방송상품 모음, 라이브 캘린더 등 MSRT 계열)를
    #    재귀로 훑어 아직 없는 상품을 추가 (겟잇스타일의 08/10 라이브쇼,
    #    08/21 예고 상품이 여기서 나온다)
    for m in modules:
        if m is target_module:
            continue
        code = (m.get("moduleBaseInfo", {}) or {}).get("repModulTpCd") or ""
        if not code.startswith(MODULE_CODE_PREFIX):
            continue
        found = []
        walk_collect_items(m, "", found)
        added = sum(1 for label, base in found if add_product(label, base))
        if added:
            print(f"    -> [{code}] 구좌에서 +{added}개")

    # 방송 타임당 대표상품만 온 경우를 대비해 편성표 itemList 전체로 보강
    supplement_from_schedule(session, config, products)

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