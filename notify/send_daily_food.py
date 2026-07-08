# -*- coding: utf-8 -*-
"""
텔레그램 봇으로 오늘의 식품(건강식품/일반식품) 방송 편성을 발송

== 동작 ==
1. weather/forecast/{지역}/latest.json 에서 오늘 날씨(최저/최고/강수확률)를 읽고
2. homeshopping/{회사}_live/{YYYY-MM}.json 에서 오늘자 건강식품/일반식품 방송을 모아
3. 아래 형식의 메시지를 만들어 텔레그램으로 발송한다.

   mm/dd(요일)
   ☀️ 날씨: 최저 25도, 최고 27도, 비 60%

   금일 식품 방송 리스트입니다

   💊 건강식품
   총 49회, (4개사) 16회
   06:40 [현대] 하루틴
   프리미엄 리포좀 비타민C
   ...

   🥩 일반식품
   총 55회, (4개사) 9회
   ...

'총 N회'는 전체 회사·전체 시간 기준이고, 목록과 '(4개사) N회'는
COMPANIES + 시간대(TIME_START~TIME_END) 필터를 적용한 것이다.
텔레그램 메시지는 1건당 4,096자 제한이라 보통 1건에 다 들어간다.

== 환경변수 ==
  TELEGRAM_BOT_TOKEN  (필수) BotFather 에서 발급받은 봇 토큰
  TELEGRAM_CHAT_ID    (선택) 수신처. 쉼표로 여러 곳 지정 가능
                      예: "123456789" / "123,456" / "@channelname" (채널은 봇을
                      관리자로 추가해야 함)

== 수신자 관리 ==
수신자 목록은 notify/recipients.txt (한 줄에 하나, # 주석 가능)로 관리한다.
TELEGRAM_CHAT_ID 환경변수와 합쳐서 발송하며, 둘 다 비어 있으면
봇이 최근에 받은 메시지의 발신자를 자동 감지한다 (최초 1회는 봇에게
아무 메시지나 먼저 보내둬야 함).
  COMPANIES           (선택) 목록에 표시할 회사 코드, 쉼표 구분 (기본: HD,GS,CJ,LT)
                      전체 표시: "ALL"
  TIME_START          (선택) 목록 시작 시각 (기본: 06:00)
  TIME_END            (선택) 목록 종료 시각 (기본: 23:59)
  WEATHER_REGION      (선택) 날씨 지역 폴더명 (기본: seoul)
  TARGET_DATE         (선택) YYYY-MM-DD, 미지정 시 오늘(KST)
  DRY_RUN             (선택) "1"이면 발송하지 않고 메시지만 출력
"""

import os
import json
import glob
from datetime import datetime, timezone, timedelta

from tg import esc, chunk_lines, broadcast

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WEATHER_REGION = os.environ.get("WEATHER_REGION") or "seoul"
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"

DEFAULT_COMPANIES = "HD,GS,CJ,LT"
TIME_START = os.environ.get("TIME_START") or "06:00"
TIME_END = os.environ.get("TIME_END") or "23:59"

COMPANY_NAMES = {
    "CJ": "CJ",
    "GS": "GS",
    "HD": "HD",
    "LT": "LT",
    "NS": "NS",
    "HNS": "홈앤",
    "PUBLIC": "공영",
    "KTALPHA": "KT알파",
    "SHINSEGAE": "신세계",
    "SHOPPINGNT": "쇼핑엔티",
    "SKSTOA": "SK스토아",
}

SECTIONS = [
    ("💊 건강식품", "건강식품"),
    ("🥩 일반식품", "일반식품"),
]

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


def target_date():
    s = os.environ.get("TARGET_DATE", "")
    if s:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=KST)
    return datetime.now(KST)


def load_weather(day_str):
    path = os.path.join(ROOT, "weather", "forecast", WEATHER_REGION, "latest.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get(day_str)
    except (OSError, json.JSONDecodeError):
        return None



def clean_product(name):
    # 'ㅣ 원산지 : ...' 같은 꼬리표 제거
    for sep in ("| 원산지", "ㅣ 원산지", "|원산지"):
        if sep in name:
            name = name.split(sep)[0]
    return " ".join(name.split())


def load_broadcasts(day_str, month_str, companies):
    """rows: 필터(회사+시간대) 적용된 표시용 목록, totals: 전체 회사·전체 시간 건수"""
    rows = {cat: [] for _, cat in SECTIONS}
    totals = {cat: 0 for _, cat in SECTIONS}
    for path in glob.glob(os.path.join(ROOT, "homeshopping", "*_live", month_str + ".json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        company = data.get("company", "")
        for item in data.get("days", {}).get(day_str, []):
            cat = item.get("category")
            if cat not in rows:
                continue
            totals[cat] += 1
            if companies and company not in companies:
                continue
            start = item.get("start", "")
            if not (TIME_START <= start <= TIME_END):
                continue
            brand = (item.get("brand") or "").strip()
            product = clean_product(item.get("product") or "")
            # 상품명이 브랜드로 시작하면 중복 표기 생략
            if brand and product.startswith(brand):
                brand = ""
            name = COMPANY_NAMES.get(company, company)
            # '시간 [회사] 브랜드'(굵게) + 다음 줄 상품명(판매페이지 링크) — 두 줄이 한 묶음
            head = "%s [%s]" % (start or "??:??", name)
            if brand:
                head += " " + brand
            link = (item.get("link") or "").strip()
            if link:
                body = '<a href="%s">%s</a>' % (esc(link), esc(product))
            else:
                body = esc(product)
            rows[cat].append((start, "<b>%s</b>\n%s" % (esc(head), body)))
    for cat in rows:
        rows[cat].sort(key=lambda r: r[0])
    return rows, totals


def build_message(now, weather, rows, totals, n_companies):
    lines = []
    lines.append("%02d/%02d(%s)" % (now.month, now.day, WEEKDAY_KO[now.weekday()]))
    if weather:
        parts = []
        if weather.get("minTa") is not None:
            parts.append("최저 %d도" % round(weather["minTa"]))
        if weather.get("maxTa") is not None:
            parts.append("최고 %d도" % round(weather["maxTa"]))
        if weather.get("pop_max") is not None:
            parts.append("비 %d%%" % round(weather["pop_max"]))
        if parts:
            lines.append("☀️ 날씨: " + ", ".join(parts))
    lines.append("")
    lines.append("금일 식품 방송 리스트입니다")
    for header, cat in SECTIONS:
        items = rows.get(cat, [])
        lines.append("")
        lines.append("<b>%s</b>" % header)
        lines.append("총 %d회, (%d개사) %d회" % (totals.get(cat, 0), n_companies, len(items)))
        lines.append("")
        for _, text in items:
            lines.append(text)
            lines.append("")  # 항목 사이 여백
        if items:
            lines.pop()  # 마지막 항목 뒤 여백 제거
    return lines






def main():
    now = target_date()
    day_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")

    companies_env = (os.environ.get("COMPANIES") or DEFAULT_COMPANIES).strip()
    if companies_env.upper() == "ALL":
        companies = None
        n_companies = len(COMPANY_NAMES)
    else:
        companies = set(c.strip() for c in companies_env.split(",") if c.strip())
        n_companies = len(companies)

    weather = load_weather(day_str)
    if weather is None:
        print("※ %s 날씨 데이터 없음 (%s)" % (day_str, WEATHER_REGION))
    rows, totals = load_broadcasts(day_str, month_str, companies)
    total = sum(totals.values())
    if total == 0:
        print("%s 식품 방송 데이터가 없습니다. 발송 생략." % day_str)
        return

    lines = build_message(now, weather, rows, totals, n_companies)
    chunks = chunk_lines(lines)
    print("방송 %d건 → 메시지 %d건" % (total, len(chunks)))

    if DRY_RUN:
        for i, c in enumerate(chunks, 1):
            print("\n----- [%d/%d] (%d자) -----" % (i, len(chunks), len(c)))
            print(c)
        return

    broadcast(ROOT, chunks)


if __name__ == "__main__":
    main()
