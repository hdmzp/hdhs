# -*- coding: utf-8 -*-
"""
GS홈쇼핑 편성표 수집기 (라방바 데이터랩 API 우회용)

GS 본진 서버의 클라우드 IP 차단을 우회하기 위해,
데이터 어그리게이터인 '라방바'의 API를 활용하여 데이터를 수집합니다.
GitHub Actions 등 클라우드 환경에서도 정상 동작합니다.

주의: 라방바 목록 API 특성상 '가격'과 '링크' 정보는 수집되지 않습니다.
"""

import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
OUTPUT_DIR = "homeshopping"
REQUEST_DELAY = 1.0  # 서버 부담을 줄이기 위해 1초 대기
DAYS_RANGE = range(-1, 6)  # 어제 ~ +5일

def today_kst():
    return datetime.now(KST)

def fetch_ecomm_gs(date_obj: datetime, broadcast: str) -> list:
    """라방바 API를 통해 GS 편성표 1일치 수집. broadcast: 'live' | 'data'"""
    url = "https://live.ecomm-data.com/api/schedule/list_hs"
    
    # 1. API가 요구하는 필수 헤더 설정 (크롬 브라우저 위장)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Origin": "https://live.ecomm-data.com",
        "Referer": "https://live.ecomm-data.com/schedule/hs"
    }
    
    # 2. 날짜 포맷팅 (YYYY-MM-DD -> YYMMDD) ex: 2026-06-22 -> 260622
    date_yy = date_obj.strftime("%y%m%d")
    
    # 3. 플랫폼 ID 매핑
    platform_id = "hs_gsshop" if broadcast == "live" else "hs_gsshopmyshop"
    
    # 4. POST 요청에 담을 Payload (Body)
    payload = {
        "date": date_yy,
        "type": None,
        "platform": [platform_id],
        "cid": None
    }
    
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        if r.status_code != 200:
            print(f"    [GS_{broadcast}] HTTP {r.status_code} 오류")
            return []
            
        data = r.json()
        programs = []
        
        for item in data.get("list", []):
            start_raw = item.get("hsshow_datetime_start", "") # 예: 202606220038
            end_raw = item.get("hsshow_datetime_end", "")
            
            # 기존 스키마에 맞게 날짜 문자열에 하이픈과 콜론 추가 (선택)
            if len(start_raw) == 12:
                start_raw = f"{start_raw[:4]}-{start_raw[4:6]}-{start_raw[6:8]} {start_raw[8:10]}:{start_raw[10:]}"
            if len(end_raw) == 12:
                end_raw = f"{end_raw[:4]}-{end_raw[4:6]}-{end_raw[6:8]} {end_raw[8:10]}:{end_raw[10:]}"

            # 기존 3사와 동일한 스키마 구조로 딕셔너리 생성
            programs.append({
                "start": start_raw,
                "end": end_raw,
                "brand": "",
                "product": item.get("hsshow_title", ""),
                "price": 0,  # 제공되지 않음
                "link": "",  # 제공되지 않음
                "category": item.get("cat", {}).get("cat_name", ""),
            })
            
        # 시간순 정렬
        programs.sort(key=lambda x: x["start"])
        return programs
        
    except Exception as e:
        print(f"    [GS_{broadcast}] API 호출/파싱 오류: {e}")
        return []

def load_month(sub_dir, ym):
    path = os.path.join(sub_dir, f"{ym}.json")
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8")).get("days", {})
        except Exception:
            return {}
    return {}

def main():
    base = today_kst()
    today_str = base.strftime("%Y-%m-%d")

    for broadcast in ("live", "data"):
        sub_dir = os.path.join(OUTPUT_DIR, f"GS_{broadcast}")
        os.makedirs(sub_dir, exist_ok=True)
        month_data = {}

        for offset in DAYS_RANGE:
            d = base + timedelta(days=offset)
            date_dash = d.strftime("%Y-%m-%d")
            ym = d.strftime("%Y-%m")
            
            if ym not in month_data:
                month_data[ym] = load_month(sub_dir, ym)
            days = month_data[ym]

            is_past = date_dash < today_str
            if is_past and days.get(date_dash):
                print(f"[GS_{broadcast}] {date_dash}: 이미 기록됨, 건너뜀")
                continue

            print(f"[GS_{broadcast}] {date_dash} 수집 중...")
            
            # API 함수 호출 (datetime 객체를 직접 넘김)
            programs = fetch_ecomm_gs(d, broadcast)
            
            if is_past and not programs:
                print(f"  -> 0개 (과거, 기존값 유지)")
                time.sleep(REQUEST_DELAY)
                continue
                
            days[date_dash] = programs
            print(f"  -> {len(programs)}개 편성")
            time.sleep(REQUEST_DELAY)

        for ym, days in month_data.items():
            if not days:
                continue
            out_path = os.path.join(sub_dir, f"{ym}.json")
            sorted_days = {k: days[k] for k in sorted(days)}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({
                    "company": "GS", "broadcast": broadcast,
                    "month": ym, "days": sorted_days,
                }, f, ensure_ascii=False, indent=2)
            print(f"  저장: {out_path} ({len(sorted_days)}일)")

    print("\n완료. (이제 클라우드 환경에서도 동작할 수 있습니다.)")

if __name__ == "__main__":
    main()