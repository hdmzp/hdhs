# -*- coding: utf-8 -*-
"""
4사 동일 브랜드 가격 추적 - 가격 수집 + 변경 감지

pricewatch/tracked.json(pricewatch_tracker.py 산출물)의 상품들에 대해
각 사 상품 페이지/API에서 현재 판매가·상품명·품절 여부를 수집하고,
직전 스냅샷과 비교해 가격 인하/인상·구성(상품명) 변경·품절/재개 이벤트를
이력화한다. 편성표에는 없는 "방송 후 상시가 변동"을 잡는 것이 목적.

== 저장 구조 ==
pricewatch/
├── current.json            # 상품별 최신 스냅샷 (매 실행 전체 재작성)
│   { "checked_at": "...", "items": { "HD|2244486431": {
│       "co","brand","pid","name","price","soldout","src",
│       "prev_price","prev_date","last_change":{"type","date","pct"} } } }
└── history/{YYYY-MM}.json  # 변경 이벤트만 append (일일 전량 스냅샷 아님)
    { "month": "2026-07", "events": [
      {"date","ts","co","brand","pid","type", ...},
      # type: obs         월 첫 관측 앵커 {price,name,src}
      #       price_drop  가격 인하 {old,new,pct,src}
      #       price_raise 가격 인상 {old,new,pct,src}
      #       name_change 상품명(구성) 변경 {old_name,new_name,price}
      #       sold_out / restock  {price}
      #       new_product 추적 신규 진입 {price,name}
    ]}

== 수집 전략 (회사별 폴백 체인) ==
HD: hmall /md/api/cache 상세 API 후보 → itemPtc HTML 인라인 JSON/메타
CJ: display-frontapi itemDetails/{pid}/(summary|priceInfo|itemInfo) →
    item.cjonstyle.com/nfront/item/{pid} HTML 인라인 JSON
LT: viewGoodsDetail.lotte HTML → m.lotteimall.com 재시도
GS: m.gsshop.com/prd/prd.gs HTML (gs_scraper의 모바일 UA 재사용;
    gsshop은 클라우드 IP에서 www가 막혀 있으나 m.은 specialPgm 수집 실적 있음)
모든 전략 실패 시 tracked.json의 최근 방송가로 폴백(src="schedule").
회사별로 실행 초반 CIRCUIT_BREAK_N건 연속 실패하면 그 회사는 이번 실행
전체를 schedule 폴백 처리(차단 상황에서 헛요청 방지).

== 변경 감지 규칙 ==
- 같은 src끼리만 비교 (실측가 ↔ 방송가 폴백 혼동 방지)
- 가격 변동이 1% 미만이면서 1,000원 미만이면 노이즈로 보고 이벤트 미생성
- 상품명은 공백 제거 후 비교
- current.json의 price/name은 이벤트 여부와 무관하게 항상 최신값으로 갱신

== 사용법 ==
  pip install requests
  python pricewatch_tracker.py && python pricewatch_scraper.py
"""

import os
import json
import re
import time
import requests
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
PW_DIR = "pricewatch"
TRACKED_PATH = os.path.join(PW_DIR, "tracked.json")
CURRENT_PATH = os.path.join(PW_DIR, "current.json")
HISTORY_DIR = os.path.join(PW_DIR, "history")

REQUEST_DELAY = 0.5
TIMEOUT = 10
CIRCUIT_BREAK_N = 6      # 실행 초반 연속 실패 이 수치면 회사 전체 폴백
NOISE_PCT = 1.0          # 이 % 미만이고
NOISE_WON = 1000         # 이 금액(원) 미만 변동이면 이벤트 미생성

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
GS_MOBILE_UA = ("Mozilla/5.0 (Linux; Android 13; SM-S918N) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

SOLDOUT_MARKERS = ("일시품절", "판매종료", "판매중지", "품절")


def now_kst():
    return datetime.now(KST)


def parse_price(v):
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


def find_key(obj, keys):
    """중첩 JSON에서 keys 중 첫 번째로 발견되는 키의 값을 반환 (BFS)."""
    queue = [obj]
    while queue:
        cur = queue.pop(0)
        if isinstance(cur, dict):
            for k in keys:
                if k in cur and cur[k] not in (None, "", []):
                    return cur[k]
            queue.extend(cur.values())
        elif isinstance(cur, list):
            queue.extend(cur)
    return None


def first_match(patterns, text):
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


def html_soldout(text):
    """인라인 JSON 플래그 우선, 없으면 텍스트 마커로 품절 추정."""
    if re.search(r'"(soldOutYn|soldoutYn)"\s*:\s*"Y"', text):
        return True
    if re.search(r'"(soldOut|soldout|isSoldOut)"\s*:\s*true', text):
        return True
    return any(m in text for m in SOLDOUT_MARKERS[:3])  # '품절' 단독은 오탐 잦아 제외


def og_title(text):
    t = first_match([r'<meta\s+property="og:title"\s+content="([^"]+)"',
                     r'<meta\s+content="([^"]+)"\s+property="og:title"'], text)
    if t:
        # "상품명 - 현대Hmall" 류 사이트명 접미 제거
        t = re.sub(r"\s*[\-|:]\s*(현대Hmall|현대홈쇼핑|GS SHOP|GSSHOP|롯데아이몰|롯데홈쇼핑|CJ온스타일|CJ ONSTYLE).*$",
                   "", t, flags=re.IGNORECASE).strip()
    return t or ""


# ---------------------------------------------------------------- HD (hmall)

# /md/api/cache 게이트웨이의 상세 API 경로 후보. 1회차 Actions 실행 로그의
# src 카운트로 동작하는 후보를 확인해 확정한다 (실패해도 HTML 폴백으로 동작).
HD_API_CANDIDATES = [
    "https://www.hmall.com/md/api/cache?url=/api/hf/gd/v1/item-base-info&slitmCd={pid}&deviceInfo=pc",
    "https://www.hmall.com/md/api/cache?url=/api/hf/pd/v1/item-info&slitmCd={pid}&deviceInfo=pc",
]
_hd_api_idx = None  # 이번 실행에서 확정된 후보 인덱스 (-1: 전부 실패, HTML만 사용)


def fetch_hd(pid):
    global _hd_api_idx
    headers = {"User-Agent": UA, "Referer": "https://www.hmall.com/"}

    candidates = (HD_API_CANDIDATES if _hd_api_idx is None
                  else [HD_API_CANDIDATES[_hd_api_idx]] if _hd_api_idx >= 0
                  else [])
    for tmpl in candidates:
        try:
            r = requests.get(tmpl.format(pid=pid), headers=headers, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            data = r.json()
            price = parse_price(find_key(data, ["sellPrc", "salePrc"]))
            name = find_key(data, ["convertedSlitmNm", "slitmNm"]) or ""
            if price > 0:
                _hd_api_idx = HD_API_CANDIDATES.index(tmpl)
                soldout = str(find_key(data, ["soldOutYn", "sldoutYn"]) or "") == "Y"
                return {"price": price, "name": str(name), "soldout": soldout, "src": "hd_api"}
        except Exception:
            continue
    if _hd_api_idx is None:
        _hd_api_idx = -1  # API 후보 전멸 → 이후 아이템은 HTML로 직행

    try:
        r = requests.get(f"https://www.hmall.com/md/pda/itemPtc?slitmCd={pid}",
                         headers=headers, timeout=TIMEOUT)
        if r.status_code == 200 and len(r.text) > 2000:
            text = r.text
            price = parse_price(first_match([
                r'"sellPrc"\s*:\s*"?([\d,\.]+)',
                r'property="product:sale_price:amount"\s+content="([\d\.]+)"',
                r'property="og:price:amount"\s+content="([\d\.]+)"',
            ], text))
            name = first_match([r'"slitmNm"\s*:\s*"([^"]+)"'], text) or og_title(text)
            if price > 0:
                return {"price": price, "name": name, "soldout": html_soldout(text),
                        "src": "hd_html"}
    except Exception:
        pass
    return None


# ---------------------------------------------------------- CJ (cjonstyle)

# 1회차 실행 결과 summary/priceInfo/itemInfo는 전부 실패 → 후보 확장.
# repBrandTag(cj_scraper.py에서 동작 확인됨)와 같은 API 패밀리에서 탐색한다.
CJ_DETAIL_RESOURCES = ["summary", "basicInfo", "price", "itemInfo", "detailInfo"]
_cj_resource = None  # 이번 실행에서 확정된 리소스명 ("": API 전멸, HTML만 사용)
_cj_debug_left = 2   # 초반 N개 상품은 전략별 응답 상태를 로그로 남김 (엔드포인트 진단용)


def fetch_cj(pid):
    global _cj_resource, _cj_debug_left
    debug = _cj_debug_left > 0
    if debug:
        _cj_debug_left -= 1
    headers = {"User-Agent": UA, "Referer": "https://display.cjonstyle.com/p/item/"}

    resources = ([_cj_resource] if _cj_resource else
                 CJ_DETAIL_RESOURCES if _cj_resource is None else [])
    for res in resources:
        try:
            r = requests.get(f"https://display-frontapi.cjonstyle.com/itemDetails/{pid}/{res}",
                             headers=headers, timeout=TIMEOUT)
            if debug:
                print(f"    [CJ진단] {pid} api/{res}: HTTP {r.status_code} {len(r.text)}b")
            if r.status_code != 200:
                continue
            data = r.json()
            price = parse_price(find_key(data, ["salePrice", "sellPrice", "custSalePrice"]))
            name = find_key(data, ["itemNm", "itemName"]) or ""
            if price > 0:
                _cj_resource = res
                soldout = bool(find_key(data, ["soldOut", "soldOutYn"]) in (True, "Y"))
                return {"price": price, "name": str(name), "soldout": soldout, "src": "cj_api"}
        except Exception as e:
            if debug:
                print(f"    [CJ진단] {pid} api/{res}: {e}")
            continue
    if _cj_resource is None:
        _cj_resource = ""  # 리소스 후보 전멸 → HTML만 사용

    # HTML: 편성 링크와 동일한 display.cjonstyle.com 상품페이지 우선
    # (cj_scraper가 같은 호스트의 REST를 Actions에서 정상 호출 중 → 미차단 확인됨)
    for url in (f"https://display.cjonstyle.com/p/item/{pid}",
                f"https://item.cjonstyle.com/nfront/item/{pid}"):
        try:
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
            if debug:
                print(f"    [CJ진단] {pid} {url.split('/')[2]}: HTTP {r.status_code} {len(r.text)}b")
            if r.status_code != 200 or len(r.text) < 2000:
                continue
            text = r.text
            price = parse_price(first_match([
                r'"salePrice"\s*:\s*"?([\d,]+)',
                r'"sellPrice"\s*:\s*"?([\d,]+)',
                r'property="og:price:amount"\s+content="([\d\.]+)"',
                r'property="product:sale_price:amount"\s+content="([\d\.]+)"',
            ], text))
            name = first_match([r'"itemNm"\s*:\s*"([^"]+)"',
                                r'"itemName"\s*:\s*"([^"]+)"'], text) or og_title(text)
            if price > 0:
                return {"price": price, "name": name, "soldout": html_soldout(text),
                        "src": "cj_html"}
        except Exception as e:
            if debug:
                print(f"    [CJ진단] {pid} {url.split('/')[2]}: {e}")
            continue
    return None


# --------------------------------------------------------- LT (lotteimall)

def fetch_lt(pid):
    headers = {"User-Agent": UA, "Referer": "https://www.lotteimall.com/main/viewMain.lotte"}
    for host in ("www.lotteimall.com", "m.lotteimall.com"):
        try:
            r = requests.get(f"https://{host}/goods/viewGoodsDetail.lotte?goods_no={pid}",
                             headers=headers, timeout=TIMEOUT)
            if r.status_code != 200 or len(r.text) < 2000:
                continue
            text = r.text
            price = parse_price(first_match([
                r'property="product:sale_price:amount"\s+content="([\d\.]+)"',
                r'property="og:price:amount"\s+content="([\d\.]+)"',
                r'"(?:dscnt_prc|dscntPrc|sale_prc|salePrc|salePrice)"\s*:\s*"?([\d,]+)',
                r'판매가[^0-9]{0,60}?([\d,]{4,})\s*원',
            ], text))
            name = og_title(text) or (first_match([r'"goods_nm"\s*:\s*"([^"]+)"'], text) or "")
            if price > 0:
                return {"price": price, "name": name, "soldout": html_soldout(text),
                        "src": "lt_html"}
        except Exception:
            continue
    return None


# ------------------------------------------------------------- GS (gsshop)

_gs_debug_left = 2  # 초반 N개 상품은 응답 상태를 로그로 남김 (차단 형태 진단용)


def fetch_gs(pid):
    global _gs_debug_left
    debug = _gs_debug_left > 0
    if debug:
        _gs_debug_left -= 1
    headers = {"User-Agent": GS_MOBILE_UA, "Referer": "https://m.gsshop.com/index.gs"}
    try:
        r = requests.get(f"https://m.gsshop.com/prd/prd.gs?prdid={pid}",
                         headers=headers, timeout=TIMEOUT)
        if debug:
            print(f"    [GS진단] {pid}: HTTP {r.status_code} {len(r.text)}b")
        if r.status_code == 200 and len(r.text) > 2000:
            text = r.text
            price = parse_price(first_match([
                r'property="og:price:amount"\s+content="([\d\.]+)"',
                r'property="product:sale_price:amount"\s+content="([\d\.]+)"',
                r'"(?:salePrice|sellPrice|finalPrice)"\s*:\s*"?([\d,]+)',
            ], text))
            name = og_title(text) or (first_match([r'"prdNm"\s*:\s*"([^"]+)"'], text) or "")
            if price > 0:
                return {"price": price, "name": name, "soldout": html_soldout(text),
                        "src": "gs_html"}
    except Exception as e:
        if debug:
            print(f"    [GS진단] {pid}: {e}")
    return None


FETCHERS = {"HD": fetch_hd, "CJ": fetch_cj, "LT": fetch_lt, "GS": fetch_gs}


# ------------------------------------------------------------ 수집 파이프라인

def collect_items(tracked):
    """tracked.json → 회사별 [{co, pid, brand, name, link, fallback_price}] (pid 중복 제거)."""
    per_co = {co: {} for co in FETCHERS}
    for brand, info in tracked.get("brands", {}).items():
        for co, products in info.get("products", {}).items():
            if co not in per_co:
                continue
            for item in products:
                pid = item.get("pid")
                if not pid or pid in per_co[co]:
                    continue
                per_co[co][pid] = {
                    "co": co, "pid": pid, "brand": brand,
                    "name": item.get("name") or "",
                    "link": item.get("link") or "",
                    "fallback_price": int(item.get("last_broadcast_price") or 0),
                }
    return {co: list(m.values()) for co, m in per_co.items()}


def run_company(co, items, src_counts):
    """회사 하나를 순차 수집. 초반 연속 실패 시 서킷브레이커로 전체 폴백."""
    results = {}
    fetcher = FETCHERS[co]
    n_success = 0
    consecutive_fail = 0
    tripped = False
    for it in items:
        res = None
        if not tripped:
            try:
                res = fetcher(it["pid"])
            except Exception as e:
                print(f"    [{co}] {it['pid']} 오류: {e}")
            time.sleep(REQUEST_DELAY)
            if res is None:
                consecutive_fail += 1
                if n_success == 0 and consecutive_fail >= CIRCUIT_BREAK_N:
                    # 성공이 한 건도 없이 초반부터 전부 실패 → 차단으로 판단
                    tripped = True
                    print(f"  [{co}] 초반 {CIRCUIT_BREAK_N}건 연속 실패 - 이번 실행은 방송가 폴백")
            else:
                n_success += 1
                consecutive_fail = 0
        if res is None:
            res = {"price": it["fallback_price"], "name": it["name"],
                   "soldout": False, "src": "schedule"}
        res["brand"] = it["brand"]
        res["link"] = it["link"]
        results[f"{co}|{it['pid']}"] = res
        src_counts.setdefault(co, {}).setdefault(res["src"], 0)
        src_counts[co][res["src"]] += 1
    return results


# ------------------------------------------------------------ 변경 감지/저장

def norm_name(s):
    return re.sub(r"\s+", "", s or "")


def detect_events(prev_items, results, date_str, ts_str, month_seen):
    """직전 current 스냅샷과 비교해 이벤트 목록 생성."""
    events = []

    def ev(key, type_, **kw):
        co, pid = key.split("|", 1)
        e = {"date": date_str, "ts": ts_str, "co": co,
             "brand": results[key]["brand"], "pid": pid, "type": type_}
        e.update(kw)
        events.append(e)
        return e

    for key, new in results.items():
        prev = prev_items.get(key)

        if key not in month_seen:
            ev(key, "obs", price=new["price"], name=new["name"], src=new["src"])

        if prev is None:
            ev(key, "new_product", price=new["price"], name=new["name"])
            continue

        # 같은 src끼리만 diff (방송가 폴백 ↔ 실측가 전환 시 오탐 방지)
        if prev.get("src") == new["src"]:
            old_p, new_p = int(prev.get("price") or 0), int(new["price"] or 0)
            if old_p > 0 and new_p > 0 and old_p != new_p:
                diff = new_p - old_p
                pct = round(diff / old_p * 100, 1)
                if abs(pct) >= NOISE_PCT or abs(diff) >= NOISE_WON:
                    ev(key, "price_drop" if diff < 0 else "price_raise",
                       old=old_p, new=new_p, pct=pct, src=new["src"])

            if norm_name(prev.get("name")) and norm_name(new["name"]) and \
               norm_name(prev.get("name")) != norm_name(new["name"]):
                ev(key, "name_change", old_name=prev.get("name"),
                   new_name=new["name"], price=new["price"])

            if bool(prev.get("soldout")) != bool(new["soldout"]):
                ev(key, "sold_out" if new["soldout"] else "restock",
                   price=new["price"])

    return events


def build_current(prev_items, results, events, checked_at):
    items = {}
    ev_by_key = {}
    for e in events:
        if e["type"] in ("price_drop", "price_raise", "name_change", "sold_out", "restock"):
            ev_by_key.setdefault(f"{e['co']}|{e['pid']}", []).append(e)

    for key, new in results.items():
        prev = prev_items.get(key) or {}
        item = {
            "co": key.split("|", 1)[0],
            "brand": new["brand"],
            "pid": key.split("|", 1)[1],
            "name": new["name"],
            "price": int(new["price"] or 0),
            "soldout": bool(new["soldout"]),
            "src": new["src"],
            "link": new["link"],
            "prev_price": prev.get("prev_price"),
            "prev_date": prev.get("prev_date"),
            "last_change": prev.get("last_change"),
        }
        # 가격 이벤트를 마지막에 적용해 last_change에서 우선하도록 (뱃지 중요도)
        for e in sorted(ev_by_key.get(key, []),
                        key=lambda e: e["type"] in ("price_drop", "price_raise")):
            if e["type"] in ("price_drop", "price_raise"):
                item["prev_price"] = e["old"]
                item["prev_date"] = e["date"]
                item["last_change"] = {"type": e["type"], "date": e["date"], "pct": e["pct"]}
            else:
                item["last_change"] = {"type": e["type"], "date": e["date"]}
        items[key] = item
    return {"checked_at": checked_at, "items": items}


def load_json(path, default):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            return default
    return default


def main():
    base = now_kst()
    date_str = base.strftime("%Y-%m-%d")
    ts_str = base.strftime("%H:%M")
    ym = base.strftime("%Y-%m")

    tracked = load_json(TRACKED_PATH, None)
    if not tracked:
        print(f"[scraper] {TRACKED_PATH} 없음 - pricewatch_tracker.py를 먼저 실행하세요")
        return

    per_co = collect_items(tracked)
    total = sum(len(v) for v in per_co.values())
    print(f"[scraper] 수집 대상 {total}개 "
          f"({', '.join(f'{co} {len(v)}' for co, v in per_co.items())})")

    src_counts = {}
    results = {}
    for co, items in per_co.items():
        print(f"[scraper] {co} 수집 중... ({len(items)}개)")
        results.update(run_company(co, items, src_counts))

    prev_current = load_json(CURRENT_PATH, {})
    prev_items = prev_current.get("items", {})

    history_path = os.path.join(HISTORY_DIR, f"{ym}.json")
    history = load_json(history_path, {"month": ym, "events": []})
    month_seen = {f"{e['co']}|{e['pid']}" for e in history["events"]}

    events = detect_events(prev_items, results, date_str, ts_str, month_seen)
    history["events"].extend(events)

    current = build_current(prev_items, results, events,
                            base.isoformat(timespec="seconds"))

    os.makedirs(HISTORY_DIR, exist_ok=True)
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    with open(CURRENT_PATH, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)

    n_changes = sum(1 for e in events if e["type"] not in ("obs", "new_product"))
    print(f"\n[scraper] 이벤트 {len(events)}건 (변경 {n_changes}건) → {history_path}")
    print(f"[scraper] 스냅샷 {len(results)}개 → {CURRENT_PATH}")
    print("[scraper] src 요약 (1회차 실행에서 엔드포인트 확정용):")
    for co in FETCHERS:
        counts = src_counts.get(co, {})
        summary = " / ".join(f"{s} {n}" for s, n in sorted(counts.items())) or "-"
        print(f"  {co}: {summary}")


if __name__ == "__main__":
    main()
