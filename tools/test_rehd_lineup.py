# -*- coding: utf-8 -*-
"""
rehd.py 편성표 훑기 테스트 (pytest 없이 그냥 실행:
python tools/test_rehd_lineup.py)

지키려는 것:
  (A) 같은 날 2회 방송하는 날의 회차를 전부 잡을 것
      -> 2026-09-08 오감쇼: 08:15 세포랩은 잡히는데 19:30 신세계푸드 원육이
         통째로 누락된 사고. pgm-comm이 '가장 가까운 회차'만 알려주는 걸
         그대로 시간대 필터로 쓴 게 원인이었다.
  (B) 같은 상품이 두 회차에 다 편성되면 회차별로 남을 것
      (세포랩은 08:15/19:30 양쪽에 있다 - 상품코드로만 중복 제거하면 사라진다)
  (C) 편성표에 프로그램명이 안 붙은 경우엔 예전처럼 시간대 필터로 폴백할 것
"""

import os
import sys
import types
import importlib.util
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# rehd는 실제 수집용이라 playwright/bs4를 import한다. 테스트는 순수 로직만
# 보므로 없으면 껍데기로 대체한다.
if importlib.util.find_spec("playwright") is None:
    fake = types.ModuleType("playwright")
    sync = types.ModuleType("playwright.sync_api")
    sync.sync_playwright = lambda: None
    sys.modules["playwright"] = fake
    sys.modules["playwright.sync_api"] = sync

_spec = importlib.util.spec_from_file_location(
    "_rehd", os.path.join(ROOT, "fixed", "rehd.py"))
rehd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rehd)

FAILURES = []


def check(label, got, want):
    if got == want:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


def tv_item(start, end, title, code, brand, name):
    return {"brodStrtDtm": start, "brodEndDtm": end, "brodTitl": title,
            "slitmCd": code, "brndNm": brand, "slitmNm": name,
            "sellPrc": 10000, "orglImgNm": f"{code}_0.jpg"}


# 2026-09-08 실제 편성 (오감쇼 08:15 / 19:30 2회 + 다른 방송)
DAY_0908 = [
    tv_item("08:15", "09:25", "오감쇼", "2253069033", "세포랩", "세포랩 에센스 오오 패키지"),
    tv_item("11:20", "12:20", "Club Noblesse", "111", "다른브랜드", "다른상품"),
    tv_item("19:30", "21:45", "오감쇼", "2253069033", "세포랩", "세포랩 에센스 오오 패키지"),
    tv_item("19:30", "21:45", "오감쇼", "2262058812", "신세계푸드", "신세계푸드 호주산 LA갈비 꽃갈비 원육"),
]


def run_collect(day_items, program_names, brod_start, brod_end, local_entries=()):
    """fetch_day_items / load_local_day_entries를 갈아끼우고 라인업 수집을 돌린다."""
    orig_fetch = rehd.fetch_day_items
    orig_local = rehd.load_local_day_entries
    rehd.fetch_day_items = lambda brod_dt: list(day_items)
    rehd.load_local_day_entries = lambda brod_date: list(local_entries)
    try:
        return rehd.collect_lineup_products(
            date(2026, 9, 8), program_names, brod_start, brod_end)
    finally:
        rehd.fetch_day_items = orig_fetch
        rehd.load_local_day_entries = orig_local


def test_two_broadcasts_same_day():
    print("[1] 같은 날 2회 방송(2026-09-08 오감쇼)을 전부 훑는다")
    # pgm-comm은 가장 가까운 회차(08:15)만 알려준다 - 그래도 저녁 회차가 잡혀야 한다.
    products = run_collect(DAY_0908, ["오감쇼", "오감쇼"], "08:15", "09:25")
    labels = sorted({p["broadcast_date_label"] for p in products})
    check("회차 2개 다 수집", labels,
          ["09/08(화) 08:15 방송", "09/08(화) 19:30 방송"])
    names = [p["name"] for p in products if "19:30" in p["broadcast_date_label"]]
    check("저녁 회차 신세계푸드 원육 포함",
          any("신세계푸드" in (n or "") for n in names), True)
    check("다른 프로그램 방송은 안 섞임",
          any("다른상품" == p["name"] for p in products), False)


def test_same_product_in_both_slots():
    print("[2] 두 회차에 다 편성된 상품은 회차별로 남는다")
    products = run_collect(DAY_0908, ["오감쇼"], "08:15", "09:25")
    merged = rehd.merge_sources(products, [], [], [])
    sepolab = [p for p in merged if "세포랩" in (p["name"] or "")]
    check("세포랩이 회차별로 2건", len(sepolab), 2)
    check("총 3건(아침1 + 저녁2)", len(merged), 3)


def test_withitemlist_expanded():
    print("[3] withItemList(함께 방송하는 상품)도 펼쳐서 담는다")
    parent = tv_item("19:30", "21:45", "오감쇼", "1", "세포랩", "대표상품")
    parent["withItemList"] = [
        {"slitmCd": "2", "brndNm": "신세계푸드", "slitmNm": "원육"},  # 시각/제목 없음
    ]

    captured = {}

    def fake_json(url, **kwargs):
        # 페이지 0에만 데이터가 있는 척한다
        captured["called"] = captured.get("called", 0) + 1
        if "brodPrrgPage=0" in url:
            return {"respData": {"broadItemList": [parent]}}
        return {"respData": {"broadItemList": []}}

    orig = rehd.scrape_guard.fetch_json
    rehd.scrape_guard.fetch_json = fake_json
    try:
        items = rehd.fetch_day_items("20260908")
    finally:
        rehd.scrape_guard.fetch_json = orig

    codes = sorted(str(i.get("slitmCd")) for i in items)
    check("대표상품 + 서브상품 둘 다", codes, ["1", "2"])
    sub = next(i for i in items if i["slitmCd"] == "2")
    check("서브상품이 부모 방송시각을 물려받음", sub["brodStrtDtm"], "19:30")
    check("서브상품이 부모 방송제목을 물려받음", sub["brodTitl"], "오감쇼")


def test_fallback_to_time_window():
    print("[4] 편성표에 프로그램명이 없으면 시간대 필터로 폴백")
    nameless = [dict(it, brodTitl="") for it in DAY_0908]
    products = run_collect(nameless, ["오감쇼"], "19:30", "21:45")
    labels = sorted({p["broadcast_date_label"] for p in products})
    check("pgm-comm이 알려준 시간대만 수집", labels, ["09/08(화) 19:30 방송"])
    check("그 시간대 상품 2건", len(products), 2)


def test_local_schedule_supplement():
    print("[5] 로컬 편성(HD_live)도 하루치를 훑어 보충한다")
    local = [
        ("08:15", "09:25", "오감쇼",
         {"broadcast_date_label": None, "brand": "세포랩", "name": "세포랩 에센스",
          "price": 1, "image": None, "link": None, "_code": "2253069033"}),
        ("19:30", "21:45", "오감쇼",
         {"broadcast_date_label": None, "brand": "신세계푸드", "name": "원육",
          "price": 2, "image": None, "link": None, "_code": "2262058812"}),
    ]
    # tv-list가 아직 라인업을 안 열어준 상황(빈 응답)
    products = run_collect([], ["오감쇼"], "08:15", "09:25", local_entries=local)
    labels = sorted(p["broadcast_date_label"] for p in products)
    check("로컬 편성에서 회차 2개 보충", labels,
          ["09/08(화) 08:15 방송", "09/08(화) 19:30 방송"])


def main():
    test_two_broadcasts_same_day()
    test_same_product_in_both_slots()
    test_withitemlist_expanded()
    test_fallback_to_time_window()
    test_local_schedule_supplement()

    print()
    if FAILURES:
        print(f"실패 {len(FAILURES)}건: {', '.join(FAILURES)}")
        return 1
    print("모든 테스트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
