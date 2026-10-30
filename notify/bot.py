# -*- coding: utf-8 -*-
"""
텔레그램 봇 신청 처리 - 봇에 들어온 메시지·버튼을 읽어 구독자 설정을 저장한다.

서버 없이 GitHub Actions(telegram-bot.yml, 1시간마다)가 getUpdates 로 쌓인 것을
한꺼번에 처리한다. 그래서 버튼을 누르면 답이 바로 오지 않고 다음 실행 때 온다.
(텔레그램은 안 읽은 업데이트를 24시간 보관한다)

신청 흐름 (아무나 신청 가능, 승인 없음)
  /start   -> [셀럽PGM 알림] [카테고리 알림] [둘 다]
           -> (카테고리면) 카테고리 버튼 여러 개 골라 [완료]
  /brand 설화수   특정 브랜드 방송 알림 추가 (/brand 만 보내면 목록)
  /unbrand 설화수 브랜드 알림 빼기
  /status  내 설정 보기
  /stop    알림 그만 받기
  /list    (관리자만) 구독자 표

관리자 = notify/recipients.txt / TELEGRAM_CHAT_ID 에 적힌 ID. 새 구독자가 설정을
마치면 관리자에게 알려준다.

환경변수: TELEGRAM_BOT_TOKEN (필수), TELEGRAM_CHAT_ID (선택)
"""

import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

import tg
import subscribers as subs

KST = timezone(timedelta(hours=9))
ROOT = subs.ROOT
BRAND_MAX = 20

HELP = ("📮 홈쇼핑 모니터링 알림봇\n\n"
        "/start  알림 신청·변경\n"
        "/brand 브랜드명  특정 브랜드 방송 알림 (예: /brand 설화수)\n"
        "/unbrand 브랜드명  브랜드 알림 빼기\n"
        "/status  내 설정 보기\n"
        "/stop  알림 그만 받기\n\n"
        "※ 서버 없이 돌아서 답이 바로 오지 않고 1시간쯤 뒤에 와요.")

COMMANDS = [
    {"command": "start", "description": "알림 신청·변경"},
    {"command": "status", "description": "내 설정 보기"},
    {"command": "brand", "description": "브랜드 방송 알림 추가 (/brand 설화수)"},
    {"command": "unbrand", "description": "브랜드 알림 빼기"},
    {"command": "stop", "description": "알림 그만 받기"},
]


def call(method, **data):
    try:
        r = requests.post(tg.api(method), json=data, timeout=20)
        if r.status_code != 200:
            print("%s 실패: %s %s" % (method, r.status_code, r.text[:200]))
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except requests.RequestException as e:
        print("%s 오류: %s" % (method, e))
        return {}


def now_iso():
    return datetime.now(KST).isoformat(timespec="seconds")


def messages_keyboard():
    return {"inline_keyboard": [
        [{"text": "셀럽PGM 알림", "callback_data": "m:celeb"}],
        [{"text": "카테고리 알림", "callback_data": "m:category"}],
        [{"text": "둘 다", "callback_data": "m:both"}],
    ]}


def cats_keyboard(user):
    picked = set(user.get("cats") or [])
    rows = [[{"text": ("✅ " if k in picked else "⬜ ") + name, "callback_data": "c:" + k}]
            for k, name in subs.CATEGORIES]
    rows.append([{"text": "완료", "callback_data": "done"}])
    return {"inline_keyboard": rows}


def send(chat_id, text, markup=None):
    tg.send(chat_id, text, reply_markup=markup)
    time.sleep(0.2)


def finish(data, chat_id, user, admins):
    send(chat_id, "✅ 설정 완료! 내일 아침부터 보내드려요\n\n" + tg.esc(subs.summary(user)) +
         "\n\n바꾸려면 /start, 그만 받으려면 /stop")
    if not user.get("announced"):
        user["announced"] = True
        who = user.get("name") or ""
        if user.get("username"):
            who += " (@%s)" % user["username"]
        for a in admins:
            if a != chat_id:
                send(a, "🆕 새 구독자: %s\n%s" % (tg.esc(who), tg.esc(subs.summary(user))))


def user_table(data):
    users = data.get("users", {})
    if not users:
        return "구독자가 없어요."
    lines = ["👥 구독자 %d명" % len(users), ""]
    for cid, u in sorted(users.items(), key=lambda kv: kv[1].get("joined", "")):
        who = u.get("name") or cid
        if u.get("username"):
            who += " (@%s)" % u["username"]
        lines.append("<b>%s</b> · %s" % (tg.esc(who), (u.get("joined") or "")[:10]))
        lines.append(tg.esc(subs.summary(u)))
        lines.append("")
    return "\n".join(lines).strip()


def handle_message(data, msg, admins):
    chat = msg.get("chat", {})
    if chat.get("type") != "private":
        return
    chat_id = str(chat["id"])
    text = (msg.get("text") or "").strip()
    users = data["users"]
    frm = msg.get("from", {})
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    arg = arg.strip()

    if cmd == "/start":
        user = users.setdefault(chat_id, {"joined": now_iso(), "cats": [], "brands": []})
        user["name"] = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x)
        user["username"] = frm.get("username") or ""
        user["updated"] = now_iso()
        send(chat_id, "📮 받을 알림을 골라주세요", messages_keyboard())
    elif cmd == "/stop":
        if users.pop(chat_id, None) is not None:
            send(chat_id, "알림을 그만 보낼게요. 다시 받으려면 /start")
    elif cmd == "/status":
        user = users.get(chat_id)
        send(chat_id, tg.esc(subs.summary(user)) if user else "아직 신청 전이에요. /start 를 눌러주세요.")
    elif cmd == "/brand":
        user = users.setdefault(chat_id, {"joined": now_iso(), "cats": [], "brands": [],
                                          "name": frm.get("first_name") or "", "username": frm.get("username") or ""})
        if not arg:
            send(chat_id, "알림 받는 브랜드: " + (tg.esc(", ".join(user["brands"])) if user["brands"] else "없음") +
                 "\n추가는 /brand 브랜드명")
            return
        if arg not in user["brands"]:
            if len(user["brands"]) >= BRAND_MAX:
                send(chat_id, "브랜드는 %d개까지 등록할 수 있어요." % BRAND_MAX)
                return
            user["brands"].append(arg[:30])
        user["updated"] = now_iso()
        send(chat_id, "🔔 '%s' 방송이 있는 날 아침에 알려드릴게요.\n지금 등록: %s" %
             (tg.esc(arg[:30]), tg.esc(", ".join(user["brands"]))))
    elif cmd == "/unbrand":
        user = users.get(chat_id)
        if user and arg in (user.get("brands") or []):
            user["brands"].remove(arg)
            user["updated"] = now_iso()
            send(chat_id, "'%s' 브랜드 알림을 뺐어요." % tg.esc(arg))
        else:
            send(chat_id, "등록된 브랜드가 아니에요. /brand 로 목록을 볼 수 있어요.")
    elif cmd == "/list" and chat_id in admins:
        send(chat_id, user_table(data))
    else:
        send(chat_id, HELP)


def handle_callback(data, cq, admins):
    call("answerCallbackQuery", callback_query_id=cq["id"])  # 오래된 버튼이면 실패해도 무시
    msg = cq.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if not chat_id:
        return
    user = data["users"].get(chat_id)
    if user is None:
        send(chat_id, "먼저 /start 를 눌러주세요.")
        return
    action = cq.get("data") or ""
    user["updated"] = now_iso()
    if action.startswith("m:"):
        user["messages"] = action[2:]
        if user["messages"] == "celeb":
            finish(data, chat_id, user, admins)
        else:
            send(chat_id, "📂 받을 카테고리를 골라주세요 (여러 개 가능, 누를 때마다 켜고 꺼요)", cats_keyboard(user))
    elif action.startswith("c:"):
        key = action[2:]
        if key in subs.CATEGORY_NAMES:
            cats = user.setdefault("cats", [])
            cats.remove(key) if key in cats else cats.append(key)
            call("editMessageReplyMarkup", chat_id=chat_id, message_id=msg.get("message_id"),
                 reply_markup=cats_keyboard(user))
    elif action == "done":
        if user.get("messages") in ("category", "both") and not user.get("cats"):
            send(chat_id, "카테고리를 하나 이상 골라주세요.", cats_keyboard(user))
        else:
            finish(data, chat_id, user, admins)


def main():
    if not tg.BOT_TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN 환경변수가 필요합니다.")
    data = subs.load()
    admins = tg.load_chat_ids(ROOT, detect=False)
    call("setMyCommands", commands=COMMANDS)

    handled = 0
    while True:
        r = call("getUpdates", offset=data["offset"], timeout=0,
                 allowed_updates=["message", "callback_query"])
        updates = r.get("result") or []
        if not updates:
            break
        for up in updates:
            data["offset"] = up["update_id"] + 1
            try:
                if "message" in up:
                    handle_message(data, up["message"], admins)
                elif "callback_query" in up:
                    handle_callback(data, up["callback_query"], admins)
            except Exception as e:  # 한 건 때문에 나머지를 놓치지 않게
                print("처리 실패 update %s: %s" % (up.get("update_id"), e))
            handled += 1
    print("처리 %d건 · 구독자 %d명" % (handled, len(data["users"])))
    if handled:
        subs.save(data)


if __name__ == "__main__":
    main()
