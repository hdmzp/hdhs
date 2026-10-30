# -*- coding: utf-8 -*-
"""
텔레그램 봇으로 '어제의 셀럽PGM 운영상품'을 발송 (식품 리스트와 별도 메시지)

homeshopping/representative_programs/history/{YYYY-MM}.json (셀럽PGM 월 누적)에서
어제 방송한 회차를 모아 아래 형식으로 보낸다.

   📺 10/29(목) 셀럽PGM 운영상품

   🔵HD
   어제 20:45에는 슈캐시미어 26FW 캐시미어100 후드가 방송됐어요

   🟢GS
   20:45 지금 백지연 · 브랜드 5개
   · LG HOM-BOT AI 오브제컬렉션 RONi 히든스테이션
   ...

   📌 특이사항
   · CJ 최화정쇼 10/14(수) 하루 2회 방송 (08:15, 20:45 · 평소 수 20:45)

== 규칙 ==
- 회사 순서 HD → GS → CJ → LT, 회사 안에서는 방송 시각 순.
- 상품은 브랜드당 1개. 브랜드가 불분명하면(빈 값·'GS SHOP' 같은 자리표시자)
  상품명으로 보고, 상품명이 거의 같은 상품도 1개로 줄인다.
- 상품명은 꼬리표([..] (..) 【..】)와 '+ 구성'을 지워 짧게, 판매 링크를 건다. 가격은 안 쓴다.
- 셀럽PGM 방송이 없던 회사는 그날 셀럽PGM이 가장 많이 몰린 시각(없으면 20:45)에
  그 회사 편성표(homeshopping/{회사}_live)에서 방송된 상품을 한 줄로 쓴다.
- 특이사항: 셀럽PGM 탭 '방송 체크'와 같은 기준 (그 주 시작 전 12주 최빈값)으로
  어제 회차의 다른 요일·시간 / 하루 N회 / 휴방 / 평소 방송일인데 기록 없음.

== 환경변수 ==
  TELEGRAM_BOT_TOKEN  (필수) BotFather 에서 발급받은 봇 토큰
  TELEGRAM_CHAT_ID    (선택) 수신처 (notify/recipients.txt 와 합집합)
  TARGET_DATE         (선택) YYYY-MM-DD, 이 날짜의 방송을 보낸다 (기본: 어제 KST)
  DRY_RUN             (선택) "1"이면 발송하지 않고 메시지만 출력
"""

import os
import re
import json
from collections import Counter
from datetime import datetime, date, timedelta, timezone

from tg import esc, chunk_lines, broadcast

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DIR = os.path.join(ROOT, "homeshopping", "representative_programs", "history")
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"

COMPANIES = [("HD", "🔵"), ("GS", "🟢"), ("CJ", "🟣"), ("LT", "🔴")]
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]
DEFAULT_PRIME = "20:45"
LOOKBACK_DAYS = 84  # 평소 = 직전 12주 (셀럽PGM 탭 CEL_NEWS_LOOKBACK_DAYS와 같음)
# 요일이 매번 바뀌거나 비정기라 '평소 방송일인데 기록 없음'을 안 따지는 프로그램
# (셀럽PGM 탭 CEL_NEWS_MISSING_SKIP과 같음)
MISSING_SKIP = {"CJ_SIH", "CJ_KSY"}
# 브랜드 칸에 들어오는 자리표시자 - 브랜드로 안 본다
PLACEHOLDER_BRANDS = {"", "GS SHOP", "1등급"}


def target_day():
    s = os.environ.get("TARGET_DATE", "")
    if s:
        return date.fromisoformat(s)
    return datetime.now(KST).date() - timedelta(days=1)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def shift_month(d, delta):
    y, m = d.year, d.month + delta
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return "%04d-%02d" % (y, m)


# ---------------- 상품명 정리 / 중복 제거 ----------------

# 상품명에 붙는 홍보 문구 - 지운다
PROMO_PATTERNS = [
    r"올해\s*(단\s*)?한\s*번!?", r"단\s*한\s*번!?", r"최초\s*공개!?", r"방송\s*최초!?",
    r"\S*쇼\s*단독", r"\S*쇼\s*패키지", r"단독\s*구성", r"단독", r"NEW", r"●", r"★", r"☆",
]


def clean_name(name):
    n = name or ""
    # 겹친 꼬리표('[[1등급]LG ...')까지 지워지도록 반복
    for _ in range(3):
        n = re.sub(r"\[[^\[\]]*\]|\([^()]*\)|【[^】]*】", " ", n)
    n = n.replace("[", " ").replace("]", " ")
    n = re.split(r"\s\+\s|\+|_", n)[0]
    for pat in PROMO_PATTERNS:
        n = re.sub(pat, " ", n)
    n = re.sub(r"\s+", " ", n).strip(" ,/·!")
    # 끝의 옵션 수량(6박스*14포, 120팩, 3종, 155ml 등)은 뗀다
    unit = r"(박스|팩|포|병|통|종|개|입|매|세트|SET|set|인분|봉|캔|kg|g|ml|mL|L|구|EA|ea|미)"
    n = re.sub(r"(\s+(총\s*)?[\dx×*,.]+\s*%s?([*x×]\s*[\d,.]+\s*%s?)*)+$" % (unit, unit), "", n)
    n = re.sub(r"\s+x$", "", n)
    n = n.strip(" ,/·")
    # 수량만 남았으면('1병', '155ml') 빈 이름 - 브랜드만 쓰게 한다
    if re.fullmatch(r"[\dx×*,.\s]*%s?" % unit, n):
        return ""
    return n


NAME_MAX = 40  # 한 줄이 너무 길어지지 않게


def brand_of(product):
    """브랜드 칸 값 (자리표시자면 상품명 첫 단어). 괄호 병기는 뗀다 ('모라가(MORAGA)' -> '모라가')."""
    brand = clean_name((product.get("brand") or "").strip())
    if brand in PLACEHOLDER_BRANDS:
        words = clean_name(product.get("name")).split()
        brand = words[0] if words else ""
    return brand


def display_name(brand, name):
    """브랜드가 상품명 안에 다시 나오면 거기서부터 쓰고, 없으면 '브랜드 상품명'."""
    if not brand or not name:
        text = name or brand
    else:
        idx = name.lower().find(brand.lower())
        text = name[idx:] if idx >= 0 else "%s %s" % (brand, name)
    return text if len(text) <= NAME_MAX else text[:NAME_MAX - 1].rstrip() + "…"


def name_tokens(name):
    n = re.sub(r"\d+[a-zA-Z가-힣]*", "", (name or "").lower())
    return {t for t in re.split(r"[\s,/·\-]+", n) if len(t) > 1}


def pick_products(products):
    """브랜드당 1개, 상품명이 거의 같으면 1개. [(표시명, 링크)]"""
    kept = []  # (brand, tokens, display, link)
    for p in products:
        brand = brand_of(p)
        name = clean_name(p.get("name"))
        if not name and not brand:
            continue
        toks = name_tokens(name or brand)
        dup = False
        for k_brand, k_toks, _, _ in kept:
            if brand and brand == k_brand:
                dup = True
                break
            if toks and k_toks and len(toks & k_toks) / min(len(toks), len(k_toks)) >= 0.5:
                dup = True
                break
        if dup:
            continue
        display = display_name(brand, name)
        kept.append((brand, toks, display, (p.get("link") or "").strip()))
    return [(d, l) for _, _, d, l in kept], len({k[0] or k[2] for k in kept})


# ---------------- 데이터 ----------------

def load_programs(day):
    """program_key -> {company, title, broadcasts:[{date, hm, start(datetime), off_air, products}]}"""
    progs = {}
    for delta in (-3, -2, -1, 0):
        hist = load_json(os.path.join(HISTORY_DIR, shift_month(day, delta) + ".json")) or {}
        for p in hist.get("programs") or []:
            key = p.get("program_key")
            prog = progs.setdefault(key, {"company": p.get("company", ""),
                                          "title": p.get("program_title") or p.get("tab_name") or key,
                                          "broadcasts": {}})
            for b in p.get("broadcasts") or []:
                m = re.search(r"(\d{1,2}):(\d{2})", b.get("label") or "")
                d = b.get("date")
                if not d:
                    continue
                hm = "%02d:%s" % (int(m.group(1)), m.group(2)) if m else ""
                bd = date.fromisoformat(d)
                start = datetime(bd.year, bd.month, bd.day, int(hm[:2]) if hm else 0, int(hm[3:]) if hm else 0)
                prog["broadcasts"]["%s#%s" % (d, hm)] = {
                    "date": d, "hm": hm, "start": start, "wd": bd.weekday(),
                    "off_air": bool(b.get("off_air")), "products": b.get("products") or [],
                }
    for prog in progs.values():
        prog["broadcasts"] = sorted(prog["broadcasts"].values(), key=lambda b: b["start"])
    return progs


def mode(values):
    c = Counter(values)
    if not c:
        return None, c
    best = max(c.items(), key=lambda kv: (kv[1], kv[0]))
    return best[0], c


def usual_of(broadcasts, week_start):
    """그 주 시작 전 12주 기준 평소 요일·요일별 시각·주간/하루 횟수 (셀럽PGM 탭 celBuildNews와 같은 규칙)"""
    since = week_start - timedelta(days=LOOKBACK_DAYS)
    past = [b for b in broadcasts if not b["off_air"] and b["hm"]
            and since <= b["start"] < week_start]
    _, wd_counts = mode([b["wd"] for b in past])
    top = max(wd_counts.values()) if wd_counts else 0
    days = sorted(wd for wd, n in wd_counts.items() if n >= max(2, -(-top // 2)))
    times = {wd: mode([b["hm"] for b in past if b["wd"] == wd])[0] for wd in days}
    per_week = Counter((b["start"] - timedelta(days=b["start"].weekday())).date() for b in past)
    per_day = Counter(b["date"] for b in past)
    return {
        "days": days, "times": times,
        "per_week": mode(list(per_week.values()))[0] or 1,
        "per_day": mode(list(per_day.values()))[0] or 1,
        "overall_time": mode([b["hm"] for b in past])[0],
    }


def hm_text(hm):
    h, m = hm.split(":")
    return "%s시" % h if m == "00" else hm


def josa(word, batchim, no_batchim):
    hangul = re.findall(r"[가-힣]", word or "")
    if not hangul:
        return no_batchim
    return batchim if (ord(hangul[-1]) - 0xAC00) % 28 else no_batchim


def build_notes(progs, day):
    notes = []
    day_iso = day.isoformat()
    week_start = datetime.combine(day - timedelta(days=day.weekday()), datetime.min.time())
    for key, prog in progs.items():
        co, title = prog["company"], prog["title"]
        who = "%s %s" % (co, title)
        today_bs = [b for b in prog["broadcasts"] if b["date"] == day_iso]
        if any(b["off_air"] for b in today_bs):
            notes.append("%s %s 휴방이었어요" % (who, josa(title, "은", "는")))
            continue
        aired = [b for b in today_bs if b["hm"]]
        u = usual_of(prog["broadcasts"], week_start)
        if not u["days"]:
            continue  # 평소를 정할 기록이 부족 (신규·중간 편입 직후)
        usual_text = " / ".join("%s %s" % (WEEKDAY_KO[wd], hm_text(u["times"][wd])) for wd in u["days"])
        if len(aired) > u["per_day"]:
            notes.append("%s%s 하루 %d회 방송됐어요 (%s · 평소 %s)" % (
                who, josa(title, "이", "가"), len(aired), ", ".join(hm_text(b["hm"]) for b in aired), usual_text))
            continue
        for b in aired:
            day_diff = b["wd"] not in u["days"]
            time_diff = (b["hm"] != u["overall_time"]) if day_diff else (u["times"].get(b["wd"]) not in (None, b["hm"]))
            if not (day_diff or time_diff):
                continue
            what = "요일·시간" if day_diff and time_diff else ("요일" if day_diff else "시간")
            usual = usual_text if day_diff else "%s %s" % (WEEKDAY_KO[b["wd"]], hm_text(u["times"][b["wd"]]))
            notes.append("%s%s 평소와 다른 %s에 방송됐어요 (평소 %s → %s %s)" % (
                who, josa(title, "이", "가"), what, usual, WEEKDAY_KO[b["wd"]], hm_text(b["hm"])))
        # 평소 방송일인데 기록이 아예 없음 (그 주 방송 횟수가 평소보다 적을 때만)
        if not today_bs and key not in MISSING_SKIP and day.weekday() in u["days"]:
            first = next((b["start"] for b in prog["broadcasts"] if not b["off_air"]), None)
            week_end = week_start + timedelta(days=7)
            week_cnt = sum(1 for b in prog["broadcasts"]
                           if not b["off_air"] and week_start <= b["start"] < week_end)
            if first and first.date() < day and week_cnt < u["per_week"]:
                notes.append("%s 평소 방송일인데 상품 기록이 없어요 (휴방 가능성)" % who)
    return notes


def live_at(company, day, hm):
    """그 회사 라이브 편성에서 hm에 방송 중이던 상품 한 줄 (브랜드 + 정리된 상품명)."""
    data = load_json(os.path.join(ROOT, "homeshopping", "%s_live" % company, day.strftime("%Y-%m") + ".json")) or {}
    for item in data.get("days", {}).get(day.isoformat(), []):
        start, end = item.get("start", ""), item.get("end", "")
        if start and end and start <= hm < end:
            name = clean_name(item.get("product"))
            return display_name(brand_of({"brand": item.get("brand"), "name": name}), name), item.get("link") or ""
    return None, None


# ---------------- 메시지 ----------------

def link_html(text, link):
    return '<a href="%s">%s</a>' % (esc(link), esc(text)) if link else esc(text)


def build_message(day, progs):
    day_iso = day.isoformat()
    aired = []  # (company, hm, title, products)
    for prog in progs.values():
        for b in prog["broadcasts"]:
            if b["date"] == day_iso and not b["off_air"] and b["products"]:
                aired.append((prog["company"], b["hm"], prog["title"], b["products"]))

    lines = ["📺 <b>%02d/%02d(%s) 셀럽PGM 운영상품</b>" % (day.month, day.day, WEEKDAY_KO[day.weekday()])]
    prime = Counter(hm for _, hm, _, _ in aired if hm).most_common(1)
    prime = prime[0][0] if prime else DEFAULT_PRIME

    for company, icon in COMPANIES:
        rows = sorted([a for a in aired if a[0] == company], key=lambda a: a[1])
        lines.append("")
        lines.append("<b>%s%s</b>" % (icon, company))
        if not rows:
            text, link = live_at(company, day, prime)
            if text:
                lines.append("어제 %s에는 %s%s 방송됐어요" % (prime, link_html(text, link), josa(text, "이", "가")))
            else:
                lines.append("어제는 셀럽PGM 방송이 없었어요")
            continue
        for i, (_, hm, title, products) in enumerate(rows):
            picked, n_brands = pick_products(products)
            if i:
                lines.append("")
            lines.append("<b>%s %s</b> · 브랜드 %d개" % (esc(hm or "시각 미상"), esc(title), n_brands))
            for text, link in picked:
                lines.append("· " + link_html(text, link))

    notes = build_notes(progs, day)
    lines.append("")
    lines.append("📌 <b>특이사항</b>")
    if notes:
        lines.extend("· " + esc(n) for n in notes)
    else:
        lines.append("· 평소와 다른 편성이 없었어요")
    return lines


def main():
    day = target_day()
    progs = load_programs(day)
    if not progs:
        print("셀럽PGM 누적 데이터가 없습니다. 발송 생략.")
        return
    lines = build_message(day, progs)
    chunks = chunk_lines(lines)
    print("%s 셀럽PGM 운영상품 → 메시지 %d건" % (day, len(chunks)))
    if DRY_RUN:
        for i, c in enumerate(chunks, 1):
            print("\n----- [%d/%d] (%d자) -----" % (i, len(chunks), len(c)))
            print(c)
        return
    broadcast(ROOT, chunks)


if __name__ == "__main__":
    main()
