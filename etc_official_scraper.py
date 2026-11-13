# -*- coding: utf-8 -*-
"""
etc_official_scraper.py
기타 홈쇼핑사 편성을 **각 사 공식 편성표**에서 받아 별도 폴더에 저장한다 (라방바 미사용).

== 왜 따로 두는가 (2026-11-13) ==
기타7개사 편성(etc_scraper.py)은 라방바(live.ecomm-data.com) list_hs API에서 받아왔는데,
2026-11-11 라방바가 GitHub Actions IP를, 11-12에는 Cloudflare Worker 중계까지 405로
막아 수집이 멈췄다. 4사(HD/LT/CJ/GS)처럼 공식 편성표로 옮기되, 기존 라방바 기록을
바로 덮어쓰지 않고 이 스크립트가 homeshopping/_official/{채널}/ 에만 저장한다.
tools/compare_official.py 로 라방바 기록과 날짜별로 비교해 일치가 확인된 회사부터
etc_scraper.py 의 출처를 하나씩 바꾼다. (_official 은 밑줄로 시작해서 notify/*,
build_brand_index 등 `homeshopping/*_live` glob 에 잡히지 않는다.)

== 회사별 출처 ==
  홈앤      HNS_live        GET www.hnsmall.com/display/tvschedule-list?broadDay=YYYYMMDD (HTML 조각)
                            방송마다 대표상품 1개 + '함께 방송하는 상품' N개
  쇼핑엔티  SHOPPINGNT_live GET www.shoppingntmall.com/broadcast/tvBroadcastList?fromDate=YYYYMMDD (HTML 조각)
                            먼저 /broadcast/tvBroadcastMain 에서 세션쿠키 + X-CSRF-TOKEN 메타를 받아 보낸다.
                            미래는 오늘+4일까지만 준다 (그 뒤 날짜는 빈 응답).
  NS        NS_live/NS_plus GET mapi.nsmall.com/md/api/v1/display/media/schedule/{tv|shopplus}/YYYYMMDD (JSON)
                            totalOrgan[] 한 항목 = 방송 상품 1개 (같은 프로그램에 brdctOrd 1,2,...).
  SK스토아  SKSTOA_live     GET www.skstoa.com/broadcast/scdGoodsList?date=YYYYMMDD (HTML 조각)
                            시간대마다 대표상품 1개만 준다 (라방바는 같이 방송되는 보험·렌탈 등
                            2번째 상품까지 줬었다 - 공식으로는 받을 수 없음).
  미지원: 공영(gongyoung)·K쇼핑(kshop)·신세계 - 해외 IP를 막아 Actions/개발환경에서 열리지 않음.
          워크플로우의 접속 확인 스텝(etc-official.yml)으로 열리는지 계속 본다.

== 저장 스키마 == etc_scraper.py 와 같다 (프론트/알림이 그대로 읽을 수 있게).
  {"company","broadcast","month","updated_at","days":{"YYYY-MM-DD":[
     {"start","end","brand","product","price","link","category","lavangba_category":"","hsshow_id":""}]}}
  라방바 전용 필드 두 개는 빈 문자열. 라방바가 다시 열리면 hsshow_id 는 시작시각으로 붙일 수 있다.

== 사용법 ==
  python etc_official_scraper.py            # 어제~+5일
  python etc_official_scraper.py HNS NS     # 일부 회사만
"""

import html as htmlmod
import json
import os
import re
import sys
import time
from datetime import timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from etc_scraper import add_categories, load_month, today_kst, KST, DAYS_RANGE  # noqa: E402
from tools import scrape_guard  # noqa: E402

OUTPUT_DIR = os.path.join("homeshopping", "_official")
REQUEST_DELAY = 1.0
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BASE_HEADERS = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}


def _price(s):
    try:
        return int(re.sub(r"[^\d]", "", s or "") or 0)
    except ValueError:
        return 0


def _row(start, end, product, price, link, brand=""):
    return {
        "start": start, "end": end, "brand": brand or "", "product": htmlmod.unescape(product or "").strip(),
        "price": price, "link": link, "category": "", "lavangba_category": "", "hsshow_id": "",
    }


# ============================== 홈앤 ==============================

HNS_LIST_URL = "https://www.hnsmall.com/display/tvschedule-list"
HNS_LINK = "http://www.hnsmall.com/display/goods.do?goods_code={code}"  # 라방바 기록과 같은 형식


def fetch_hns(date_obj, _session, label):
    r = scrape_guard.request(
        "GET", HNS_LIST_URL, params={"categoryCode": "", "areaCode": "", "broadDay": date_obj.strftime("%Y%m%d")},
        headers={**BASE_HEADERS, "X-Requested-With": "XMLHttpRequest",
                 "Referer": "https://www.hnsmall.com/display/tvschedule"},
        timeout=20, label=label)
    h = r.text
    rows = []
    for item in re.split(r'<li class="item[^"]*">', h)[1:]:
        tm = re.search(r"<b>(\d\d:\d\d)</b>\s*~\s*(\d\d:\d\d)</span>", item)
        if not tm:
            continue
        start, end = tm.group(1), tm.group(2)
        main, _, sub = item.partition("함께 방송하는 상품")
        for part in [main] + re.findall(r"<li[^>]*>(.*?)</li>", sub, re.S):
            code = re.search(r"goGoods\('(\d+)'", part)
            title = re.search(r'class="tit">\s*(.*?)\s*</div>', part, re.S)
            price = re.search(r"<strong>([\d,]+)</strong>", part)
            if not code or not title:
                continue
            rows.append(_row(start, end, re.sub(r"<[^>]+>", "", title.group(1)), _price(price and price.group(1)),
                             HNS_LINK.format(code=code.group(1))))
    if "scheduleWrap" not in h and not rows:
        raise scrape_guard.FetchError(f"{label}: 편성표 조각이 아님 (len={len(h)})")
    return rows


# ============================== 쇼핑엔티 ==============================

NT_MAIN_URL = "https://www.shoppingntmall.com/broadcast/tvBroadcastMain"
NT_LIST_URL = "https://www.shoppingntmall.com/broadcast/tvBroadcastList"
NT_LINK = "https://www.shoppingntmall.com/display/goods/{code}"
_nt_token = {}


def _nt_prepare(session, label):
    """세션 쿠키 + CSRF 토큰. 한 실행에 한 번만 받는다."""
    if "token" in _nt_token:
        return _nt_token["token"]
    r = scrape_guard.request("GET", NT_MAIN_URL, session=session, headers=BASE_HEADERS, timeout=20,
                             label=f"{label} main")
    m = re.search(r'name="X-CSRF-TOKEN" content="([^"]+)"', r.text)
    _nt_token["token"] = m.group(1) if m else ""
    return _nt_token["token"]


def fetch_shoppingnt(date_obj, session, label):
    token = _nt_prepare(session, label)
    r = scrape_guard.request(
        "GET", NT_LIST_URL, session=session, params={"fromDate": date_obj.strftime("%Y%m%d")},
        headers={**BASE_HEADERS, "X-CSRF-TOKEN": token, "X-Requested-With": "XMLHttpRequest", "Referer": NT_MAIN_URL},
        timeout=20, label=label)
    h = r.text
    rows = []
    blocks = re.split(r'<div class="tit-time">', h)[1:]
    for blk in blocks:
        tm = re.search(r"(\d\d:\d\d)\s*~\s*(\d\d:\d\d)", blk)
        if not tm:
            continue
        for g in re.findall(r'<div class="goods">(.*?)<div class="btn-area', blk, re.S):
            code = re.search(r"/display/goods/(\d+)", g)
            name = re.search(r'class="name">([^<]*)', g)
            price = re.search(r'class="price">.*?<em[^>]*>([\d,]+)', g, re.S)
            if not code or not name:
                continue
            rows.append(_row(tm.group(1), tm.group(2), name.group(1), _price(price and price.group(1)),
                             NT_LINK.format(code=code.group(1))))
    return rows


# ============================== NS ==============================

NS_API = "https://mapi.nsmall.com/md/api/v1/display/media/schedule/{kind}/{ymd}"
NS_LINK = "https://m.nsmall.com/goods/{code}"


def _fetch_ns(date_obj, kind, label):
    r = scrape_guard.request("GET", NS_API.format(kind=kind, ymd=date_obj.strftime("%Y%m%d")),
                             headers={**BASE_HEADERS, "Accept": "application/json"}, timeout=20,
                             expect_json=True, label=label)
    data = (r.json().get("data") or {}).get("resultData") or {}
    rows = []
    for it in data.get("totalOrgan") or []:
        s, e = it.get("startDtm") or "", it.get("endDtm") or ""
        if len(s) < 12 or len(e) < 12:
            continue
        g = it.get("goods") or {}
        name = g.get("productNm") or it.get("pgmNm") or ""
        price = g.get("applyPrice") or g.get("salePrice") or 0
        code = it.get("goodsCd") or g.get("goodsCd")
        rows.append(_row(f"{s[8:10]}:{s[10:12]}", f"{e[8:10]}:{e[10:12]}", name, int(price or 0),
                         NS_LINK.format(code=code) if code else "", brand=g.get("brandNm") or ""))
    return rows


def fetch_ns_live(d, _s, label):
    return _fetch_ns(d, "tv", label)


def fetch_ns_plus(d, _s, label):
    return _fetch_ns(d, "shopplus", label)


# ============================== SK스토아 ==============================

SK_URL = "https://www.skstoa.com/broadcast/scdGoodsList"
SK_LINK = "https://www.skstoa.com/display/goods/{code}"


def fetch_skstoa(date_obj, _session, label):
    r = scrape_guard.request("GET", SK_URL, params={"date": date_obj.strftime("%Y%m%d")},
                             headers={**BASE_HEADERS, "X-Requested-With": "XMLHttpRequest",
                                      "Referer": "https://www.skstoa.com/tv_schedule"},
                             timeout=20, label=label)
    rows = []
    for art in r.text.split("<article")[1:]:
        art = art.split("</article>")[0].split('<div class="tv_wrap"')[0]  # 영상/추천상품 영역 제외
        tm = re.search(r'class="timebox">\s*(\d\d:\d\d)\s*~\s*(\d\d:\d\d)', art)
        if not tm:
            continue
        for tr in re.findall(r"<tr>(.*?)</tr>", art, re.S):
            code = re.search(r"goGoodsDetail\('(\d+)", tr)
            if not code:
                continue
            name = re.search(r'alt="([^"]*)"', tr) or re.search(r'class="name">([^<]*)', tr)
            brand = re.search(r'class="brand">([^<]*)', tr)
            price = re.search(r'class="price"><span>([\d,]+)', tr)
            rows.append(_row(tm.group(1), tm.group(2), name.group(1) if name else "", _price(price and price.group(1)),
                             SK_LINK.format(code=code.group(1)), brand=htmlmod.unescape(brand.group(1)) if brand else ""))
    return rows


# ============================== 공통 ==============================

# {저장 폴더명: (company, broadcast, fetch 함수)} - 폴더명은 etc_scraper 와 같게 둔다 (비교·교체 편의)
SOURCES = {
    "HNS_live":        ("HNS", "live", fetch_hns),
    "SHOPPINGNT_live": ("SHOPPINGNT", "live", fetch_shoppingnt),
    "NS_live":         ("NS", "live", fetch_ns_live),
    "NS_plus":         ("NS", "plus", fetch_ns_plus),
    "SKSTOA_live":     ("SKSTOA", "live", fetch_skstoa),
}


def _dedupe_sort(rows):
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: (x["start"], x["link"])):
        key = (r["start"], r["link"] or r["product"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def main(argv):
    only = {a.upper() for a in argv}
    base = today_kst()
    today_str = base.strftime("%Y-%m-%d")
    failures = []

    for label, (company, broadcast, fetch) in SOURCES.items():
        if only and company not in only and label.upper() not in only:
            continue
        sub_dir = os.path.join(OUTPUT_DIR, label)
        os.makedirs(sub_dir, exist_ok=True)
        session = requests.Session()
        month_data = {}

        for offset in DAYS_RANGE:
            d = base + timedelta(days=offset)
            date_dash, ym = d.strftime("%Y-%m-%d"), d.strftime("%Y-%m")
            if ym not in month_data:
                month_data[ym] = load_month(sub_dir, ym)
            days = month_data[ym]
            if date_dash < today_str and days.get(date_dash):
                print(f"[{label}] {date_dash}: 이미 기록됨, 건너뜀")
                continue

            print(f"[{label}] {date_dash} 수집 중...")
            try:
                rows = fetch(d, session, f"{label} {date_dash}")
            except Exception as e:  # 재시도까지 다 실패
                failures.append(f"{label} {date_dash}")
                print(f"  -> 수집 실패, 기존값 유지 ({len(days.get(date_dash) or [])}개): {e}")
                time.sleep(REQUEST_DELAY)
                continue

            rows = add_categories(_dedupe_sort(rows))
            if not rows and days.get(date_dash):
                print(f"  -> 0개 (기존 {len(days[date_dash])}개 유지)")
            elif not rows and date_dash > today_str:
                print("  -> 0개 (아직 편성 미공개로 보고 저장하지 않음)")
            else:
                days[date_dash] = rows
                print(f"  -> {len(rows)}개 편성 (방송 {len({r['start'] for r in rows})}개)")
            time.sleep(REQUEST_DELAY)

        for ym, days in month_data.items():
            if not days:
                continue
            path = os.path.join(sub_dir, f"{ym}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"company": company, "broadcast": broadcast, "month": ym,
                           "updated_at": today_kst().isoformat(timespec="seconds"),
                           "days": {k: days[k] for k in sorted(days)}}, f, ensure_ascii=False, indent=2)
            print(f"  저장: {path} ({len(days)}일)")

    if failures:
        print(f"\n수집하지 못한 날짜 {len(failures)}건: " + ", ".join(failures))
        sys.exit(1)
    print("\n완료.")


if __name__ == "__main__":
    main(sys.argv[1:])
