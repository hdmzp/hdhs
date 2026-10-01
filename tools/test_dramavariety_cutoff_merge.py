# -*- coding: utf-8 -*-
"""
드라마/예능 수집기 — 컷오프 경계 프로그램의 요일별 시청률 보존 테스트
(pytest 없이 그냥 실행: PYTHONPATH=<bs4/playwright 경로> python tools/test_dramavariety_cutoff_merge.py)

지키려는 것:
  (A) 표에 실린 프로그램의 다른 회차가 컷오프 미만으로 나와도 그 요일 값은 표 항목에 남을 것
      (표에는 안 보이지만 다음 주 전주 대비 증감 계산에 쓰인다)
  (B) 주초 회차가 컷오프 미만이었다가 나중 회차로 컷오프를 넘으면 앞선 회차 값을 함께 가져올 것
      -> 2026-09-28 주차 '로또 1등도 출근합니다': 월 3.5%(버려짐) / 화 5.0%만 남았던 사고
  (C) 컷오프 미만 관측치는 신규·종영이 아니어도 기록으로 남길 것 ((B)와 전주 대비 계산의 전제)
      표에 오른 프로그램의 기준 미만 기록은 표 항목에 합친 뒤 목록에서 뺄 것
"""

import os
import sys
import json
import shutil
import tempfile
import importlib.util
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scraper"))   # scrape_naver가 nielsen_backfill을 import

_spec = importlib.util.spec_from_file_location(
    "_scrape_naver", os.path.join(ROOT, "scraper", "scrape_naver.py"))
sn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sn)

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
# 확실히 지난(닫힌) 주차의 월요일. ratingDate("MM.DD")는 오늘 기준으로
# 가장 가까운 과거 날짜로 해석되므로 과거 주차를 써야 결과가 안정적이다.
MON = sn.monday_of(TODAY - timedelta(days=21))
FAILURES = []


def check(label, got, want):
    if got == want:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


def md(d):
    return f"{d.month:02d}.{d.day:02d}"


def lotto(rating, rating_date, day):
    """'로또 1등도 출근합니다' 카드 한 장(parse_card 결과와 같은 스키마)."""
    return {
        "id": "drama_로또 1등도 출근합니다_tvN_오후 09:00",
        "category": "drama",
        "channel": "tvN",
        "title": "로또 1등도 출근합니다",
        "days": ["월", "화"],
        "time": "오후 09:00",
        "rating": rating,
        "ratingDate": md(rating_date),
        "ratingByDay": {day: {"rating": rating, "ratingDate": md(rating_date)}},
        "link": "https://example.com/lotto",
    }


def write_week(out_dir, programs, below):
    payload = {
        "weekStart": MON.isoformat(),
        "weekEnd": (MON + timedelta(days=6)).isoformat(),
        "collectedAt": datetime.now(KST).isoformat(),
        "programs": programs,
        "newBelowCutoff": below,
    }
    with open(os.path.join(out_dir, f"{MON.isoformat()}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def read_week(out_dir):
    with open(os.path.join(out_dir, f"{MON.isoformat()}.json"), encoding="utf-8") as f:
        return json.load(f)


def by_day_ratings(p):
    return {d: e["rating"] for d, e in (p.get("ratingByDay") or {}).items()}


def with_out_dir(fn):
    out_dir = tempfile.mkdtemp(prefix="dv-test-")
    try:
        fn(out_dir)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def test_below_cutoff_episode_merges_into_table_entry():
    """(A) 월 5.2%로 표에 실린 뒤 화 4.8%(컷오프 미만)가 들어오면 표 항목의 화 칸에 합쳐진다."""
    def run(out_dir):
        write_week(out_dir, programs=[lotto(5.2, MON, "월")], below=[])
        sn.dispatch_below_cutoff(out_dir, [lotto(4.8, MON + timedelta(days=1), "화")])
        data = read_week(out_dir)
        entry = next((p for p in data["programs"] if "로또" in p["title"]), None)
        check("(A) 표 항목 유지", entry is not None, True)
        check("(A) 요일별 시청률 월·화 모두 보존", by_day_ratings(entry or {}), {"월": 5.2, "화": 4.8})
        check("(A) 대표 시청률은 최신 회차(화)", (entry or {}).get("ratingDate"), md(MON + timedelta(days=1)))
        check("(A) 컷오프 미만 목록에는 넣지 않음",
              any("로또" in p["title"] for p in data.get("newBelowCutoff", [])), False)
    with_out_dir(run)


def test_table_entry_keeps_earlier_below_cutoff_episode():
    """(B) 월 3.5%(컷오프 미만 보관) 뒤 화 5.0%로 표에 오르면 월 값을 함께 가져온다."""
    def run(out_dir):
        write_week(out_dir, programs=[], below=[lotto(3.5, MON, "월")])
        sn.dispatch_by_rating_date(out_dir, [lotto(5.0, MON + timedelta(days=1), "화")])
        data = read_week(out_dir)
        entry = next((p for p in data["programs"] if "로또" in p["title"]), None)
        check("(B) 표에 실림", entry is not None, True)
        check("(B) 앞선 회차(월 3.5%)까지 보존", by_day_ratings(entry or {}), {"월": 3.5, "화": 5.0})
        check("(B) 대표 시청률은 컷오프를 넘은 회차", (entry or {}).get("rating"), 5.0)
    with_out_dir(run)


def _write_known_old_program_cache(out_dir):
    """첫 방송일이 이 주차보다 2주 전인(=신규 아님, 종영 아님 확정) 캐시."""
    cache = {"drama|로또 1등도 출근합니다|tvN": {
        "date": (MON - timedelta(days=14)).isoformat(), "endDate": None}}
    with open(os.path.join(out_dir, sn.FIRST_AIR_CACHE_FILE), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def test_known_not_new_below_entry_is_kept_as_record():
    """(C) 신규·종영 아님이 확정돼도 컷오프 미만 관측치는 기록으로 남긴다."""
    def run(out_dir):
        write_week(out_dir, programs=[], below=[lotto(3.5, MON, "월")])
        _write_known_old_program_cache(out_dir)
        sn.recompute_new_flags(out_dir)
        data = read_week(out_dir)
        kept = data.get("newBelowCutoff", [])
        check("(C) 컷오프 미만 관측치 유지", [p["title"] for p in kept], ["로또 1등도 출근합니다"])
        check("(C) 신규 배지는 붙지 않음", kept[0].get("isNew") if kept else None, None)
    with_out_dir(run)


def test_below_record_merges_into_table_entry_on_recompute():
    """(C) 표에 오른 프로그램의 기준 미만 기록은 표 항목의 요일별 값에 합친 뒤 목록에서 뺀다."""
    def run(out_dir):
        write_week(out_dir, programs=[lotto(5.0, MON + timedelta(days=1), "화")],
                   below=[lotto(3.5, MON, "월")])
        _write_known_old_program_cache(out_dir)
        sn.recompute_new_flags(out_dir)
        data = read_week(out_dir)
        entry = data["programs"][0]
        check("(C) 표 항목에 월 3.5% 합쳐짐", by_day_ratings(entry), {"월": 3.5, "화": 5.0})
        check("(C) 목록에서는 제거", data.get("newBelowCutoff", []), [])
    with_out_dir(run)


def main():
    test_below_cutoff_episode_merges_into_table_entry()
    test_table_entry_keeps_earlier_below_cutoff_episode()
    test_known_not_new_below_entry_is_kept_as_record()
    test_below_record_merges_into_table_entry_on_recompute()

    print()
    if FAILURES:
        print(f"실패 {len(FAILURES)}건: {', '.join(FAILURES)}")
        return 1
    print("모든 테스트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
