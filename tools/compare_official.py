# -*- coding: utf-8 -*-
"""
compare_official.py
공식 편성표 수집(homeshopping/_official/{채널}/)과 기존 라방바 기록(homeshopping/{채널}/)을
날짜별로 비교해 일치율을 낸다. etc_official_scraper.py 로 모은 데이터를 믿고 출처를
바꿀지 판단하는 근거 (2026-11-13, 라방바 차단 대응).

== 비교 방법 ==
  - 방송 시작시각: 라방바 기록의 각 시작시각이 공식에도 있는가 (±2분. 라방바는 끝시각을
    1분 늦게 잡는 등 반올림 차이가 있다).
  - 상품코드: 라방바 기록 link 의 상품코드가 공식 어느 행에든 있는가. 공식은 방송마다
    상품이 여러 개(함께 방송하는 상품)라 보통 공식 쪽이 더 많다 - 그래서 '라방바 → 공식'
    방향으로만 본다.
  - 라방바에만 있는 행은 상품명과 함께 적는다 (공식이 빼는 유형을 파악하려고).

== 출력 ==
  표준출력 + homeshopping/_official/COMPARE.md (워크플로우가 커밋해서 GitHub 에서 바로 본다)

== 사용법 ==
  python tools/compare_official.py            # 두 쪽 모두 있는 모든 날짜
  python tools/compare_official.py 2026-11-12 # 특정 날짜만
"""

import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFICIAL = os.path.join(ROOT, "homeshopping", "_official")
KST = timezone(timedelta(hours=9))
TOL_MIN = 2


def _hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _code(link):
    m = re.search(r"goods_code=(\d+)|/goods/(\d+)|/detail/(\d+)|prdId=(\d+)", link or "")
    return next((g for g in m.groups() if g), "") if m else ""


def _load_days(path):
    try:
        return json.load(open(path, encoding="utf-8")).get("days", {})
    except Exception:
        return {}


def compare_day(lav, off):
    starts = sorted({_hm(r["start"]) for r in off})
    codes = {_code(r["link"]) for r in off} - {""}

    def start_hit(r):
        s = _hm(r["start"])
        return any(abs(s - o) <= TOL_MIN for o in starts)

    hit_time = [r for r in lav if start_hit(r)]
    hit_code = [r for r in lav if _code(r["link"]) and _code(r["link"]) in codes]
    only_lav = [r for r in lav if not start_hit(r) or (_code(r["link"]) and _code(r["link"]) not in codes)]
    return {
        "lav": len(lav), "off": len(off), "off_shows": len(starts),
        "time_hit": len(hit_time), "code_hit": len(hit_code),
        "only_lav": [f"{r['start']} {r['product'][:22]}" for r in only_lav][:6],
    }


def main(argv):
    want_dates = set(argv)
    lines = [f"# 공식 편성표 ↔ 라방바 기록 비교", "",
             f"갱신: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST · "
             f"시간 일치 = 라방바 방송 시작시각이 공식에도 있음(±{TOL_MIN}분) · "
             f"코드 일치 = 라방바 상품코드가 공식에도 있음", ""]
    summary = []
    for off_dir in sorted(glob.glob(os.path.join(OFFICIAL, "*_*"))):
        label = os.path.basename(off_dir)
        lav_dir = os.path.join(ROOT, "homeshopping", label)
        if not os.path.isdir(lav_dir):
            continue
        rows = []
        tot = {"lav": 0, "time_hit": 0, "code_hit": 0}
        for off_path in sorted(glob.glob(os.path.join(off_dir, "*.json"))):
            off_days = _load_days(off_path)
            lav_days = _load_days(os.path.join(lav_dir, os.path.basename(off_path)))
            for date in sorted(set(off_days) & set(lav_days)):
                if want_dates and date not in want_dates:
                    continue
                lav, off = lav_days[date], off_days[date]
                if not lav or not off:
                    continue
                c = compare_day(lav, off)
                for k in tot:
                    tot[k] += c[k]
                rows.append(f"| {date} | {c['lav']} | {c['off']} ({c['off_shows']}방송) | "
                            f"{c['time_hit']}/{c['lav']} | {c['code_hit']}/{c['lav']} | "
                            f"{', '.join(c['only_lav']) or '-'} |")
        if not rows:
            continue
        pct = lambda n: f"{100 * n // tot['lav']}%" if tot["lav"] else "-"
        summary.append(f"- **{label}**: 시간 일치 {pct(tot['time_hit'])} ({tot['time_hit']}/{tot['lav']}), "
                       f"코드 일치 {pct(tot['code_hit'])} ({tot['code_hit']}/{tot['lav']})")
        lines += [f"## {label}", "", "| 날짜 | 라방바 | 공식 | 시간 일치 | 코드 일치 | 라방바에만 있는 행 |",
                  "|---|---|---|---|---|---|"] + rows + [""]
    out = "\n".join(lines[:3] + ["## 요약", ""] + summary + [""] + lines[3:])
    print(out)
    os.makedirs(OFFICIAL, exist_ok=True)
    with open(os.path.join(OFFICIAL, "COMPARE.md"), "w", encoding="utf-8") as f:
        f.write(out + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
