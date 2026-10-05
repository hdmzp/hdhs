# -*- coding: utf-8 -*-
"""
드라마/예능 수집기 — 종영일(End) 누락 재발 방지 테스트
(pytest 없이 그냥 실행: PYTHONPATH=<bs4/playwright 경로> python tools/test_air_end_recheck.py)

2026-10-04 포핸즈(tvN 토일) 사례: 최종회 당일 오전에 확인한 뒤로 7일 주기 재확인만
남아, 종영일이 상세 페이지에 떠도 일주일 가까이 End가 빠졌다.

지키려는 것:
  (A) 최근 주차에 있던 프로그램이 이번 수집(방영중 위젯)에서 빠지면 바로 재확인해 종영일을 받을 것
  (B) 위젯에 남아 있어도 지난주 방송이 끝난 뒤 아직 확인 안 했으면 한 번 재확인할 것
  (C) 이번 주 월요일 이후 이미 확인했고 위젯에도 있으면 다시 보지 않을 것(과조회 방지)
  (D) 이번 수집이 통째로 비면(수집 실패) 위젯 이탈 경로로 대량 조회하지 않을 것
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
sys.path.insert(0, os.path.join(ROOT, "scraper"))

_spec = importlib.util.spec_from_file_location(
    "_scrape_naver", os.path.join(ROOT, "scraper", "scrape_naver.py"))
sn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sn)

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
THIS_MON = sn.monday_of(TODAY)
LAST_MON = THIS_MON - timedelta(days=7)
LAST_SUN = THIS_MON - timedelta(days=1)
START = LAST_MON - timedelta(days=35)
KEY = "drama|포핸즈|tvN"
FAILURES = []


def check(label, got, want):
    ok = got == want
    print(f"  {'OK ' if ok else 'FAIL'} {label}: got={got!r} want={want!r}")
    if not ok:
        FAILURES.append(label)


def prog(title="포핸즈"):
    return {"id": f"drama_{title}_tvN_오후 09:10", "category": "drama", "channel": "tvN",
            "title": title, "days": ["일"], "time": "오후 09:10", "rating": 9.0,
            "link": f"https://search.naver.com/search.naver?query={title}"}


class FakePage:
    def __init__(self):
        self.visited = []

    def goto(self, url, **kw):
        self.visited.append(url)

    def wait_for_timeout(self, ms):
        pass

    def content(self):
        return (f"<div class='cs_common_module'>편성 tvN "
                f"{START:%Y.%m.%d}.~{LAST_SUN:%Y.%m.%d}. (토) 오후 09:30 "
                f"시청률 9.4% {LAST_SUN:%Y.%m.%d}. 기준 · 12회</div>")


def run(collected, checked_at):
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, f"{LAST_MON}.json"), "w", encoding="utf-8") as f:
            json.dump({"weekStart": LAST_MON.isoformat(), "programs": [prog()]}, f, ensure_ascii=False)
        with open(os.path.join(d, sn.FIRST_AIR_CACHE_FILE), "w", encoding="utf-8") as f:
            json.dump({KEY: {"checkedAt": checked_at.isoformat(), "date": START.isoformat(),
                             "endDate": None, "lastRating": 8.7, "lastRatingParser": sn.RATING_PARSER_VERSION}},
                      f, ensure_ascii=False)
        page = FakePage()
        cache = sn.lookup_air_periods(page, collected, d)
        return len(page.visited), cache[KEY].get("endDate")
    finally:
        shutil.rmtree(d)


def main():
    finale_morning = datetime.combine(LAST_SUN, datetime.min.time(), KST) + timedelta(hours=11, minutes=50)
    after_monday = datetime.now(KST) - timedelta(hours=7)
    after_monday = max(after_monday, datetime.combine(THIS_MON, datetime.min.time(), KST))

    print("(A) 위젯에서 빠짐 -> 바로 재확인")
    check("A 종영일", run([prog("다른드라마")], finale_morning)[1], LAST_SUN.isoformat())

    print("(B) 위젯에 남아 있어도 주차 마감 뒤 1회 재확인")
    check("B 종영일", run([prog()], finale_morning)[1], LAST_SUN.isoformat())

    print("(C) 월요일 이후 확인 + 위젯에 있음 -> 조회 안 함")
    check("C 조회 수", run([prog()], after_monday)[0], 0)

    print("(D) 수집 실패(빈 목록) -> 위젯 이탈 경로 끔")
    check("D 조회 수", run([], after_monday)[0], 0)

    print()
    if FAILURES:
        print(f"실패 {len(FAILURES)}건: {', '.join(FAILURES)}")
        return 1
    print("모든 테스트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
