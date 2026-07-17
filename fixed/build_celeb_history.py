# -*- coding: utf-8 -*-
"""
build_celeb_history.py
셀럽PGM 스크레이퍼(rehd/regs/relt/recj)가 만든 프로그램별 JSON은
"다음 방송" 기준으로 매번 덮어써서 지난 방송 데이터가 사라진다.
이 스크립트는 스크레이퍼 실행 직후에 돌면서 모든 셀럽PGM의 상품을
방송일자 기준 월(月) 파일에 누적 보존한다 -> 월 조회 기능의 데이터 소스.

== 출력 ==
homeshopping/representative_programs/history/{YYYY-MM}.json
{
  "month": "2026-07",
  "updated_at": "2026-07-17T03:05:00+09:00",
  "programs": [                       # 순서 = SOURCE_FILES 순서 (프런트 슬리서와 동일)
    {
      "program_key": "HD_OGS",
      "company": "HD", "tab_name": "오감쇼", "program_title": "오감쇼",
      "schedule_raw": "매주 화요일 19시 30분", "detail_link": "https://...",
      "broadcasts": [                 # 방송일 내림차순 = 최신 방송이 맨 위
        {"date": "2026-07-21", "label": "07/21(화) 19:30 방송",
         "collected_at": "...", "products": [...]},
        {"date": "2026-07-14", "label": "07/14(화) 19:30 방송", ...}
      ]
    },
    ...
  ]
}

== 누적 규칙 ==
- 같은 프로그램의 같은 방송일: 방송일이 오늘(KST) 이후면 최신 수집분으로
  교체(방송 전에는 라인업이 바뀔 수 있음), 오늘보다 과거면 기존 기록을
  절대 덮어쓰지 않는다(이미 확정된 지난 방송).
- 상품 라벨에서 월/일을 못 읽는 상품은 건너뛴다(어느 방송인지 알 수 없음).
- 라벨에 연도가 없으므로 "오늘과 가장 가까운 해석"으로 연도를 정한다
  (12월에 1/5 라벨 -> 내년, 1월에 12/28 라벨 -> 작년).

== 사용법 ==
  python fixed/build_celeb_history.py   (스크레이퍼들 실행 후)
"""

import os
import re
import json
from datetime import datetime, date, timezone, timedelta

KST = timezone(timedelta(hours=9))
SRC_DIR = os.path.join("homeshopping", "representative_programs")
HISTORY_DIR = os.path.join(SRC_DIR, "history")

# build_representative_programs.py의 SOURCE_FILES와 동일한 순서.
SOURCE_FILES = [
    "HD_HJM.json",
    "HD_OGS.json",
    "GS_BJY.json",
    "GS_SYJ.json",
    "LT_CYR.json",
    "CJ_KJE.json",
    "CJ_CHJ.json",
    "CJ_KCO.json",
]

WEEKDAY_ABBR = ["월", "화", "수", "목", "금", "토", "일"]

# 회사별 broadcast_date_label 형식 (전부 월/일 포함, 연도 없음):
#   HD: "07/21(화) 19:30 방송" / "7/21(화) 방송상품"
#   GS: "7월 23일(목) 20:45 방송"
#   LT: "07/18 토요일 08:20"
#   CJ: "07/20(월) 19:35"
DATE_PATTERN = re.compile(r"(\d{1,2})\s*[/월]\s*(\d{1,2})")
TIME_PATTERN = re.compile(r"(\d{1,2}:\d{2})")


def parse_label_date(label: str, today: date):
    """라벨에서 (date, 'HH:MM' or None)을 뽑는다. 연도는 오늘과 가장
    가까운 해석을 택한다. 파싱 실패 시 (None, None)."""
    if not label:
        return None, None
    m = DATE_PATTERN.search(label)
    if not m:
        return None, None
    month, day = int(m.group(1)), int(m.group(2))

    best = None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            cand = date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((cand - today).days) < abs((best - today).days):
            best = cand
    if best is None:
        return None, None

    tm = TIME_PATTERN.search(label)
    return best, (tm.group(1) if tm else None)


def make_broadcast_label(brod_date: date, start_hm: str) -> str:
    label = f"{brod_date.month:02d}/{brod_date.day:02d}({WEEKDAY_ABBR[brod_date.weekday()]})"
    if start_hm:
        label += f" {start_hm}"
    return label + " 방송"


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[경고] {path} 읽기 실패: {e}")
        return None


def collect_current_broadcasts(today: date) -> dict:
    """프로그램별 JSON을 읽어 {program_key: {meta, {date_iso: broadcast}}}로 묶는다."""
    collected = {}
    now_iso = datetime.now(KST).isoformat()

    for filename in SOURCE_FILES:
        path = os.path.join(SRC_DIR, filename)
        if not os.path.isfile(path):
            continue
        data = load_json(path)
        if not data:
            continue

        program_key = filename[:-len(".json")]
        by_date = {}
        skipped = 0
        for product in data.get("products") or []:
            brod_date, start_hm = parse_label_date(product.get("broadcast_date_label"), today)
            if brod_date is None:
                skipped += 1
                continue
            key = brod_date.isoformat()
            entry = by_date.setdefault(key, {
                "date": key,
                "label": make_broadcast_label(brod_date, start_hm),
                "collected_at": now_iso,
                "products": [],
            })
            entry["products"].append(product)

        if skipped:
            print(f"[경고] {program_key}: 날짜를 못 읽은 상품 {skipped}개 건너뜀")
        if not by_date:
            continue

        collected[program_key] = {
            "meta": {
                "program_key": program_key,
                "company": data.get("company", ""),
                "tab_name": data.get("tab_name", ""),
                "program_title": data.get("program_title", ""),
                "schedule_raw": data.get("schedule_raw", ""),
                "detail_link": data.get("detail_link", ""),
            },
            "broadcasts": by_date,
        }
    return collected


def merge_into_month(existing: dict, program_key: str, meta: dict,
                     new_broadcasts: dict, today: date):
    """월 파일의 프로그램 항목에 새 방송분을 병합한다.
    오늘 이후 방송일은 최신 수집분으로 교체, 과거 방송일은 기존 기록 보존."""
    programs = existing.setdefault("programs", [])
    prog = next((p for p in programs if p.get("program_key") == program_key), None)
    if prog is None:
        prog = {**meta, "broadcasts": []}
        programs.append(prog)
    else:
        # 편성/링크가 바뀌었을 수 있으니 메타는 항상 최신으로
        prog.update(meta)

    by_date = {b["date"]: b for b in prog.get("broadcasts") or []}
    today_iso = today.isoformat()
    for date_iso, broadcast in new_broadcasts.items():
        if date_iso in by_date and date_iso < today_iso:
            continue  # 지난 방송 확정 기록은 보존
        by_date[date_iso] = broadcast

    # 최신 방송이 맨 위로 오도록 내림차순 정렬
    prog["broadcasts"] = [by_date[k] for k in sorted(by_date, reverse=True)]


def main():
    if not os.path.isdir(SRC_DIR):
        print(f"[실패] 소스 디렉토리 없음: {SRC_DIR}")
        return
    os.makedirs(HISTORY_DIR, exist_ok=True)

    today = datetime.now(KST).date()
    collected = collect_current_broadcasts(today)
    if not collected:
        print("[경고] 누적할 셀럽PGM 데이터가 없음")
        return

    # 방송일이 속한 달 기준으로 월 파일에 나눠 담는다
    # (예: 7/31 수집분에 8/4 방송이 있으면 2026-08.json으로)
    months = {}
    for program_key, info in collected.items():
        for date_iso, broadcast in info["broadcasts"].items():
            ym = date_iso[:7]
            months.setdefault(ym, {}).setdefault(program_key, {})[date_iso] = broadcast

    for ym in sorted(months):
        path = os.path.join(HISTORY_DIR, f"{ym}.json")
        existing = (load_json(path) if os.path.isfile(path) else None) or {}
        existing["month"] = ym
        existing["updated_at"] = datetime.now(KST).isoformat()

        for program_key, new_broadcasts in months[ym].items():
            merge_into_month(existing, program_key, collected[program_key]["meta"],
                             new_broadcasts, today)

        # 프로그램 순서를 SOURCE_FILES 순서로 고정
        order = {f[:-len(".json")]: i for i, f in enumerate(SOURCE_FILES)}
        existing["programs"].sort(key=lambda p: order.get(p.get("program_key"), 99))

        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        total = sum(len(p["broadcasts"]) for p in existing["programs"])
        print(f"[성공] {path} 저장 (프로그램 {len(existing['programs'])}개, 누적 방송 {total}회)")


if __name__ == "__main__":
    main()
