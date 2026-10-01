# -*- coding: utf-8 -*-
"""
nielsen_backfill 테스트 (pytest 없이 그냥 실행: python tools/test_nielsen_backfill.py)

네트워크 없이 닐슨 순위 페이지 조각(실제 마크업을 본뜬 것)으로 검증한다.

지키려는 것:
  (A) 순위 페이지에서 시청률 표만 읽을 것 (뒤따르는 시청자 수 표는 무시)
  (B) 닐슨 제목 표기('티빙오리지널로또1등도출근합니다', 'KBS1일일드라마(엄마가미쳤어요)',
      'TV CHOSUN')와 우리 표기를 맞출 것. 본방을 재방보다 우선할 것
  (C) 파일에 있는 프로그램의 '비어 있고 이미 방송된' 요일만 채울 것
      - 값이 있는 요일·아직 안 온 요일은 건드리지 않고 조회도 하지 않는다
      - 기준 미만 수치도 기록으로 채운다 (표 노출 여부는 프론트가 요일별로 판단)
      - 표에 못 오른 프로그램(newBelowCutoff)의 빈 요일도 채운다
      - 채운 날이 대표 시청률 날짜보다 뒤면 대표 값을 바꾼다
  (D) 이번 주·지난주 파일만 손댈 것
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

_spec = importlib.util.spec_from_file_location(
    "_nielsen_backfill", os.path.join(ROOT, "scraper", "nielsen_backfill.py"))
nb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nb)

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
FAILURES = []


def check(label, got, want):
    if got == want:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


def md(d):
    return f"{d.month:02d}.{d.day:02d}"


def row_html(rank, channel, title, value):
    return f"""
        <tr>
        <td class="tb_txt_center">{rank}\t</td>
\t                    <td class="tb_txt_center">{channel}\t</td>
\t                    <td class="tb_txt">{title}\t</td>
                        <td class="percent" align="center">
                        {value}
                        </td>
                        </tr>"""


def ranking_page(rating_rows, count_rows):
    """실제 페이지처럼 시청률 표 뒤에 시청자 수 표가 하나 더 붙는다."""
    return ("<html><body><table class=\"ranking_tb\" border=\"0\">"
            + "".join(row_html(*r) for r in rating_rows)
            + "</table><table class=\"ranking_tb\" border=\"0\">"
            + "".join(row_html(*r) for r in count_rows)
            + "</table></body></html>")


CABLE_PAGE = ranking_page(
    [(1, "tvN", "하나은행초청축구국가대표팀친선경기<본>", "3.886"),
     (2, "ENA", "신병4사보타주<본>", "3.856"),
     (3, "tvN", "티빙오리지널로또1등도출근합니다<본>", "3.545"),
     (4, "tvN", "티빙오리지널로또1등도출근합니다<재>", "0.912"),
     (5, "TV CHOSUN", "TV조선스포츠축구<본>", "3.764")],
    [(1, "tvN", "하나은행초청축구국가대표팀친선경기<본>", "1,120"),
     (3, "tvN", "티빙오리지널로또1등도출근합니다<본>", "854")])

TERRESTRIAL_PAGE = ranking_page(
    [(1, "KBS1", "KBS1일일드라마(엄마가미쳤어요)<본>", "7.7"),
     (2, "KBS1", "인간극장<본>", "7.3")],
    [])


def test_parse_reads_rating_table_only():
    rows = nb.parse_ranking_html(CABLE_PAGE)
    check("(A) 시청률 표 행 수(시청자 수 표 제외)", len(rows), 5)
    check("(A) 제목에서 <본> 표기 제거", rows[2]["title"], "티빙오리지널로또1등도출근합니다")
    check("(A) 재방송 표기 인식", [r["rerun"] for r in rows[2:4]], [False, True])
    check("(A) 시청률 숫자", rows[2]["rating"], 3.545)


def test_find_rating_matches_nielsen_spelling():
    cable = nb.parse_ranking_html(CABLE_PAGE)
    terr = nb.parse_ranking_html(TERRESTRIAL_PAGE)
    check("(B) 접두어 붙은 제목 포함 매칭(재방 아닌 본방)", nb.find_rating(cable, "tvN", "로또 1등도 출근합니다"), 3.545)
    check("(B) 괄호 안 제목 매칭", nb.find_rating(terr, "KBS1", "엄마가 미쳤어요"), 7.7)
    check("(B) 채널 표기 차이(TV조선/TV CHOSUN)", nb.find_rating(cable, "TV조선", "TV조선 스포츠 축구"), 3.764)
    check("(B) 다른 채널이면 매칭 안 함", nb.find_rating(cable, "JTBC", "로또 1등도 출근합니다"), None)
    check("(B) 없는 제목", nb.find_rating(cable, "tvN", "포핸즈"), None)
    check("(B) 플랫폼 분류", [nb.platform_for_channel(c) for c in ("KBS2", "TV조선", "tvN STORY")],
          ["terrestrial", "jongpyun", "cable"])


def lotto_entry(days, by_day, rating, rating_date):
    return {
        "id": "drama_로또 1등도 출근합니다_tvN_오후 09:00",
        "category": "drama", "channel": "tvN", "title": "로또 1등도 출근합니다",
        "days": days, "time": "오후 09:00",
        "rating": rating, "ratingDate": rating_date,
        "ratingByDay": by_day, "link": "https://example.com/lotto",
    }


def write_week(out_dir, monday, programs, below=None):
    path = os.path.join(out_dir, f"{monday.isoformat()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"weekStart": monday.isoformat(),
                   "weekEnd": (monday + timedelta(days=6)).isoformat(),
                   "collectedAt": datetime.now(KST).isoformat(),
                   "programs": programs, "newBelowCutoff": below or []}, f, ensure_ascii=False, indent=2)
    return path


def read_week(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def with_out_dir(fn):
    out_dir = tempfile.mkdtemp(prefix="nielsen-test-")
    try:
        fn(out_dir)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def fake_fetch(pages):
    """(platform, date) -> html. 호출 내역을 기록한다."""
    calls = []

    def fetch(platform, d):
        calls.append((platform, d))
        return pages.get((platform, d), "")
    return fetch, calls


def test_fills_only_missing_aired_days():
    def run(out_dir):
        mon = nb.monday_of(TODAY)
        today = mon + timedelta(days=3)   # 목요일 아침: 월·화·수 방송분 집계 완료
        # 화요일 값은 네이버에서 받았고 월요일이 비어 있다
        path = write_week(out_dir, mon, [lotto_entry(
            ["월", "화"], {"화": {"rating": 5.0, "ratingDate": md(mon + timedelta(days=1))}},
            5.0, md(mon + timedelta(days=1)))])
        page = ranking_page([(3, "tvN", "티빙오리지널로또1등도출근합니다<본>", "5.412")], [])
        fetch, calls = fake_fetch({("cable", mon): page})
        n = nb.backfill_recent_weeks(out_dir, today, fetch_html=fetch)
        p = read_week(path)["programs"][0]
        check("(C) 채운 건수", n, 1)
        check("(C) 월요일 값 채움(첫째 자리 반올림, 출처 표시)",
              p["ratingByDay"]["월"], {"rating": 5.4, "ratingDate": md(mon), "source": "nielsen"})
        check("(C) 기존 화요일 값 유지", p["ratingByDay"]["화"]["rating"], 5.0)
        check("(C) 비어 있는 요일만 조회", calls, [("cable", mon)])
        check("(C) 더 최근 회차(화)가 대표 값으로 유지", (p["rating"], p["ratingDate"]), (5.0, md(mon + timedelta(days=1))))
    with_out_dir(run)


def test_fills_below_cutoff_value_as_record():
    def run(out_dir):
        mon = nb.monday_of(TODAY)
        today = mon + timedelta(days=3)
        path = write_week(out_dir, mon, [lotto_entry(
            ["월", "화"], {"화": {"rating": 5.0, "ratingDate": md(mon + timedelta(days=1))}},
            5.0, md(mon + timedelta(days=1)))])
        fetch, _ = fake_fetch({("cable", mon): CABLE_PAGE})   # 로또 월 3.545 (기준 미만)
        n = nb.backfill_recent_weeks(out_dir, today, fetch_html=fetch)
        p = read_week(path)["programs"][0]
        check("(C) 기준 미만도 기록으로 채움", (n, p["ratingByDay"]["월"]["rating"]), (1, 3.5))
        check("(C) 대표 값은 더 최근 회차(화 5.0) 유지", (p["rating"], p["ratingDate"]), (5.0, md(mon + timedelta(days=1))))
    with_out_dir(run)


def test_fills_below_cutoff_list_entries_too():
    def run(out_dir):
        mon = nb.monday_of(TODAY)
        today = mon + timedelta(days=3)
        path = write_week(out_dir, mon, [], below=[lotto_entry(
            ["월", "화"], {"화": {"rating": 4.8, "ratingDate": md(mon + timedelta(days=1))}},
            4.8, md(mon + timedelta(days=1)))])
        fetch, calls = fake_fetch({("cable", mon): CABLE_PAGE})
        n = nb.backfill_recent_weeks(out_dir, today, fetch_html=fetch)
        p = read_week(path)["newBelowCutoff"][0]
        check("(C) 표 미등재 프로그램의 빈 요일도 채움", (n, p["ratingByDay"]["월"]["rating"]), (1, 3.5))
    with_out_dir(run)


def test_updates_representative_when_filled_day_is_newer():
    def run(out_dir):
        mon = nb.monday_of(TODAY)
        today = mon + timedelta(days=3)
        path = write_week(out_dir, mon, [lotto_entry(
            ["월", "화"], {"월": {"rating": 5.2, "ratingDate": md(mon)}}, 5.2, md(mon))])
        page = ranking_page([(2, "tvN", "티빙오리지널로또1등도출근합니다<본>", "5.0")], [])
        fetch, _ = fake_fetch({("cable", mon + timedelta(days=1)): page})
        nb.backfill_recent_weeks(out_dir, today, fetch_html=fetch)
        p = read_week(path)["programs"][0]
        check("(C) 채운 화요일이 더 최근이면 대표 값 교체", (p["rating"], p["ratingDate"]), (5.0, md(mon + timedelta(days=1))))
    with_out_dir(run)


def test_skips_days_not_yet_aired_and_fetch_failures():
    def run(out_dir):
        mon = nb.monday_of(TODAY)
        today = mon + timedelta(days=1)   # 화요일: 월요일 분만 집계됨
        write_week(out_dir, mon, [lotto_entry(["월", "화"], {}, 4.0, None)])

        def fetch(platform, d):
            raise OSError("network down")
        n = nb.backfill_recent_weeks(out_dir, today, fetch_html=fetch)
        check("(C) 조회 실패 시 건너뜀(예외 전파 없음)", n, 0)

        fetch2, calls = fake_fetch({})
        nb.backfill_recent_weeks(out_dir, today, fetch_html=fetch2)
        check("(C) 아직 방송 전인 화요일은 조회하지 않음", calls, [("cable", mon)])
    with_out_dir(run)


def test_only_recent_weeks_are_touched():
    def run(out_dir):
        mon = nb.monday_of(TODAY)
        old = mon - timedelta(days=28)
        write_week(out_dir, old, [lotto_entry(["월", "화"], {"화": {"rating": 5.0, "ratingDate": md(old + timedelta(days=1))}}, 5.0, md(old + timedelta(days=1)))])
        write_week(out_dir, mon - timedelta(days=7), [lotto_entry(["월"], {}, 5.0, None)])
        fetch, calls = fake_fetch({})
        nb.backfill_recent_weeks(out_dir, mon + timedelta(days=2), fetch_html=fetch)
        check("(D) 4주 전 파일은 조회하지 않음", calls, [("cable", mon - timedelta(days=7))])
    with_out_dir(run)


def main():
    test_parse_reads_rating_table_only()
    test_find_rating_matches_nielsen_spelling()
    test_fills_only_missing_aired_days()
    test_fills_below_cutoff_value_as_record()
    test_fills_below_cutoff_list_entries_too()
    test_updates_representative_when_filled_day_is_newer()
    test_skips_days_not_yet_aired_and_fetch_failures()
    test_only_recent_weeks_are_touched()

    print()
    if FAILURES:
        print(f"실패 {len(FAILURES)}건: {', '.join(FAILURES)}")
        return 1
    print("모든 테스트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
