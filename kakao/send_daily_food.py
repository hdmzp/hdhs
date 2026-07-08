# -*- coding: utf-8 -*-
"""
카카오톡 '나에게 보내기'로 오늘의 식품(건강식품/일반식품) 방송 편성을 발송

== 동작 ==
1. weather/forecast/{지역}/latest.json 에서 오늘 날씨(최저/최고/강수확률)를 읽고
2. homeshopping/{회사}_live/{YYYY-MM}.json 에서 오늘자 건강식품/일반식품 방송을 모아
3. 아래 형식의 메시지를 만들어 카카오톡 '나와의 채팅'으로 발송한다.

   mm/dd(요일)
   ☀️ 날씨: 최저 25도, 최고 27도, 비 60%

   오늘 진행예정인 식품 방송입니다

   💊 건강식품
   49회
   01:00 롯데 프롬바이오 위엔 매스틱 24주분
   ...

   🥩 일반식품
   55회
   ...

카카오 텍스트 템플릿은 200자 제한이 있어 줄 단위로 쪼개 여러 건을 순서대로 보낸다.

== 환경변수 ==
  KAKAO_REST_API_KEY  (필수) 카카오 개발자 앱 REST API 키
  KAKAO_REFRESH_TOKEN (필수) get_token.py 로 발급받은 리프레시 토큰
  COMPANIES           (선택) 포함할 회사 코드, 쉼표 구분 (기본: *_live 전체)
                      예: "CJ,GS,HD,LT,NS,HNS,PUBLIC"
  WEATHER_REGION      (선택) 날씨 지역 폴더명 (기본: seoul)
  LINK_URL            (선택) 메시지 하단 링크 (기본: GitHub Pages)
  TARGET_DATE         (선택) YYYY-MM-DD, 미지정 시 오늘(KST)
  DRY_RUN             (선택) "1"이면 발송하지 않고 메시지만 출력

리프레시 토큰이 갱신되어 새로 내려오면 kakao/.new_refresh_token 파일에 기록한다.
(워크플로우에서 이 파일을 감지해 GitHub Secret을 갱신할 수 있음)
"""

import os
import sys
import json
import glob
import time
import requests
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "")
REFRESH_TOKEN = os.environ.get("KAKAO_REFRESH_TOKEN", "")
WEATHER_REGION = os.environ.get("WEATHER_REGION") or "seoul"
LINK_URL = os.environ.get("LINK_URL", "https://hdmzp.github.io/hdhs/")
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"

# 카카오 텍스트 템플릿 text 필드 최대 길이
TEXT_LIMIT = 200

COMPANY_NAMES = {
    "CJ": "CJ",
    "GS": "GS",
    "HD": "현대",
    "LT": "롯데",
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
    rows = {cat: [] for _, cat in SECTIONS}
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
            cat = item.get("category")
            if cat not in rows:
                continue
            brand = (item.get("brand") or "").strip()
            product = clean_product(item.get("product") or "")
            # 상품명이 브랜드로 시작하면 중복 표기 생략
            if brand and product.startswith(brand):
                brand = ""
            name = COMPANY_NAMES.get(company, company)
            parts = [item.get("start", "??:??"), name]
            if brand:
                parts.append(brand)
            parts.append(product)
            rows[cat].append((item.get("start", ""), " ".join(parts)))
    for cat in rows:
        rows[cat].sort(key=lambda r: r[0])
    return rows


def build_message(now, weather, rows):
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
    lines.append("오늘 진행예정인 식품 방송입니다")
    for header, cat in SECTIONS:
        items = rows.get(cat, [])
        lines.append("")
        lines.append(header)
        lines.append("%d회" % len(items))
        for _, text in items:
            lines.append(text)
    return lines


def chunk_lines(lines, limit=TEXT_LIMIT):
    """줄 단위로 최대 limit자 이내의 청크들로 묶는다."""
    chunks = []
    cur = ""
    for line in lines:
        # 한 줄이 limit을 넘으면 잘라낸다
        if len(line) > limit:
            line = line[: limit - 1] + "…"
        candidate = line if not cur else cur + "\n" + line
        if len(candidate) > limit:
            if cur:
                chunks.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def refresh_access_token():
    resp = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": REST_API_KEY,
            "refresh_token": REFRESH_TOKEN,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        print("토큰 갱신 실패:", resp.status_code, resp.text)
        sys.exit(1)
    body = resp.json()
    # 리프레시 토큰의 유효기간이 1개월 미만이면 새 리프레시 토큰이 내려온다
    new_refresh = body.get("refresh_token")
    if new_refresh and new_refresh != REFRESH_TOKEN:
        out = os.path.join(ROOT, "kakao", ".new_refresh_token")
        with open(out, "w", encoding="utf-8") as f:
            f.write(new_refresh)
        print("※ 새 리프레시 토큰 발급됨 → %s (Secret 갱신 필요)" % out)
    return body["access_token"]


def send_to_me(access_token, text):
    template = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": LINK_URL, "mobile_web_url": LINK_URL},
        "button_title": "편성표 보기",
    }
    resp = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": "Bearer " + access_token},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=15,
    )
    if resp.status_code != 200:
        print("발송 실패:", resp.status_code, resp.text)
        return False
    return True


def main():
    now = target_date()
    day_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")

    companies_env = os.environ.get("COMPANIES", "").strip()
    companies = set(c.strip() for c in companies_env.split(",") if c.strip()) or None

    weather = load_weather(day_str)
    if weather is None:
        print("※ %s 날씨 데이터 없음 (%s)" % (day_str, WEATHER_REGION))
    rows = load_broadcasts(day_str, month_str, companies)
    total = sum(len(v) for v in rows.values())
    if total == 0:
        print("%s 식품 방송 데이터가 없습니다. 발송 생략." % day_str)
        return

    lines = build_message(now, weather, rows)
    chunks = chunk_lines(lines)
    print("방송 %d건 → 메시지 %d건으로 분할" % (total, len(chunks)))

    if DRY_RUN:
        for i, c in enumerate(chunks, 1):
            print("\n----- [%d/%d] (%d자) -----" % (i, len(chunks), len(c)))
            print(c)
        return

    if not REST_API_KEY or not REFRESH_TOKEN:
        print("KAKAO_REST_API_KEY / KAKAO_REFRESH_TOKEN 환경변수가 필요합니다.")
        sys.exit(1)

    access_token = refresh_access_token()
    sent = 0
    for i, chunk in enumerate(chunks, 1):
        if send_to_me(access_token, chunk):
            sent += 1
        else:
            print("[%d/%d] 발송 실패, 중단" % (i, len(chunks)))
            sys.exit(1)
        time.sleep(0.5)  # 순서 보장용
    print("발송 완료: %d/%d건" % (sent, len(chunks)))


if __name__ == "__main__":
    main()
