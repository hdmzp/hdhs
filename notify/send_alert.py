# -*- coding: utf-8 -*-
"""
수집 파이프라인 실패를 텔레그램으로 알린다.

워크플로우의 실패 스텝에서만 호출된다 (`if: failure()`).
GitHub Actions 화면을 매일 들여다보지 않아도 "오늘 CJ 셀럽PGM이 안 걷혔다"를
바로 알 수 있게 하는 것이 목적이다.

== 환경변수 ==
  TELEGRAM_BOT_TOKEN  (필수)
  TELEGRAM_CHAT_ID    (선택, notify/recipients.txt와 합집합)
  ALERT_TITLE         알림 제목 (예: "셀럽PGM 수집 실패")
  ALERT_DETAIL        실패 상세 (check_scrape_health.py의 failures 출력)
  ALERT_RUN_URL       해당 워크플로우 실행 URL

토큰이 없으면 조용히 종료한다 (알림 미설정이 워크플로우를 더 망가뜨리면 안 된다).
"""

import os
import sys

from tg import esc, chunk_lines, broadcast

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        print("[알림] TELEGRAM_BOT_TOKEN이 없어 알림을 건너뜁니다.")
        return 0

    title = os.environ.get("ALERT_TITLE", "수집 파이프라인 실패")
    detail = os.environ.get("ALERT_DETAIL", "").strip()
    run_url = os.environ.get("ALERT_RUN_URL", "").strip()

    lines = [f"🚨 <b>{esc(title)}</b>", ""]

    if detail:
        # check_scrape_health.py가 "경로: 사유; 경로: 사유" 형태로 넘긴다
        for item in [d.strip() for d in detail.split(";") if d.strip()]:
            lines.append(f"• {esc(item)}")
    else:
        lines.append("• 스텝 실패 (상세 없음) - 실행 로그를 확인하세요")

    if run_url:
        lines += ["", f'<a href="{esc(run_url)}">실행 로그 열기</a>']

    broadcast(ROOT, chunk_lines(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
