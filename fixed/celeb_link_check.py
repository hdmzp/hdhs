# -*- coding: utf-8 -*-
"""
셀럽PGM 상품 링크 열림 여부 점검

방송한정코드 상품은 상품페이지가 "죄송합니다. 현재 매진된 상품입니다" 같은
안내로 막힌다. 화면(index.html 셀럽PGM 탭)은 이 파일의 결과를 보고 막힌 상품만
홈사 검색결과(브랜드, 없으면 상품명)로 연결한다.

== 판정 (pricewatch_scraper의 회사별 상품페이지 수집기 재사용) ==
- 수집기가 상품정보를 읽음 → soldout이면 "x"(안 열림), 아니면 "ok"
- 수집기가 상품정보를 못 읽음 → 같은 페이지를 다시 받아 매진/판매중단 안내문이
  있으면 "x". 안내문도 없으면(예: CJ 트래픽 대기 페이지) 판단 보류 → 기록만 하고
  화면은 상품 링크 유지
  (2026-11-03 실측: LT는 82KB짜리 "판매하지 않는 상품" 안내 페이지, CJ는
   '매진'/'죄송' 문구가 든 페이지가 이 경우였다)

== 부하 제한 ==
- 대상: 셀럽PGM 월별 누적(이번 달 + 지난 달) 상품
- "x"는 다시 안 본다. 단, 그 상품이 앞으로 있을 방송에 다시 잡히면 하루 한 번 재확인
- "ok"/보류는 방송일 ±7일 이내면 하루 한 번, 그 밖이면 7일에 한 번 재확인
- 한 번 실행에 최대 MAX_CHECKS_PER_RUN건 (안 본 상품 → 오래된 순으로 우선)

== 저장 ==
homeshopping/representative_programs/link_status.json
{"checked_at": "...", "items": {"CJ|2094159470": {"s": "x", "t": "...", "d": "2026-10-03"}}}
"""

import os
import re
import sys
import json
import time
import requests
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import pricewatch_scraper as pw  # noqa: E402

KST = timezone(timedelta(hours=9))
DIR = os.path.join(ROOT, "homeshopping", "representative_programs")
OUT_PATH = os.path.join(DIR, "link_status.json")

MAX_CHECKS_PER_RUN = 120
REQUEST_DELAY_SEC = 0.3
NEAR_DAYS = 7               # 방송일 ±7일 이내는 매일 재확인
NEAR_RECHECK = timedelta(hours=20)
FAR_RECHECK = timedelta(days=7)

PID_RE = {
    "HD": re.compile(r"slitmCd=(\d+)"),
    "GS": re.compile(r"prdid=(\d+)"),
    "CJ": re.compile(r"/item/(\d+)"),
    "LT": re.compile(r"goods_no=(\d+)"),
}
# 수집기가 상품정보를 못 읽었을 때 다시 받아볼 페이지 (수집기와 같은 주소)
RAW_URL = {
    "HD": "https://www.hmall.com/md/pda/itemPtc?slitmCd={pid}",
    "GS": "https://m.gsshop.com/prd/prd.gs?prdid={pid}",
    "CJ": "https://item.cjonstyle.com/nfront/item/{pid}",
    "LT": "https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no={pid}",
}
CLOSED_MARKERS = ("매진", "죄송합니다", "판매하지 않", "판매종료", "판매가 종료", "판매중지")


def now_kst():
    return datetime.now(KST)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def collect_products(today):
    """이번 달 + 지난 달 월별 누적에서 {(co, pid): 대표 방송일} 수집.
    같은 상품이 여러 회차에 있으면, 앞으로 있을 방송이 있으면 그 날짜(가장 가까운),
    없으면 가장 최근 방송일을 쓴다."""
    first = today.replace(day=1)
    months = [first.strftime("%Y-%m"), (first - timedelta(days=1)).strftime("%Y-%m")]
    dates = {}
    for ym in months:
        hist = load_json(os.path.join(DIR, "history", f"{ym}.json"), {})
        for prog in hist.get("programs", []) or []:
            co = prog.get("company", "")
            if co not in PID_RE:
                continue
            for b in prog.get("broadcasts", []) or []:
                d = b.get("date") or ""
                for p in b.get("products", []) or []:
                    m = PID_RE[co].search(p.get("link") or "")
                    if m and d:
                        dates.setdefault((co, m.group(1)), set()).add(d)
    today_s = today.isoformat()
    out = {}
    for key, ds in dates.items():
        upcoming = sorted(d for d in ds if d >= today_s)
        out[key] = upcoming[0] if upcoming else max(ds)
    return out


def needs_check(entry, bdate, today, now):
    if not entry:
        return True
    try:
        last = datetime.fromisoformat(entry["t"])
    except Exception:
        return True
    upcoming = bdate >= today.isoformat()
    if entry.get("s") == "x":
        return upcoming and now - last >= NEAR_RECHECK
    near = abs((datetime.fromisoformat(bdate).date() - today).days) <= NEAR_DAYS
    return now - last >= (NEAR_RECHECK if near else FAR_RECHECK)


def check(co, pid):
    """'x'(안 열림) / 'ok' / '?'(판단 보류) 반환."""
    try:
        res = pw.FETCHERS[co](pid)
    except Exception:
        res = None
    if isinstance(res, dict):
        return "x" if res.get("soldout") else "ok"
    try:
        headers = {"User-Agent": pw.GS_MOBILE_UA if co == "GS" else pw.UA}
        r = requests.get(RAW_URL[co].format(pid=pid), headers=headers, timeout=pw.TIMEOUT)
        if r.status_code == 200 and any(m in r.text for m in CLOSED_MARKERS):
            return "x"
    except Exception:
        pass
    return "?"


def main():
    now = now_kst()
    today = now.date()
    prev = load_json(OUT_PATH, {}).get("items", {}) or {}
    products = collect_products(today)

    todo = []
    for (co, pid), bdate in products.items():
        key = f"{co}|{pid}"
        entry = prev.get(key)
        if needs_check(entry, bdate, today, now):
            # 안 본 상품 우선 → 오래전에 본 순 → 방송일이 오늘에 가까운 순
            # (첫 실행 땐 1,500개 남짓이 한꺼번에 밀려 있어, 지금 화면에 보이는
            #  최근·예정 회차부터 채운다)
            dist = abs((datetime.fromisoformat(bdate).date() - today).days)
            todo.append(((entry or {}).get("t", ""), dist, key, co, pid, bdate))
    todo.sort()
    print(f"[링크점검] 대상 {len(products)}개 중 점검 필요 {len(todo)}개 "
          f"(이번 실행 최대 {MAX_CHECKS_PER_RUN}개)")

    # 지난 달보다 오래된 상품은 화면에서 안 쓰므로 버린다 (파일 크기 유지)
    live_keys = {f"{co}|{pid}" for co, pid in products}
    items = {k: v for k, v in prev.items() if k in live_keys}
    counts = {"x": 0, "ok": 0, "?": 0}
    shown = 0
    for _, _, key, co, pid, bdate in todo[:MAX_CHECKS_PER_RUN]:
        s = check(co, pid)
        counts[s] += 1
        old = prev.get(key, {}).get("s")
        if s == "?" and old in ("x", "ok"):
            s = old  # 판단 보류면 직전 판정 유지 (시각만 갱신해 재시도 간격을 둔다)
        items[key] = {"s": s, "t": now.isoformat(timespec="seconds"), "d": bdate}
        if s == "x" and old != "x" and shown < 20:
            print(f"    -> 안 열림: {key} (방송 {bdate})")
            shown += 1
        time.sleep(REQUEST_DELAY_SEC)

    os.makedirs(DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"checked_at": now.isoformat(timespec="seconds"),
                   "items": dict(sorted(items.items()))}, f, ensure_ascii=False, indent=1)
    total_x = sum(1 for v in items.values() if v.get("s") == "x")
    print(f"[링크점검] 이번 점검 {sum(counts.values())}건: 안 열림 {counts['x']} / 정상 {counts['ok']} "
          f"/ 보류 {counts['?']} · 누적 안 열림 {total_x}개")


if __name__ == "__main__":
    main()
