# -*- coding: utf-8 -*-
"""
official_schedule.py
기타 홈쇼핑사 편성을 **각 사 공식 편성표**에서 받는 수집 함수 모음 (etc_scraper.py 가 쓴다).

== 배경 (2026-11-13) ==
기타7개사 편성은 라방바(live.ecomm-data.com) list_hs 에서 받아왔는데, 11-11 라방바가
GitHub Actions IP를, 11-12에는 Cloudflare Worker 중계까지 405로 막았다. 4사(HD/LT/CJ/GS)처럼
공식 편성표를 1순위로 쓰고 라방바는 공식이 안 될 때의 2순위로 내린다.
전환 전 11/12~17 엿새를 라방바 기록과 비교했을 때 방송 시작시각 일치 92~99%,
상품코드 일치 홈앤 97% · 쇼핑엔티 98% · SK스토아 97% · NS 80~85%(대표상품 변경분)였다.

== 회사별 출처 ==
  홈앤      HNS_live        GET www.hnsmall.com/display/tvschedule-list?broadDay=YYYYMMDD (HTML 조각)
                            방송마다 대표상품 1개 + '함께 방송하는 상품' N개. 오늘 -10 ~ +10일 제공.
  쇼핑엔티  SHOPPINGNT_live GET www.shoppingntmall.com/broadcast/tvBroadcastList?fromDate=YYYYMMDD (HTML 조각)
                            먼저 /broadcast/tvBroadcastMain 에서 세션쿠키 + X-CSRF-TOKEN 메타를 받아 보낸다.
                            미래는 오늘+4일까지만 준다 (그 뒤 날짜는 빈 응답 -> [] 로 돌려준다).
  NS        NS_live/NS_plus GET mapi.nsmall.com/md/api/v1/display/media/schedule/{tv|shopplus}/YYYYMMDD (JSON)
                            totalOrgan[] 한 항목 = 방송 상품 1개 (같은 프로그램에 brdctOrd 1,2,...).
  SK스토아  SKSTOA_live     GET www.skstoa.com/broadcast/scdGoodsList?date=YYYYMMDD (HTML 조각)
                            화면엔 시간대당 대표상품 1개만 그리지만 GA 태깅 input(ga-prd-data,
                            p-list="TV편성_방송상품")에 같이 방송하는 상품이 전부 있어 그걸 쓴다.
  공영      PUBLIC_live     POST m.gongyoungshop.kr/tvshopping/selectScheduleSub.do  brcStdDate=YYYYMMDD (JSON)
                            회사 사이트 gongyoung.com 은 해외 IP 차단이지만 쇼핑몰 도메인은 열려 있다.
                            11/12 라방바 기록 26건과 시간·상품코드 100% 일치 (2026-11-14 확인).
  미지원: K쇼핑(kshop)·신세계 - 해외 IP를 막아 Actions 에서도 열리지 않는다 (K쇼핑 403, 신세계 417).
          etc-scrape.yml 의 접속 확인 스텝으로 계속 본다. 다른 도메인이 열리면 여기에 추가.

== 반환 형식 == etc_scraper.py 의 편성 행과 같다 (category 는 비워서 돌려주고 호출부가 채운다).
  {"start","end","brand","product","price","link","category":"","lavangba_category":"","hsshow_id":""}
  라방바 전용 필드 두 개는 빈 문자열. 라방바가 다시 열리면 hsshow_id 는 시작시각으로 붙일 수 있다.
실패(재시도 소진·구조 변경)는 scrape_guard.FetchError 로 던진다. 호출부가 라방바로 넘어간다.

== 사용법 ==
    from tools.official_schedule import OFFICIAL_FETCHERS
    fetch = OFFICIAL_FETCHERS["HNS_live"]
    rows = fetch(date_obj, requests.Session(), "HNS_live 2026-11-13")
"""

import html as htmlmod
import re

from tools import scrape_guard

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
BASE_HEADERS = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}


def _price(s):
    try:
        return int(re.sub(r"[^\d]", "", (s or "").split(".")[0]) or 0)
    except ValueError:
        return 0


def _row(start, end, product, price, link, brand=""):
    return {
        "start": start, "end": end, "brand": htmlmod.unescape(brand or "").strip(),
        "product": htmlmod.unescape(re.sub(r"<[^>]+>", "", product or "")).strip(),
        "price": price, "link": link, "category": "", "lavangba_category": "", "hsshow_id": "",
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
    if "scheduleWrap" not in h:
        raise scrape_guard.FetchError(f"{label}: 편성표 조각이 아님 (len={len(h)})")
    rows = []
    for item in re.split(r'<li class="item[^"]*">', h)[1:]:
        tm = re.search(r"<b>(\d\d:\d\d)</b>\s*~\s*(\d\d:\d\d)</span>", item)
        if not tm:
            continue
        main, _, sub = item.partition("함께 방송하는 상품")
        for part in [main] + re.findall(r"<li[^>]*>(.*?)</li>", sub, re.S):
            code = re.search(r"goGoods\('(\d+)'", part)
            title = re.search(r'class="tit">\s*(.*?)\s*</div>', part, re.S)
            price = re.search(r"<strong>([\d,]+)</strong>", part)
            if not code or not title:
                continue
            rows.append(_row(tm.group(1), tm.group(2), title.group(1), _price(price and price.group(1)),
                             HNS_LINK.format(code=code.group(1))))
    return _dedupe_sort(rows)


# ============================== 쇼핑엔티 ==============================

NT_MAIN_URL = "https://www.shoppingntmall.com/broadcast/tvBroadcastMain"
NT_LIST_URL = "https://www.shoppingntmall.com/broadcast/tvBroadcastList"
NT_LINK = "https://www.shoppingntmall.com/display/goods/{code}"


def _nt_token(session, label):
    """세션 쿠키 + CSRF 토큰. 세션마다 한 번만 받는다."""
    token = getattr(session, "_nt_csrf", None)
    if token is None:
        r = scrape_guard.request("GET", NT_MAIN_URL, session=session, headers=BASE_HEADERS, timeout=20,
                                 label=f"{label} main")
        m = re.search(r'name="X-CSRF-TOKEN" content="([^"]+)"', r.text)
        token = session._nt_csrf = m.group(1) if m else ""
    return token


def fetch_shoppingnt(date_obj, session, label):
    token = _nt_token(session, label)
    r = scrape_guard.request(
        "GET", NT_LIST_URL, session=session, params={"fromDate": date_obj.strftime("%Y%m%d")},
        headers={**BASE_HEADERS, "X-CSRF-TOKEN": token, "X-Requested-With": "XMLHttpRequest", "Referer": NT_MAIN_URL},
        timeout=20, label=label)
    rows = []
    for blk in re.split(r'<div class="tit-time">', r.text)[1:]:
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
    return _dedupe_sort(rows)


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
        code = it.get("goodsCd") or g.get("goodsCd")
        rows.append(_row(f"{s[8:10]}:{s[10:12]}", f"{e[8:10]}:{e[10:12]}",
                         g.get("productNm") or it.get("pgmNm") or "",
                         int(g.get("applyPrice") or g.get("salePrice") or 0),
                         NS_LINK.format(code=code) if code else "", brand=g.get("brandNm") or ""))
    return _dedupe_sort(rows)


def fetch_ns_live(d, _s, label):
    return _fetch_ns(d, "tv", label)


def fetch_ns_plus(d, _s, label):
    return _fetch_ns(d, "shopplus", label)


# ============================== SK스토아 ==============================

SK_URL = "https://www.skstoa.com/broadcast/scdGoodsList"
SK_LINK = "https://www.skstoa.com/display/goods/{code}"
SK_GA_INPUT = re.compile(r'<input type="hidden" class="ga-prd-data"((?:\s+p-[a-z]+="[^"]*")+)')


def fetch_skstoa(date_obj, _session, label):
    """맨 앞 <section> 은 전날 밤 시간대(예: 23:31~00:31)가 와서 날짜가 맞지 않으므로
    시작시각이 다음 section 보다 늦으면 버린다. 'TV편성_추천방송상품'(함께 보면 좋은 상품)은 제외."""
    r = scrape_guard.request("GET", SK_URL, params={"date": date_obj.strftime("%Y%m%d")},
                             headers={**BASE_HEADERS, "X-Requested-With": "XMLHttpRequest",
                                      "Referer": "https://www.skstoa.com/tv_schedule"},
                             timeout=20, label=label)
    sections = []
    for sec in r.text.split("<section")[1:]:
        sec = sec.split("</section>")[0]
        tm = re.search(r'class="timebox">\s*(\d\d:\d\d)\s*~\s*(\d\d:\d\d)', sec)
        if tm:
            sections.append((tm.group(1), tm.group(2), sec))
    while len(sections) >= 2 and sections[0][0] > sections[1][0]:
        sections.pop(0)

    rows = []
    for start, end, sec in sections:
        for m in SK_GA_INPUT.finditer(sec):
            a = dict(re.findall(r'(p-[a-z]+)="([^"]*)"', m.group(1)))
            if not a.get("p-id") or not (a.get("p-list") or "").startswith("TV편성_방송상품"):
                continue
            rows.append(_row(start, end, a.get("p-name", ""), _price(a.get("p-price")),
                             SK_LINK.format(code=a["p-id"]), brand=a.get("p-brand", "")))
    return _dedupe_sort(rows)


# ============================== 공영쇼핑 ==============================

GY_LIST_URL = "https://m.gongyoungshop.kr/tvshopping/selectScheduleSub.do"
GY_LINK = "https://www.gongyoungshop.kr/goods/selectGoodsDetail.do?prdId={code}"  # 라방바 기록과 같은 형식


def fetch_public(date_obj, _session, label):
    """회사 소개 사이트(gongyoung.com)는 해외 IP를 막지만 쇼핑몰(gongyoungshop.kr)은 열려 있다.
    모바일 편성표 페이지가 날짜 탭을 누를 때 부르는 POST 를 그대로 쓴다. Accept 에 json 이
    없으면 같은 내용을 XML 로 준다. prdList[] 한 항목 = 방송 상품 1개 (같은 방송은 brcBgnDtm 이 같음)."""
    r = scrape_guard.request(
        "POST", GY_LIST_URL, data={"brcStdDate": date_obj.strftime("%Y%m%d")},
        headers={**BASE_HEADERS, "Accept": "application/json, text/javascript, */*; q=0.01",
                 "X-Requested-With": "XMLHttpRequest",
                 "Referer": "https://m.gongyoungshop.kr/tvshopping/tvSchedule.do"},
        timeout=20, expect_json=True, label=label)
    rows = []
    for it in r.json().get("prdList") or []:
        s, e = it.get("brcBgnDtm") or "", it.get("brcEndDtm") or ""
        if len(s) < 12 or len(e) < 12 or not it.get("prdId"):
            continue
        rows.append(_row(f"{s[8:10]}:{s[10:12]}", f"{e[8:10]}:{e[10:12]}", it.get("prdNm") or "",
                         int(it.get("prdPrc") or 0), GY_LINK.format(code=it["prdId"])))
    return _dedupe_sort(rows)


# {etc_scraper 의 log_label(저장 폴더명): fetch(date_obj, session, label) -> rows}
OFFICIAL_FETCHERS = {
    "PUBLIC_live":     fetch_public,
    "HNS_live":        fetch_hns,
    "SHOPPINGNT_live": fetch_shoppingnt,
    "NS_live":         fetch_ns_live,
    "NS_plus":         fetch_ns_plus,
    "SKSTOA_live":     fetch_skstoa,
}
