# -*- coding: utf-8 -*-
"""
build_celeb_history 누적 규칙 테스트 (pytest 없이 그냥 실행:
python tools/test_build_celeb_history.py)

지키려는 것은 서로 반대 방향의 두 가지다.
  (A) 방송 직전/직후에 빠지거나 코드가 바뀐 상품이 확정 기록에 반영될 것
      -> 2026-08-31 강주은 굿라이프 '농협 영암 무화과' 사고
  (B) 방송이 끝난 뒤의 잔여 노출이 확정 기록을 덮어쓰지 않을 것
      -> 2026-08-22 왕영은의 톡투게더 사고
둘 중 하나만 만족시키는 수정은 반대쪽 사고를 되살린다. 규칙을 손볼 땐
이 테스트를 먼저 돌려본다.
"""

import os
import sys
import importlib.util
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "_celeb_history", os.path.join(ROOT, "fixed", "build_celeb_history.py"))
bch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bch)

KST = timezone(timedelta(hours=9))
FAILURES = []


def check(label, got, want):
    if got == want:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=KST)


def product(name, code):
    return {"broadcast_date_label": "08/31(월) 19:35", "name": name,
            "link": f"https://item.cjonstyle.com/item/{code}?channelCode=30002002"}


def broadcast(label, products, **extra):
    d = {"date": "2026-08-31", "label": label,
         "collected_at": "2026-08-31T20:10:00+09:00", "products": products}
    d.update(extra)
    return d


# ---------------------------------------------------------------- phase
def test_phase():
    print("[1] 회차 수명 단계 (before / reconcile / final)")
    hints = ("08/31(월) 19:35 방송", "매주 월요일 19시35분")
    cases = [
        ("방송 전날",        "2026-08-30T22:00:00", "before"),
        ("방송 당일 아침",   "2026-08-31T10:22:00", "before"),
        ("방송 1분 전",      "2026-08-31T19:34:00", "before"),
        ("방송 중",          "2026-08-31T20:10:00", "reconcile"),
        ("방송 직후",        "2026-08-31T22:00:00", "reconcile"),
        ("다음날 새벽 03시", "2026-09-01T03:00:00", "reconcile"),
        ("정정 창 종료 후",  "2026-09-01T09:00:00", "final"),
        ("한참 뒤",          "2026-09-05T09:00:00", "final"),
    ]
    for label, now, want in cases:
        check(label, bch.broadcast_phase("2026-08-31", at(now), *hints), want)

    # 시각을 어디서도 못 읽으면 보존 우선 (오늘 이전이면 바로 확정)
    check("시각 불명 + 지난 날짜",
          bch.broadcast_phase("2026-08-31", at("2026-08-31T23:00:00"), "8/31(월) 방송상품"),
          "final")
    check("시각 불명 + 미래 날짜",
          bch.broadcast_phase("2026-09-30", at("2026-08-31T23:00:00"), "9/30(수) 방송상품"),
          "before")

    # check_scrape_health.py가 쓰는 두 파생 판정
    check("already_started(방송 중)",
          bch.already_started("2026-08-31", at("2026-08-31T20:10:00"), *hints), True)
    check("record_is_final(방송 중) - 정정 창이라 아직 확정 아님",
          bch.record_is_final("2026-08-31", at("2026-08-31T20:10:00"), *hints), False)
    check("record_is_final(창 종료 후)",
          bch.record_is_final("2026-08-31", at("2026-09-01T09:00:00"), *hints), True)


# ------------------------------------------------------------ diff/정정
def test_reconcile_removal():
    """(A) 2026-08-31 강주은 굿라이프 재현: 방송 직전에 빠진 상품이 제거돼야 한다."""
    print("[2] 정정 창 안의 '상품 제외' 반영 (2026-08-31 무화과 사고)")
    kept = broadcast("08/31(월) 19:35 방송", [
        product("올트라이탄 데일리패키지 16종", "2091642625"),
        product("올트라이탄 대용량패키지 6종", "2091649594"),
        product("도투락 야생 빌베리 퓨레 100% 8박스", "2091508600"),
        product("도투락 야생 빌베리 퓨레 100% 1박스", "2089942607"),
        product("유러피안데일리베지믹스 230g x 16봉", "2091538799"),
        product("유러피안데일리베지믹스 230g x 32봉", "2092274747"),
        product("영암 햇 생 무화과 500g x 4팩 총 2kg", "2091300670"),  # <- 방송 29분 전 제외
        product("유러피안데일리베지믹스 230g x 12봉", "2091320090"),
    ])
    new = broadcast("08/31(월) 19:35 방송",
                    [p for p in kept["products"] if "무화과" not in p["name"]])

    merged, note = bch.reconcile_broadcast(kept, new, "2026-08-31T20:10:00+09:00")
    check("정정이 적용됨", merged is not None, True)
    check("건수 8 -> 7", len(merged["products"]), 7)
    check("무화과가 빠짐",
          any("무화과" in p["name"] for p in merged["products"]), False)
    check("정정 이력에 제외 상품명이 남음",
          merged["revisions"][-1]["removed"], ["영암 햇 생 무화과 500g x 4팩 총 2kg"])
    check("reconciled_at 기록됨", merged["reconciled_at"], "2026-08-31T20:10:00+09:00")


def test_reconcile_code_change():
    print("[3] 정정 창 안의 '상품코드 변경' 반영")
    kept = broadcast("08/31(월) 19:35 방송", [
        product("올트라이탄 데일리패키지 16종", "2091642625"),
        product("도투락 야생 빌베리 퓨레 100% 8박스", "2091508600"),
    ])
    new = broadcast("08/31(월) 19:35 방송", [
        product("올트라이탄 데일리패키지 16종", "2091642625"),
        product("도투락 야생 빌베리 퓨레 100% 8박스", "2099999999"),  # 코드만 교체
    ])

    merged, note = bch.reconcile_broadcast(kept, new, "2026-08-31T20:10:00+09:00")
    check("정정이 적용됨", merged is not None, True)
    check("건수 유지 (제외+추가로 세지 않음)", len(merged["products"]), 2)
    check("코드 변경으로 기록됨", merged["revisions"][-1]["code_changed"],
          [{"name": "도투락 야생 빌베리 퓨레 100% 8박스",
            "from": "2091508600", "to": "2099999999"}])
    check("removed/added 로는 안 잡힘",
          ("removed" in merged["revisions"][-1] or "added" in merged["revisions"][-1]), False)


def test_gate_untimed_label():
    """(B) 왕영은 사고: 시각 없는 잔여 라벨은 확정 기록을 못 건드린다."""
    print("[4] 게이트1 - 시각 없는 잔여 노출 거부 (2026-08-22 왕톡 사고)")
    kept = broadcast("08/22(토) 08:20 방송",
                     [product("A", "1"), product("B", "2"), product("C", "3")])
    new = broadcast("08/22(토) 방송", [product("다음 회차 잔여상품", "9")])

    merged, note = bch.reconcile_broadcast(kept, new, "2026-08-22T20:24:00+09:00")
    check("정정 거부", merged, None)
    check("사유가 남음", bool(note), True)


def test_gate_retention():
    print("[5] 게이트2 - 절반 넘게 사라지면 정정 거부")
    kept = broadcast("08/31(월) 19:35 방송",
                     [product(f"상품{i}", str(i)) for i in range(8)])
    new = broadcast("08/31(월) 19:35 방송", [product("상품0", "0")])

    merged, note = bch.reconcile_broadcast(kept, new, "2026-08-31T20:10:00+09:00")
    check("정정 거부", merged, None)
    check("사유에 건수가 남음", "8건 중 1건" in note, True)

    # 8건 중 5건 남음(62.5%) -> 임계 위라 정상 정정
    new_ok = broadcast("08/31(월) 19:35 방송",
                       [product(f"상품{i}", str(i)) for i in range(5)])
    merged_ok, _ = bch.reconcile_broadcast(kept, new_ok, "2026-08-31T20:10:00+09:00")
    check("임계 위면 정정 적용", merged_ok is not None, True)


def test_merge_into_month():
    """merge_into_month가 단계별로 교체/정정/보존을 고르는지."""
    print("[6] merge_into_month 단계별 동작")
    meta = {"program_key": "CJ_KJE", "schedule_raw": "매주 월요일 19시35분"}
    kept_products = [product("올트라이탄", "1"), product("빌베리", "2"),
                     product("베지믹스", "3"), product("무화과", "4")]
    new_products = [p for p in kept_products if p["name"] != "무화과"]

    def run(now_text):
        existing = {"programs": [{**meta, "broadcasts": [
            broadcast("08/31(월) 19:35 방송", [dict(p) for p in kept_products])]}]}
        bch.merge_into_month(
            existing, "CJ_KJE", meta,
            {bch.broadcast_key("2026-08-31", "19:35"):
                broadcast("08/31(월) 19:35 방송", [dict(p) for p in new_products])},
            at(now_text))
        return existing["programs"][0]["broadcasts"][0]

    before = run("2026-08-31T10:22:00")
    check("before - 최신 수집분으로 교체", len(before["products"]), 3)
    check("before - 정정 이력은 안 남김", "revisions" in before, False)

    mid = run("2026-08-31T20:10:00")
    check("reconcile - 제외 반영", len(mid["products"]), 3)
    check("reconcile - 정정 이력 남김", mid["revisions"][-1]["removed"], ["무화과"])

    final = run("2026-09-02T09:00:00")
    check("final - 확정 기록 보존", len(final["products"]), 4)

    # 새 회차는 그대로 추가돼야 한다
    existing = {"programs": [{**meta, "broadcasts": []}]}
    bch.merge_into_month(existing, "CJ_KJE", meta,
                         {bch.broadcast_key("2026-08-31", "19:35"):
                          broadcast("08/31(월) 19:35 방송", new_products)},
                         at("2026-09-02T09:00:00"))
    check("기존 기록이 없으면 그냥 추가",
          len(existing["programs"][0]["broadcasts"][0]["products"]), 3)


def test_same_day_two_broadcasts():
    """하루 2회 방송(2026-09-08 오감쇼 08:15 / 19:30)이 회차별로 나뉘는지.

    날짜만으로 묶던 옛 방식에선 저녁 회차 상품이 아침 회차 기록에 섞였다."""
    print("[7] 같은 날 2회 방송은 회차별로 분리된다")

    def hd_product(name, label):
        return {"broadcast_date_label": label, "name": name,
                "link": "https://www.hmall.com/md/pda/itemPtc?slitmCd=1"}

    src = {
        "company": "HD", "tab_name": "오감쇼", "program_title": "오감쇼",
        "products": [
            hd_product("세포랩 에센스", "09/08(화) 08:15 방송"),
            hd_product("신세계푸드 원육", "09/08(화) 19:30 방송"),
            hd_product("세포랩 에센스(저녁)", "09/08(화) 19:30 방송"),
            hd_product("알리미 카드 상품", "9/8(화) 방송상품"),  # 시각 없는 잔여 라벨
        ],
    }

    import json
    import tempfile
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "HD_OGS.json"), "w", encoding="utf-8") as f:
        json.dump(src, f, ensure_ascii=False)

    src_dir_backup = bch.SRC_DIR
    bch.SRC_DIR = tmp
    try:
        collected = bch.collect_current_broadcasts(at("2026-09-04T03:00:00").date())
    finally:
        bch.SRC_DIR = src_dir_backup

    broadcasts = collected["HD_OGS"]["broadcasts"]
    check("회차 2개로 분리", sorted(broadcasts),
          ["2026-09-08#08:15", "2026-09-08#19:30"])
    morning = broadcasts["2026-09-08#08:15"]
    evening = broadcasts["2026-09-08#19:30"]
    check("아침 회차 라벨", morning["label"], "09/08(화) 08:15 방송")
    check("저녁 회차 라벨", evening["label"], "09/08(화) 19:30 방송")
    check("저녁 회차 상품 2건", len(evening["products"]), 2)
    # 시각 없는 라벨(잔여/알리미 카드)은 그 날 첫 회차에 붙는다
    check("시각 없는 상품은 첫 회차로", len(morning["products"]), 2)


def test_absorbed_slot_removed():
    """회차 병합으로 흡수된 옛 회차만 지우고, 수집이 놓친 회차는 지키는지.

    - 최유라쇼가 한 방송을 구간별로 쪼개 주던 걸(09/05 08:20/09:20/10:20)
      한 회차(08:20)로 묶으면서 옛 09:20/10:20이 유령으로 남았다 -> 제거
    - 반대로 2026-09-08 오감쇼 08:15 기록은, 편성표 brodTitl이 비어 오는
      바람에 수집이 그 회차를 놓친 것뿐인데 지워졌다 -> 보존해야 한다
    """
    print("[8] 병합으로 흡수된 회차만 지우고, 놓친 회차는 지킨다")
    meta = {"program_key": "LT_CYR", "schedule_raw": "매주 토 08시 20분"}

    def three_slots():
        return [
            {"date": "2026-09-05", "label": "09/05 토요일 08:20",
             "collected_at": "2026-09-04T03:00:00+09:00", "products": [product("A", "1")]},
            {"date": "2026-09-05", "label": "09/05 토요일 09:20",
             "collected_at": "2026-09-04T03:00:00+09:00", "products": [product("B", "2")]},
            {"date": "2026-09-05", "label": "09/05 토요일 10:20",
             "collected_at": "2026-09-04T03:00:00+09:00", "products": [product("C", "3")]},
        ]

    def seg(p, segment_time):
        return {**p, "segment_time": segment_time}

    merged_new = {bch.broadcast_key("2026-09-05", "08:20"): {
        "date": "2026-09-05", "label": "09/05 토요일 08:20",
        "collected_at": "2026-09-04T04:00:00+09:00",
        "products": [seg(product("A", "1"), "08:20-09:20(60')"),
                     seg(product("B", "2"), "09:20-10:20(60')"),
                     seg(product("C", "3"), "10:20-10:35(15')")]}}

    existing = {"programs": [{**meta, "broadcasts": three_slots()}]}
    bch.merge_into_month(existing, "LT_CYR", meta, merged_new, at("2026-09-04T04:00:00"))
    broadcasts = existing["programs"][0]["broadcasts"]
    check("흡수된 회차 정리 -> 1개", len(broadcasts), 1)
    check("상품 3건 유지", len(broadcasts[0]["products"]), 3)

    # 확정 회차는 흡수 대상이어도 안 지운다
    existing2 = {"programs": [{**meta, "broadcasts": three_slots()}]}
    bch.merge_into_month(existing2, "LT_CYR", meta, merged_new, at("2026-09-07T04:00:00"))
    check("확정 회차는 보존", len(existing2["programs"][0]["broadcasts"]), 3)

    # 수집이 놓친 회차(구간 밖)는 지키기 - 2026-09-08 오감쇼 08:15 사고
    hd_meta = {"program_key": "HD_OGS", "schedule_raw": "매주 화요일 19시 30분"}
    hd_existing = {"programs": [{**hd_meta, "broadcasts": [
        {"date": "2026-09-08", "label": "09/08(화) 08:15 방송",
         "collected_at": "2026-09-04T03:00:00+09:00", "products": [product("아침", "9")]}]}]}
    hd_new = {bch.broadcast_key("2026-09-08", "19:30"): {
        "date": "2026-09-08", "label": "09/08(화) 19:30 방송",
        "collected_at": "2026-09-04T20:00:00+09:00", "products": [product("저녁", "8")]}}
    bch.merge_into_month(hd_existing, "HD_OGS", hd_meta, hd_new, at("2026-09-04T20:00:00"))
    labels = sorted(b["label"] for b in hd_existing["programs"][0]["broadcasts"])
    check("수집이 놓친 아침 회차 보존", labels,
          ["09/08(화) 08:15 방송", "09/08(화) 19:30 방송"])


def main():
    test_phase()
    test_reconcile_removal()
    test_reconcile_code_change()
    test_gate_untimed_label()
    test_gate_retention()
    test_merge_into_month()
    test_same_day_two_broadcasts()
    test_absorbed_slot_removed()

    print()
    if FAILURES:
        print(f"실패 {len(FAILURES)}건: {FAILURES}")
        return 1
    print("모든 테스트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
