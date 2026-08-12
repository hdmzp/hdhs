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
  { "YYYY-MM-DD": {"minTa": 18.0, "maxTa": 29.0, "pop_max": 60, "source": "kma"}, ... }
  - pop_max: 그날 시간대별 강수확률(POP) 중 최댓값
  - source: 이 값을 어디서 받았는지 (kma / open-meteo)

== 백필(backfill) 정책 (지역 공통) ==
- 시작일: 2023-01-01 (BACKFILL_START)
- 최초 실행 시 2023-01 ~ {전월}까지 월별로 한 번에 수집
- asos/{지역코드}/{그 월}.json이 있고 그 달의 날짜가 모두 채워져 있으면 건너뜀
- 파일이 있어도 빠진 날짜가 있으면 그 달을 다시 받아 채움
  (현재월 파일은 '1일~어제'까지만 담기므로, 달이 바뀌면 말일이 빈 채로
   과거 파일이 된다. 존재 여부만 보고 건너뛰면 말일이 영구 결손된다.)
- 재수집 결과는 기존 데이터와 병합 -> 응답이 비어도 기존 데이터를 잃지 않음

== 예보 수집 폴백 ==
- 기상청(apis.data.go.kr)이 막힌 환경(GitHub Actions 러너에서 ConnectTimeout)이
  있어서, 기상청 단기예보가 실패하면 Open-Meteo(무료/키 불필요)로 같은 형식의
  예보를 받아 채운다. 어느 쪽도 실패하면 기존 파일을 건드리지 않는다.

== 시간대 ==
- 모든 날짜 계산은 한국시간(KST) 기준. 워크플로우는 UTC 20:30(=KST 05:30)에
  도는데 UTC로 '오늘'을 잡으면 하루 전 날짜로 예보를 요청하게 되어
  미래 예보가 하루씩 밀린다.

== 사용법 ==
  pip install requests
  API_KEY="발급받은_서비스키(Decoding)" python weather.py
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta, timezone
from calendar import monthrange

API_KEY = os.environ.get("API_KEY", "")
if not API_KEY:
    print("환경변수 API_KEY가 비어있습니다.")
    sys.exit(1)

# 지역코드: {ASOS 관측소ID(stn), 단기예보 격자(nx, ny), 폴백용 위경도(lat, lon)}
# 좌표는 기상청 공식 ASOS 지점코드 및 단기예보 격자표 기준 (광역시 대표 지점)
REGIONS = {
    "seoul":   {"name": "서울", "stn": "108", "nx": 60,  "ny": 127, "lat": 37.5665, "lon": 126.9780},
    "busan":   {"name": "부산", "stn": "159", "nx": 98,  "ny": 76,  "lat": 35.1796, "lon": 129.0756},
    "daegu":   {"name": "대구", "stn": "143", "nx": 89,  "ny": 90,  "lat": 35.8714, "lon": 128.6014},
    "incheon": {"name": "인천", "stn": "112", "nx": 55,  "ny": 124, "lat": 37.4563, "lon": 126.7052},
    "gwangju": {"name": "광주", "stn": "156", "nx": 58,  "ny": 74,  "lat": 35.1595, "lon": 126.8526},
    "daejeon": {"name": "대전", "stn": "133", "nx": 67,  "ny": 100, "lat": 36.3504, "lon": 127.3845},
    "ulsan":   {"name": "울산", "stn": "152", "nx": 102, "ny": 84,  "lat": 35.5384, "lon": 129.3114},
    "sejong":  {"name": "세종", "stn": "239", "nx": 66,  "ny": 103, "lat": 36.4800, "lon": 127.2890},
}

ASOS_PATH = "/1360000/AsosDalyInfoService/getWthrDataList"
FORECAST_PATH = "/1360000/VilageFcstInfoService_2.0/getVilageFcst"

# 기상청이 막혔을 때 쓰는 예보 폴백 (API 키 불필요)
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

KST = timezone(timedelta(hours=9))
FORECAST_DAYS = 4        # 오늘 + 앞으로 3일
FALLBACK_PAST_DAYS = 3   # 폴백 시 최근 며칠치도 같이 받아 ASOS 공백을 임시로 메움
MIN_TMP_SAMPLES = 3     # TMN/TMX 없는 날을 시간대별 기온으로 대신 채울 최소 표본 수

# 평문 http(80)로만 붙으면 실행 환경에 따라 연결 자체가 타임아웃 난다
# (GitHub Actions 러너에서 전 지역·전 엔드포인트 ConnectTimeout 발생).
# https를 우선 쓰고, 그쪽이 막힌 환경을 위해 http로 폴백한다.
API_HOSTS = ["https://apis.data.go.kr", "http://apis.data.go.kr"]

WEATHER_ROOT = "weather"

BACKFILL_START = datetime(2023, 1, 1)
REQUEST_DELAY_SEC = 0.5
REQUEST_TIMEOUT_SEC = 20        # 응답 대기(읽기) 제한
REQUEST_CONNECT_TIMEOUT_SEC = 5  # 접속 제한. 막힌 호스트에 20초씩 매달리면
                                 # 지역 8곳 x 요청 2종에 15분이 날아가 잡 타임아웃에 닿는다
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 2
FAILFAST_AFTER = 3  # 연속 실패가 이만큼 쌓이면 재시도 없이 즉시 포기


def now_kst() -> datetime:
    """한국시간 기준 현재 시각(naive). 러너는 UTC라 그대로 쓰면 날짜가 하루 밀린다."""
    return datetime.now(KST).replace(tzinfo=None)


def safe_float(v, default=0.0):
    """빈 문자열/None을 안전하게 숫자로 변환. 강수량 빈값은 0.0(강수 없음)으로 처리."""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# 출처(기상청/Open-Meteo)별 연속 실패 횟수. 기상청이 죽었다고 폴백까지
# 재시도 없이 포기해 버리면 폴백이 폴백 역할을 못 한다.
_consecutive_failures = {}


def request_json_urls(urls: list[str], params: dict, group: str) -> dict:
    """주어진 URL 후보들을 순서대로 호출. 실패 시 백오프 재시도.

    API 전면 장애 시 (지역 8곳 x 요청 3종)에 매번 재시도를 다 돌면 워크플로우
    타임아웃을 넘긴다. 같은 출처에서 연속 실패가 쌓이면 재시도 없이 바로
    포기한다(성공하면 해제).
    """
    failfast = _consecutive_failures.get(group, 0) >= FAILFAST_AFTER
    attempts = 1 if failfast else MAX_RETRIES
    targets = urls[:1] if failfast else urls

    last_err = None
    for attempt in range(attempts):
        for url in targets:
            try:
                resp = requests.get(
                    url, params=params,
                    timeout=(REQUEST_CONNECT_TIMEOUT_SEC, REQUEST_TIMEOUT_SEC))
                resp.raise_for_status()
                data = resp.json()
                _consecutive_failures[group] = 0
                return data
            except Exception as e:  # 연결 실패 / HTTP 에러 / JSON 아닌 응답
                last_err = e
        if attempt < attempts - 1:
            time.sleep(RETRY_BACKOFF_SEC * (2 ** attempt))

    _consecutive_failures[group] = _consecutive_failures.get(group, 0) + 1
    raise RuntimeError(f"요청 실패 ({urls[0]}): {last_err}")


def request_json(path: str, params: dict) -> dict:
    """공공데이터포털 API 호출. https 우선 + http 폴백."""
    return request_json_urls([host + path for host in API_HOSTS], params, "kma")


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
    data = request_json(ASOS_PATH, params)

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


def next_month(dt: datetime) -> datetime:
    """다음 달 1일로 이동."""
    return (dt.replace(day=28) + timedelta(days=4)).replace(day=1)


def load_month_file(path: str) -> dict:
    """월별 json을 읽어 dict로 반환. 없거나 깨졌으면 빈 dict."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def missing_days(year: int, month: int, data: dict) -> list[str]:
    """해당 월에서 빠진 날짜 목록(YYYY-MM-DD)."""
    last_day = monthrange(year, month)[1]
    return [
        d for d in (f"{year:04d}-{month:02d}-{i:02d}" for i in range(1, last_day + 1))
        if d not in data
    ]


def collect_backfill(region_code: str, stn_id: str) -> bool:
    """2023-01 ~ 전월까지, 파일이 없거나 날짜가 빠진 달을 백필. (지역별 디렉토리)

    현재월 파일은 '1일~어제'까지만 담기므로, 달이 바뀌는 순간 그 달의 말일이
    비어 있는 채로 과거 파일이 된다. 따라서 파일 존재만으로 건너뛰면 안 되고
    그 달의 모든 날짜가 채워져 있는지까지 확인해서 빠진 달은 다시 받아 채운다.
    """
    asos_dir = os.path.join(WEATHER_ROOT, "asos", region_code)
    os.makedirs(asos_dir, exist_ok=True)

    now = now_kst()
    # 전월의 마지막날까지가 백필 대상 (현재월은 별도 로직으로 처리)
    cursor = datetime(BACKFILL_START.year, BACKFILL_START.month, 1)
    failed = False

    while True:
        if cursor.year > now.year or (cursor.year == now.year and cursor.month >= now.month):
            break  # 현재월에 도달하면 백필 종료

        ym = f"{cursor.year:04d}-{cursor.month:02d}"
        out_path = os.path.join(asos_dir, f"{ym}.json")
        existing = load_month_file(out_path) if os.path.exists(out_path) else {}

        gaps = missing_days(cursor.year, cursor.month, existing)
        if existing and not gaps:
            # 모든 날짜가 채워진 확정 과거 데이터 -> 건너뜀
            cursor = next_month(cursor)
            continue

        if existing:
            print(f"  [보정][{region_code}] {ym}: {len(gaps)}일 누락({', '.join(gaps[:3])}"
                  f"{' 외' if len(gaps) > 3 else ''}) -> 재수집")
        else:
            print(f"  [백필][{region_code}] {ym} 수집 중...")

        start_dt, end_dt = month_range_str(cursor.year, cursor.month)
        try:
            month_data = fetch_asos_range(start_dt, end_dt, stn_id)
            # 응답이 비거나 일부만 와도 기존 데이터를 잃지 않도록 병합
            merged = {**existing, **month_data}
            if merged != existing:
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(merged, f, ensure_ascii=False, indent=2)
                print(f"    -> {len(merged)}일 저장: {out_path}")
            else:
                # 관측 자체가 없는 날은 계속 비어 있을 수 있다 (다음 실행에서 재시도)
                print(f"    -> 새로 채운 날짜 없음: {out_path}")
        except Exception as e:
            print(f"    [실패] {ym}: {e}")
            failed = True

        time.sleep(REQUEST_DELAY_SEC)
        cursor = next_month(cursor)

    return not failed


def collect_current_month(region_code: str, stn_id: str) -> bool:
    """현재월: 1일~어제까지 통째로 재수집해서 덮어씀. (지역별 디렉토리)"""
    asos_dir = os.path.join(WEATHER_ROOT, "asos", region_code)
    os.makedirs(asos_dir, exist_ok=True)

    now = now_kst()
    yesterday = now - timedelta(days=1)

    # 이번 달 1일이 아직 안 지났으면(즉 오늘이 1일이면) 수집할 게 없음
    # (어제 = 지난달 말일은 collect_backfill이 지난달 파일을 채우면서 처리한다)
    if yesterday.month != now.month or yesterday.year != now.year:
        print(f"  [현재월][{region_code}] {now.year:04d}-{now.month:02d}: 아직 수집할 날이 없음 (오늘이 1일)")
        return True

    start_dt = f"{now.year:04d}{now.month:02d}01"
    end_dt = yesterday.strftime("%Y%m%d")

    print(f"  [현재월][{region_code}] {now.year:04d}-{now.month:02d} 재수집 중 ({start_dt}~{end_dt})...")
    try:
        month_data = fetch_asos_range(start_dt, end_dt, stn_id)
        out_path = os.path.join(asos_dir, f"{now.year:04d}-{now.month:02d}.json")
        # 응답이 일부만 와도 이미 받아둔 날을 잃지 않도록 병합
        existing = load_month_file(out_path) if os.path.exists(out_path) else {}
        merged = {**existing, **month_data}
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"    -> {len(merged)}일 저장: {out_path}")
        return True
    except Exception as e:
        print(f"    [실패] 현재월 수집: {e}")
        return False


def fetch_forecast_kma(nx: int, ny: int, now: datetime) -> dict:
    """기상청 단기예보. {YYYY-MM-DD: {minTa, maxTa, pop_max, source}} 반환.

    발표 시각(base_time)은 02시 발표를 먼저 쓰고, 아직 안 나온 시간대(새벽 등)에
    실행되면 전날 23시 발표로 물러난다. 예보 마지막 날은 TMN/TMX가 안 오는
    경우가 있어 시간대별 기온(TMP)의 최저/최고로 채운다.
    """
    yesterday = now - timedelta(days=1)
    candidates = [
        (now.strftime("%Y%m%d"), "0200"),
        (yesterday.strftime("%Y%m%d"), "2300"),
    ]

    last_err = None
    for base_date, base_time in candidates:
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
        try:
            data = request_json(FORECAST_PATH, params)
        except Exception as e:
            last_err = e
            continue

        header = data.get("response", {}).get("header", {})
        if header.get("resultCode") != "00":
            last_err = RuntimeError(
                f"예보 API 오류: {header.get('resultCode')} {header.get('resultMsg')}")
            continue

        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])

        by_date = {}  # {fcstDate: {"minTa":.., "maxTa":.., "pop_list":[], "tmp_list":[]}}
        for it in items:
            fdate = it.get("fcstDate")
            category = it.get("category")
            value = it.get("fcstValue")
            if not fdate:
                continue
            entry = by_date.setdefault(
                fdate, {"minTa": None, "maxTa": None, "pop_list": [], "tmp_list": []})
            if category == "TMN":
                entry["minTa"] = safe_float(value, None)
            elif category == "TMX":
                entry["maxTa"] = safe_float(value, None)
            elif category == "TMP":
                tmp = safe_float(value, None)
                if tmp is not None:
                    entry["tmp_list"].append(tmp)
            elif category == "POP":
                entry["pop_list"].append(safe_float(value))

        result = {}
        for fdate, entry in by_date.items():
            iso_date = f"{fdate[:4]}-{fdate[4:6]}-{fdate[6:8]}"
            min_ta = entry["minTa"]
            max_ta = entry["maxTa"]
            # 예보 창 끝에 한두 시간만 걸친 날은 TMP로 채우면 최저=최고가 되어
            # 하루 기온처럼 보이지 않는다. 시간대가 어느 정도 모인 날만 채운다.
            usable_tmp = entry["tmp_list"] if len(entry["tmp_list"]) >= MIN_TMP_SAMPLES else []
            if min_ta is None and usable_tmp:
                min_ta = min(usable_tmp)
            if max_ta is None and usable_tmp:
                max_ta = max(usable_tmp)
            if min_ta is None and max_ta is None:
                continue  # 값이 하나도 없는 날은 담지 않는다 ('최저 –/최고 –' 방지)
            result[iso_date] = {
                "minTa": min_ta,
                "maxTa": max_ta,
                "pop_max": max(entry["pop_list"]) if entry["pop_list"] else 0.0,
                "source": "kma",
            }
        if result:
            return result
        last_err = RuntimeError(f"예보 응답이 비어 있음 (base {base_date} {base_time})")

    raise RuntimeError(str(last_err) if last_err else "예보 응답 없음")


def fetch_forecast_openmeteo(lat: float, lon: float) -> dict:
    """폴백 예보(Open-Meteo). 기상청과 같은 형식으로 반환.

    기상청 API가 막힌 환경에서도 미래 예보가 비지 않도록 쓰는 예비 출처다.
    최근 며칠(FALLBACK_PAST_DAYS)도 같이 받아, ASOS 관측이 아직 안 들어온
    날의 임시 표시에 쓴다.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_min,temperature_2m_max,precipitation_probability_max",
        "timezone": "Asia/Seoul",
        "forecast_days": FORECAST_DAYS,
        "past_days": FALLBACK_PAST_DAYS,
    }
    data = request_json_urls([OPEN_METEO_URL], params, "open-meteo")

    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    mins = daily.get("temperature_2m_min") or []
    maxs = daily.get("temperature_2m_max") or []
    pops = daily.get("precipitation_probability_max") or []

    result = {}
    for i, iso_date in enumerate(dates):
        min_ta = safe_float(mins[i], None) if i < len(mins) else None
        max_ta = safe_float(maxs[i], None) if i < len(maxs) else None
        if min_ta is None and max_ta is None:
            continue
        result[iso_date] = {
            "minTa": min_ta,
            "maxTa": max_ta,
            "pop_max": safe_float(pops[i]) if i < len(pops) else 0.0,
            "source": "open-meteo",
        }
    if not result:
        raise RuntimeError("Open-Meteo 응답에 일자료가 없음")
    return result


def has_temp(entry: dict) -> bool:
    """최저/최고 중 하나라도 값이 있는지. 둘 다 없으면 화면에 '–'만 남는다."""
    return isinstance(entry, dict) and (
        entry.get("minTa") is not None or entry.get("maxTa") is not None)


def carry_over_past(region_code: str, existing: dict, fresh: dict, today: str) -> dict:
    """새 예보 창(오늘~글피) + '아직 ASOS 관측이 없는 지난 날'만 남긴다.

    latest.json은 원래 오늘~글피짜리지만, 프론트는 같은 날짜에 대해 예보를
    관측보다 먼저 쓴다. 지난 날짜를 무한정 들고 있으면 나중에 들어온 실제
    관측값을 계속 가리게 되므로, ASOS에 이미 있는 날은 버린다. 반대로 ASOS가
    아직 비어 있는 최근 며칠은 남겨야 달력에 '정보없음' 구멍이 생기지 않는다.
    """
    asos_cache = {}

    def asos_has(date_str: str) -> bool:
        ym = date_str[:7]
        if ym not in asos_cache:
            asos_cache[ym] = load_month_file(
                os.path.join(WEATHER_ROOT, "asos", region_code, f"{ym}.json"))
        return date_str in asos_cache[ym]

    merged = {}
    for date_str, entry in fresh.items():
        # 폴백 출처는 지난 며칠도 같이 주는데, 실제 관측이 있는 날은 그쪽이 우선이다
        if date_str < today and asos_has(date_str):
            continue
        merged[date_str] = entry

    for date_str, entry in existing.items():
        if date_str >= today or date_str in merged:
            continue  # 오늘 이후는 새 예보로 갈아끼운다
        if asos_has(date_str):
            continue  # 실제 관측이 들어왔으니 예보값은 버린다
        if not has_temp(entry):
            continue  # 기온이 없는 껍데기는 '최저 –/최고 –'로만 보이니 버린다
        merged[date_str] = entry

    return merged


def collect_forecast(region_code: str, info: dict) -> bool:
    """단기예보: 오늘~글피, 일자별 최저/최고/강수확률 최댓값 요약. (지역별 디렉토리)

    기상청 -> Open-Meteo 순으로 시도하고, 둘 다 실패하면 기존 파일을 그대로 둔다
    (빈 결과로 덮어써서 미래 예보가 통째로 사라지는 것을 막는다).
    """
    forecast_dir = os.path.join(WEATHER_ROOT, "forecast", region_code)
    os.makedirs(forecast_dir, exist_ok=True)

    now = now_kst()
    print(f"  [예보][{region_code}] 단기예보 수집 중...")

    fresh = None
    try:
        fresh = fetch_forecast_kma(info["nx"], info["ny"], now)
    except Exception as e:
        print(f"    [기상청 실패] {e}")

    if not fresh:
        print(f"    [폴백][{region_code}] Open-Meteo로 재시도...")
        try:
            fresh = fetch_forecast_openmeteo(info["lat"], info["lon"])
        except Exception as e:
            print(f"    [폴백 실패] {e}")

    if not fresh:
        print(f"    [실패] 예보 수집: 모든 출처 실패 (기존 파일 유지)")
        return False

    out_path = os.path.join(forecast_dir, "latest.json")
    existing = load_month_file(out_path) if os.path.exists(out_path) else {}
    merged = carry_over_past(region_code, existing, fresh, now.strftime("%Y-%m-%d"))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(merged.items())), f, ensure_ascii=False, indent=2)
    source = next(iter(fresh.values())).get("source", "?")
    print(f"    -> {len(merged)}일 저장 (출처 {source}): {out_path}")
    return True


def main():
    ok = 0
    total = 0
    forecast_ok = 0
    for region_code, info in REGIONS.items():
        print(f"\n=== [{info['name']}({region_code})] 수집 시작 ===")
        forecast_result = None
        for result in (
            collect_backfill(region_code, info["stn"]),
            collect_current_month(region_code, info["stn"]),
            collect_forecast(region_code, info),
        ):
            total += 1
            ok += 1 if result else 0
            forecast_result = result  # 마지막 항목이 예보
        forecast_ok += 1 if forecast_result else 0
        time.sleep(REQUEST_DELAY_SEC)

    print(f"\n완료. 성공 {ok}/{total} (예보 {forecast_ok}/{len(REGIONS)})")
    if ok == 0:
        # 전부 실패(예: API 호스트 접속 불가)했는데 종료코드가 0이면
        # 워크플로우가 '성공'으로 끝나 아무도 이상을 눈치채지 못한다.
        print("모든 수집이 실패했습니다.")
        sys.exit(1)
    if forecast_ok == 0:
        # 백필은 '받을 게 없어서' 성공으로 잡히기 때문에, 예보가 전 지역 실패해도
        # ok는 0이 아니다. 그러면 달력의 미래 날짜만 조용히 비는데 워크플로우는
        # 성공으로 끝난다. 예보가 전멸하면 실패로 끝내서 눈에 띄게 한다.
        print("모든 지역의 예보 수집이 실패했습니다 (달력의 미래 날짜가 비게 됩니다).")
        sys.exit(1)


if __name__ == "__main__":
    main()