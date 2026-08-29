# -*- coding: utf-8 -*-
"""
전국 광역시 날씨 데이터 수집기 (ASOS 과거 관측 + 단기예보 미래 + 공휴일)

== 수집 지역 (8곳, 광역시 단위) ==
서울(seoul) · 부산(busan) · 대구(daegu) · 인천(incheon) ·
광주(gwangju) · 대전(daejeon) · 울산(ulsan) · 세종(sejong)

== 저장 구조 ==
weather/
├── asos/{지역코드}/
│   ├── 2025-01.json ~ {전월}.json   : 확정된 과거 (한 번 받고 다시 안 건드림)
│   └── {현재월}.json                 : 진행 중인 달 (매일 그 달 1일~어제까지 통째로 재수집)
├── forecast/{지역코드}/
│   └── latest.json                  : 오늘~글피 (매일 갱신)
└── holiday/
    └── {YYYY}.json                  : 공휴일 (지역 무관, 2023~내년)

(서울은 기존 정책과 동일하게 유지, 나머지 7개 지역도 동일한 정책으로 동작)

== 데이터 형식 (지역별 동일) ==
asos/{지역코드}/{YYYY-MM}.json:
  { "YYYY-MM-DD": {"minTa": 18.0, "maxTa": 29.0, "sumRn": 2.4}, ... }
  - sumRn은 강수 없는 날 0.0으로 정규화

forecast/{지역코드}/latest.json:
  { "YYYY-MM-DD": {"minTa": 18.0, "maxTa": 29.0, "pop_max": 60}, ... }
  - pop_max: 그날 시간대별 강수확률(POP) 중 최댓값

holiday/{YYYY}.json:
  { "YYYY-MM-DD": "광복절", ... }
  - 한국천문연구원 특일 정보의 관공서 공휴일(isHoliday=Y)만 담는다
  - 지난 연도는 확정 -> 재수집 안 함 / 올해·내년은 매일 재수집 후 병합(임시공휴일 대응)
  - 연 단위로 한 번에 조회(평상시 하루 2회 요청). API 접속이 안 되면 즉시 중단해
    요청당 연결 타임아웃이 쌓여 워크플로우 전체가 죽는 것을 막는다

== 백필(backfill) 정책 (지역 공통) ==
- 시작일: 2023-01-01 (BACKFILL_START)
- 최초 실행 시 2023-01 ~ {전월}까지 월별로 한 번에 수집
- asos/{지역코드}/{그 월}.json이 있고 그 달의 날짜가 모두 채워져 있으면 건너뜀
- 파일이 있어도 빠진 날짜가 있으면 그 달을 다시 받아 채움
  (현재월 파일은 '1일~어제'까지만 담기므로, 달이 바뀌면 말일이 빈 채로
   과거 파일이 된다. 존재 여부만 보고 건너뛰면 말일이 영구 결손된다.)
- 재수집 결과는 기존 데이터와 병합 -> 응답이 비어도 기존 데이터를 잃지 않음

== 사용법 ==
  pip install requests
  API_KEY="기상청_서비스키(Decoding)" python weather.py
  # 공휴일 키가 따로면: HOLIDAY_API_KEY="천문연_서비스키(Decoding)" 도 함께 지정
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta, timezone
from calendar import monthrange

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """한국 시간 기준 현재 시각(naive).

    GitHub Actions 러너는 UTC라 datetime.now()를 그대로 쓰면 한국 날짜와
    하루 어긋난다(KST 05:30 실행 = UTC 전날 20:30). 그러면 예보를 전날
    발표분으로 받아와 '오늘·미래 기온'이 비어 버린다. 날짜 계산은 모두 KST로.
    """
    return datetime.now(KST).replace(tzinfo=None)


API_KEY = os.environ.get("API_KEY", "")
if not API_KEY:
    print("환경변수 API_KEY가 비어있습니다.")
    sys.exit(1)

# 공공데이터포털은 서비스별로 활용신청이 따로다. 기상청(1360000)과
# 천문연(B090041)에 서로 다른 키를 쓸 수 있도록 분리해 두고,
# HOLIDAY_API_KEY가 없으면 기존 키를 그대로 쓴다(하위호환).
HOLIDAY_API_KEY = os.environ.get("HOLIDAY_API_KEY", "") or API_KEY

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

ASOS_PATH = "/1360000/AsosDalyInfoService/getWthrDataList"
FORECAST_PATH = "/1360000/VilageFcstInfoService_2.0/getVilageFcst"
# 한국천문연구원 특일 정보 - 관공서 공휴일(실제 쉬는 날)만 반환.
# 음력 명절/대체공휴일/임시공휴일이 모두 포함되므로 이 엔드포인트 하나면 충분하다.
HOLIDAY_PATH = "/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"
# 절기: 24절기(입춘·입추·동지...)와 잡절(초복·중복·말복·한식...)은 분류가 달라
# 엔드포인트가 따로다. 쉬는 날이 아니므로 isHoliday로 거르지 않는다.
DIVISIONS_PATH = "/B090041/openapi/service/SpcdeInfoService/get24DivisionsInfo"
SUNDRY_PATH = "/B090041/openapi/service/SpcdeInfoService/getSundryDayInfo"

# 평문 http(80)로만 붙으면 실행 환경에 따라 연결 자체가 타임아웃 난다
# (GitHub Actions 러너에서 전 지역·전 엔드포인트 ConnectTimeout 발생).
# https를 우선 쓰고, 그쪽이 막힌 환경을 위해 http로 폴백한다.
API_HOSTS = ["https://apis.data.go.kr", "http://apis.data.go.kr"]

WEATHER_ROOT = "weather"

BACKFILL_START = datetime(2023, 1, 1)
REQUEST_DELAY_SEC = 0.5
REQUEST_TIMEOUT_SEC = 20
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 2
FAILFAST_AFTER = 3  # 연속 실패가 이만큼 쌓이면 재시도 없이 즉시 포기


def safe_float(v, default=0.0):
    """빈 문자열/None을 안전하게 숫자로 변환. 강수량 빈값은 0.0(강수 없음)으로 처리."""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


_consecutive_failures = 0


def request_json(path: str, params: dict) -> dict:
    """공공데이터포털 API 호출. https 우선 + http 폴백, 실패 시 백오프 재시도.

    API 전면 장애 시 (지역 8곳 x 요청 3종)에 매번 재시도를 다 돌면 워크플로우
    타임아웃을 넘긴다. 연속 실패가 쌓이면 재시도 없이 바로 포기한다(성공하면 해제).
    """
    global _consecutive_failures

    failfast = _consecutive_failures >= FAILFAST_AFTER
    attempts = 1 if failfast else MAX_RETRIES
    hosts = API_HOSTS[:1] if failfast else API_HOSTS

    last_err = None
    for attempt in range(attempts):
        for host in hosts:
            try:
                resp = requests.get(host + path, params=params, timeout=REQUEST_TIMEOUT_SEC)
                resp.raise_for_status()
                data = resp.json()
                _consecutive_failures = 0
                return data
            except Exception as e:  # 연결 실패 / HTTP 에러 / JSON 아닌 응답
                last_err = e
        if attempt < attempts - 1:
            time.sleep(RETRY_BACKOFF_SEC * (2 ** attempt))

    _consecutive_failures += 1
    raise RuntimeError(f"요청 실패 ({path}): {last_err}")


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


# 단기예보 발표 시각(KST). 발표 후 10분쯤 뒤부터 조회가 안정적이다.
FORECAST_BASE_TIMES = ["2300", "2000", "1700", "1400", "1100", "0800", "0500", "0200"]
FORECAST_PUBLISH_DELAY_MIN = 15


def forecast_base_candidates(now: datetime, count: int = 4) -> list:
    """지금 시점에서 조회 가능한 (base_date, base_time)을 최신순으로 만든다.

    발표 직후라 아직 응답이 없거나 특정 회차가 결측일 수 있으므로 몇 회차
    앞까지 후보를 만들어 두고 순서대로 시도한다.
    """
    cutoff = now - timedelta(minutes=FORECAST_PUBLISH_DELAY_MIN)
    candidates = []
    day = 0
    while len(candidates) < count and day < 2:
        d = cutoff - timedelta(days=day)
        for bt in FORECAST_BASE_TIMES:
            if day == 0 and int(bt) > int(d.strftime("%H%M")):
                continue  # 아직 발표되지 않은 회차
            candidates.append((d.strftime("%Y%m%d"), bt))
            if len(candidates) >= count:
                break
        day += 1
    return candidates


def summarize_forecast_items(items: list) -> dict:
    """예보 항목을 날짜별 {minTa, maxTa, pop_max}로 요약.

    TMN/TMX는 하루 한 번만 실리기 때문에, 이미 지난 시각의 회차이거나
    예보 마지막 날처럼 잘린 구간에서는 빠진다. 그 경우 시간별 기온(TMP)의
    최저/최고로 채워서 '기온이 아예 안 보이는' 상황을 막는다.
    """
    by_date = {}
    for it in items:
        fdate = it.get("fcstDate")
        category = it.get("category")
        value = it.get("fcstValue")
        if not fdate:
            continue
        entry = by_date.setdefault(
            fdate, {"minTa": None, "maxTa": None, "pop_list": [], "tmp_list": []}
        )
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
        if min_ta is None and entry["tmp_list"]:
            min_ta = min(entry["tmp_list"])
        if max_ta is None and entry["tmp_list"]:
            max_ta = max(entry["tmp_list"])
        result[iso_date] = {
            "minTa": min_ta,
            "maxTa": max_ta,
            "pop_max": max(entry["pop_list"]) if entry["pop_list"] else 0.0,
        }
    return result


def collect_forecast(region_code: str, nx: int, ny: int) -> bool:
    """단기예보: 오늘~글피, 일자별 최저/최고/강수확률 최댓값 요약. (지역별 디렉토리)"""
    forecast_dir = os.path.join(WEATHER_ROOT, "forecast", region_code)
    os.makedirs(forecast_dir, exist_ok=True)

    now = now_kst()
    today = now.strftime("%Y-%m-%d")
    out_path = os.path.join(forecast_dir, "latest.json")

    print(f"  [예보][{region_code}] 단기예보 수집 중...")
    fetched = {}
    last_err = None
    for base_date, base_time in forecast_base_candidates(now):
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
            header = data.get("response", {}).get("header", {})
            if header.get("resultCode") != "00":
                raise RuntimeError(
                    f"예보 API 오류: {header.get('resultCode')} {header.get('resultMsg')}"
                )
            items = normalize_items(data.get("response", {}).get("body", {}))
            summarized = summarize_forecast_items(items)
            if not summarized:
                raise RuntimeError("예보 항목이 비어 있음")
            fetched = summarized
            print(f"    발표기준 {base_date} {base_time}")
            break
        except Exception as e:
            last_err = e
            print(f"    [재시도] {base_date} {base_time}: {e}")

    if not fetched:
        print(f"    [실패] 예보 수집: {last_err}")
        return False

    # 응답이 일부만 와도 이미 받아둔 값을 잃지 않도록 병합한다.
    # 지난 날짜는 ASOS 실측이 대신하므로 예보 파일에서 정리한다.
    existing = load_month_file(out_path) if os.path.exists(out_path) else {}
    merged = {k: v for k, v in existing.items() if k >= today}
    for date_str, new in fetched.items():
        if date_str < today:
            continue
        old = merged.get(date_str) or {}
        merged[date_str] = {
            "minTa": new["minTa"] if new["minTa"] is not None else old.get("minTa"),
            "maxTa": new["maxTa"] if new["maxTa"] is not None else old.get("maxTa"),
            "pop_max": new["pop_max"],
        }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(merged.items())), f, ensure_ascii=False, indent=2)
    print(f"    -> {len(merged)}일 저장: {out_path}")
    return True


def normalize_items(body: dict) -> list:
    """공공데이터포털 JSON의 items를 항상 list로 정규화.

    결과가 0건이면 items가 dict가 아니라 빈 문자열("")로 오고,
    1건이면 item이 list가 아니라 dict 하나로 온다. 둘 다 그대로 순회하면
    터지거나 문자열을 한 글자씩 도는 사고가 난다.
    """
    items = body.get("items")
    if not isinstance(items, dict):
        return []
    item = items.get("item")
    if isinstance(item, dict):
        return [item]
    if isinstance(item, list):
        return item
    return []


def fetch_special_days(path: str, label: str, year: int, month: int = None,
                      holiday_only: bool = False) -> dict:
    """특일 정보를 {YYYY-MM-DD: "이름"} 형태로 반환. month=None이면 그 해 전체.

    solMonth는 선택 파라미터라 연 단위로 한 번에 받을 수 있다. 월별로 12번
    도는 것보다 요청이 12분의 1이고, API가 죽어 요청마다 연결 타임아웃(20초)이
    걸리는 상황에서 워크플로우 시간을 통째로 잡아먹지 않는다.

    holiday_only=True면 실제로 쉬는 날(isHoliday=Y)만 남긴다. 절기는 쉬는 날이
    아니라 이 값이 N으로 오므로 걸러내면 안 된다.
    """
    params = {
        "serviceKey": HOLIDAY_API_KEY,
        "solYear": f"{year:04d}",
        "numOfRows": "100",
        "_type": "json",  # 이 API는 dataType이 아니라 _type을 본다 (빼면 XML이 온다)
    }
    if month is not None:
        params["solMonth"] = f"{month:02d}"
    data = request_json(path, params)

    header = data.get("response", {}).get("header", {})
    if header.get("resultCode") != "00":
        raise RuntimeError(f"{label} API 오류: {header.get('resultCode')} {header.get('resultMsg')}")

    result = {}
    for it in normalize_items(data.get("response", {}).get("body", {})):
        if holiday_only and it.get("isHoliday") != "Y":
            continue
        locdate = str(it.get("locdate") or "")
        if len(locdate) != 8:
            continue
        iso_date = f"{locdate[:4]}-{locdate[4:6]}-{locdate[6:8]}"
        name = (it.get("dateName") or "").strip()
        if not name:
            continue
        # 같은 날에 이름이 둘 이상 걸리는 경우(연휴 겹침, 절기와 잡절이 같은 날 등)
        prev = result.get(iso_date)
        result[iso_date] = f"{prev} · {name}" if prev and name not in prev else (prev or name)
    return result


def fetch_holidays(year: int, month: int = None) -> dict:
    """공휴일(관공서 공휴일)."""
    return fetch_special_days(HOLIDAY_PATH, "공휴일", year, month, holiday_only=True)


def fetch_terms(year: int, month: int = None) -> dict:
    """절기: 24절기 + 잡절을 한 묶음으로 반환.

    입춘·입추·동지는 24절기, 초복·중복·말복·한식은 잡절로 분류가 달라
    엔드포인트가 나뉘어 있다. 화면에서는 구분 없이 같은 방식으로 보여주므로
    합쳐서 한 파일에 담는다.
    """
    result = fetch_special_days(DIVISIONS_PATH, "24절기", year, month)
    for iso_date, name in fetch_special_days(SUNDRY_PATH, "잡절", year, month).items():
        prev = result.get(iso_date)
        result[iso_date] = f"{prev} · {name}" if prev and name not in prev else (prev or name)
    return result


def collect_yearly(label: str, subdir: str, fetch_fn) -> bool:
    """연 단위 특일 데이터를 weather/{subdir}/{YYYY}.json으로 저장. (지역 무관)

    공휴일과 절기가 같은 정책을 쓴다:
    - 지난 연도는 확정된 과거라 파일이 있으면 건너뛴다.
    - 올해·내년은 매번 재수집하되 기존 데이터와 병합해서, 응답이 비어도
      이미 받아둔 날짜를 잃지 않는다. (ASOS 수집과 동일한 안전장치)
    - 지난 연도가 부분 실패하면 저장을 보류한다. 결손 상태로 저장하면
      '파일 있음 -> 건너뜀' 규칙에 걸려 영구 결손이 된다.
    - API 접속 자체가 안 되면 즉시 포기한다. 남은 연도를 계속 두드리면
      요청당 연결 타임아웃만 쌓여 워크플로우가 통째로 죽는다.
    """
    out_dir = os.path.join(WEATHER_ROOT, subdir)
    os.makedirs(out_dir, exist_ok=True)

    now = now_kst()
    # 내년까지 받아둬야 12월에 다음 해 달력을 넘겨봤을 때 비지 않는다
    last_year = now.year + 1
    failed = False

    for year in range(BACKFILL_START.year, last_year + 1):
        out_path = os.path.join(out_dir, f"{year}.json")
        existing = load_month_file(out_path) if os.path.exists(out_path) else {}

        if year < now.year and existing:
            continue  # 확정된 과거 -> 건너뜀

        print(f"  [{label}] {year} 수집 중...")
        fetched = {}
        year_failed = False
        try:
            fetched = fetch_fn(year)
        except Exception as e:
            print(f"    [실패] {year}: {e}")
            year_failed = True
        time.sleep(REQUEST_DELAY_SEC)

        if not year_failed and not fetched:
            # 연 단위 조회를 못 쓰는 경우(파라미터 미지원 등)를 위한 폴백.
            # 해당 항목이 한 건도 없는 해는 없으므로 빈 응답은 조회 실패로 본다.
            print("    연 단위 응답이 비어 월별로 재조회")
            for month in range(1, 13):
                try:
                    fetched.update(fetch_fn(year, month))
                except Exception as e:
                    print(f"    [실패] {year}-{month:02d}: {e}")
                    year_failed = True
                time.sleep(REQUEST_DELAY_SEC)

        failed = failed or year_failed

        if year_failed and _consecutive_failures >= FAILFAST_AFTER:
            print(f"    -> API 접속 불가로 판단, {label} 수집 중단")
            return False

        if year_failed and year < now.year:
            print("    -> 일부 실패 -> 저장 보류 (다음 실행에서 재수집)")
            continue

        merged = {**existing, **fetched}
        if merged != existing:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(dict(sorted(merged.items())), f, ensure_ascii=False, indent=2)
            print(f"    -> {len(merged)}일 저장: {out_path}")
        else:
            print(f"    -> 새로 채운 날짜 없음: {out_path}")

    return not failed


def main():
    ok = 0
    total = 0
    for region_code, info in REGIONS.items():
        print(f"\n=== [{info['name']}({region_code})] 수집 시작 ===")
        for result in (
            collect_backfill(region_code, info["stn"]),
            collect_current_month(region_code, info["stn"]),
            collect_forecast(region_code, info["nx"], info["ny"]),
        ):
            total += 1
            ok += 1 if result else 0
        time.sleep(REQUEST_DELAY_SEC)

    # 공휴일·절기는 지역과 무관하므로 지역 루프 밖에서 한 번만 수집한다
    print("\n=== [공휴일] 수집 시작 ===")
    total += 1
    ok += 1 if collect_yearly("공휴일", "holiday", fetch_holidays) else 0

    print("\n=== [절기] 수집 시작 ===")
    total += 1
    ok += 1 if collect_yearly("절기", "term", fetch_terms) else 0

    print(f"\n완료. 성공 {ok}/{total}")
    if ok == 0:
        # 전부 실패(예: API 호스트 접속 불가)했는데 종료코드가 0이면
        # 워크플로우가 '성공'으로 끝나 아무도 이상을 눈치채지 못한다.
        print("모든 수집이 실패했습니다.")
        sys.exit(1)


if __name__ == "__main__":
    main()