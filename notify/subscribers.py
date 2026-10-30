# -*- coding: utf-8 -*-
"""
텔레그램 구독자 목록 (봇에서 각자 신청한 알림 설정)

봇은 Cloudflare Worker(worker/telegram-bot.js)가 돌린다 - 버튼을 누르면 바로 답하고
설정을 Cloudflare KV 에 저장한다. 아침 발송은 여기서 Worker 의 /export 로 목록을 받아 온다.
  SUBS_URL  (GitHub Variables) Worker 주소, 예: https://hdhs-bot.xxx.workers.dev
  SUBS_KEY  (GitHub Secrets)   Worker 의 EXPORT_KEY 와 같은 값
둘 다 없으면 구독자 0명으로 보고, 기존 수신자(recipients.txt / TELEGRAM_CHAT_ID)만 받는다.

구독자 한 명:
  {"name", "username", "messages": "celeb|category|both",
   "cats": ["living", ...], "brands": ["설화수", ...], "joined", "updated"}

받는 메시지(topic):
  celeb   = 어제 셀럽PGM 운영상품           (messages 가 celeb/both)
  food / fashion / living / beauty = 카테고리 리스트 (messages 가 category/both, cats 에 있을 때)
  brand   = 특정 브랜드 방송 알림 (brands)

기존 수신자(관리자)는 봇에서 따로 설정하지 않았으면 예전처럼 식품 + 셀럽PGM 을 받는다.
"""

import os


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


_CACHE = None


def load():
    """{"users": {...}}. 한 번 받아 오면 같은 실행 안에서는 다시 안 부른다."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    url = (os.environ.get("SUBS_URL") or "").rstrip("/")
    key = os.environ.get("SUBS_KEY") or ""
    _CACHE = {"users": {}}
    if not url or not key:
        print("※ SUBS_URL / SUBS_KEY 가 없어 봇 구독자 없이 발송 (기존 수신자만)")
        return _CACHE
    import requests
    r = requests.get(url + "/export", headers={"Authorization": "Bearer " + key}, timeout=20)
    if r.status_code != 200:
        # 구독자 목록을 못 받았는데 기존 수신자에게만 보내면 신청자가 조용히 빠진다 -> 실패로 둔다
        raise SystemExit("구독자 목록을 못 받았습니다: %s %s" % (r.status_code, r.text[:200]))
    _CACHE = r.json()
    _CACHE.setdefault("users", {})
    print("봇 구독자 %d명" % len(_CACHE["users"]))
    return _CACHE


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
