# -*- coding: utf-8 -*-
"""
run_step.py
스크래퍼 한 개를 실행하면서 "어느 코드에서 무슨 오류가 났는지"를 기록한다.

== 왜 필요한가 ==
워크플로우가 `python fixed/recj.py`를 continue-on-error로 돌리면, 실패해도
종료코드가 묻히고 오류 내용은 로그 어딘가에만 남는다. 알림에
"▶️fixed/recj.py / -오류내용 / -수정권장사항"을 담으려면 실행 시점에
스크립트명·종료코드·예외 메시지·발생 위치를 잡아둬야 한다.

출력을 그대로 흘려보내면서(로그는 평소와 동일하게 보인다) 마지막
트레이스백을 파싱해 `.scrape_run_report.json`에 누적한다.
check_scrape_health.py가 이 파일을 읽어 알림 본문을 만든다.

이 스크립트는 스크래퍼가 죽어도 **항상 0으로 종료**한다 - 회사별 격리를
유지하기 위해서다(다음 회사 수집은 계속돼야 한다). 실패를 드러내는 일은
마지막의 건전성 검사가 맡는다.

== 사용법 ==
  python tools/run_step.py fixed/recj.py
  python tools/run_step.py fixed/rehd.py --label "HD 셀럽PGM"
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_PATH = os.path.join(ROOT, ".scrape_run_report.json")

# 트레이스백 마지막 줄: "AttributeError: 'NoneType' object has no attribute 'get'"
EXC_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Exit|Timeout)):?\s*(.*)$")
# 트레이스백 위치 줄: '  File "/path/recj.py", line 175, in fetch_schedule_lineup'
FILE_LINE_RE = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')


def parse_failure(output: str) -> dict:
    """실행 출력에서 예외 종류/메시지/발생 위치를 뽑는다."""
    lines = [ln.rstrip() for ln in output.splitlines() if ln.strip()]

    exc_type, exc_msg = "", ""
    for line in reversed(lines):
        m = EXC_LINE_RE.match(line.strip())
        if m:
            exc_type, exc_msg = m.group(1), m.group(2).strip()
            break

    # 트레이스백에서 우리 코드(리포 안 파일)의 가장 마지막 프레임을 위치로 쓴다
    where = ""
    for line in lines:
        m = FILE_LINE_RE.search(line)
        if m and "site-packages" not in m.group(1):
            where = f"{os.path.basename(m.group(1))}:{m.group(2)} ({m.group(3)})"

    if not exc_type:
        # 트레이스백 없이 죽은 경우(예: 스크립트가 sys.exit(1)) - 마지막 줄을 단서로
        exc_msg = lines[-1][:200] if lines else "출력 없음"

    return {"excType": exc_type, "excMessage": exc_msg, "where": where}


def load_report() -> dict:
    if os.path.exists(REPORT_PATH):
        try:
            with open(REPORT_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {"steps": []}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("script", help="실행할 파이썬 스크립트 경로")
    parser.add_argument("--label", default="", help="사람이 읽을 이름 (없으면 경로)")
    args = parser.parse_args()

    started = time.time()
    proc = subprocess.run([sys.executable, args.script], cwd=ROOT,
                          capture_output=True, text=True)
    elapsed = time.time() - started

    # 로그는 평소처럼 그대로 보이게 흘려보낸다
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)

    entry = {
        "script": args.script,
        "label": args.label or args.script,
        "exitCode": proc.returncode,
        "durationSec": round(elapsed, 1),
    }
    if proc.returncode != 0:
        entry.update(parse_failure((proc.stdout or "") + "\n" + (proc.stderr or "")))
        print(f"\n[run_step] ❌ {args.script} 실패 (exit {proc.returncode}) "
              f"- {entry.get('excType', '')} {entry.get('excMessage', '')} "
              f"@ {entry.get('where', '위치 불명')}")
    else:
        print(f"\n[run_step] ✅ {args.script} 정상 종료 ({elapsed:.1f}초)")

    report = load_report()
    report["steps"] = [s for s in report["steps"] if s.get("script") != args.script]
    report["steps"].append(entry)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 회사별 격리 유지 - 여기서 실패를 전파하지 않는다
    return 0


if __name__ == "__main__":
    sys.exit(main())
