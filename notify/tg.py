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


def send(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    resp = requests.post(api("sendMessage"), data=data, timeout=15)
    if resp.status_code != 200:
        print("발송 실패:", resp.status_code, resp.text)
        return False
    return True


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
    """정해진 채팅 ID 들에 청크를 순서대로. 하나라도 실패하면 exit 1."""
    sent = 0
    for chat_id in chat_ids:
        for i, chunk in enumerate(chunks, 1):
            if send(chat_id, chunk):
                sent += 1
            else:
                print("%s [%d/%d] 발송 실패" % (chat_id, i, len(chunks)))
            time.sleep(0.3)
    total = len(chat_ids) * len(chunks)
    print("발송 완료: %d/%d건 (수신처 %d곳)" % (sent, total, len(chat_ids)))
    if sent < total:
        sys.exit(1)
