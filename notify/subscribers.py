# -*- coding: utf-8 -*-
"""
텔레그램 구독자 저장소 (봇에서 각자 신청한 알림 설정)

notify/subscribers.enc 에 암호화해서 저장한다. 저장소가 공개라 텔레그램 ID·이름을
평문으로 두지 않는다. 키는 TELEGRAM_BOT_TOKEN 에서 만든다 (따로 등록할 비밀값 없음).
※ 봇 토큰을 새로 발급하면 기존 파일을 못 읽는다 -> 구독자가 /start 를 다시 해야 한다.

복호화한 내용:
  {"offset": 마지막으로 처리한 update_id + 1,
   "users": {"<chat_id>": {"name", "username", "messages": "celeb|category|both",
                           "cats": ["living", ...], "brands": ["설화수", ...],
                           "joined", "updated"}}}

받는 메시지(topic):
  celeb   = 어제 셀럽PGM 운영상품           (messages 가 celeb/both)
  food / fashion / living / beauty = 카테고리 리스트 (messages 가 category/both, cats 에 있을 때)
  brand   = 특정 브랜드 방송 알림 (brands)

notify/recipients.txt / TELEGRAM_CHAT_ID 에 적힌 기존 수신자(관리자)는 봇에서 따로
설정하지 않았으면 예전처럼 식품 + 셀럽PGM 을 받는다.
"""

import os
import json
import base64
import hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "notify", "subscribers.enc")

# (키, 버튼 이름) - 봇 슬라이서 순서
CATEGORIES = [
    ("living", "리빙/주방·가전"),
    ("fashion", "의류·언더웨어·잡화/주얼리"),
    ("food", "일반식품·건강식품"),
    ("beauty", "미용"),
]
CATEGORY_NAMES = dict(CATEGORIES)
MESSAGE_NAMES = {"celeb": "셀럽PGM 알림", "category": "카테고리 알림", "both": "셀럽PGM + 카테고리 알림"}
LEGACY_TOPICS = {"food", "celeb"}


def _fernet():
    from cryptography.fernet import Fernet
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return None
    key = hashlib.sha256(b"hdhs-subscribers:" + token.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def empty():
    return {"offset": 0, "users": {}}


def load():
    if not os.path.exists(PATH):
        return empty()
    f = _fernet()
    if f is None:
        print("※ TELEGRAM_BOT_TOKEN 이 없어 구독자 파일을 못 읽음")
        return empty()
    with open(PATH, "rb") as fh:
        data = json.loads(f.decrypt(fh.read().strip()).decode("utf-8"))
    data.setdefault("offset", 0)
    data.setdefault("users", {})
    return data


def save(data):
    f = _fernet()
    if f is None:
        raise SystemExit("TELEGRAM_BOT_TOKEN 이 없어 구독자 파일을 저장할 수 없습니다.")
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    with open(PATH, "wb") as fh:
        fh.write(f.encrypt(raw) + b"\n")


def topics_of(user):
    topics = set()
    msgs = user.get("messages")
    if msgs in ("celeb", "both"):
        topics.add("celeb")
    if msgs in ("category", "both"):
        topics.update(user.get("cats") or [])
    if user.get("brands"):
        topics.add("brand")
    return topics


def recipients_for(topic, legacy_ids, data=None):
    """topic 을 받을 채팅 ID 목록. 봇 구독자 + (봇 설정이 없는) 기존 수신자."""
    data = data if data is not None else load()
    users = data.get("users", {})
    ids = [cid for cid, u in users.items() if topic in topics_of(u)]
    for cid in legacy_ids:
        if cid not in users and topic in LEGACY_TOPICS and cid not in ids:
            ids.append(cid)
    return ids


def summary(user):
    lines = ["받는 알림: " + MESSAGE_NAMES.get(user.get("messages"), "아직 안 고름")]
    if user.get("messages") in ("category", "both"):
        cats = [CATEGORY_NAMES[k] for k, _ in CATEGORIES if k in (user.get("cats") or [])]
        lines.append("카테고리: " + (", ".join(cats) if cats else "없음"))
    if user.get("brands"):
        lines.append("브랜드: " + ", ".join(user["brands"]))
    return "\n".join(lines)
