# -*- coding: utf-8 -*-
"""
텔레그램 수신자별 알림 설정

두 곳을 합친다.
  1) 봇 신청 (worker/telegram-bot.js, Cloudflare) - 신청자가 버튼으로 고르고 관리자가 승인한 사람.
     Worker 의 /export 로 받아 온다. 열쇠는 봇 토큰에서 만들어서 따로 넣을 설정이 없다.
     주소를 바꾸려면 GitHub Variables 에 SUBS_URL.
  2) notify/recipients.txt - 관리자가 직접 적는 사람 (아래 형식)
같은 ID 가 둘 다 있으면 봇에서 고른 알림을 쓴다 (recipients.txt 의 admin·brand 는 유지).

recipients.txt 한 줄 = 한 사람:
  8921978250  food celeb admin  #홍길동
  123456789   fashion beauty brand:설화수  #김철수

알림 이름 (한글로 적어도 된다):
  food     식품    일반식품·건강식품 방송 리스트
  celeb    셀럽    어제 셀럽PGM 운영상품
  fashion  패션    의류·언더웨어·잡화/주얼리 방송 리스트
  living   리빙    리빙/주방·가전 방송 리스트
  beauty   미용    미용 방송 리스트
  brand:브랜드명    그 브랜드 방송이 있는 날 알림 (여러 개면 여러 번)
  admin    관리자  새 신청 알림·게스트 후보 같은 관리용 메시지
알림을 하나도 안 적으면 예전처럼 food + celeb 을 받는다.
GitHub Secrets 의 TELEGRAM_CHAT_ID 에 적힌 ID 도 food + celeb 을 받는다.
"""

import os
import hashlib

DEFAULT_SUBS_URL = "https://hdhs-bot.mj2040354.workers.dev"

TOPICS = {
    "food": "식품", "celeb": "셀럽PGM", "fashion": "패션",
    "living": "리빙", "beauty": "미용", "admin": "관리자",
}
ALIASES = {
    "식품": "food", "셀럽": "celeb", "셀럽pgm": "celeb", "패션": "fashion",
    "리빙": "living", "미용": "beauty", "관리자": "admin",
}
DEFAULT_TOPICS = {"food", "celeb"}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_CACHE = {}


def load(root=ROOT):
    """{채팅ID: {"name", "topics": set, "brands": [..]}} - 한 번 읽으면 같은 실행 안에서는 다시 안 읽는다."""
    if root not in _CACHE:
        users = load_file(root)
        merge_bot(users)
        _CACHE[root] = users
    return _CACHE[root]


def fetch_bot():
    """봇에서 승인된 사람 {ID: {"name", "topics": [..]}}. 못 받으면 None."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
    url = (os.environ.get("SUBS_URL") or DEFAULT_SUBS_URL).rstrip("/")
    if not token or not url:
        return None
    import requests
    key = hashlib.sha256(("export:" + token).encode()).hexdigest()
    try:
        r = requests.get(url + "/export", headers={"Authorization": "Bearer " + key}, timeout=20)
        r.raise_for_status()
        return r.json().get("users") or {}
    except Exception as e:  # 봇이 죽어 있어도 recipients.txt 사람에게는 보낸다
        print("※ 봇 신청자 목록을 못 받았습니다 (recipients.txt 만 발송): %s" % e)
        return None


def merge_bot(users):
    bot = fetch_bot()
    if bot is None:
        return
    print("봇 신청자 %d명" % len(bot))
    for cid, b in bot.items():
        topics = {t for t in (b.get("topics") or []) if t in TOPICS and t != "admin"}
        u = users.get(cid)
        if u:
            u["topics"] = topics | ({"admin"} & u["topics"])
        else:
            users[cid] = {"name": b.get("name") or "", "topics": topics, "brands": []}


def load_file(root=ROOT):
    """recipients.txt + TELEGRAM_CHAT_ID - 파일에 적힌 순서대로."""
    users = {}
    path = os.path.join(root, "notify", "recipients.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for raw in f:
                body, _, name = raw.partition("#")
                parts = body.split()
                if not parts:
                    continue
                cid, topics, brands = parts[0], set(), []
                for p in parts[1:]:
                    if p.lower().startswith(("brand:", "브랜드:")):
                        b = p.split(":", 1)[1].strip()
                        if b and b not in brands:
                            brands.append(b)
                        continue
                    key = ALIASES.get(p.lower(), p.lower())
                    if key in TOPICS:
                        topics.add(key)
                    else:
                        print("※ recipients.txt: 모르는 알림 이름 '%s' (%s) - 무시" % (p, cid))
                if not topics and not brands:
                    topics = set(DEFAULT_TOPICS)
                u = users.setdefault(cid, {"name": "", "topics": set(), "brands": []})
                u["name"] = u["name"] or name.strip()
                u["topics"] |= topics
                u["brands"] += [b for b in brands if b not in u["brands"]]
    for cid in (os.environ.get("TELEGRAM_CHAT_ID") or "").split(","):
        cid = cid.strip()
        if cid and cid not in users:
            users[cid] = {"name": "", "topics": set(DEFAULT_TOPICS), "brands": []}
    return users


def recipients_for(topic, root=ROOT):
    """topic 을 받을 채팅 ID 목록."""
    return [cid for cid, u in load(root).items() if topic in u["topics"]]


def admins(root=ROOT):
    """관리자 ID. admin 표시가 없으면 파일 첫 줄 사람."""
    users = load(root)
    ids = [cid for cid, u in users.items() if "admin" in u["topics"]]
    return ids or list(users)[:1]


def summary(u):
    names = [TOPICS[t] for t in TOPICS if t in u["topics"] and t != "admin"]
    if u["brands"]:
        names.append("브랜드(" + ", ".join(u["brands"]) + ")")
    return ", ".join(names) or "없음"
