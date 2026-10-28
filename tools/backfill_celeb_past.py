# -*- coding: utf-8 -*-
"""
중간 편입 셀럽PGM의 '이미 지나간' 회차를 월 누적(history)에 소급해 채운다.

배경:
  셀럽PGM 수집기(recj/relt)는 프로그램 페이지의 '방송예정' 상품만 읽는다.
  그래서 2026-10 중간에 편입한 프로그램은 편입일 ~ 첫 수집일 사이 회차가
  history에 하나도 없다. 이 스크립트는 그 구간을 편성표로 한 번 채운다.

    CJ_LMT  림잇티드        라이브(TV) 방송만 넣는다 - TV+ 재방송('T림잇티드 1부'
                            같은 데이터방송 편성)은 넣지 않는다.
                            1) CJ 편성표 API(tvSchedule, broadType=live)를 날짜별로
                               다시 조회해 pgmNm이 '림잇티드'인 방송의 itemList 전체.
                            2) API에서 못 찾은 날은 로컬 CJ_live에서 라이브 정규
                               편성 시각(live_slots: 토 10:20)에 시작하는 회차를
                               쓴다. CJ_live는 고정PGM 키워드에 없던 시절이라
                               프로그램명 표시가 없어 시각으로만 고르고, 대표상품
                               1개뿐이다. (2026-10-26 실측: API가 10/24만 주고
                               10/3·10/10·10/17은 pgmNm으로 안 잡혔다)
    CJ_BYL  바이림          림잇티드와 같은 방식(라이브만). 비정기라 정규 시각이 없어
                            CJ_live 폴백은 다른 출처로 확인한 회차(live_dates)만 쓴다
                            (10/23(금) 19:35 - 라방바 '[바이림] 레쁠레뜨', CJ_live 같은 시각).
    LT_YNH  더퍼스트 유난희  로컬 편성표 homeshopping/LT_live (pgm 표시가 있다).
                            롯데는 한 방송을 상품 구간별로 쪼개 주므로 이어지는
                            구간은 첫 구간 시각의 한 회차로 묶는다.

동작:
  - 대상 기간: 프로그램별 since ~ 어제. 오늘 이후는 수집기 몫이다.
  - build_celeb_history.merge_into_month로 합친다. 이미 기록된 회차(확정)는
    건드리지 않고 없는 회차만 추가되므로 몇 번 돌려도 결과가 같다.

사용법:
  python tools/backfill_celeb_past.py                    # 미리보기 (파일 안 건드림)
  python tools/backfill_celeb_past.py --apply            # 실제 저장
  python tools/backfill_celeb_past.py --only LT_YNH      # 한 프로그램만
"""

import os
import sys
import json
import time
import argparse
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "fixed"))
os.chdir(ROOT)

import build_celeb_history as bch  # noqa: E402
from celeb_day_sweep import (  # noqa: E402
    KST, label_cj, label_lt, title_matches, to_product, to_minutes, format_segment,
    SEGMENT_GAP_MIN, SEGMENT_GAP_EMPTY_MIN,
)

TARGETS = {
    "CJ_LMT": {
        "company": "CJ", "since": "2026-10-03", "names": ("림잇티드",),
        # 라이브 정규 편성 (요일 0=월, 시작시각) - CJ_live 폴백용
        "live_slots": ((5, "10:20"),),
        "meta": {"tab_name": "림잇티드", "program_title": "림잇티드",
                 "schedule_raw": "TV:토요일 10시20분, TV+:화요일 20시45분",
                 "detail_link": "https://display.cjonstyle.com/m/pgmShop/100082"},
    },
    "CJ_BYL": {
        "company": "CJ", "since": "2026-10-01", "names": ("바이림",),
        # 정규 시각 대신 확인된 라이브 회차 (날짜, 시작시각) - CJ_live 폴백용
        "live_dates": (("2026-10-23", "19:35"),),
        "meta": {"tab_name": "바이림", "program_title": "바이림", "schedule_raw": "",
                 "detail_link": "https://display.cjonstyle.com/m/pgmShop/100033"},
    },
    "LT_YNH": {
        "company": "LT", "since": "2026-10-01", "names": ("더퍼스트 유난희", "유난희"),
        "meta": {"tab_name": "유난희", "program_title": "더퍼스트 유난희",
                 "schedule_raw": "매주 목요일 오전 09시25분",
                 "detail_link": "https://www.lotteimall.com/contstmpl/"
                                "viewContsTmplDetail.lotte?conts_no=1149"},
    },
}

# CJ 편성표 채널. 라이브(TV)만 본다 - TV+(plus)는 재방송이라 셀럽PGM에 안 넣는다.
CJ_BROAD_TYPES = ("live",)
CJ_TV_SCHEDULE_URL = ("https://display.cjonstyle.com/c/rest/tv/tvSchedule"
                      "?bdDt={bd_dt}&isMobile=false&broadType={broad}&isEmployee=false")


def date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ---------------- CJ: 편성표 API ----------------

def cj_products(names, start: date, end: date, live_slots=(), live_dates=()) -> list:
    import requests
    import recj

    session = requests.Session()
    products = []
    months = {}
    for d in date_range(start, end):
        bd_dt = d.strftime("%Y%m%d")
        found = False
        for broad in CJ_BROAD_TYPES:
            data = recj.fetch_json(session, CJ_TV_SCHEDULE_URL.format(bd_dt=bd_dt, broad=broad))
            programs = ((data or {}).get("result") or {}).get("programList") or []
            if data is None:
                print(f"  [CJ] [경고] {d} {broad} 편성표 조회 실패")
            for pg in programs:
                if not title_matches(pg.get("pgmNm"), names) or not pg.get("bdStrDtm"):
                    continue
                st = datetime.fromtimestamp(pg["bdStrDtm"] / 1000, tz=KST)
                label = label_cj(st.date(), st.strftime("%H:%M"))
                items = [it for it in (pg.get("itemList") or []) if it.get("itemCd")]
                brands = recj.fetch_repbrands(session, [str(it["itemCd"]) for it in items])
                for it in items:
                    cd = str(it["itemCd"])
                    link = f"https://display.cjonstyle.com/p/item/{cd}"
                    if it.get("chnCd"):
                        link += f"?channelCode={it['chnCd']}"
                    products.append({
                        "broadcast_date_label": label,
                        "brand": brands.get(cd, "") or it.get("brandName") or "",
                        "name": it.get("itemNm", ""),
                        "price": it.get("salePrice"),
                        "image": recj.to_https(it.get("itemImgUrl") or it.get("imgUrl") or ""),
                        "link": link,
                        "from_schedule": True,
                    })
                found = True
                print(f"  [CJ] {label} ({broad}) '{pg.get('pgmNm')}' 상품 {len(items)}개")
            time.sleep(0.5)

        # API에서 못 찾은 날: 로컬 라이브 편성표에서 정규 편성 시각의 회차
        if found:
            continue
        starts = ([hm for wd, hm in live_slots if wd == d.weekday()]
                  + [hm for iso, hm in live_dates if iso == d.isoformat()])
        if not starts:
            continue
        ym = d.strftime("%Y-%m")
        if ym not in months:
            months[ym] = load_live_month("CJ", ym)
        for item in months[ym].get(d.isoformat()) or []:
            if item.get("start") not in starts:
                continue
            p = to_product(item)
            p["broadcast_date_label"] = label_cj(d, item["start"])
            products.append(p)
            print(f"  [CJ] {p['broadcast_date_label']} (CJ_live 폴백) "
                  f"[{p['brand']}] {p['name'][:30]}")
    return products


# ---------------- LT: 로컬 편성표 ----------------

def load_live_month(company: str, ym: str) -> dict:
    path = os.path.join("homeshopping", f"{company}_live", f"{ym}.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("days") or {}


def lt_products(names, start: date, end: date) -> list:
    products = []
    months = {}
    for d in date_range(start, end):
        ym = d.strftime("%Y-%m")
        if ym not in months:
            months[ym] = load_live_month("LT", ym)
        day_items = months[ym].get(d.isoformat()) or []

        segments = {}
        for item in day_items:
            if item.get("start") and title_matches(item.get("pgm"), names):
                segments.setdefault(item["start"], []).append(item)
        if not segments:
            continue

        def gap_is_empty(gap_start, gap_end):
            for item in day_items:
                s, e = item.get("start"), item.get("end") or item.get("start")
                if not s or title_matches(item.get("pgm"), names):
                    continue
                if to_minutes(s) < to_minutes(gap_end) and to_minutes(e) > to_minutes(gap_start):
                    return False
            return True

        # 이어지는 구간 묶기 (celeb_day_sweep.schedule_blocks와 같은 기준)
        blocks, prev_end = [], None
        for s in sorted(segments):
            end_hm = next((i.get("end") for i in segments[s] if i.get("end")), None)
            gap = None if prev_end is None else to_minutes(s) - to_minutes(prev_end)
            if blocks and gap is not None and (
                    gap <= SEGMENT_GAP_MIN
                    or (0 <= gap <= SEGMENT_GAP_EMPTY_MIN and gap_is_empty(prev_end, s))):
                blocks[-1].append((s, end_hm))
            else:
                blocks.append([(s, end_hm)])
            prev_end = end_hm or s

        for block in blocks:
            label = label_lt(d, block[0][0])
            n = 0
            for s, end_hm in block:
                for item in segments[s]:
                    p = to_product(item)
                    p["broadcast_date_label"] = label
                    if len(block) > 1:
                        p["segment_time"] = format_segment(s, end_hm)
                    products.append(p)
                    n += 1
            print(f"  [LT] {label} 상품 {n}개 (구간 {len(block)}개)")
    return products


# ---------------- history 병합 ----------------

def to_broadcasts(products: list, today: date) -> dict:
    now_iso = datetime.now(KST).isoformat()
    slots = {}
    for p in products:
        d, hm = bch.parse_label_date(p.get("broadcast_date_label"), today)
        if d and hm:
            slots.setdefault((d, hm), []).append(p)
    out = {}
    for (d, hm), items in slots.items():
        out[bch.broadcast_key(d.isoformat(), hm)] = {
            "date": d.isoformat(),
            "label": bch.make_broadcast_label(d, hm),
            "collected_at": now_iso,
            "products": items,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="history 파일에 실제 저장")
    ap.add_argument("--only", choices=sorted(TARGETS), help="한 프로그램만 처리")
    args = ap.parse_args()

    now = datetime.now(KST)
    today = now.date()
    yesterday = today - timedelta(days=1)

    months = {}   # ym -> program_key -> {slot_key: broadcast}
    for key, cfg in TARGETS.items():
        if args.only and key != args.only:
            continue
        since = date.fromisoformat(cfg["since"])
        print(f"\n===== {key} {cfg['names'][0]} ({since} ~ {yesterday}) =====")
        if cfg["company"] == "CJ":
            products = cj_products(cfg["names"], since, yesterday,
                                   cfg.get("live_slots", ()), cfg.get("live_dates", ()))
        else:
            products = lt_products(cfg["names"], since, yesterday)
        broadcasts = to_broadcasts(products, today)
        print(f"  -> 회차 {len(broadcasts)}개 / 상품 {len(products)}개")
        for slot_key, b in broadcasts.items():
            months.setdefault(b["date"][:7], {}).setdefault(key, {})[slot_key] = b

    for ym in sorted(months):
        path = os.path.join(bch.HISTORY_DIR, f"{ym}.json")
        existing = (bch.load_json(path) if os.path.isfile(path) else None) or {}
        existing["month"] = ym
        before = {p.get("program_key"): {bch.broadcast_key_of(b) for b in p.get("broadcasts") or []}
                  for p in existing.get("programs") or []}

        for key, new_broadcasts in months[ym].items():
            cfg = TARGETS[key]
            prog = next((p for p in existing.get("programs") or []
                         if p.get("program_key") == key), None)
            # 수집기가 이미 만든 항목이 있으면 그 메타(최신 편성 문구 등)를 그대로 둔다
            meta = ({k: v for k, v in prog.items() if k != "broadcasts"} if prog else
                    {"program_key": key, "company": cfg["company"], "program_image": "",
                     **cfg["meta"]})
            bch.merge_into_month(existing, key, meta, new_broadcasts, now)
            added = sorted(set(new_broadcasts) - before.get(key, set()))
            print(f"[{ym}] {key}: 새로 추가될 회차 {len(added)}개 {added}")

        order = {f[:-len(".json")]: i for i, f in enumerate(bch.SOURCE_FILES)}
        existing["programs"].sort(key=lambda p: order.get(p.get("program_key"), 99))
        if args.apply:
            existing["updated_at"] = now.isoformat()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            print(f"[저장] {path}")

    if not args.apply:
        print("\n(미리보기 - 저장하려면 --apply)")


if __name__ == "__main__":
    main()
