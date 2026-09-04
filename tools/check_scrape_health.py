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
  6) 누적기록 훼손 감지 (celeb) - history/{YYYY-MM}.json에서 '확정된
     회차'의 상품이 직전 커밋보다 줄거나 통째로 사라졌는지.
     2026-08-22 왕영은의 톡투게더(08:20 방송) 기록 3건이 같은 날 저녁
     수집분에 덮여 1건으로 줄어든 적이 있는데, 산출물 검사만으로는
     아무도 못 잡았다 (per-program 파일은 정상이었다).
     단, '방송 시작 = 확정'이 아니다. 방송 시작 후 정정 창(빌더의
     RECONCILE_HOURS) 안에서는 상품 제외/코드 변경이 정상적으로 반영되므로
     그 구간의 감소는 사고가 아니다 - 판정은 빌더의 record_is_final()을
     그대로 쓴다.

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
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK_PATH = os.path.join(ROOT, ".scrape_run_mark")


def run_url() -> str:
    """현재 GitHub Actions 실행 URL (로컬 실행이면 빈 문자열)."""
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{run_id}" if all((server, repo, run_id)) else ""

# 직전 커밋 대비 이 비율 미만으로 줄면 이상 신호로 본다
# (방송 편성상 상품 수는 원래 오르내리므로 여유를 크게 둔다)
DROP_RATIO_LIMIT = 0.4


def count_products(data) -> int:
    return len(data.get("products") or [])


def broadcast_counts(data):
    """상품 산출물을 '방송 회차별 건수'로 쪼갠다 ({라벨: 건수}).
    셀럽PGM 산출물은 "다음 방송" 기준으로 매번 덮어써지므로, 방송이 끝나
    다음 회차로 넘어가면 파일 전체 건수가 뚝 떨어진다 (예: 8/17 방송 3개 ->
    이제 막 예고가 열린 8/24 방송 1개). 이건 수집 고장이 아니라 정상 교체라,
    급감 검사는 파일 전체가 아니라 '양쪽에 다 있는 같은 회차'끼리만 비교한다.
    방송 라벨이 없는 산출물이면 None (기존처럼 전체 건수로 비교)."""
    if not isinstance(data, dict):
        return None
    products = data.get("products") or []
    counts = {}
    for p in products:
        label = str((p or {}).get("broadcast_date_label") or "").strip()
        if label:
            counts[label] = counts.get(label, 0) + 1
    return counts or None


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

# 산출물 -> 그 파일을 만드는 코드. 알림에 "▶️코드명"으로 찍기 위한 매핑.
# (파일 하나가 어느 스크래퍼 책임인지 알아야 담당자가 바로 열어볼 수 있다)
PRODUCER = {
    "HD_HJM": "fixed/rehd.py", "HD_OGS": "fixed/rehd.py", "HD_WYE": "fixed/rehd.py",
    "GS_BJY": "fixed/regs.py", "GS_SYJ": "fixed/regs.py",
    "LT_CYR": "fixed/relt.py",
    "CJ_KJE": "fixed/recj.py", "CJ_CHJ": "fixed/recj.py",
    "CJ_KCO": "fixed/recj.py", "CJ_SIH": "fixed/recj.py", "CJ_KSY": "fixed/recj.py",
    "HD": "fixed/hd_fixed_programs.py", "GS": "fixed/gs_fixed_programs.py",
    "CJ": "fixed/cj_fixed_programs.py", "LT": "fixed/lt_fixed_programs.py",
}
BUILDER = {
    "celeb": "fixed/build_representative_programs.py",
    "fixed": "fixed/build_fixed_pgm.py",
}


def producer_of(rel_path: str, group: str) -> str:
    stem = os.path.basename(rel_path).replace(".json", "")
    if stem == "merged":
        return BUILDER.get(group, "")
    return PRODUCER.get(stem, "")


def advise(reason: str, script: str) -> str:
    """실패 사유별 수정권장사항. 알림에서 '그래서 뭘 하면 되는지'를 준다."""
    if "갱신 안 됨" in reason:
        return (f"{script or '해당 스크래퍼'} 실행 로그에서 예외 확인 후 재실행. "
                f"예외가 없으면 저장 경로/조건문 점검")
    if "JSON 파싱 실패" in reason:
        return "저장 중 중단됐을 가능성 - 재실행 후에도 깨지면 저장 로직(쓰기 도중 예외) 점검"
    if "파일 없음" in reason:
        return f"{script or '해당 스크래퍼'}가 한 번도 저장에 성공하지 못함 - 대상 URL/식별자부터 확인"
    if "건수 부족" in reason or "급감" in reason:
        return "사이트 구조 변경 의심 - 모듈코드/셀렉터가 아직 유효한지 확인 (일시적이면 재실행)"
    return "실행 로그 확인 후 재실행"


def advise_exception(exc_type: str, message: str) -> str:
    """스크래퍼가 던진 예외 종류별 수정권장사항."""
    blob = f"{exc_type} {message}"
    if "NoneType" in blob and "has no attribute" in blob:
        return "API가 해당 필드를 null로 준 경우 - 응답 접근부에 `or {}` 방어 추가"
    if exc_type == "KeyError":
        return "API 응답 스키마 변경 의심 - 해당 키 경로를 실제 응답과 대조"
    if exc_type in ("IndexError", "TypeError"):
        return "응답 형태가 기대와 다름 - 빈 리스트/None 케이스 방어 추가"
    if "FetchError" in blob or "재시도" in blob:
        return "재시도까지 소진된 사이트 장애/차단 - 잠시 후 재실행, 반복되면 UA·헤더 점검"
    if "JSONDecode" in blob:
        return "JSON이 아닌 응답(차단 페이지 등) - 헤더/쿠키 점검 후 재실행"
    if "Timeout" in blob or "Connection" in blob:
        return "네트워크 일시 장애 - 재실행. 반복되면 타임아웃 상향 검토"
    if "Playwright" in blob or "playwright" in blob:
        return "브라우저 자동화 실패 - 셀렉터 변경 또는 브라우저 설치 상태 확인"
    return "실행 로그의 트레이스백 위치부터 확인"


def load_run_report() -> list:
    """run_step.py가 남긴 스크래퍼별 실행 결과."""
    path = os.path.join(ROOT, ".scrape_run_report.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return (json.load(f) or {}).get("steps", []) or []
    except (OSError, ValueError):
        return []


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def previous_data(rel_path: str):
    """직전 커밋(HEAD) 버전의 JSON. 못 읽으면 None (급감 검사 생략)."""
    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{rel_path}"],
            cwd=ROOT, capture_output=True, timeout=30,
        )
        if blob.returncode != 0:
            return None
        return json.loads(blob.stdout.decode("utf-8"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def drop_reason(data, prev_data, count, prev):
    """급감이면 실패 사유 문자열, 아니면 None. 두 번째 반환값은 메모.

    방송 라벨이 있는 산출물은 '양쪽에 다 있는 같은 방송 회차'끼리만 비교한다.
    회차가 통째로 바뀌었거나(다음 방송으로 넘어감) 끝난 회차가 빠진 것은
    정상이라 건드리지 않는다. 다만 상품이 0개가 되는 건 회차와 무관하게
    수집 고장이므로 그대로 잡는다."""
    if count == 0:
        return f"급감: {prev} -> 0 (산출물이 비었다)", None

    now_by_bcast = broadcast_counts(data)
    prev_by_bcast = broadcast_counts(prev_data)

    # 방송 라벨이 없는 산출물(merged.json 등)은 기존대로 전체 건수로 비교
    if not now_by_bcast or not prev_by_bcast:
        if count < prev * DROP_RATIO_LIMIT:
            return (f"급감: {prev} -> {count} "
                    f"(직전의 {count / prev:.0%}, 임계 {DROP_RATIO_LIMIT:.0%})"), None
        return None, None

    shared = sorted(set(now_by_bcast) & set(prev_by_bcast))
    if not shared:
        return None, "방송 회차 교체됨 - 급감 검사 생략"

    for label in shared:
        was, now = prev_by_bcast[label], now_by_bcast[label]
        if now < was * DROP_RATIO_LIMIT:
            return (f"급감: [{label}] {was} -> {now} "
                    f"(직전의 {now / was:.0%}, 임계 {DROP_RATIO_LIMIT:.0%})"), None
    return None, f"동일 회차 {len(shared)}건 유지"


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

    prev_data = previous_data(rel_path)
    prev = counter(prev_data) if prev_data is not None else -1
    if prev > 0:
        notes.append(f"직전 {prev}건")
        reason, note = drop_reason(data, prev_data, count, prev)
        if reason:
            return "fail", reason
        if note:
            notes.append(note)

    return "ok", " / ".join(notes)


HISTORY_DIR = f"{REP}/history"
HISTORY_BUILDER = "fixed/build_celeb_history.py"


def _record_is_final_fn():
    """build_celeb_history.py의 '기록 확정' 판정을 그대로 빌려온다.
    누적 규칙과 검사 기준이 갈라지면 검사가 무의미해지므로 재구현하지 않는다.
    (구버전 빌더에는 record_is_final이 없어 already_started로 폴백한다)"""
    import importlib.util
    path = os.path.join(ROOT, "fixed", "build_celeb_history.py")
    spec = importlib.util.spec_from_file_location("_celeb_history", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # 빌더가 깨졌으면 이 검사는 생략 (다른 검사로 잡힌다)
        return None
    return getattr(module, "record_is_final", None) or getattr(module, "already_started", None)


def history_broadcasts(data):
    """{(program_key, date, 시작시각): (건수, 라벨, 편성문구)}

    같은 날 2회 방송하는 날이 있어서(2026-09-08 오감쇼 08:15/19:30) 날짜만
    키로 쓰면 두 회차가 겹쳐 엉뚱한 회차끼리 비교된다. 라벨의 시각까지 넣는다."""
    out = {}
    for prog in (data or {}).get("programs") or []:
        key = prog.get("program_key")
        schedule = prog.get("schedule_raw", "")
        for b in prog.get("broadcasts") or []:
            label = b.get("label", "")
            m = re.search(r"\d{1,2}:\d{2}", label or "")
            out[(key, b.get("date"), m.group(0) if m else "")] = (
                len(b.get("products") or []), label, schedule)
    return out


def check_history_regressions():
    """확정된(이미 시작된) 방송 기록이 줄거나 사라졌으면 실패 사유 리스트를 낸다."""
    from datetime import datetime, timezone, timedelta

    record_is_final = _record_is_final_fn()
    if record_is_final is None:
        return ["누적기록 검사 생략: fixed/build_celeb_history.py를 읽을 수 없음"]

    now = datetime.now(timezone(timedelta(hours=9)))
    problems = []
    hist_dir = os.path.join(ROOT, HISTORY_DIR)
    if not os.path.isdir(hist_dir):
        return problems

    for name in sorted(os.listdir(hist_dir)):
        if not name.endswith(".json"):
            continue
        rel_path = f"{HISTORY_DIR}/{name}"
        prev_data = previous_data(rel_path)
        if prev_data is None:
            continue  # 새로 생긴 월 파일
        try:
            now_data = read_json(os.path.join(hist_dir, name))
        except (OSError, ValueError) as e:
            problems.append(f"{rel_path}: JSON 파싱 실패 ({type(e).__name__})")
            continue

        before, after = history_broadcasts(prev_data), history_broadcasts(now_data)
        for key, (was, label, schedule) in sorted(before.items()):
            program_key, date_iso, start_hm = key
            if not record_is_final(date_iso, now, label, schedule):
                continue  # 방송 전 라인업 변경 / 정정 창 안의 정정은 정상
            if key not in after:
                problems.append(
                    f"{rel_path}: [{program_key} {date_iso} {start_hm}] "
                    f"확정 기록 {was}건이 통째로 사라짐")
            elif after[key][0] < was:
                problems.append(
                    f"{rel_path}: [{program_key} {date_iso} {start_hm}] 확정 기록 {was} -> "
                    f"{after[key][0]}건으로 줄어듦 (방송 후 잔여 노출에 덮였을 가능성)")
    return problems


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

    # 코드명(스크립트) 단위로 모은다: {script: [{"오류내용", "수정권장사항"}, ...]}
    # 알림이 "▶️코드명 / -오류내용 / -수정권장사항" 형태라서 이 단위가 맞다.
    findings = {}

    def add_finding(script, problem, suggestion):
        key = script or "(코드 불명)"
        items = findings.setdefault(key, [])
        if not any(i["problem"] == problem for i in items):
            items.append({"problem": problem, "suggestion": suggestion})

    # 1) 스크래퍼가 예외로 죽은 경우 - run_step.py가 잡아둔 실제 오류 내용
    for step in load_run_report():
        if step.get("exitCode", 0) == 0:
            continue
        exc_type = step.get("excType", "")
        exc_msg = (step.get("excMessage") or "").strip()
        where = step.get("where", "")
        problem = f"{exc_type}: {exc_msg}" if exc_type else exc_msg or "비정상 종료"
        if where:
            problem += f" @ {where}"
        add_finding(step.get("script", ""), problem, advise_exception(exc_type, exc_msg))

    # 2) 산출물 검사
    print(f"\n===== 수집 건전성 검사: {args.group} =====")
    fails, warns = [], []
    for rel_path, counter, min_count, required in SPECS[args.group]:
        status, message = check_one(rel_path, counter, min_count, required, mark_ts)
        icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}[status]
        print(f"  {icon} {rel_path}: {message}")
        if status == "fail":
            fails.append(f"{rel_path}: {message}")
            script = producer_of(rel_path, args.group)
            add_finding(script, f"{os.path.basename(rel_path)} - {message}",
                        advise(message, script))
        elif status == "warn":
            warns.append(f"{rel_path}: {message}")

    # 누적기록(history) 훼손 검사 - 산출물이 멀쩡해도 과거 기록이 덮이는 사고를 잡는다
    if args.group == "celeb":
        for problem in check_history_regressions():
            print(f"  \u274c {problem}")
            fails.append(problem)
            add_finding(HISTORY_BUILDER, problem,
                        "덮어쓰기 규칙(merge_into_month) 점검. 기록은 "
                        "git에서 직전 정상 커밋의 회차를 되살려 복구한다")
        if not fails:
            print(f"  \u2705 {HISTORY_DIR}: 확정 방송 기록 유지됨")

    ok_count = max(len(SPECS[args.group]) - len(fails) - len(warns), 0)
    print(f"\n  정상 {ok_count}건 / 경고 {len(warns)}건 / 실패 {len(fails)}건")

    if fails or findings:
        print("\n[health] 수집 실패 항목이 있습니다:")
        for line in fails:
            print(f"  - {line}")
        print("\n  -> 위 스크래퍼의 로그를 확인하세요. 사이트 구조 변경이면 "
              "해당 스크래퍼를, 일시적 장애면 재실행으로 해결됩니다.")

        # 코드(스크립트)별 원인·조치 요약. 실행 로그 맨 아래에서 이것만 보면
        # 어느 파일을 열어야 하는지와 무엇을 하면 되는지가 바로 나온다.
        if findings:
            print("\n===== 원인·조치 요약 =====")
            for name, items in findings.items():
                print(f"\n▶ {name}")
                for item in items:
                    print(f"  - {item['problem']}")
                    if item["suggestion"]:
                        print(f"    -> {item['suggestion']}")
            url = run_url()
            if url:
                print(f"\n  실행 로그: {url}")

        # 워크플로우 로그/스텝 출력용 한 줄 요약
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a", encoding="utf-8") as f:
                f.write(f"failures={'; '.join(fails) or payload['summary']}\n")
        return 0 if args.warn_only else 1

    print("[health] 이상 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
