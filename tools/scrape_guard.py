# -*- coding: utf-8 -*-
"""
scrape_guard.py
스크래퍼 공통 HTTP 재시도 유틸.

== 왜 필요한가 ==
홈쇼핑사 API/페이지는 하루에도 몇 번씩 타임아웃·502·429·WAF의 HTML 에러페이지를
돌려준다. 기존 스크래퍼들은 대부분 단발 요청이라 그 한 번의 실패가 곧
"그 회사 데이터 통째로 누락"으로 이어졌다 (2026-08 CJ 셀럽PGM 사고:
편성표 API가 result:null을 준 날 recj.py가 첫 프로그램에서 죽고,
워크플로우는 continue-on-error 때문에 초록불이라 며칠간 아무도 몰랐다).

여기서 요청을 지수 백오프 재시도로 감싸 "일시적 실패"를 흡수한다.
재시도로도 안 되는 진짜 장애는 check_scrape_health.py가 잡아 워크플로우를
빨간불로 만든다 (두 층은 역할이 다르므로 둘 다 필요하다).

== 재시도 대상 ==
  - 연결 실패/타임아웃 (requests.RequestException)
  - HTTP 408/425/429/5xx  (429는 Retry-After 헤더를 존중)
  - expect_json=True인데 JSON 파싱 실패 (= WAF 차단 HTML을 받은 상황)
재시도하지 않는 것: 404/403 같은 명확한 4xx (재시도해도 같은 답이라 시간만 낭비)

== 사용법 ==
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools.scrape_guard import get, fetch_json

    resp = get(url, headers=H, timeout=12)          # 최종 실패 시 예외
    data = fetch_json(url, session=s, headers=H)    # 최종 실패 시 None
"""

import json
import random
import time

import requests

DEFAULT_RETRIES = 3          # 최초 1회 + 재시도 3회 = 최대 4회 시도
DEFAULT_TIMEOUT = 15
BACKOFF_BASE_SEC = 1.0       # 1s -> 2s -> 4s (+ 지터)
BACKOFF_MAX_SEC = 20.0
RETRY_AFTER_CAP_SEC = 30.0   # 서버가 말도 안 되게 긴 Retry-After를 줘도 여기까지만 기다린다

# 재시도할 가치가 있는 상태코드 (일시적 장애/혼잡)
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class FetchError(Exception):
    """재시도를 모두 소진하고도 실패했을 때."""


def _sleep_for(attempt: int, resp) -> float:
    """이번 재시도 전에 쉴 시간(초). 429면 Retry-After를 우선한다."""
    if resp is not None and resp.status_code == 429:
        raw = resp.headers.get("Retry-After", "")
        try:
            return min(float(raw), RETRY_AFTER_CAP_SEC)
        except (TypeError, ValueError):
            pass
    delay = min(BACKOFF_BASE_SEC * (2 ** attempt), BACKOFF_MAX_SEC)
    # 여러 스크래퍼가 동시에 같은 서버를 두드릴 때 재시도가 겹치지 않게 지터
    return delay + random.uniform(0, delay * 0.25)


def request(method: str, url: str, *, session=None, retries: int = DEFAULT_RETRIES,
            timeout: float = DEFAULT_TIMEOUT, expect_json: bool = False,
            label: str = "", **kwargs) -> requests.Response:
    """재시도를 붙인 requests 호출. 최종 실패하면 FetchError를 던진다.

    label: 로그에 찍을 호출 이름 (없으면 URL 앞부분). 어느 요청이 흔들리는지
           워크플로우 로그에서 바로 보이게 하기 위한 것.
    """
    caller = session or requests
    tag = label or url.split("?")[0][-60:]
    last_error = ""

    for attempt in range(retries + 1):
        resp = None
        try:
            resp = caller.request(method, url, timeout=timeout, **kwargs)

            if resp.status_code in RETRY_STATUS:
                last_error = f"HTTP {resp.status_code}"
            else:
                resp.raise_for_status()
                if expect_json:
                    # 여기서 한 번 파싱해봐야 WAF의 HTML 에러페이지를 걸러낼 수 있다.
                    # (상태코드는 200인데 본문이 JSON이 아닌 경우가 실제로 있다)
                    resp.json()
                return resp

        except requests.HTTPError as e:
            # raise_for_status가 올린 4xx는 재시도해도 답이 같다 -> 즉시 포기
            raise FetchError(f"{tag}: {e}") from e
        except (requests.RequestException, ValueError, json.JSONDecodeError) as e:
            last_error = f"{type(e).__name__}: {e}"

        if attempt < retries:
            wait = _sleep_for(attempt, resp)
            print(f"    -> [재시도 {attempt + 1}/{retries}] {tag} ({last_error}) "
                  f"{wait:.1f}초 후 재시도")
            time.sleep(wait)

    raise FetchError(f"{tag}: {retries + 1}회 시도 모두 실패 ({last_error})")


def get(url: str, **kwargs) -> requests.Response:
    """재시도 붙은 GET. 최종 실패 시 FetchError."""
    return request("GET", url, **kwargs)


def fetch_json(url: str, **kwargs):
    """재시도 붙은 GET + JSON 파싱. 최종 실패 시 None (기존 스크래퍼들이
    None을 '이번엔 못 가져옴'으로 이미 처리하고 있어 시그니처를 맞춘다)."""
    kwargs.setdefault("expect_json", True)
    try:
        return get(url, **kwargs).json()
    except FetchError as e:
        print(f"    -> [실패] {e}")
        return None
