# -*- coding: utf-8 -*-
"""텔레그램 발송 공통 모듈 (notify/ 스크립트들이 공유)"""

import os
import sys
import json
import time
import requests

# 텔레그램 메시지 최대 4,096자 — 여유를 두고 자른다
TEXT_LIMIT = 4000

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")


def esc(s):
    """텔레그램 HTML parse_mode 용 이스케이프"""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def chunk_lines(lines, limit=TEXT_LIMIT):
    """줄 단위로 최대 limit자 이내의 청크들로 묶는다."""
    chunks = []
    cur = ""
    for line in lines:
        # 한 줄이 limit을 넘으면 잘라낸다
        if len(line) > limit:
            line = line[: limit - 1] + "…"
        candidate = line if not cur else cur + "\n" + line
        if len(candidate) > limit:
            if cur:
                chunks.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def api(method):
    return "https://api.telegram.org/bot%s/%s" % (BOT_TOKEN, method)


# 받는 사람이 봇을 차단했거나 탈퇴한 경우 - 실패로 세지 않고 건너뛴다 (한 명 때문에 매일 빨간불이 뜨지 않게)
GONE_HINTS = ("bot was blocked", "user is deactivated", "chat not found", "bot can't initiate")


def send(chat_id, text, reply_markup=None):
    """보내면 True, 실패면 False, 받을 수 없는 사람(차단·탈퇴)이면 None.

    텔레그램이 너무 빠르다고 하면(429) 알려준 시간만큼 쉬고 다시, 일시적 오류(5xx·네트워크)는 3번까지 다시."""
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    for attempt in range(1, 6):
        try:
            resp = requests.post(api("sendMessage"), data=data, timeout=15)
        except requests.RequestException as e:
            print("발송 오류 (%d/5): %s" % (attempt, e))
            time.sleep(2 * attempt)
            continue
        if resp.status_code == 200:
            return True
        body = resp.text[:300]
        if resp.status_code == 429:
            try:
                wait = int(resp.json().get("parameters", {}).get("retry_after", 5))
            except ValueError:
                wait = 5
            print("텔레그램 속도 제한 - %d초 쉬고 다시 (%d/5)" % (wait, attempt))
            time.sleep(wait + 1)
            continue
        if resp.status_code == 403 or any(h in body for h in GONE_HINTS):
            print("받을 수 없는 사람 건너뜀 %s: %s" % (chat_id, body))
            return None
        if resp.status_code >= 500:
            print("텔레그램 서버 오류 %d (%d/5)" % (resp.status_code, attempt))
            time.sleep(2 * attempt)
            continue
        print("발송 실패:", resp.status_code, body)
        return False
    print("발송 실패: 재시도 소진", chat_id)
    return False


def broadcast(root, chunks, topic=None):
    """수신처에 청크들을 순서대로 발송. 실패 시 exit 1.

    topic 을 주면 봇에서 그 알림을 골라 승인된 사람에게만 보낸다 (notify/subscribers.py).
    topic 이 없거나 ADMIN_ONLY=1 (수동 테스트) 이면 관리자에게만 보낸다."""
    if not BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN 환경변수가 필요합니다.")
        sys.exit(1)
    import subscribers
    if os.environ.get("ADMIN_ONLY") == "1":
        print("※ 테스트 발송 - 관리자에게만 (%s)" % (topic or "관리용"))
        chat_ids = subscribers.admins()
    else:
        chat_ids = subscribers.recipients_for(topic) if topic else subscribers.admins()
    if not chat_ids:
        print("'%s' 알림 받는 사람이 없어 발송 생략" % (topic or "관리자"))
        return
    send_all(chunks, chat_ids)


def send_all(chunks, chat_ids):
    """정해진 채팅 ID 들에 청크를 순서대로. 실패가 하나라도 있으면 exit 1 (받을 수 없는 사람은 제외)."""
    sent = gone = failed = 0
    for chat_id in chat_ids:
        for i, chunk in enumerate(chunks, 1):
            r = send(chat_id, chunk)
            if r is None:
                gone += 1
                break  # 차단·탈퇴한 사람에게 나머지 조각은 안 보낸다
            if r:
                sent += 1
            else:
                failed += 1
                print("%s [%d/%d] 발송 실패" % (chat_id, i, len(chunks)))
            time.sleep(0.3)  # 텔레그램 한도(초당 30건) 아래로 여유 있게
    print("발송 완료: %d건 성공, 실패 %d건, 받을 수 없는 사람 %d명 (수신처 %d곳 x %d건)"
          % (sent, failed, gone, len(chat_ids), len(chunks)))
    if failed:
        sys.exit(1)
