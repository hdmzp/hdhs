# -*- coding: utf-8 -*-
"""
텔레그램 봇 알림 신청 접수 (GitHub Actions 가 주기적으로 실행 - telegram-requests.yml)

누군가 봇에 /start 나 아무 메시지를 보내면
  - 그 사람에게: "알림 신청 접수됐어요" 답장
  - 관리자에게: 이름·@아이디·채팅 ID·보낸 말 + recipients.txt 에 붙여넣을 한 줄
이미 recipients.txt 에 있는 사람이면 지금 받는 알림을 알려준다.

서버 없이 돌기 때문에 답장은 실행 주기만큼(보통 수십 분, GitHub 이 밀리면 더) 늦게 간다.
텔레그램은 처리 안 한 메시지를 24시간 보관하므로 그 안에만 실행되면 빠지지 않는다.

환경변수: TELEGRAM_BOT_TOKEN (필수), DRY_RUN=1 (보내지 않고 출력만, 메시지도 남겨둠)
"""

import os
import sys

import requests

import subscribers as subs
import tg

DRY_RUN = os.environ.get("DRY_RUN") == "1"


def call(method, **params):
    r = requests.post(tg.api(method), json=params, timeout=20)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not data.get("ok"):
        raise SystemExit("%s 실패: %s %s" % (method, r.status_code, r.text[:300]))
    return data["result"]


def send(chat_id, text):
    if DRY_RUN:
        print("\n----- → %s -----\n%s" % (chat_id, text))
        return True
    return tg.send(chat_id, text)


def main():
    if not tg.BOT_TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN 환경변수가 필요합니다.")
    # 예전 Cloudflare 봇용 webhook 이 걸려 있으면 getUpdates 가 막힌다 - 풀어 둔다 (쌓인 메시지는 유지)
    call("deleteWebhook", drop_pending_updates=False)
    updates = call("getUpdates", timeout=0, allowed_updates=["message"])
    if not updates:
        print("새 신청 없음")
        return

    # 같은 사람이 여러 번 보냈으면 한 번만 처리 (보낸 말은 모아서)
    people = {}
    for up in updates:
        msg = up.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            continue
        cid = str(chat["id"])
        frm = msg.get("from") or {}
        p = people.setdefault(cid, {"from": frm, "texts": []})
        text = (msg.get("text") or "").strip()
        if text and text.split("@")[0].lower() != "/start":
            p["texts"].append(text)

    users = subs.load()
    admins = subs.admins()
    for cid, p in people.items():
        frm = p["from"]
        name = " ".join(filter(None, [frm.get("first_name"), frm.get("last_name")])) or cid
        who = name + (" (@%s)" % frm["username"] if frm.get("username") else "")
        if cid in users:
            send(cid, "📮 지금 받는 알림: %s\n바꾸고 싶으면 관리자에게 말해 주세요." % tg.esc(subs.summary(users[cid])))
            print("기존 수신자 %s" % cid)
            continue
        send(cid, "✅ 알림 신청이 접수됐어요.\n관리자가 확인하고 받을 알림을 설정해 드릴게요.")
        lines = [
            "🆕 알림 신청",
            "이름: %s" % tg.esc(who),
            "ID: <code>%s</code>" % cid,
        ]
        if p["texts"]:
            lines.append("보낸 말: %s" % tg.esc(" / ".join(p["texts"]))[:500])
        lines += [
            "",
            "notify/recipients.txt 에 한 줄 추가 (받을 알림만 남기기):",
            "<code>%s food celeb fashion living beauty #%s</code>" % (cid, tg.esc(name.replace("#", ""))),
        ]
        for a in admins:
            send(a, "\n".join(lines))
        print("새 신청 %s %s" % (cid, who))

    if not DRY_RUN:
        # 처리한 메시지 확인 처리 - 다음 실행 때 다시 안 받는다
        call("getUpdates", offset=updates[-1]["update_id"] + 1, timeout=0, limit=1)
    print("처리 %d건 (사람 %d명)" % (len(updates), len(people)))


if __name__ == "__main__":
    main()
