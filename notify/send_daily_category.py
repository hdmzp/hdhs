# -*- coding: utf-8 -*-
"""
텔레그램 봇으로 오늘의 패션·리빙·미용 방송 편성을 발송 (식품 리스트와 같은 양식)

   10/30(금)

   금일 미용 방송 리스트입니다

   💄 미용
   총 41회, (4개사) 12회

   18:30 [LT] 유난희💗슈바넨가르텐
   매직버블크림 유난희패키지
   ...

묶음 (2026-10 사용자 지정) - 묶음마다 메시지를 따로 보낸다
  패션 = 의류 · 언더웨어 · 잡화/주얼리
  리빙 = 리빙/주방 · 가전 · 문화/스포츠 (나누지 않고 한 목록, 시간순)
  미용 = 미용

💗 게스트 강조는 패션·미용만. 규칙과 이름 목록은 notify/guests.py.
맨 아래에 그 묶음 카테고리의 경쟁사 맞편성·신규 브랜드 (notify/competitor.py).
매주 월요일에는 목록에 없는 '새 게스트 후보'(지난 7일 미용 상품명)를 따로 보낸다.

== 환경변수 ==
  TELEGRAM_BOT_TOKEN  (필수) / TELEGRAM_CHAT_ID (선택) - send_daily_food.py 와 같음
  LIST_GROUPS         (선택) 보낼 묶음, 쉼표 구분 fashion,living,beauty (기본: 전부)
  COMPANIES / TIME_START / TIME_END / TARGET_DATE / DRY_RUN - send_daily_food.py 와 같음
  GUEST_CANDIDATES    (선택) "1"이면 요일과 상관없이 새 게스트 후보도 보낸다
"""

import os
import re
import json
import glob
from collections import Counter, defaultdict
from datetime import timedelta

from tg import esc, chunk_lines, broadcast
import send_daily_food as food
from guests import GUESTS, EXCLUDE, TITLE_SUFFIXES, PROGRAM_HOSTS
from competitor import competitor_lines

ROOT = food.ROOT
HISTORY_DIR = os.path.join(ROOT, "homeshopping", "representative_programs", "history")
DRY_RUN = food.DRY_RUN

# (키, 이름, [(소제목, [카테고리...])]) - 소제목 하나에 카테고리 여럿이면 합쳐서 시간순
# 리빙은 리빙/주방·가전·문화/스포츠를 나누지 않고 한 목록으로 (2026-10 사용자 요청)
# 이름에는 담당 팀이 있는 카테고리를 다 적는다 (문화/스포츠만 이름에서 생략, 방송은 포함)
GROUPS = [
    ("fashion", "패션", [("👗 의류", ["의류"]), ("🩲 언더웨어", ["언더웨어"]), ("👜 잡화/주얼리", ["잡화/주얼리"])]),
    ("living", "리빙/주방·가전", [("🏠 리빙/주방 · 가전", ["리빙/주방", "가전", "문화/스포츠"])]),
    ("beauty", "미용", [("💄 미용", ["미용"])]),
]
FASHION_CATS = {"의류", "언더웨어", "잡화/주얼리"}
BEAUTY_CATS = {"미용"}
HEART = "💗"

_TITLES = "|".join(TITLE_SUFFIXES)
# 이름 앞이 한글이면 다른 단어의 일부 ('신현영' 속 '현영'), 뒤에 직함이 붙으면 모델·전문가
GUEST_RES = [(n, re.compile(r"(?<![가-힣])%s(?!\s?(?:%s))" % (re.escape(n), _TITLES)))
             for n in GUESTS if n not in EXCLUDE]


def host_of(company, title):
    title = title or ""
    for co, key, host in PROGRAM_HOSTS:
        if co == company and key in title:
            return host if host not in EXCLUDE else ""
    return ""


def load_celeb_slots(day_str):
    """셀럽PGM 누적(history)에서 그날 방송 -> {(회사, 'HH:MM'): 프로그램명}."""
    path = os.path.join(HISTORY_DIR, day_str[:7] + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    slots = {}
    for prog in data.get("programs", []):
        title = prog.get("title") or prog.get("program_title") or ""
        for b in prog.get("broadcasts", []):
            if b.get("date") != day_str or b.get("off_air"):
                continue
            m = re.search(r"(\d{1,2}):(\d{2})", b.get("label", ""))
            if m:
                slots[(prog.get("company"), "%02d:%s" % (int(m.group(1)), m.group(2)))] = title
    return slots


def program_host(company, items, celeb_slots):
    """고정PGM(편성표 pgm 칸)·셀럽PGM 회차면 그 진행자."""
    for it in items:
        host = host_of(company, it.get("pgm"))
        if host:
            return host
    for it in items:
        host = host_of(company, celeb_slots.get((company, it.get("start", ""))))
        if host:
            return host
    return ""


def product_guest(items):
    for it in items:
        name = it.get("product") or ""
        for guest, rx in GUEST_RES:
            if rx.search(name):
                return guest
    return ""


def make_decorate(celeb_slots):
    def decorate(company, start, cat, items):
        if cat in FASHION_CATS:
            guest = program_host(company, items, celeb_slots)
        elif cat in BEAUTY_CATS:
            guest = product_guest(items) or program_host(company, items, celeb_slots)
        else:
            guest = ""
        return guest + HEART if guest else ""
    return decorate


def build_message(now, title, sections, rows, totals, n_companies):
    lines = ["%02d/%02d(%s)" % (now.month, now.day, food.WEEKDAY_KO[now.weekday()]), "",
             "금일 %s 방송 리스트입니다" % title]
    for header, cats in sections:
        items = sorted((r for c in cats for r in rows.get(c, [])), key=lambda r: r[0])
        total = sum(totals.get(c, 0) for c in cats)
        lines += ["", "<b>%s</b>" % header,
                  "총 %d회, (%d개사) %d회" % (total, n_companies, len(items)), ""]
        for _, text in items:
            lines += [text, ""]
        lines.pop()
    return lines


# ---- 새 게스트 후보 (목록에 없는 사람 이름 같은 표기) ----
SURNAMES = "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경사부"
CANDIDATE_RE = re.compile(
    r"(?<![가-힣])([%s][가-힣]{1,2})(?:\s?패키지|\s?(?:PICK|Pick|pick|픽)(?![가-힣])|의\s)" % SURNAMES)
# 사람 이름이 아닌데 위 표기에 걸리는 말
NOT_NAMES = {
    "기본", "모녀", "한정", "현대", "전고객", "오감", "원장님", "최다", "신세계", "추석", "주년", "신상",
    "남성", "여성", "공영", "오렌지", "원피스", "선크림", "마스크", "금빛", "공구", "구성", "왕톡",
    "기초", "최저가", "여배우", "하트", "방송", "마지막", "인기", "선물", "오로라", "주사세", "고급",
    "한정판", "지금딱", "최고가", "반대", "백설", "스페셜", "특별", "단독", "명품", "최초", "가을",
    "겨울", "여름", "봄", "대용량", "정품", "시즌", "프리미엄", "베스트", "초특가", "역대", "최대",
}


def guest_candidates(today, companies):
    """지난 7일 미용 상품명에서 목록에 없는 이름 후보 -> [(이름, 횟수, 예시)]."""
    known = set(GUESTS) | set(EXCLUDE) | {h for _, _, h in PROGRAM_HOSTS}
    count, example = Counter(), {}
    days = {(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 8)}
    for month in sorted({d[:7] for d in days}):
        for path in glob.glob(os.path.join(ROOT, "homeshopping", "*_live", month + ".json")):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if companies and data.get("company") not in companies:
                continue
            seen = set()
            for day, items in data.get("days", {}).items():
                if day not in days:
                    continue
                for it in items:
                    if it.get("category") not in BEAUTY_CATS:
                        continue
                    name = it.get("product") or ""
                    for m in CANDIDATE_RE.finditer(name):
                        cand = m.group(1)
                        if cand in known or cand in NOT_NAMES or cand.endswith(TITLE_SUFFIXES):
                            continue
                        key = (day, it.get("start"), cand)
                        if key in seen:
                            continue
                        seen.add(key)
                        count[cand] += 1
                        example.setdefault(cand, "[%s] %s" % (food.COMPANY_NAMES.get(data.get("company"), ""), name))
    return [(c, n, example[c]) for c, n in count.most_common()]


def candidate_message(cands):
    lines = ["🔎 새 게스트 후보 (지난 7일 미용 상품명)",
             "목록에 없어 💗를 안 붙인 이름이에요. 게스트면 '넣어', 아니면 '빼'라고 알려주세요.", ""]
    for name, n, ex in cands:
        lines.append("<b>%s</b> %d회 · %s" % (esc(name), n, esc(ex[:60])))
    return lines


def main():
    now = food.target_date()
    day_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")

    companies_env = (os.environ.get("COMPANIES") or food.DEFAULT_COMPANIES).strip()
    if companies_env.upper() == "ALL":
        companies, n_companies = None, len(food.COMPANY_NAMES)
    else:
        companies = set(c.strip() for c in companies_env.split(",") if c.strip())
        n_companies = len(companies)

    wanted = {g.strip() for g in (os.environ.get("LIST_GROUPS") or "fashion,living,beauty").split(",") if g.strip()}
    decorate = make_decorate(load_celeb_slots(day_str))

    messages = []
    for key, title, sections in GROUPS:
        if key not in wanted:
            continue
        cat_sections = [(c, c) for _, cats in sections for c in cats]
        rows, totals = food.load_broadcasts(day_str, month_str, companies, cat_sections, decorate)
        if sum(totals.values()) == 0:
            print("%s %s 방송 데이터가 없습니다. 생략." % (day_str, title))
            continue
        lines = build_message(now, title, sections, rows, totals, n_companies)
        lines += competitor_lines(now.date(), companies, {c for _, c in cat_sections})
        messages.append((title, key, lines))

    if now.weekday() == 0 or os.environ.get("GUEST_CANDIDATES") == "1":
        cands = guest_candidates(now, companies)
        print("새 게스트 후보 %d명" % len(cands))
        if cands:
            # 후보 확인은 관리자(recipients.txt / TELEGRAM_CHAT_ID)에게만
            messages.append(("새 게스트 후보", None, candidate_message(cands)))

    for title, topic, lines in messages:
        chunks = chunk_lines(lines)
        print("%s → 메시지 %d건" % (title, len(chunks)))
        if DRY_RUN:
            for i, c in enumerate(chunks, 1):
                print("\n----- %s [%d/%d] (%d자) -----" % (title, i, len(chunks), len(c)))
                print(c)
            continue
        # 봇에서 이 카테고리를 신청한 사람에게만 (notify/subscribers.py)
        broadcast(ROOT, chunks, topic=topic)


if __name__ == "__main__":
    main()
