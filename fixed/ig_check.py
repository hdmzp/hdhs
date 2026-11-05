# -*- coding: utf-8 -*-
"""
고정PGM 인스타그램 계정 접속 점검 (매주 화요일 새벽, .github/workflows/ig-check.yml)

fixed/pgm_hosts.json에서 화면에 보이는 계정(진행자 verified=true + 채널
program_instagram)을 모아 하나씩 열어보고, 결과를 fixed/ig_status.json에 쓴다.
화면(index.html 고정PGM > SNS 바로가기)은 이 파일을 읽어 "missing"/"error"
계정 옆에 경고 표시를 붙인다.

== 판정 ==
1) 프로필 API(web_profile_info)
   - 200 + user 정보 있음 → "ok"
   - 404, 또는 200인데 user가 비어 있음 → "missing" (사용자 없음)
2) API가 막히면(401/403/429/로그인 리다이렉트 등) 프로필 페이지 HTML로 재확인
   - 404 또는 "Sorry, this page isn't available" 안내 → "missing"
   - og:title 등에 "(@아이디)"가 있음 → "ok"
3) 둘 다 판단이 안 되면 → "error" (접속 실패)

== 러너 IP 차단 대비 ==
GitHub Actions IP를 인스타가 통째로 막으면 모든 계정이 "error"가 된다. 그건
계정 문제가 아니므로 이전 결과를 그대로 두고 blocked=true만 기록한 뒤
종료코드 1로 워크플로를 실패시켜(=사람이 알 수 있게) 끝낸다.

== 저장 ==
{"checked_at": "2026-11-10T03:17:00+09:00", "blocked": false,
 "handles": {"choiyura_show": {"s": "ok", "t": "...", "code": 200,
                               "since": "첫 경고 시각(경고일 때만)"}}}
"""

import os
import re
import sys
import json
import time
from datetime import datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOSTS_PATH = os.path.join(ROOT, "fixed", "pgm_hosts.json")
OUT_PATH = os.path.join(ROOT, "fixed", "ig_status.json")
KST = timezone(timedelta(hours=9))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
IG_APP_ID = "936619743392459"   # instagram.com 웹이 쓰는 공개 앱 ID
TIMEOUT = 15
DELAY_SEC = 4                    # 계정 사이 간격 (차단 방지)

NOT_FOUND_MARKERS = (
    "Sorry, this page isn't available",
    "Sorry, this page isn&#39;t available",
    "페이지를 사용할 수 없습니다",
    "\"HttpErrorPage\"",
)


def collect_handles(hosts):
    """화면에 실제로 보이는 계정만 (verified=false 진행자 계정은 제외)."""
    out = []
    for p in hosts.get("programs", []):
        for h in p.get("hosts", []) or []:
            if h.get("instagram") and h.get("verified"):
                out.append(h["instagram"])
        if p.get("program_instagram"):
            out.append(p["program_instagram"])
    seen, uniq = set(), []
    for h in out:
        k = h.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(h)
    return uniq


def check_api(session, handle):
    """("ok"|"missing"|None, status_code). None = 판단 불가."""
    try:
        r = session.get(
            "https://www.instagram.com/api/v1/users/web_profile_info/",
            params={"username": handle},
            headers={"x-ig-app-id": IG_APP_ID, "Accept": "application/json",
                     "Referer": f"https://www.instagram.com/{handle}/"},
            timeout=TIMEOUT, allow_redirects=False)
    except requests.RequestException:
        return None, None
    if r.status_code == 404:
        return "missing", 404
    if r.status_code != 200:
        return None, r.status_code
    try:
        data = r.json()
    except ValueError:
        return None, 200
    user = (data.get("data") or {}).get("user")
    if user and str(user.get("username", "")).lower() == handle.lower():
        return "ok", 200
    if isinstance(data.get("data"), dict) and not user:
        return "missing", 200
    return None, 200


def check_html(session, handle):
    try:
        r = session.get(f"https://www.instagram.com/{handle}/",
                        headers={"Accept": "text/html", "Accept-Language": "ko,en;q=0.8"},
                        timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return None, None
    if r.status_code == 404:
        return "missing", 404
    if r.status_code != 200 or "/accounts/login" in r.url:
        return None, r.status_code
    text = r.text
    if re.search(r"\(@" + re.escape(handle) + r"\)", text, re.I):
        return "ok", 200
    if any(m in text for m in NOT_FOUND_MARKERS):
        return "missing", 200
    return None, 200


def check_handle(session, handle):
    s, code = check_api(session, handle)
    if s is None and code == 429:
        time.sleep(30)
        s, code = check_api(session, handle)
    if s is None:
        s2, code2 = check_html(session, handle)
        if s2 is not None or code is None:
            s, code = s2, code2
    return (s or "error"), code


def main():
    with open(HOSTS_PATH, encoding="utf-8") as f:
        handles = collect_handles(json.load(f))

    prev = {}
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                prev = json.load(f)
        except (OSError, ValueError):
            prev = {}
    prev_handles = prev.get("handles", {})

    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    now = datetime.now(KST).isoformat(timespec="seconds")
    results = {}
    for i, h in enumerate(handles):
        if i:
            time.sleep(DELAY_SEC)
        s, code = check_handle(session, h)
        entry = {"s": s, "t": now, "code": code}
        if s != "ok":
            old = prev_handles.get(h, {})
            entry["since"] = old.get("since") if old.get("s", "ok") != "ok" and old.get("since") else now
        results[h] = entry
        print(f"[{s:7}] @{h} (HTTP {code})")

    blocked = bool(results) and all(r["s"] == "error" for r in results.values())
    if blocked:
        print("모든 계정이 접속 실패 - 러너가 차단된 것으로 보고 이전 결과를 유지합니다.")
        out = dict(prev)
        out.setdefault("handles", {})
        # 새로 추가된 계정은 결과가 없으므로 남길 게 없다. 지워진 계정만 정리.
        out["handles"] = {h: v for h, v in out["handles"].items() if h in handles}
        out["blocked"] = True
        out["blocked_at"] = now
    else:
        out = {"checked_at": now, "blocked": False, "handles": results}

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")

    bad = [h for h, r in results.items() if r["s"] != "ok"]
    print(f"점검 {len(results)}개 / 경고 {0 if blocked else len(bad)}개")
    if blocked:
        sys.exit(1)


if __name__ == "__main__":
    main()
