# -*- coding: utf-8 -*-
"""
수집 파이프라인 오류를 텔레그램으로 알린다.

GitHub Actions 화면을 매일 들여다보지 않아도 "오늘 CJ 셀럽PGM이 안 걷혔다"를
바로 알 수 있게 하는 것이 목적. 워크플로우의 실패 스텝에서만 호출된다
(`if: steps.health.outcome == 'failure'`).

== 메시지 형식 ==
    ⛔️HDHS 오류감지

    ▶️fixed/recj.py
    -AttributeError: 'NoneType' object has no attribute 'get' @ recj.py:175
    -API가 해당 필드를 null로 준 경우 - 응답 접근부에 `or {}` 방어 추가

    ▶️rep-pgm-scrape.yml
    -셀럽PGM 산출물 4건 미갱신 (CJ 4개 프로그램)
    -스크래퍼 수정 후 워크플로우 재실행 필요

코드(스크립트) 단위 블록 + 워크플로우 단위 블록으로 나눠 보낸다.
어느 파일을 열어야 하는지(코드명), 무엇이 잘못됐는지(오류내용),
무엇을 하면 되는지(수정권장사항)가 한 화면에 들어오게 하기 위함.

== 입력 ==
  .scrape_alert.json   check_scrape_health.py가 남긴 구조화된 실패 내역 (우선)
  ALERT_DETAIL         위 파일이 없을 때 쓰는 평문 요약 (백업)

== 환경변수 ==
  TELEGRAM_BOT_TOKEN  (필수, 없으면 조용히 종료 - 알림 미설정이 파이프라인을
                       더 망가뜨리면 안 되므로)
  TELEGRAM_CHAT_ID    (선택, notify/recipients.txt와 합집합)
  ALERT_WORKFLOW      워크플로우 표시명 (없으면 GITHUB_WORKFLOW)
  ALERT_RUN_URL       실행 로그 URL
  DRY_RUN=1           발송하지 않고 메시지만 출력 (형식 확인용)

== 사용법 ==
  python notify/send_alert.py            # 실패 내역으로 발송
  python notify/send_alert.py --test     # 예시 메시지 발송 (형식 확인용)
  DRY_RUN=1 python notify/send_alert.py --test   # 발송 없이 출력만
"""

import argparse
import json
import os
import sys

from tg import esc, chunk_lines, broadcast

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERT_PATH = os.path.join(ROOT, ".scrape_alert.json")

TITLE = "⛔️HDHS 오류감지"

SAMPLE = {
    "group": "celeb",
    "workflow": "Representative PGM Scrape",
    "workflowFile": "rep-pgm-scrape.yml",
    "runUrl": "",
    "summary": "celeb 산출물 4건 실패",
    "codes": [
        {"name": "fixed/recj.py", "items": [
            {"problem": "AttributeError: 'NoneType' object has no attribute 'get' "
                        "@ recj.py:175 (fetch_schedule_lineup)",
             "suggestion": "API가 해당 필드를 null로 준 경우 - 응답 접근부에 `or {}` 방어 추가"},
            {"problem": "CJ_KJE.json - 이번 실행에서 갱신 안 됨 (마지막 기록 180분 전)",
             "suggestion": "fixed/recj.py 실행 로그에서 예외 확인 후 재실행"},
        ]},
        {"name": "fixed/regs.py", "items": [
            {"problem": "GS_BJY.json - 급감: 28 -> 3 (직전의 11%, 임계 40%)",
             "suggestion": "사이트 구조 변경 의심 - 셀렉터가 아직 유효한지 확인"},
        ]},
    ],
}


def build_lines(data: dict, is_test: bool = False) -> list:
    lines = [f"<b>{TITLE}</b>"]
    if is_test:
        lines.append("<i>(형식 확인용 예시 - 실제 오류 아님)</i>")
    lines.append("")

    for code in data.get("codes") or []:
        lines.append(f"▶️<b>{esc(code.get('name', '(코드 불명)'))}</b>")
        for item in code.get("items") or []:
            lines.append(f"-{esc(item.get('problem', ''))}")
            suggestion = item.get("suggestion", "")
            if suggestion:
                lines.append(f"-{esc(suggestion)}")
        lines.append("")

    # 워크플로우 블록: 이 실행 자체를 어떻게 처리하면 되는지
    wf_name = (os.environ.get("ALERT_WORKFLOW")
               or data.get("workflowFile")
               or data.get("workflow")
               or os.environ.get("GITHUB_WORKFLOW", "워크플로우"))
    code_count = len(data.get("codes") or [])
    lines.append(f"▶️<b>{esc(wf_name)}</b>")
    lines.append(f"-{esc(data.get('summary') or '수집 건전성 검사 실패')}"
                 f"{f' (코드 {code_count}곳)' if code_count else ''}")
    lines.append("-수정 후 워크플로우 수동 재실행 필요 "
                 "(일시적 장애면 재실행만으로 복구)")

    run_url = os.environ.get("ALERT_RUN_URL") or data.get("runUrl") or ""
    if run_url:
        lines += ["", f'<a href="{esc(run_url)}">실행 로그 열기</a>']

    return lines


def load_alert(is_test: bool) -> dict:
    if is_test:
        return SAMPLE
    if os.path.exists(ALERT_PATH):
        try:
            with open(ALERT_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    # 구조화 파일이 없으면 평문 요약이라도 보낸다
    detail = (os.environ.get("ALERT_DETAIL") or "").strip()
    items = [{"problem": d.strip(), "suggestion": ""}
             for d in detail.split(";") if d.strip()]
    return {
        "codes": [{"name": "(상세 없음)", "items": items}] if items else [],
        "summary": "수집 건전성 검사 실패 (상세 없음) - 실행 로그 확인 필요",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                        help="예시 메시지를 보낸다 (형식 확인용)")
    args = parser.parse_args()

    data = load_alert(args.test)
    lines = build_lines(data, is_test=args.test)

    if os.environ.get("DRY_RUN") == "1":
        print("\n".join(lines))
        return 0

    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        print("[알림] TELEGRAM_BOT_TOKEN이 없어 알림을 건너뜁니다. 메시지 본문:")
        print("\n".join(lines))
        return 0

    broadcast(ROOT, chunk_lines(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
