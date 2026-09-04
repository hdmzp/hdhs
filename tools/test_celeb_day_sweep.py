# -*- coding: utf-8 -*-
"""
celeb_day_sweep 테스트 (pytest 없이 그냥 실행:
python tools/test_celeb_day_sweep.py)

지키려는 것:
  (A) 하루 2회 방송하는 날의 빠진 회차를 편성표로 채울 것
      -> 2026-09-08 오감쇼(08:15/19:30) 사고를 다른 3사에도 안 나게 하는 안전망
  (B) 이미 수집된 회차는 절대 안 건드릴 것
      (편성표 상품명은 화면용으로 정제돼 있어 섞으면 같은 상품이 두 줄로 보인다)
  (C) 스크래퍼가 통째로 실패한 프로그램은 편성표로 덮지 말 것
      (덮으면 건전성 검사가 '조용한 실패'를 못 잡는다)
  (D) 지난 회차는 새로 만들지 말 것 (확정 기록의 영역)
  (E) 회사별 방송회차 라벨 표기를 그대로 따라갈 것
      (라벨의 HH:MM으로 회차를 특정하는 누적 규칙이 여기 의존한다)
  (F) 롯데처럼 한 방송이 구간별로 쪼개져 오면 한 회차로 묶을 것
      -> 2026-09-05 최유라쇼 08:20/09:20/10:20 = 이어지는 한 방송
      단, 하루 2회 방송(오감쇼 08:15/19:30)은 묶지 말 것
"""

import os
import sys
import json
import shutil
import tempfile
import importlib.util
from datetime import datetime, date, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "_celeb_day_sweep", os.path.join(ROOT, "fixed", "celeb_day_sweep.py"))
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
FAILURES = []


def check(label, got, want):
    if got == want:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


def slot(start, end, pgm, brand, product, price=1000):
    """편성표 한 줄 ({사}_live의 days[날짜][] 스키마)."""
    return {"start": start, "end": end, "pgm": pgm, "brand": brand,
            "product": product, "price": price,
            "link": "https://example.com/goods/1"}


def with_live_data(company, days, fn):
    """{사}_live/{YYYY-MM}.json을 임시 디렉토리에 만들어 두고 fn을 실행한다."""
    tmp = tempfile.mkdtemp()
    try:
        by_month = {}
        for day, items in days.items():
            by_month.setdefault(day[:7], {})[day] = items
        for ym, month_days in by_month.items():
            path = os.path.join(tmp, f"{company}_live", f"{ym}.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"company": company, "days": month_days}, f, ensure_ascii=False)

        original = sweep.LIVE_DIR_TEMPLATE
        sweep.LIVE_DIR_TEMPLATE = os.path.join(tmp, "{company}_live", "{ym}.json")
        try:
            return fn()
        finally:
            sweep.LIVE_DIR_TEMPLATE = original
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def collected(label, name="이미 수집된 상품"):
    return {"broadcast_date_label": label, "brand": "브랜드", "name": name,
            "price": 1000, "link": "https://example.com/x"}


def test_fills_missing_slot():
    print("[1] 같은 날 빠진 회차를 편성표로 채운다")
    day = TODAY + timedelta(days=2)
    days = {day.isoformat(): [
        slot("08:20", "09:20", "최유라쇼", "고트만", "밀폐용기 세트"),
        slot("20:45", "21:45", "최유라쇼", "신세계푸드", "LA갈비 원육"),
        slot("13:00", "14:00", "다른쇼", "다른브랜드", "남의 상품"),
    ]}
    products = [collected(sweep.label_lt(day, "08:20"))]

    added = with_live_data("LT", days, lambda: sweep.supplement_missing_slots(
        "LT", ["최유라쇼", "최유라"], products))

    check("추가된 상품 1건", added, 1)
    check("빠진 회차 라벨", products[-1]["broadcast_date_label"],
          sweep.label_lt(day, "20:45"))
    check("빠진 회차 상품명", products[-1]["name"], "LA갈비 원육")
    check("다른 프로그램은 안 섞임",
          any("남의" in (p["name"] or "") for p in products), False)


def test_keeps_collected_slot():
    print("[2] 이미 수집된 회차는 안 건드린다")
    day = TODAY + timedelta(days=1)
    days = {day.isoformat(): [
        slot("20:35", "21:35", "소유진쇼", "GS", "편성표에만 있는 표기"),
    ]}
    products = [collected(sweep.label_gs(day, "20:35"))]

    added = with_live_data("GS", days, lambda: sweep.supplement_missing_slots(
        "GS", ["소유진쇼", "소유진"], products))

    check("추가 없음", added, 0)
    check("수집분 그대로", len(products), 1)


def test_skips_empty_scrape():
    print("[3] 스크래퍼가 통째로 실패한 프로그램은 안 덮는다")
    day = TODAY + timedelta(days=1)
    days = {day.isoformat(): [slot("20:45", "21:45", "최화정쇼", "라이프밀", "동결건조칩")]}
    products = []

    added = with_live_data("CJ", days, lambda: sweep.supplement_missing_slots(
        "CJ", ["최화정쇼"], products))

    check("추가 없음(빈 수집분 보호)", added, 0)
    check("여전히 0건", len(products), 0)


def test_skips_started_slot_today():
    """오늘 이미 시작한 회차를 채우면 안 된다 (2026-09-04 소유진쇼 사고).

    방송이 시작되면 상세페이지가 그 회차를 목록에서 빼는데, 편성표는
    슬롯당 대표상품 1개뿐이라 '빠진 회차'로 보고 채우면 20건짜리 회차가
    1건으로 쪼그라든다. 실제로 방송 12분 뒤 수집에서 급감 경보가 났다."""
    print("[4-1] 오늘이라도 이미 시작한 회차는 안 채운다")
    now = datetime.now(KST)
    started = f"{max(now.hour - 1, 0):02d}:00"
    upcoming = f"{min(now.hour + 2, 23):02d}:00"
    days = {TODAY.isoformat(): [
        slot(started, "23:00", "소유진쇼", "바삭", "김부각(편성표 대표상품)"),
        slot(upcoming, "23:59", "소유진쇼", "올록담", "올리브오일"),
    ]}
    products = [collected(sweep.label_gs(TODAY, "23:59"), "다른 회차 상품")]

    added = with_live_data("GS", days, lambda: sweep.supplement_missing_slots(
        "GS", ["소유진쇼"], products))

    names = [p["name"] for p in products]
    check("시작한 회차는 안 채움",
          any("김부각" in n for n in names), False)
    # 아직 시작 안 한 회차는 정상적으로 채운다 (시간대 경계에서만 유효한 검사)
    if int(upcoming[:2]) > now.hour:
        check("남은 회차는 채움", added, 1)


def test_skips_past_slot():
    print("[4] 지난 회차는 새로 만들지 않는다")
    past = TODAY - timedelta(days=1)
    future = TODAY + timedelta(days=1)
    days = {
        past.isoformat(): [slot("19:35", "20:35", "굿 라이프", "다이슨", "지난 방송 상품")],
        future.isoformat(): [slot("19:35", "20:35", "굿 라이프", "라이프밀", "다음 방송 상품")],
    }
    products = [collected(sweep.label_cj(TODAY, "19:35"))]

    added = with_live_data("CJ", days, lambda: sweep.supplement_missing_slots(
        "CJ", ["강주은 굿라이프", "강주은"], products))

    check("미래 회차만 추가", added, 1)
    check("추가된 게 다음 방송분", products[-1]["name"], "다음 방송 상품")


def test_label_formats():
    print("[5] 회사별 방송회차 라벨 표기")
    d = date(2026, 9, 8)  # 화요일
    check("HD", sweep.label_hd(d, "19:30"), "09/08(화) 19:30 방송")
    check("GS", sweep.label_gs(d, "20:35"), "9월 8일(화) 20:35 방송")
    check("LT", sweep.label_lt(d, "08:20"), "09/08 화요일 08:20")
    check("CJ", sweep.label_cj(d, "19:35"), "09/08(화) 19:35")
    # 라벨 -> (날짜, 시각) 왕복이 되어야 회차 대조가 성립한다
    for fn in (sweep.label_hd, sweep.label_gs, sweep.label_lt, sweep.label_cj):
        parsed = sweep.parse_label(fn(d, "19:30"), d)
        check(f"왕복 파싱 {fn.__name__}", parsed, (d, "19:30"))


def test_title_matching():
    print("[6] 프로그램명 대조(표기 흔들림 허용)")
    check("공백 차이", sweep.title_matches("왕영은의 톡 투게더", ["왕영은의 톡투게더"]), True)
    check("부분 표기", sweep.title_matches("굿 라이프", ["강주은 굿라이프", "강주은"]), True)
    check("다른 프로그램", sweep.title_matches("아쇼라", ["오감쇼"]), False)
    check("빈 값", sweep.title_matches("", ["오감쇼"]), False)


def test_merge_continuous_segments():
    print("[7] 이어지는 구간(롯데식 구간 쪼개기)은 한 회차로 묶는다")
    day = TODAY + timedelta(days=2)
    days = {day.isoformat(): [
        slot("08:20", "09:20", "최유라쇼", "고트만", "밀폐용기"),
        slot("09:20", "10:20", "최유라쇼", "에피큐리언", "도마 세트"),
        slot("10:20", "10:35", "최유라쇼", "솔닙", "잣 세트"),
    ]}
    products = [collected(sweep.label_lt(day, hm), f"{hm} 상품")
                for hm in ("08:20", "09:20", "10:20")]

    changed = with_live_data("LT", days, lambda: sweep.merge_continuous_slots(
        "LT", ["최유라쇼"], products))

    check("라벨 바뀐 상품 2건", changed, 2)
    check("모두 첫 구간 회차로",
          {p["broadcast_date_label"] for p in products},
          {sweep.label_lt(day, "08:20")})
    check("상품은 하나도 안 잃음", len(products), 3)
    # 묶었어도 어느 구간에 나온 상품인지는 남긴다
    check("구간 시간 표기", [p.get("segment_time") for p in products],
          ["08:20-09:20(60')", "09:20-10:20(60')", "10:20-10:35(15')"])


def test_no_segment_time_for_single_slot():
    print("[7-1] 구간이 하나뿐인 방송엔 구간 시간을 안 붙인다")
    day = TODAY + timedelta(days=2)
    days = {day.isoformat(): [
        slot("08:15", "09:25", "오감쇼", "세포랩", "에센스"),
        slot("19:30", "21:45", "오감쇼", "신세계푸드", "원육"),
    ]}
    products = [collected(sweep.label_hd(day, "08:15"), "아침"),
                collected(sweep.label_hd(day, "19:30"), "저녁")]

    with_live_data("HD", days, lambda: sweep.merge_continuous_slots(
        "HD", ["오감쇼"], products))

    check("회차 라벨과 같은 말이라 생략",
          [p.get("segment_time") for p in products], [None, None])


def test_does_not_merge_two_broadcasts():
    print("[8] 하루 2회 방송(오감쇼 08:15/19:30)은 안 묶는다")
    day = TODAY + timedelta(days=2)
    days = {day.isoformat(): [
        slot("08:15", "09:25", "오감쇼", "세포랩", "에센스"),
        slot("19:30", "21:45", "오감쇼", "신세계푸드", "원육"),
    ]}
    products = [collected(sweep.label_hd(day, "08:15"), "아침 상품"),
                collected(sweep.label_hd(day, "19:30"), "저녁 상품")]

    changed = with_live_data("HD", days, lambda: sweep.merge_continuous_slots(
        "HD", ["오감쇼"], products))

    check("병합 없음", changed, 0)
    check("회차 2개 유지",
          sorted({p["broadcast_date_label"] for p in products}),
          [sweep.label_hd(day, "08:15"), sweep.label_hd(day, "19:30")])


def test_merge_fallback_without_schedule():
    print("[9] 편성표에 아직 없는 날은 시작시각 간격으로 잠정 병합")
    # 편성표는 오늘~+5일뿐이라 먼 미래 방송은 근거가 없다.
    far = TODAY + timedelta(days=8)
    products = [collected(sweep.label_lt(far, "19:35"), "1구간"),
                collected(sweep.label_lt(far, "21:45"), "2구간")]  # 130분 간격

    changed = with_live_data("LT", {}, lambda: sweep.merge_continuous_slots(
        "LT", ["최유라쇼"], products))

    check("간격 130분은 한 방송", changed, 1)
    check("첫 구간 회차로 통일",
          {p["broadcast_date_label"] for p in products},
          {sweep.label_lt(far, "19:35")})
    # 종료시각을 모르니 다음 구간 시작을 끝으로 보고, 마지막 구간은 미상으로 둔다
    check("폴백 구간 표기", [p.get("segment_time") for p in products],
          ["19:35-21:45(130')", "21:45~"])

    # 하루 2회 방송 간격(675분)은 폴백에서도 안 묶인다
    apart = [collected(sweep.label_hd(far, "08:15"), "아침"),
             collected(sweep.label_hd(far, "19:30"), "저녁")]
    changed2 = with_live_data("HD", {}, lambda: sweep.merge_continuous_slots(
        "HD", ["오감쇼"], apart))
    check("멀리 떨어진 회차는 폴백에서도 분리", changed2, 0)


def main():
    test_fills_missing_slot()
    test_keeps_collected_slot()
    test_skips_empty_scrape()
    test_skips_started_slot_today()
    test_skips_past_slot()
    test_label_formats()
    test_title_matching()
    test_merge_continuous_segments()
    test_no_segment_time_for_single_slot()
    test_does_not_merge_two_broadcasts()
    test_merge_fallback_without_schedule()

    print()
    if FAILURES:
        print(f"실패 {len(FAILURES)}건: {', '.join(FAILURES)}")
        return 1
    print("모든 테스트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
