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

   🔔 10/30(금) 오늘의 셀럽PGM
   [GS 소유진] 브랜드1, 브랜드2, 브랜드3
   [CJ 바이림] 브랜드1, 브랜드2

   🔗 셀럽PGM 모니터링 바로가기   (https://hdmzp.github.io/hdhs/#celebrity)

== 규칙 ==
- 회사 순서 HD → GS → CJ → LT, 회사 안에서는 방송 시각 순.
- 상품은 브랜드당 1개. 브랜드가 불분명하면(빈 값·'GS SHOP' 같은 자리표시자)
  상품명으로 보고, 상품명이 거의 같은 상품도 1개로 줄인다.
- 상품명은 꼬리표([..] (..) 【..】)와 '+ 구성'을 지워 짧게, 판매 링크를 건다. 가격은 안 쓴다.
- 셀럽PGM 방송이 없던 회사는 그날 셀럽PGM이 가장 많이 몰린 시각(없으면 20:45)에
  그 회사 편성표(homeshopping/{회사}_live)에서 방송된 상품을 한 줄로 쓴다.
- 특이사항: 셀럽PGM 탭 '방송 체크'와 같은 기준 (그 주 시작 전 12주 최빈값)으로
  어제 회차의 다른 요일·시간 / 하루 N회 / 휴방 / 평소 방송일인데 기록 없음.
  비정기 프로그램 중 REST_NOTICE(김신영이 산다)는 '기록 없음' 대신 '이번 주 쉬어요 (다음 방송 MM/DD 요일 HH:MM)'.
- 오늘의 셀럽PGM: 발송일(방송일 다음 날)에 잡힌 셀럽PGM 회차를 [회사 탭이름] 브랜드 나열로.
  회사 순서 → 방송 시각 순, 브랜드는 중복 없이 최대 TODAY_BRAND_MAX개 (넘으면 '외 N개').
  상품이 아직 안 올라온 회차는 데이터에 없어서 빠진다.
- 어제 셀럽PGM 방송이 하나도 없으면(주로 일요일) 회사별 '어제 20:45에는 ...' 채움 줄을
  보내지 않고 '어제는 셀럽PGM이 없었어요' 한 줄 + 특이사항·오늘의 셀럽PGM만 보낸다.
  특이사항·오늘의 셀럽PGM이 둘 다 없으면 발송 자체를 생략.
- 보고 양식(별도 메시지): '📃복사해서 보고에 활용하세요' + 어제 회차별 실적 보고 틀 (HD 제외).
  셀럽PGM 신청자가 아니라 비공개 저장소의 telegram/보고수신자.txt 에 적힌 사람에게만 간다
  (그 저장소가 봇에 올려 둔 목록을 받아 온다, 없으면 관리자에게만).
  운영시간은 회사 편성표(homeshopping/{회사}_live)의 회차 끝 시각으로 채우고,
  순주문·달성률·상품별 시간은 알 수 없어 '0.0억, %' / '00:00 ~ 00:00, 0분' 빈칸으로 둔다.
  (상품별 시간은 편성표·라방바 모두 회차 단위로만 와서 정확히 알 수 없다 - 2026-11 확인.
   단 롯데는 상품마다 실제 편성 구간 segment_time이 있어 상품별·전체 시간을 그걸로 채운다)
  같은 프로그램이 하루 2회 이상이면 [1부]/[2부]로 나누고 회차별 순주문만 쓴다.

== 환경변수 ==
  TELEGRAM_BOT_TOKEN  (필수) BotFather 에서 발급받은 봇 토큰
  TARGET_DATE         (선택) YYYY-MM-DD, 이 날짜의 방송을 보낸다 (기본: 어제 KST)
  DRY_RUN             (선택) "1"이면 발송하지 않고 메시지만 출력
"""

import os
import re
import json
from collections import Counter
from datetime import datetime, date, timedelta, timezone

from tg import esc, chunk_lines, broadcast, send_all
import subscribers

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DIR = os.path.join(ROOT, "homeshopping", "representative_programs", "history")
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"

COMPANIES = [("HD", "🔵"), ("GS", "🟢"), ("CJ", "🟣"), ("LT", "🔴")]
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]
DEFAULT_PRIME = "20:45"
TODAY_BRAND_MAX = 5  # 오늘의 셀럽PGM 한 줄에 쓰는 브랜드 수
# 메시지 맨 아래 바로가기 - 주소 끝 #celebrity로 셀럽PGM 탭이 바로 열린다 (index.html openTabFromHash)
SITE_CELEB_URL = "https://hdmzp.github.io/hdhs/#celebrity"
LOOKBACK_DAYS = 84  # 평소 = 직전 12주 (셀럽PGM 탭 CEL_NEWS_LOOKBACK_DAYS와 같음)
# 요일이 매번 바뀌거나 비정기라 '평소 방송일인데 기록 없음'을 안 따지는 프로그램
# (셀럽PGM 탭 CEL_NEWS_MISSING_SKIP과 같음)
MISSING_SKIP = {"CJ_SIH", "CJ_KSY"}
# ※ 강주은 굿라이프(CJ_KJE)는 2026-11부터 격주 월 19:35지만 넣지 않는다 - 쉬는 주도
#   휴방(기록 없음) 안내를 계속 띄운다 (셀럽PGM 탭과 같은 기준).
# 그중 쉬는 주에 '이번 주 쉬어요 (다음 방송 …)'를 따로 알리는 프로그램 (겟잇스타일은 요일이 수시로 바뀌어 제외)
REST_NOTICE = {"CJ_KSY"}
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
    r"\S*쇼\s*단독", r"\S*쇼\s*패키지", r"단독\s*구성", r"단독\s*조건", r"GS\s*단독", r"단독", r"NEW", r"●", r"★", r"☆",
    r"론칭가\s*[\d,]*\s*원?", r"파격가", r"특가", r"[\d,]+\s*원", r"최신상", r"더블\s*구성", r"\d{2,4}\s*년형",
]
# 수량·포장 단위 (중간에 끼어 있어도 지운다). '1인용', '4인조', '100%'는 상품 특징이라 남긴다.
QTY_UNIT = r"(박스|팩|포|병|통|개입|개|매입|매|대|과|미|구|EA|ea|P|p|봉|캔|인분|kg|g|ml|mL|L)"
# 모델명: 영문 대문자와 숫자가 섞인 4자 이상 토큰 (DQ205PSVA, BU3641KR, IMB-095, MCF-180R).
# 시즌 표기(26FW, 25SS)와 숫자만 있는 토큰은 남긴다.
MODEL_RE = re.compile(r"^(?=.*[A-Z])(?=.*\d)[A-Z\d][A-Z\d\-]{3,}[A-Za-z\d]?$")
SEASON_RE = re.compile(r"^\d{2}(FW|SS|F/W|S/S)$")


def clean_name(name):
    n = name or ""
    # 겹친 꼬리표('[[1등급]LG ...')까지 지워지도록 반복
    for _ in range(3):
        n = re.sub(r"\[[^\[\]]*\]|\([^()]*\)|【[^】]*】", " ", n)
    n = n.replace("[", " ").replace("]", " ")
    # 사이즈 코드 접두어('LK_코튼텐셀')는 떼고, 나머지 '_'는 띄어쓰기로
    n = re.sub(r"(^|\s)[A-Z]{1,3}_", " ", n).replace("_", " ")
    n = re.split(r"\s\+\s|\+|\s/\s", n)[0]
    for pat in PROMO_PATTERNS:
        n = re.sub(pat, " ", n)
    # 맨 앞 연도('2026 신일 ...')
    n = re.sub(r"^\s*20\d{2}\s+", " ", n)
    # 중간에 낀 수량('12박스', '24매입', '1대', '5P', '6박스*14포')
    n = re.sub(r"(?<![\w%])[\d,.]+\s*" + QTY_UNIT + r"([*x×]\s*[\d,.]+\s*" + QTY_UNIT + r"?)*(?![가-힣A-Za-z])", " ", n)
    # 모델명 토큰
    n = " ".join(t for t in n.split()
                 if SEASON_RE.match(t) or not all(MODEL_RE.match(x) for x in t.strip(",").split(",") if x))
    for _ in range(2):  # 붙어 있는 잔여물(' * 총')이 한 번에 다 안 지워져 두 번
        n = re.sub(r"(^|\s)총(?=\s|$)|\s[*x×](?=\s|$)|\s단품$", " ", n)
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
    for delta in (-3, -2, -1, 0, 1):  # +1: 오늘의 셀럽PGM이 다음 달 1일일 때
        hist = load_json(os.path.join(HISTORY_DIR, shift_month(day, delta) + ".json")) or {}
        for p in hist.get("programs") or []:
            key = p.get("program_key")
            prog = progs.setdefault(key, {"company": p.get("company", ""),
                                          "title": p.get("program_title") or p.get("tab_name") or key,
                                          "tab": p.get("tab_name") or p.get("program_title") or key,
                                          "page": p.get("detail_link") or "",
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
        if not today_bs and day.weekday() in u["days"]:
            first = next((b["start"] for b in prog["broadcasts"] if not b["off_air"]), None)
            week_end = week_start + timedelta(days=7)
            week_cnt = sum(1 for b in prog["broadcasts"]
                           if not b["off_air"] and week_start <= b["start"] < week_end)
            if first and first.date() < day and week_cnt < u["per_week"]:
                if key in REST_NOTICE:
                    # 비정기 프로그램은 '기록 없음(휴방 가능성)' 대신 쉬는 주 + 다음 방송 안내
                    day_end = datetime.combine(day + timedelta(days=1), datetime.min.time())
                    nxt = next((b for b in prog["broadcasts"]
                                if not b["off_air"] and b["start"] >= day_end), None)
                    when = ("다음 방송 %d/%d %s %s" % (nxt["start"].month, nxt["start"].day,
                                                    WEEKDAY_KO[nxt["wd"]], nxt["hm"] or "시각 미정")
                            if nxt else "다음 방송 미정")
                    notes.append("%s%s 이번 주 쉬어요 (%s)" % (who, josa(title, "은", "는"), when))
                elif key not in MISSING_SKIP:
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

    header = "📺 <b>%02d/%02d(%s) 셀럽PGM 운영상품</b>" % (day.month, day.day, WEEKDAY_KO[day.weekday()])
    notes = build_notes(progs, day)
    today_lines, has_today = build_today(progs, day + timedelta(days=1))
    link_line = '🔗 <a href="%s">셀럽PGM 모니터링 바로가기</a>' % SITE_CELEB_URL

    if not aired:
        # 어제 셀럽PGM이 없던 날(주로 일요일): 편성표 채움 줄은 안 보낸다
        if not notes and not has_today:
            return None
        lines = ["📺 어제(%02d/%02d %s)는 셀럽PGM이 없었어요" % (day.month, day.day, WEEKDAY_KO[day.weekday()]), ""]
        if notes:
            lines += ["📌 <b>특이사항</b>"] + ["· " + esc(n) for n in notes] + [""]
        if has_today:
            lines += today_lines + [""]
        return lines + [link_line]

    lines = [header]
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

    lines.append("")
    lines.append("📌 <b>특이사항</b>")
    if notes:
        lines.extend("· " + esc(n) for n in notes)
    else:
        lines.append("· 평소와 다른 편성이 없었어요")
    lines.append("")
    lines.extend(today_lines)
    lines.append("")
    lines.append(link_line)
    return lines


def today_brands(products):
    """상품 목록 -> 중복 없는 브랜드 이름들 (순서 유지).
    브랜드 칸이 비었거나 자리표시자면 상품명에서 짐작하지 않고 '확인필요'로 쓴다
    (상품명 첫 단어를 쓰면 '프리미엄'처럼 브랜드가 아닌 말이 섞인다)."""
    seen = []
    for p in products:
        b = clean_name((p.get("brand") or "").strip())
        if b in PLACEHOLDER_BRANDS:
            b = "확인필요"
        if b and b not in seen:
            seen.append(b)
    if "확인필요" in seen:  # 확인필요는 맨 뒤로
        seen.remove("확인필요")
        seen.append("확인필요")
    return seen


def build_today(progs, today):
    """🔔 MM/DD(요일) 오늘의 셀럽PGM - [회사 탭이름] 브랜드1, 브랜드2 ... -> (줄들, 회차 있음)"""
    today_iso = today.isoformat()
    order = {co: i for i, (co, _) in enumerate(COMPANIES)}
    rows = []  # (회사순, 시각, 회사, 탭, 브랜드들)
    for prog in progs.values():
        for b in prog["broadcasts"]:
            if b["date"] == today_iso and not b["off_air"]:
                brands = today_brands(b["products"])
                if brands:
                    rows.append((order.get(prog["company"], 9), b["hm"], prog["company"], prog["tab"], brands))
    rows.sort(key=lambda r: (r[0], r[1]))
    lines = ["🔔 <b>%02d/%02d(%s) 오늘의 셀럽PGM</b>" % (today.month, today.day, WEEKDAY_KO[today.weekday()])]
    if not rows:
        lines.append("· 오늘 예정된 셀럽PGM 상품 정보가 아직 없어요")
    for _, _, co, tab, brands in rows:
        text = ", ".join(brands[:TODAY_BRAND_MAX])
        if len(brands) > TODAY_BRAND_MAX:
            text += " 외 %d개" % (len(brands) - TODAY_BRAND_MAX)
        lines.append("[%s %s] %s" % (esc(co), esc(tab), esc(text)))
    return lines, bool(rows)


# ---------------- 보고 양식 (별도 메시지) ----------------

REPORT_BLANK_SALES = "순주문 0.0억, %"
REPORT_SKIP_COMPANIES = {"HD"}  # 보고 양식에서 빼는 회사 (2026-11-04 사용자 요청)
REPORT_BLANK_TIME = "(00:00 ~ 00:00, 0분)"


def block_end(company, day, hm):
    """그 회사 편성표(TV → 데이터방송 순)에서 hm에 시작한 회차의 끝 시각 (없으면 '').
    시작 시각이 정확히 같은 회차만 본다 - 'hm에 방송 중이던 회차'로 넓히면
    TV+ 방송(예: 겟잇스타일 19:00)이 TV의 다른 상품 회차 끝 시각을 잘못 집는다."""
    for kind in ("live", "data"):
        data = load_json(os.path.join(ROOT, "homeshopping", "%s_%s" % (company, kind),
                                      day.strftime("%Y-%m") + ".json")) or {}
        ends = [it["end"] for it in data.get("days", {}).get(day.isoformat(), [])
                if it.get("start") == hm and it.get("end")]
        if ends:
            return max(ends)
    return ""


SEG_RE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


def seg_of(product):
    """롯데 상품의 구간 시각 segment_time("20:45-21:45(60')") -> ('20:45', '21:45') 또는 None"""
    m = SEG_RE.search(product.get("segment_time") or "")
    if not m:
        return None
    return "%02d:%s" % (int(m.group(1)), m.group(2)), "%02d:%s" % (int(m.group(3)), m.group(4))


def span_text(start, end, sep="~"):
    mins = (int(end[:2]) * 60 + int(end[3:])) - (int(start[:2]) * 60 + int(start[3:]))
    if mins <= 0:
        mins += 24 * 60
    return "%s%s%s (%d분)" % (start, sep, end, mins) if sep == "~" else "(%s ~ %s, %d분)" % (start, end, mins)


def run_time(company, day, hm):
    """'19:35~21:45 (130분)'. 끝 시각을 모르면 '19:35~'"""
    end = block_end(company, day, hm) if hm else ""
    if not hm:
        return "시각 미상"
    if not end:  # 편성표에 없는 회차 (겟잇스타일 모바일 라이브 등)
        return "%s~ (끝 시각 확인필요)" % hm
    return span_text(hm, end)


def part_brands_html(products):
    """[1부] 줄의 브랜드들 - 브랜드마다 그 브랜드 첫 상품 링크를 건다"""
    first_link = {}
    for p in products:
        b = clean_name((p.get("brand") or "").strip())
        b = "확인필요" if b in PLACEHOLDER_BRANDS else b
        first_link.setdefault(b, (p.get("link") or "").strip())
    return ", ".join(link_html(b, first_link.get(b, "")) for b in today_brands(products))


def build_report(progs, day):
    """어제 셀럽PGM 회차별 실적 보고 틀. 방송이 없으면 []"""
    day_iso = day.isoformat()
    order = {co: i for i, (co, _) in enumerate(COMPANIES)}
    week_start = datetime.combine(day - timedelta(days=day.weekday()), datetime.min.time())
    groups = []
    for prog in progs.values():
        if prog["company"] in REPORT_SKIP_COMPANIES:
            continue
        bs = sorted([b for b in prog["broadcasts"]
                     if b["date"] == day_iso and not b["off_air"] and b["products"]],
                    key=lambda b: b["hm"])
        if bs:
            groups.append((order.get(prog["company"], 9), bs[0]["hm"], prog, bs))
    if not groups:
        return []
    groups.sort(key=lambda g: (g[0], g[1]))
    md = "%d/%d" % (day.month, day.day)
    lines = ["📃 <b>복사해서 보고에 활용하세요</b>"]
    for _, _, prog, bs in groups:
        co, title = prog["company"], prog["title"]
        lines.append("")
        if prog["page"]:
            lines.append('🔔 <a href="%s">%s 페이지 바로가기</a>' % (esc(prog["page"]), esc(prog["tab"])))
        if len(bs) == 1:
            b = bs[0]
            lines.append("■ %s %s %s 실적 보고" % (md, esc(co), esc(title)))
            lines.append("")
            # 롯데는 상품마다 실제 편성 구간(segment_time)이 있어 그걸로 채운다
            seg_by_link = {(x.get("link") or "").strip(): seg_of(x) for x in b["products"]}
            segs = [v for v in seg_by_link.values() if v]
            if segs and b["hm"]:
                end = max(segs, key=lambda v: (v[1] < b["hm"], v[1]))[1]  # 자정 넘김 고려
                total = span_text(b["hm"], end)
            else:
                total = run_time(co, day, b["hm"])
            lines.append("ㆍ운영시간 : %s" % total)
            lines.append("ㆍ합계 : %s" % REPORT_BLANK_SALES)
            for text, link in pick_products(b["products"])[0]:
                seg = seg_by_link.get(link)
                lines.append("")
                lines.append("ㆍ%s" % link_html(text, link))  # 복사하면 글자만 남는다
                lines.append("   %s" % (span_text(seg[0], seg[1], sep="") if seg else REPORT_BLANK_TIME))
                lines.append("   %s" % REPORT_BLANK_SALES)
        else:
            lines.append("■ %s %s %s %s" % (md, esc(co), esc(title), "/".join("%d부" % (i + 1) for i in range(len(bs)))))
            u = usual_of(prog["broadcasts"], week_start)
            if u["days"]:
                usual_text = " / ".join("%s %s" % (WEEKDAY_KO[wd], u["times"][wd]) for wd in u["days"])
                lines.append("  ※ %s 고정 편성, 그 외 추가 방송 수시 운영" % usual_text)
            for i, b in enumerate(bs, 1):
                lines.append("")
                lines.append("[%d부] %s" % (i, part_brands_html(b["products"])))
                lines.append("ㆍ운영시간 : %s" % run_time(co, day, b["hm"]))
                lines.append("ㆍ%s" % REPORT_BLANK_SALES)
    return lines


def main():
    day = target_day()
    progs = load_programs(day)
    if not progs:
        print("셀럽PGM 누적 데이터가 없습니다. 발송 생략.")
        return
    lines = build_message(day, progs)
    if not lines:
        print("%s 셀럽PGM 방송·특이사항·오늘 예정 모두 없음 - 발송 생략" % day)
        return
    chunks = chunk_lines(lines)
    # 보고 양식은 따로 복사하기 쉽게 별도 메시지, 셀럽PGM 신청자가 아니라 보고수신자에게만 (2026-11 사용자 요청)
    report = chunk_lines(build_report(progs, day) or [])
    print("%s 셀럽PGM 운영상품 → 메시지 %d건, 보고 양식 %d건" % (day, len(chunks), len(report)))
    if DRY_RUN:
        for i, c in enumerate(chunks + report, 1):
            print("\n----- [%d/%d] (%d자) -----" % (i, len(chunks) + len(report), len(c)))
            print(c)
        return
    broadcast(ROOT, chunks, topic="celeb")
    if report:
        ids = subscribers.admins() if os.environ.get("ADMIN_ONLY") == "1" else subscribers.report_recipients()
        if ids:
            send_all(report, ids)


if __name__ == "__main__":
    main()
