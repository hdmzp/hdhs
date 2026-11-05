# -*- coding: utf-8 -*-
"""
텔레그램 알림 받는 사람 = 봇에서 승인된 사람 (한 곳에서만 관리)

신청자가 봇(worker/telegram-bot.js, Cloudflare)에서 START → 버튼으로 알림 고르기 → 관리자 승인.
아침 발송은 여기서 Worker 의 /export 로 승인된 사람과 관리자 ID 를 받아 온다.
열쇠는 봇 토큰(TELEGRAM_BOT_TOKEN)에서 만들어서 따로 넣을 설정이 없다.
Worker 주소를 바꾸려면 GitHub Variables 에 SUBS_URL.

알림 이름: celeb 셀럽PGM / fashion 패션 / beauty 미용 / food 식품 / living 리빙
관리용 메시지(게스트 후보 등)는 관리자(wrangler.jsonc 의 ADMIN_ID)에게 간다.
"""

import os
import sys
import hashlib

DEFAULT_SUBS_URL = "https://hdhs-bot.mj2040354.workers.dev"
TOPICS = {"celeb": "셀럽PGM", "fashion": "패션", "beauty": "미용", "food": "식품", "living": "리빙"}

_CACHE = None


def load():
    """{"users": {ID: {"name", "topics": [..]}}, "admins": [ID, ..]} - 한 실행에 한 번만 받아 온다.

    못 받으면 실패로 끝낸다 (조용히 아무에게도 안 보내는 것보다 Actions 에 빨간 표시가 낫다)."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
    url = (os.environ.get("SUBS_URL") or DEFAULT_SUBS_URL).rstrip("/")
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN 환경변수가 필요합니다.")
    import requests
    key = hashlib.sha256(("export:" + token).encode()).hexdigest()
    try:
        r = requests.get(url + "/export", headers={"Authorization": "Bearer " + key}, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        sys.exit("봇에서 받는 사람 목록을 못 받았습니다 (%s): %s" % (url, e))
    _CACHE = {"users": data.get("users") or {}, "admins": [str(a) for a in data.get("admins") or []]}
    print("받는 사람 %d명 (봇 승인)" % len(_CACHE["users"]))
    return _CACHE


def recipients_for(topic):
    """topic 을 받을 채팅 ID 목록."""
    return [cid for cid, u in load()["users"].items() if topic in (u.get("topics") or [])]


def admins():
    return load()["admins"]
