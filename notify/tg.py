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
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


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


def detect_chat_id():
    """봇이 최근 받은 메시지에서 채팅 ID를 찾는다."""
    resp = requests.get(api("getUpdates"), timeout=15)
    if resp.status_code == 200:
        for update in reversed(resp.json().get("result", [])):
            msg = update.get("message") or update.get("edited_message") or {}
            chat = msg.get("chat", {})
            if chat.get("id"):
                print("채팅 ID 자동 감지: %s (%s)" % (chat["id"], chat.get("first_name", "")))
                return str(chat["id"])
    print(
        "채팅 ID를 찾지 못했습니다. 텔레그램에서 봇에게 아무 메시지나 먼저 보낸 뒤\n"
        "다시 실행하거나, TELEGRAM_CHAT_ID 또는 notify/recipients.txt 를 설정해 주세요."
    )
    sys.exit(1)


def load_chat_ids(root, detect=True):
    """notify/recipients.txt + TELEGRAM_CHAT_ID 합집합. 없으면 자동 감지."""
    chat_ids = []
    rec_path = os.path.join(root, "notify", "recipients.txt")
    if os.path.exists(rec_path):
        with open(rec_path, encoding="utf-8") as f:
            for raw in f:
                # '#' 뒤는 주석 — 줄 끝 주석("12345 #이름")도 허용
                entry = raw.split("#")[0].strip()
                if entry and entry not in chat_ids:
                    chat_ids.append(entry)
    for c in CHAT_ID.split(","):
        c = c.strip()
        if c and c not in chat_ids:
            chat_ids.append(c)
    if not chat_ids and detect:
        chat_ids = [detect_chat_id()]
    return chat_ids


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

    topic 을 주면 봇에서 그 알림을 신청한 사람(notify/subscribers.py)에게만 보낸다.
    기존 수신자(recipients.txt / TELEGRAM_CHAT_ID)는 봇 설정이 없으면 식품·셀럽PGM만 받는다."""
    if not BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN 환경변수가 필요합니다.")
        sys.exit(1)
    if topic:
        import subscribers
        chat_ids = subscribers.recipients_for(topic, load_chat_ids(root, detect=False))
        if not chat_ids:
            print("'%s' 알림 신청자가 없어 발송 생략" % topic)
            return
    else:
        chat_ids = load_chat_ids(root)
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
