# -*- coding: utf-8 -*-
"""
텔레그램 봇에서 /brand 로 등록한 브랜드의 오늘 방송을 사람마다 따로 알려준다.

   🔔 10/31(토) 브랜드 방송 알림

   설화수
   10:25 [CJ] 설화수
   자음생 크림 세트

브랜드 칸 또는 상품명에 등록한 글자가 들어가면 (대소문자 무시) 그 방송으로 본다.
회사·시간대는 식품 리스트와 같다 (COMPANIES / TIME_START / TIME_END).
방송이 없는 날은 안 보낸다.

환경변수: TELEGRAM_BOT_TOKEN (필수), TARGET_DATE / DRY_RUN / COMPANIES / TIME_START / TIME_END
"""

import os
import json
import glob
import time

import tg
import subscribers as subs
import send_daily_food as food

ROOT = food.ROOT


def load_slots(day_str, month_str, companies):
    """[(start, company, [items])] - 한 회사 같은 시작시각 = 한 방송."""
    slots = {}
    for path in glob.glob(os.path.join(ROOT, "homeshopping", "*_live", month_str + ".json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        company = data.get("company", "")
        if companies and company not in companies:
            continue
        for item in data.get("days", {}).get(day_str, []):
            start = item.get("start", "")
            if food.TIME_START <= start <= food.TIME_END:
                slots.setdefault((start, company), []).append(item)
    return [(s, c, items) for (s, c), items in sorted(slots.items())]


def matches(brand, items):
    b = brand.lower().replace(" ", "")
    for it in items:
        text = ((it.get("brand") or "") + " " + (it.get("product") or "")).lower().replace(" ", "")
        if b and b in text:
            return it
    return None


def build_lines(now, brands, slots):
    lines = ["🔔 %02d/%02d(%s) 브랜드 방송 알림" % (now.month, now.day, food.WEEKDAY_KO[now.weekday()])]
    found = 0
    for brand in brands:
        hits = [(s, c, matches(brand, items)) for s, c, items in slots]
        hits = [h for h in hits if h[2]]
        if not hits:
            continue
        lines += ["", "<b>%s</b>" % tg.esc(brand)]
        for start, company, it in hits:
            found += 1
            product = food.clean_product(it.get("product") or "")
            link = (it.get("link") or "").strip()
            body = '<a href="%s">%s</a>' % (tg.esc(link), tg.esc(product)) if link else tg.esc(product)
            lines.append("%s [%s] %s" % (start, food.COMPANY_NAMES.get(company, company), body))
    return lines, found


def main():
    now = food.target_date()
    day_str, month_str = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    env = (os.environ.get("COMPANIES") or food.DEFAULT_COMPANIES).strip()
    companies = None if env.upper() == "ALL" else {c.strip() for c in env.split(",") if c.strip()}

    users = {cid: u for cid, u in subs.load().get("users", {}).items() if u.get("brands")}
    if not users:
        print("브랜드 알림 신청자가 없어 발송 생략")
        return
    slots = load_slots(day_str, month_str, companies)
    sent = 0
    for cid, u in users.items():
        lines, found = build_lines(now, u["brands"], slots)
        if not found:
            continue
        for chunk in tg.chunk_lines(lines):
            if food.DRY_RUN:
                print("\n----- %s -----\n%s" % (cid, chunk))
            elif tg.send(cid, chunk):
                sent += 1
            time.sleep(0.3)
    print("브랜드 알림 %d건 발송 (신청자 %d명)" % (sent, len(users)))


if __name__ == "__main__":
    main()
