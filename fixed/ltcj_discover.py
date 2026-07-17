# -*- coding: utf-8 -*-
"""
ltcj_discover.py  (일회성 탐색 스크립트)

LT(롯데홈쇼핑)/CJ(CJ온스타일) 편성 API 응답 원본을 필드 하나도 안 버리고
그대로 덤프한다. HD의 withItemList처럼 "대표상품 외 나머지 방송상품"이
응답 안에 숨어있는지, 고정PGM을 판별할 프로그램명 필드가 있는지 확인용.
GitHub Actions(네트워크 제한 없음)에서 workflow_dispatch로 실행.

출력: homeshopping/_debug_ltcj_discovery/
  lt_scheduleLive_{날짜}.json / lt_scheduleOne_{날짜}.json
  cj_tvSchedule_live_{날짜}.json / cj_tvSchedule_plus_{날짜}.json
"""

import os
import json
import datetime
import requests

OUT_DIR = os.path.join("homeshopping", "_debug_ltcj_discovery")
os.makedirs(OUT_DIR, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

KST = datetime.timezone(datetime.timedelta(hours=9))
today = datetime.datetime.now(KST).date()

# 셀럽PGM이 걸린 날짜들: 토(최유라쇼 LT 08:20), 월(강주은 CJ 19:35),
# 화(김창옥 CJ 19:35), 수(최화정 CJ 20:45)
def next_weekday(wd):
    return (today + datetime.timedelta(days=(wd - today.weekday()) % 7)).strftime("%Y%m%d")

DATES = os.environ.get("BROD_DTS", "").split(",") if os.environ.get("BROD_DTS") \
    else [next_weekday(5), next_weekday(0), next_weekday(2)]  # 토, 월, 수


def dump(name, url, headers):
    try:
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json() if r.status_code == 200 else {"_status": r.status_code, "_text": r.text[:500]}
    except Exception as e:
        data = {"_error": str(e)}
    path = os.path.join(OUT_DIR, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"저장: {path} ({os.path.getsize(path)} bytes)")


for dt in DATES:
    dt = dt.strip()
    if not dt:
        continue
    dash = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}"

    lt_headers = {"User-Agent": UA, "Referer": "https://www.lotteimall.com/main/viewMain.lotte"}
    for ep in ("scheduleLive", "scheduleOne"):
        dump(f"lt_{ep}_{dt}",
             f"https://www.lotteimall.com/main/{ep}.lotte?bdDate={dt}&date={dash}",
             lt_headers)

    cj_headers = {"User-Agent": UA, "Referer": "https://display.cjonstyle.com/p/tv/tvSchedule"}
    for bt in ("live", "plus"):
        dump(f"cj_tvSchedule_{bt}_{dt}",
             f"https://display.cjonstyle.com/c/rest/tv/tvSchedule"
             f"?bdDt={dt}&isMobile=false&broadType={bt}&isEmployee=false",
             cj_headers)
