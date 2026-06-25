# -*- coding: utf-8 -*-
"""
전국 광역시 날씨 데이터 수집기 (ASOS 과거 관측 + 단기예보 미래)

== 수집 지역 (8곳, 광역시 단위) ==
서울(seoul) · 부산(busan) · 대구(daegu) · 인천(incheon) ·
광주(gwangju) · 대전(daejeon) · 울산(ulsan) · 세종(sejong)

== 저장 구조 ==
weather/
├── asos/{지역코드}/
│   ├── 2025-01.json ~ {전월}.json   : 확정된 과거 (한 번 받고 다시 안 건드림)
│   └── {현재월}.json                 : 진행 중인 달 (매일 그 달 1일~어제까지 통째로 재수집)
└── forecast/{지역코드}/
    └── latest.json                  : 오늘~글피 (매일 갱신)

(서울은 기존 정책과 동일하게 유지, 나머지 7개 지역도 동일한 정책으로 동작)

== 데이터 형식 (지역별 동일) ==
asos/{지역코드}/{YYYY-MM}.json:
  { "YYYY-MM-DD": {"minTa": 18.0, "maxTa": 29.0, "sumRn": 2.4}, ... }
  - sumRn은 강수 없는 날 0.0으로 정규화

forecast/{지역코드}/latest.json:
  { "YYYY-MM-DD": {"minTa": 18.0, "maxTa": 29.0, "pop_max": 60}, ... }
  - pop_max: 그날 시간대별 강수확률(POP) 중 최댓값

== 백필(backfill) 정책 (지역 공통) ==
- 시작일: 2023-01-01 (BACKFILL_START)
- 최초 실행 시 2023-01 ~ {전월}까지 월별로 한 번에 수집
- 이미 asos/{지역코드}/{그 월}.json 파일이 존재하면 건너뜀 (확정된 과거는 재수집 안 함)
- 현재월 파일은 매번 덮어씀

== 사용법 ==
  pip install requests
  API_KEY="발급받은_서비스키(Decoding)" python weather.py
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta
from calendar import monthrange

API_KEY = os.environ.get("API_KEY", "")
if not API_KEY:
    print("환경변수 API_KEY가 비어있습니다.")
    sys.exit(1)

# 지역코드: {ASOS 관측소ID(stn), 단기예보 격자(nx, ny)}
# 좌표는 기상청 공식 ASOS 지점코드 및 단기예보 격자표 기준 (광역시 대표 지점)
REGIONS = {
    "seoul":   {"name": "서울", "stn": "108", "nx": 60,  "ny": 127},
    "busan":   {"name": "부산", "stn": "159", "nx": 98,  "ny": 76},
    "daegu":   {"name": "대구", "stn": "143", "nx": 89,  "ny": 90},
    "incheon": {"name": "인천", "stn": "112", "nx": 55,  "ny": 124},
    "gwangju": {"name": "광주", "stn": "156", "nx": 58,  "ny": 74},
    "daejeon": {"name": "대전", "stn": "133", "nx": 67,  "ny": 100},
    "ulsan":   {"name": "울산", "stn": "152", "nx": 102, "ny": 84},
    "sejong":  {"name": "세종", "stn": "239", "nx": 66,  "ny": 103},
}

ASOS_URL = "http://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"
FORECAST_URL = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"

WEATHER_ROOT = "weather"

BACKFILL_START = datetime(2023, 1, 1)
REQUEST_DELAY_SEC = 0.5


def safe_float(v, default=0.0):
    """빈 문자열/None을 안전하게 숫자로 변환. 강수량 빈값은 0.0(강수 없음)으로 처리."""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def fetch_asos_range(start_dt: str, end_dt: str, stn_id: str) -> dict:
    """start_dt~end_dt(YYYYMMDD) 범위의 ASOS 일자료를 받아서 {YYYY-MM-DD: {...}} 형태로 반환."""
    params = {
        "serviceKey": API_KEY,
        "pageNo": "1",
        "numOfRows": "999",
        "dataType": "JSON",
        "dataCd": "ASOS",
        "dateCd": "DAY",
        "startDt": start_dt,
        "endDt": end_dt,
        "stnIds": stn_id,
    }
    resp = requests.get(ASOS_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    header = data.get("response", {}).get("header", {})
    if header.get("resultCode") != "00":
        raise RuntimeError(f"ASOS API 오류: {header.get('resultCode')} {header.get('resultMsg')}")

    items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
    result = {}
    for it in items:
        date_str = it.get("tm")  # "2026-06-21" 형식으로 옴
        if not date_str:
            continue
        result[date_str] = {
            "minTa": safe_float(it.get("minTa")),
            "maxTa": safe_float(it.get("maxTa")),
            "sumRn": safe_float(it.get("sumRn")),
        }
    return result


def month_range_str(year: int, month: int) -> tuple[str, str]:
    """해당 월의 1일과 마지막날을 YYYYMMDD 문자열로 반환."""
    last_day = monthrange(year, month)[1]
    start = f"{year:04d}{month:02d}01"
    end = f"{year:04d}{month:02d}{last_day:02d}"
    return start, end


def collect_backfill(region_code: str, stn_id: str):
    """2023-01 ~ 전월까지, 아직 파일이 없는 달만 백필. (지역별 디렉토리)"""
    asos_dir = os.path.join(WEATHER_ROOT, "asos", region_code)
    os.makedirs(asos_dir, exist_ok=True)

    now = datetime.now()
    # 전월의 마지막날까지가 백필 대상 (현재월은 별도 로직으로 처리)
    cursor = datetime(BACKFILL_START.year, BACKFILL_START.month, 1)

    while True:
        if cursor.year > now.year or (cursor.year == now.year and cursor.month >= now.month):
            break  # 현재월에 도달하면 백필 종료

        out_path = os.path.join(asos_dir, f"{cursor.year:04d}-{cursor.month:02d}.json")
        if os.path.exists(out_path):
            # 이미 확정된 과거 데이터 -> 건너뜀
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
            continue

        start_dt, end_dt = month_range_str(cursor.year, cursor.month)
        print(f"  [백필][{region_code}] {cursor.year:04d}-{cursor.month:02d} 수집 중...")
        try:
            month_data = fetch_asos_range(start_dt, end_dt, stn_id)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(month_data, f, ensure_ascii=False, indent=2)
            print(f"    -> {len(month_data)}일 저장: {out_path}")
        except Exception as e:
            print(f"    [실패] {cursor.year:04d}-{cursor.month:02d}: {e}")

        time.sleep(REQUEST_DELAY_SEC)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)


def collect_current_month(region_code: str, stn_id: str):
    """현재월: 1일~어제까지 통째로 재수집해서 덮어씀. (지역별 디렉토리)"""
    asos_dir = os.path.join(WEATHER_ROOT, "asos", region_code)
    os.makedirs(asos_dir, exist_ok=True)

    now = datetime.now()
    yesterday = now - timedelta(days=1)

    # 이번 달 1일이 아직 안 지났으면(즉 오늘이 1일이면) 수집할 게 없음
    if yesterday.month != now.month or yesterday.year != now.year:
        print(f"  [현재월][{region_code}] {now.year:04d}-{now.month:02d}: 아직 수집할 날이 없음 (오늘이 1일)")
        return

    start_dt = f"{now.year:04d}{now.month:02d}01"
    end_dt = yesterday.strftime("%Y%m%d")

    print(f"  [현재월][{region_code}] {now.year:04d}-{now.month:02d} 재수집 중 ({start_dt}~{end_dt})...")
    try:
        month_data = fetch_asos_range(start_dt, end_dt, stn_id)
        out_path = os.path.join(asos_dir, f"{now.year:04d}-{now.month:02d}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(month_data, f, ensure_ascii=False, indent=2)
        print(f"    -> {len(month_data)}일 저장: {out_path}")
    except Exception as e:
        print(f"    [실패] 현재월 수집: {e}")


def collect_forecast(region_code: str, nx: int, ny: int):
    """단기예보: 오늘~글피, 일자별 최저/최고/강수확률 최댓값 요약. (지역별 디렉토리)"""
    forecast_dir = os.path.join(WEATHER_ROOT, "forecast", region_code)
    os.makedirs(forecast_dir, exist_ok=True)

    now = datetime.now()
    base_date = now.strftime("%Y%m%d")
    base_time = "0200"  # 가장 안정적으로 발표 완료된 시각 기준

    params = {
        "serviceKey": API_KEY,
        "pageNo": "1",
        "numOfRows": "1000",
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": nx,
        "ny": ny,
    }

    print(f"  [예보][{region_code}] 단기예보 수집 중...")
    try:
        resp = requests.get(FORECAST_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        header = data.get("response", {}).get("header", {})
        if header.get("resultCode") != "00":
            raise RuntimeError(f"예보 API 오류: {header.get('resultCode')} {header.get('resultMsg')}")

        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])

        by_date = {}  # {fcstDate: {"minTa":.., "maxTa":.., "pop_list":[...]}}
        for it in items:
            fdate = it.get("fcstDate")
            category = it.get("category")
            value = it.get("fcstValue")
            if not fdate:
                continue
            entry = by_date.setdefault(fdate, {"minTa": None, "maxTa": None, "pop_list": []})
            if category == "TMN":
                entry["minTa"] = safe_float(value)
            elif category == "TMX":
                entry["maxTa"] = safe_float(value)
            elif category == "POP":
                entry["pop_list"].append(safe_float(value))

        result = {}
        for fdate, entry in by_date.items():
            iso_date = f"{fdate[:4]}-{fdate[4:6]}-{fdate[6:8]}"
            pop_max = max(entry["pop_list"]) if entry["pop_list"] else 0.0
            result[iso_date] = {
                "minTa": entry["minTa"],
                "maxTa": entry["maxTa"],
                "pop_max": pop_max,
            }

        out_path = os.path.join(forecast_dir, "latest.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"    -> {len(result)}일 저장: {out_path}")

    except Exception as e:
        print(f"    [실패] 예보 수집: {e}")


def main():
    for region_code, info in REGIONS.items():
        print(f"\n=== [{info['name']}({region_code})] 수집 시작 ===")
        collect_backfill(region_code, info["stn"])
        collect_current_month(region_code, info["stn"])
        collect_forecast(region_code, info["nx"], info["ny"])
        time.sleep(REQUEST_DELAY_SEC)

    print("\n완료.")


if __name__ == "__main__":
    main()