# -*- coding: utf-8 -*-
"""
텔레그램 아침 '🔔 관심 브랜드' 알림 - 사람마다 봇에서 등록한 브랜드가 오늘 4사 편성에 있으면 보낸다.

   11/12(목)

   🔔 오늘 관심 브랜드 방송

   10:40 [GS] 세포랩
   세포랩 바이옴 앰플 ...

   📅 다음 방송 (7일 안)
   비에날씬 · 11/14(토) 08:20 [CJ]

  - 등록은 봇 '🔔 브랜드 알림' (worker/telegram-bot.js) - /export 의 users[ID].brands
      {"t": "b", "v": 브랜드명}  편성 브랜드명이 같으면 (notify/brand_alias.json 의 다른 이름도 같은 브랜드로)
      {"t": "k", "v": 단어}      브랜드명이나 상품명에 그 단어가 들어가면
    비교는 소문자·글자/숫자만 (띄어쓰기·기호 무시) - 봇의 normB 와 같다.
  - 사람마다 코드가 따로 있는 게 아니라 등록 목록(데이터)만 다르다. 편성은 한 번만 읽는다.
  - 오늘 방송이 하나도 없으면 그 사람에게는 안 보낸다 (매일 같은 '다음 방송'만 오지 않게).

== 환경변수 ==
  TELEGRAM_BOT_TOKEN (필수) / COMPANIES (기본 HD,GS,CJ,LT) / TARGET_DATE / DRY_RUN / ADMIN_ONLY
  ADMIN_ONLY=1 이면 관리자 본인 것만 보낸다 (관리자가 등록한 브랜드 기준).
"""

import os
import re
import json
import glob
import time
from datetime import timedelta

import tg
from tg import esc, chunk_lines
import subscribers
import send_daily_food as food

ROOT = food.ROOT
DRY_RUN = food.DRY_RUN
UPCOMING_DAYS = 7
ALIAS_PATH = os.path.join(ROOT, "notify", "brand_alias.json")


def norm(s):
    return re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())


def load_alias():
    """정규화한 이름 -> 같은 브랜드로 볼 정규화 이름들 (편성 브랜드명과 다른 이름 서로)."""
    try:
        with open(ALIAS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    groups = {}
    for k, v in data.items():
        if k.startswith("_") or not isinstance(v, list):
            continue
        g = {norm(k)} | {norm(x) for x in v}
        g.discard("")
        for x in g:
            groups[x] = groups.get(x, set()) | g
    return groups


def load_slots(days, companies):
    """{날짜: [(회사, 시작, [items])]} - 한 방송은 한 묶음 (이어지는 구간·구성상품 합침)."""
    out = {d: [] for d in days}
    for month in sorted({d[:7] for d in days}):
        for path in glob.glob(os.path.join(ROOT, "homeshopping", "*_live", month + ".json")):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            co = data.get("company", "")
            if companies and co not in companies:
                continue
            for d, items in (data.get("days") or {}).items():
                if d not in out:
                    continue
                by_start = {}
                for it in items:
                    slot = by_start.setdefault(it.get("start", ""), {"end": it.get("end", ""), "items": []})
                    slot["items"].append(it)
                for start, _, its in food.merge_continuous(
                        [(st, v["end"], v["items"]) for st, v in sorted(by_start.items())]):
                    out[d].append((co, start, its))
    for d in out:
        out[d].sort(key=lambda x: (x[1], x[0]))
    return out


def make_matcher(entry, alias):
    v = norm(entry.get("v"))
    if len(v) < 2:
        return None
    if entry.get("t") == "k":
        return lambda it: v in norm(it.get("brand")) + "|" + norm(it.get("product"))
    names = alias.get(v, {v})
    return lambda it: norm(it.get("brand")) in names


def label(entry):
    return "'%s' 들어간 방송" % entry["v"] if entry.get("t") == "k" else entry["v"]


def slot_lines(co, start, items):
    first = items[0]
    brand = (first.get("brand") or "").strip()
    product = food.clean_product(first.get("product") or "")
    others = {food.clean_product(i.get("product") or "") for i in items[1:]} - {product, ""}
    head = "%s [%s] %s" % (start or "??:??", food.COMPANY_NAMES.get(co, co), brand)
    link = (first.get("link") or "").strip()
    body = '<a href="%s">%s</a>' % (esc(link), esc(product)) if link else esc(product)
    if others:
        body += " 외 %d개" % len(others)
    return "<b>%s</b>\n%s" % (esc(head.strip()), body)


def short_day(d):
    from datetime import datetime
    dt = datetime.strptime(d, "%Y-%m-%d")
    return "%d/%d(%s)" % (dt.month, dt.day, food.WEEKDAY_KO[dt.weekday()])


def build_for(entries, today, slots, alias, now, weather):
    """한 사람의 메시지 줄 목록. 오늘 방송이 없으면 None."""
    matchers = [(e, make_matcher(e, alias)) for e in entries]
    matchers = [(e, m) for e, m in matchers if m]
    if not matchers:
        return None
    todays = []
    for co, start, items in slots[today]:
        if not (food.TIME_START <= start <= food.TIME_END):
            continue
        if any(m(it) for _, m in matchers for it in items):
            todays.append(slot_lines(co, start, items))
    if not todays:
        return None
    lines = food.header_lines(now, weather) + ["", "<b>🔔 오늘 관심 브랜드 방송</b> (%d회)" % len(todays), ""]
    for t in todays:
        lines += [t, ""]
    lines.pop()
    # 오늘 방송이 없는 브랜드는 다음 방송 한 줄
    upcoming = []
    for e, m in matchers:
        if any(m(it) for _, _, its in slots[today] for it in its):
            continue
        for d in sorted(slots):
            if d <= today:
                continue
            hit = next(((co, st) for co, st, its in slots[d] if any(m(it) for it in its)), None)
            if hit:
                upcoming.append("%s · %s %s [%s]" % (esc(label(e)), short_day(d), hit[1],
                                                      food.COMPANY_NAMES.get(hit[0], hit[0])))
                break
    if upcoming:
        lines += ["", "<b>📅 다음 방송 (%d일 안)</b>" % UPCOMING_DAYS] + upcoming
    lines += ["", "브랜드 바꾸기: /brand"]
    return lines


def main():
    now = food.target_date()
    today = now.strftime("%Y-%m-%d")
    companies_env = (os.environ.get("COMPANIES") or food.DEFAULT_COMPANIES).strip()
    companies = None if companies_env.upper() == "ALL" else {c.strip() for c in companies_env.split(",") if c.strip()}

    users = subscribers.load()["users"]
    if os.environ.get("ADMIN_ONLY") == "1":
        admins = set(subscribers.admins())
        users = {cid: u for cid, u in users.items() if cid in admins}
        print("※ 테스트 발송 - 관리자 본인 브랜드만")
    users = {cid: u for cid, u in users.items() if u.get("brands")}
    print("브랜드 등록한 사람 %d명" % len(users))
    if not users:
        return

    days = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(UPCOMING_DAYS + 1)]
    slots = load_slots(days, companies)
    alias = load_alias()
    weather = food.load_weather(today)

    sent = skipped = failed = 0
    for cid, u in users.items():
        lines = build_for(u["brands"], today, slots, alias, now, weather)
        if not lines:
            skipped += 1
            continue
        chunks = chunk_lines(lines)
        if DRY_RUN:
            print("\n----- %s (%s) -----\n%s" % (u.get("name") or cid, cid, "\n".join(chunks)))
            sent += 1
            continue
        for c in chunks:
            r = tg.send(cid, c)
            if r is None:
                break
            if r is False:
                failed += 1
            time.sleep(0.3)
        sent += 1
    print("브랜드 알림: %d명 보냄, 오늘 방송 없음 %d명, 실패 %d건" % (sent, skipped, failed))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
