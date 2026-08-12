# -*- coding: utf-8 -*-
"""
check_scrape_health.py
수집 파이프라인이 "조용히 실패"하는 것을 막는 검증 스크립트.

== 왜 필요한가 ==
회사별 스크래퍼는 하나가 죽어도 나머지는 살아야 하므로 워크플로우에서
continue-on-error로 격리돼 있다. 그 대가로 스크래퍼가 죽어도 워크플로우는
초록불이고, 산출물은 며칠 전 데이터 그대로 남는다. 실제로 2026-08에
recj.py가 매일 첫 프로그램에서 죽으면서 CJ 셀럽PGM 4개 파일이 갱신되지
않는 상태가 한동안 아무도 모르게 이어졌다.

이 스크립트는 수집이 끝난 뒤 산출물을 검사해서, 문제가 있으면 종료코드 1로
워크플로우를 빨간불로 만든다. 핵심 검사는 "이번 실행에서 다시 쓰였는가"다.
내용이 그대로여도 파일은 다시 쓰이므로, 스크래퍼가 죽으면 바로 잡힌다.

== 검사 항목 ==
  1) 파일 존재
  2) JSON 파싱 가능
  3) 이번 실행에서 갱신됨 (--mark로 찍어둔 시각 이후 mtime)
  4) 최소 건수 충족 (빈 껍데기 커밋 방지)
  5) 급감 감지 - 직전 커밋(git HEAD) 대비 건수가 임계 비율 밑으로 떨어졌는지

== 사용법 ==
  # 수집 시작 직전
  python tools/check_scrape_health.py --mark
  # 수집 종료 후 (문제가 있으면 exit 1)
  python tools/check_scrape_health.py --group celeb
  python tools/check_scrape_health.py --group fixed
  # 실패해도 리포트만 보고 싶을 때
  python tools/check_scrape_health.py --group celeb --warn-only
"""

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK_PATH = os.path.join(ROOT, ".scrape_run_mark")

# 직전 커밋 대비 이 비율 미만으로 줄면 이상 신호로 본다
# (방송 편성상 상품 수는 원래 오르내리므로 여유를 크게 둔다)
DROP_RATIO_LIMIT = 0.4


def count_products(data) -> int:
    return len(data.get("products") or [])


def count_programs(data) -> int:
    return len(data.get("programs") or [])


def count_merged_programs(data) -> int:
    programs = data.get("programs") if isinstance(data, dict) else data
    return len(programs or [])


def count_slots(data) -> int:
    return len(data.get("slots") or [])


# ── 파이프라인별 기대 산출물 ────────────────────────────────────────────
# min_count: 이 값보다 적으면 실패 (0이면 건수 검사 안 함 - 휴방 등으로
#            정말 0개일 수 있는 산출물)
# required: 이번 실행에서 반드시 갱신돼야 하는 파일인지.
#           False면 없거나 안 갱신돼도 경고만 (스크래퍼 미완성/비정기 PGM)
REP = "homeshopping/representative_programs"
FIX = "homeshopping/fixed_programs"

SPECS = {
    "celeb": [
        (f"{REP}/HD_HJM.json", count_products, 0, True),
        (f"{REP}/HD_OGS.json", count_products, 0, True),
        (f"{REP}/HD_WYE.json", count_products, 0, True),
        (f"{REP}/GS_BJY.json", count_products, 0, True),
        (f"{REP}/GS_SYJ.json", count_products, 0, True),
        (f"{REP}/LT_CYR.json", count_products, 0, True),
        (f"{REP}/CJ_KJE.json", count_products, 0, True),
        (f"{REP}/CJ_CHJ.json", count_products, 0, True),
        (f"{REP}/CJ_KCO.json", count_products, 0, True),
        (f"{REP}/CJ_SIH.json", count_products, 0, True),
        # 김신영이 산다: 연 10회 내외 비정기 편성이라 방송 예정이 없는 기간엔
        # pgmShop에 상품이 안 걸린다 -> 갱신 누락을 실패로 보지 않는다
        (f"{REP}/CJ_KSY.json", count_products, 0, False),
        (f"{REP}/merged.json", count_merged_programs, 8, True),
    ],
    "fixed": [
        (f"{FIX}/HD.json", count_programs, 3, True),
        (f"{FIX}/GS.json", count_programs, 3, True),
        (f"{FIX}/CJ.json", count_programs, 5, True),
        (f"{FIX}/LT.json", count_programs, 5, True),
        (f"{FIX}/merged.json", count_slots, 20, True),
    ],
}


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def previous_count(rel_path: str, counter) -> int:
    """직전 커밋(HEAD) 버전의 건수. 못 읽으면 -1 (검사 생략)."""
    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{rel_path}"],
            cwd=ROOT, capture_output=True, timeout=30,
        )
        if blob.returncode != 0:
            return -1
        return counter(json.loads(blob.stdout.decode("utf-8")))
    except (OSError, ValueError, subprocess.SubprocessError):
        return -1


def check_one(rel_path, counter, min_count, required, mark_ts):
    """(상태, 메시지) 반환. 상태: ok / warn / fail"""
    path = os.path.join(ROOT, rel_path)

    if not os.path.exists(path):
        return ("fail" if required else "warn"), "파일 없음"

    try:
        data = read_json(path)
    except (OSError, ValueError) as e:
        return "fail", f"JSON 파싱 실패 ({type(e).__name__})"

    count = counter(data)
    notes = [f"{count}건"]

    # 핵심 검사: 이번 실행에서 다시 쓰였는가
    fresh = os.path.getmtime(path) >= mark_ts if mark_ts else True
    if not fresh:
        age_min = (time.time() - os.path.getmtime(path)) / 60
        return (("fail" if required else "warn"),
                f"이번 실행에서 갱신 안 됨 (마지막 기록 {age_min:.0f}분 전) - "
                f"스크래퍼가 죽었거나 산출물을 못 썼다")

    if min_count and count < min_count:
        return "fail", f"건수 부족: {count} < 최소 {min_count}"

    prev = previous_count(rel_path, counter)
    if prev > 0:
        notes.append(f"직전 {prev}건")
        if count < prev * DROP_RATIO_LIMIT:
            return "fail", (f"급감: {prev} -> {count} "
                            f"(직전의 {count / prev:.0%}, 임계 {DROP_RATIO_LIMIT:.0%})")

    return "ok", " / ".join(notes)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mark", action="store_true",
                        help="수집 시작 시각을 찍는다 (수집 직전에 호출)")
    parser.add_argument("--group", choices=sorted(SPECS),
                        help="검사할 파이프라인")
    parser.add_argument("--warn-only", action="store_true",
                        help="문제가 있어도 종료코드 0")
    args = parser.parse_args()

    if args.mark:
        with open(MARK_PATH, "w") as f:
            f.write(str(time.time()))
        print(f"[health] 수집 시작 시각 기록: {MARK_PATH}")
        return 0

    if not args.group:
        parser.error("--mark 또는 --group 중 하나가 필요합니다")

    mark_ts = 0.0
    if os.path.exists(MARK_PATH):
        try:
            with open(MARK_PATH) as f:
                mark_ts = float(f.read().strip())
        except (OSError, ValueError):
            mark_ts = 0.0
    if not mark_ts:
        print("[health] [경고] 시작 시각 마크가 없어 '이번 실행 갱신' 검사를 건너뜁니다 "
              "(워크플로우에 --mark 스텝이 빠졌을 수 있음)")

    print(f"\n===== 수집 건전성 검사: {args.group} =====")
    fails, warns = [], []
    for rel_path, counter, min_count, required in SPECS[args.group]:
        status, message = check_one(rel_path, counter, min_count, required, mark_ts)
        icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}[status]
        print(f"  {icon} {rel_path}: {message}")
        if status == "fail":
            fails.append(f"{rel_path}: {message}")
        elif status == "warn":
            warns.append(f"{rel_path}: {message}")

    print(f"\n  정상 {len(SPECS[args.group]) - len(fails) - len(warns)}건 / "
          f"경고 {len(warns)}건 / 실패 {len(fails)}건")

    if fails:
        print("\n[health] 수집 실패 항목이 있습니다:")
        for line in fails:
            print(f"  - {line}")
        print("\n  -> 위 스크래퍼의 로그를 확인하세요. 사이트 구조 변경이면 "
              "해당 스크래퍼를, 일시적 장애면 재실행으로 해결됩니다.")
        # 알림 스텝이 본문에 넣을 수 있게 GitHub Actions 출력으로도 넘긴다
        summary = "; ".join(fails)
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a", encoding="utf-8") as f:
                f.write(f"failures={summary}\n")
        return 0 if args.warn_only else 1

    print("[health] 이상 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
