# -*- coding: utf-8 -*-
"""
scrape_guard 자체 테스트 (pytest 없이 그냥 실행: python tools/test_scrape_guard.py)

네트워크 없이 가짜 세션으로 재시도 동작을 검증한다.
재시도 로직이 조용히 망가지면 다시 "실패해도 아무도 모르는" 상태로 돌아가므로,
스크래퍼를 손볼 때 이 테스트를 먼저 돌려보면 된다.
"""

import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import scrape_guard


class FakeResponse:
    def __init__(self, status_code=200, body='{"ok": true}', headers=None):
        self.status_code = status_code
        self.text = body
        self.headers = headers or {}

    def json(self):
        import json
        return json.loads(self.text)

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    """정해진 순서대로 응답/예외를 돌려주는 가짜 세션."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        item = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'✅' if condition else '❌'} {name}{'' if condition else ' <- ' + detail}")


def main():
    # 테스트에서 실제로 기다리지 않도록 백오프를 죽인다
    scrape_guard.time.sleep = lambda _s: None

    print("\n===== scrape_guard 재시도 테스트 =====")

    # 1) 두 번 연결 실패 후 성공 -> 성공값을 돌려주고 3번 호출
    s = FakeSession([requests.ConnectionError("boom"),
                     requests.ConnectionError("boom"),
                     FakeResponse()])
    data = scrape_guard.fetch_json("https://x/api", session=s, label="t1")
    check("연결 실패 2회 후 성공하면 값을 돌려준다", data == {"ok": True}, repr(data))
    check("이때 요청은 3회 (최초 1 + 재시도 2)", s.calls == 3, f"calls={s.calls}")

    # 2) 계속 500 -> 재시도 소진 후 None
    s = FakeSession([FakeResponse(500, "err")])
    data = scrape_guard.fetch_json("https://x/api", session=s, retries=2, label="t2")
    check("500이 계속되면 None", data is None, repr(data))
    check("retries=2면 요청 3회", s.calls == 3, f"calls={s.calls}")

    # 3) 404는 재시도하지 않고 즉시 포기 (같은 답이 올 게 뻔하므로)
    s = FakeSession([FakeResponse(404, "not found")])
    data = scrape_guard.fetch_json("https://x/api", session=s, label="t3")
    check("404는 None", data is None, repr(data))
    check("404는 재시도 없이 1회만 요청", s.calls == 1, f"calls={s.calls}")

    # 4) 200인데 본문이 HTML (WAF 차단) -> JSON 기대면 재시도 대상
    s = FakeSession([FakeResponse(200, "<html>blocked</html>"),
                     FakeResponse(200, "<html>blocked</html>"),
                     FakeResponse(200, '{"ok": true}')])
    data = scrape_guard.fetch_json("https://x/api", session=s, label="t4")
    check("200 HTML(WAF)도 재시도해서 결국 JSON을 얻는다", data == {"ok": True}, repr(data))
    check("이때 요청은 3회", s.calls == 3, f"calls={s.calls}")

    # 5) 429의 Retry-After를 존중 (상한 이내)
    waited = []
    scrape_guard.time.sleep = lambda s_: waited.append(s_)
    s = FakeSession([FakeResponse(429, "slow down", {"Retry-After": "7"}),
                     FakeResponse()])
    scrape_guard.fetch_json("https://x/api", session=s, label="t5")
    check("429면 Retry-After만큼 기다린다", waited and waited[0] == 7.0, repr(waited))
    scrape_guard.time.sleep = lambda _s: None

    # 6) get()은 최종 실패 시 예외 (호출부가 try/except로 잡을 수 있게)
    s = FakeSession([requests.Timeout("timeout")])
    try:
        scrape_guard.get("https://x/api", session=s, retries=1, label="t6")
        check("최종 실패 시 get()은 FetchError", False, "예외가 안 났다")
    except scrape_guard.FetchError:
        check("최종 실패 시 get()은 FetchError", True)

    # 7) 성공하면 재시도 안 함 (정상 경로에 불필요한 지연이 없어야 한다)
    s = FakeSession([FakeResponse()])
    scrape_guard.fetch_json("https://x/api", session=s, label="t7")
    check("정상 응답이면 1회만 요청", s.calls == 1, f"calls={s.calls}")

    print(f"\n  통과 {len(PASS)}건 / 실패 {len(FAIL)}건")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
