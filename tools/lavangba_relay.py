# -*- coding: utf-8 -*-
"""
lavangba_relay.py
라방바(live.ecomm-data.com) 요청을 '직접 -> 막히면 Cloudflare Worker 중계'로 보내는 공통 세션.

== 왜 필요한가 ==
2026-11-11부터 라방바가 GitHub Actions IP에서 오는 요청에 HTTP 405를 돌려준다
(같은 요청이 다른 곳에선 200). 그래서 hdhs-bot Worker(worker/telegram-bot.js)의
/lavangba/... 경로가 라방바에 대신 요청해 응답을 그대로 돌려준다.

== 동작 ==
- 처음엔 라방바에 직접 요청한다.
- 직접 요청이 403/405를 받으면 그 실행 동안은 계속 Worker로 보낸다(매번 직접 시도해
  시간을 낭비하지 않도록 한 번 전환하면 유지).
- Worker 열쇠는 /export 와 같다: sha256("export:" + TELEGRAM_BOT_TOKEN).
  TELEGRAM_BOT_TOKEN 환경변수가 없으면 중계 없이 직접 요청 결과를 그대로 돌려준다.

== 사용법 ==
    from tools.lavangba_relay import session as lavangba
    r = lavangba.request("POST", "https://live.ecomm-data.com/api/schedule/list_hs", json=..., headers=...)
    # scrape_guard 재시도와 함께: scrape_guard.request("POST", url, session=lavangba, ...)
"""

import hashlib
import os

import requests

LAVANGBA_BASE = "https://live.ecomm-data.com"
RELAY_BASE = os.environ.get("LAVANGBA_RELAY_URL",
                            "https://hdhs-bot.mj2040354.workers.dev/lavangba")
BLOCKED_STATUS = frozenset({403, 405})


def _relay_key() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    return hashlib.sha256(("export:" + token).encode()).hexdigest() if token else ""


class LavangbaBlocked(requests.HTTPError):
    """라방바가 직접 요청도, Worker 중계도 막은 상태. 이번 실행에선 라방바를 더 부르지 않는다.
    requests.HTTPError 를 상속해서 scrape_guard.request 가 재시도 없이 바로 FetchError 로 바꾼다
    (2026-11-12부터 중계까지 405 - 날짜·채널마다 5회씩 재시도하느라 실행당 수십 분을 버렸다)."""


class LavangbaSession:
    def __init__(self):
        self.use_relay = False
        self.blocked = False

    def request(self, method, url, **kwargs):
        if not url.startswith(LAVANGBA_BASE):
            return requests.request(method, url, **kwargs)
        if self.blocked:
            raise LavangbaBlocked("라방바 차단 상태(직접·중계 모두 405) - 이번 실행에서는 라방바 호출을 생략")

        key = _relay_key()
        if not self.use_relay:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code not in BLOCKED_STATUS or not key:
                return resp
            print(f"    [lavangba] 직접 요청 HTTP {resp.status_code} -> Worker 중계로 전환")
            self.use_relay = True

        headers = dict(kwargs.pop("headers", None) or {})
        headers["Authorization"] = "Bearer " + key
        resp = requests.request(method, RELAY_BASE + url[len(LAVANGBA_BASE):], headers=headers, **kwargs)
        if resp.status_code in BLOCKED_STATUS:
            self.blocked = True
            print(f"    [lavangba] Worker 중계도 HTTP {resp.status_code} -> 이번 실행에서는 라방바 호출 생략")
            raise LavangbaBlocked(f"라방바 중계 HTTP {resp.status_code}", response=resp)
        return resp

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


session = LavangbaSession()
