# -*- coding: utf-8 -*-
"""
셀럽PGM 누적 기록(history)에 2026-08-31 무화과와 '같은 유형'의 오류가
더 있는지 감사한다.

== 사용법 ==
  python tools/audit_celeb_history.py

편성표 파일의 git 이력을 읽으므로 얕은 클론(shallow clone)에서는 결과가
부실하다. 필요하면 먼저 `git fetch --unshallow`.

== 왜 방송 전 스냅샷만 보는가 ==
1차 시도에서 '방송 후 편성표에서 사라진 코드'를 찾았더니 20건 넘게 나왔는데
대부분 오탐이었다. 편성표는 방송이 끝난 뒤에도 계속 갱신되면서 지난 슬롯의
대표상품이 줄거나 교체된다(실제 방송된 상품도 사라진다). 즉 방송 후 편성표는
그 회차의 증인이 못 된다.

2026-08-31 무화과 사고의 진짜 서명은 이거였다:
  - 편성표가 '방송 시작 전에' 그 상품을 슬롯에서 뺐고
  - 셀럽PGM 기록은 그보다 앞선 수집분에서 굳어 있었다
그래서 방송 시작 이전 스냅샷만으로 판정한다. 이건 오탐이 거의 없는 대신
'놓친 게 없다'는 증명은 아니다(편성표에 한 번도 안 실린 상품은 판단 불가).
"""

import os
import re
import json
import subprocess
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = timezone(timedelta(hours=9))
HIST_DIR = os.path.join(ROOT, "homeshopping", "representative_programs", "history")

CODE_PATTERNS = {
    "CJ": re.compile(r"/item/(\d+)"),
    "HD": re.compile(r"slitmCd=(\d+)"),
    "GS": re.compile(r"prdid=(\d+)"),
    "LT": re.compile(r"goods_no=(\d+)"),
}
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


def code_of(company, link):
    pat = CODE_PATTERNS.get(company)
    if not pat or not link:
        return ""
    m = pat.search(link)
    return m.group(1) if m else ""


def git(*args):
    r = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def snapshots_for(rel_path, company, date_iso, start_hm, start_dt):
    """방송 시작 이전에 찍힌 편성표 스냅샷들: [(시각, {코드: 상품명})] 오래된 순."""
    d = date.fromisoformat(date_iso)
    out = git("log", "--format=%H|%aI",
              f"--since={(d - timedelta(days=7)).isoformat()}",
              f"--until={(d + timedelta(days=1)).isoformat()}", "--", rel_path)
    rows = []
    for line in out.strip().splitlines():
        sha, iso = line.split("|", 1)
        when = datetime.fromisoformat(iso).astimezone(KST)
        if when >= start_dt:
            continue  # 방송 시작 이후는 증인으로 안 쓴다
        rows.append((when, sha))
    rows.sort()

    snaps = []
    for when, sha in rows:
        blob = git("show", f"{sha}:{rel_path}")
        if not blob:
            continue
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        slots = (data.get("days") or {}).get(date_iso)
        if not slots:
            continue
        codes = OrderedDict()
        for s in slots:
            if s.get("start") != start_hm:
                continue
            cd = code_of(company, s.get("link"))
            if cd:
                codes[cd] = f"{s.get('brand') or ''} {s.get('product') or ''}".strip()
        if codes:
            snaps.append((when, codes))
    return snaps


def audit():
    findings, skipped = [], []

    for name in sorted(os.listdir(HIST_DIR)):
        if not name.endswith(".json"):
            continue
        data = json.load(open(os.path.join(HIST_DIR, name), encoding="utf-8"))
        for prog in data.get("programs") or []:
            company = prog.get("company") or ""
            if company not in CODE_PATTERNS:
                continue
            for b in prog.get("broadcasts") or []:
                date_iso = b.get("date")
                m = TIME_RE.search(b.get("label") or "")
                if not date_iso or not m:
                    continue
                start_hm = f"{int(m.group(1)):02d}:{m.group(2)}"
                d = date.fromisoformat(date_iso)
                start_dt = datetime(d.year, d.month, d.day,
                                    int(m.group(1)), int(m.group(2)), tzinfo=KST)
                rel = f"homeshopping/{company}_live/{date_iso[:7]}.json"
                if not os.path.exists(os.path.join(ROOT, rel)):
                    continue

                snaps = sn = snapshots_for(rel, company, date_iso, start_hm, start_dt)
                if len(snaps) < 2:
                    skipped.append((prog["program_key"], date_iso,
                                    f"방송 전 편성표 스냅샷 {len(snaps)}개"))
                    continue

                last_when, last_codes = snaps[-1]
                gap_h = (start_dt - last_when).total_seconds() / 3600

                # 편성표가 셀럽PGM보다 '더 최신 증인'일 때만 판정한다.
                # (무화과 사고의 조건: 편성표는 19:06, 셀럽 기록은 10:22)
                collected = b.get("collected_at")
                collected_dt = (datetime.fromisoformat(collected).astimezone(KST)
                                if collected else None)
                if collected_dt and collected_dt >= last_when:
                    skipped.append((prog["program_key"], date_iso,
                                    "셀럽 기록이 편성표보다 최신 - 판정 불가"))
                    continue

                # 마지막 스냅샷이 이전 최대치보다 크게 쪼그라들었으면 수집 실패로 본다
                ever = OrderedDict()
                max_n = 0
                for when, codes in snaps:
                    ever.update(codes)
                    max_n = max(max_n, len(codes))
                # 슬롯에 상품을 1개만 싣는 편성표는 증인이 못 된다. GS가 그렇다 -
                # 슬롯마다 대표상품 1개만 돌아가며 보여줘서, '사라졌다'가
                # 라인업 제외가 아니라 그냥 회전이다.
                if max_n < 2 or len(last_codes) < 2:
                    skipped.append((prog["program_key"], date_iso,
                                    f"편성표가 슬롯당 대표상품 {max_n}건만 실음 - 증인 불가"))
                    continue

                if max_n and len(last_codes) / max_n < 0.5:
                    skipped.append((prog["program_key"], date_iso,
                                    f"마지막 편성표가 {max_n}건 -> {len(last_codes)}건 "
                                    f"(부분 수집 의심)"))
                    continue

                # 편성표에 실렸던 적이 있는데 마지막 방송 전 스냅샷엔 없는 코드.
                # '언제 빠졌나'는 마지막으로 보인 스냅샷의 '다음' 스냅샷 시각으로 본다.
                last_seen, drop_at = {}, {}
                for i, (when, codes) in enumerate(snaps):
                    for cd in codes:
                        last_seen[cd] = i
                for cd, i in last_seen.items():
                    if cd not in last_codes and i + 1 < len(snaps):
                        drop_at[cd] = snaps[i + 1][0]
                dropped = {cd: ever[cd] for cd in drop_at}
                if not dropped:
                    continue

                rec = {}
                for p in b.get("products") or []:
                    cd = code_of(company, p.get("link"))
                    if cd:
                        rec[cd] = p.get("name") or ""

                stale = []
                for cd, live_name in dropped.items():
                    if cd not in rec:
                        continue
                    # 셀럽 기록이 '제외 관측'보다 나중에 수집됐다면, 그때도 회사
                    # 페이지엔 그 상품이 있었다는 뜻 -> 셀럽 쪽이 더 최신 증인이다.
                    if collected_dt and collected_dt >= drop_at[cd]:
                        continue
                    stale.append({"code": cd, "live_name": live_name,
                                  "record_name": rec[cd],
                                  "dropped_at": drop_at[cd].isoformat()})
                if stale:
                    findings.append({
                        "program": prog["program_key"],
                        "title": prog.get("program_title") or "",
                        "company": company, "date": date_iso, "start": start_hm,
                        "collected_at": collected, "record_count": len(rec),
                        "last_pre_air": last_when.isoformat(), "gap_h": gap_h,
                        "stale": stale,
                    })
    return findings, skipped


def main():
    findings, skipped = audit()
    print(f"=== 같은 유형(방송 전 제외됐는데 기록에 남음): {len(findings)}건 ===\n")
    for f in findings:
        print(f"[{f['program']}] {f['title']} {f['date']} {f['start']} "
              f"(기록 {f['record_count']}건, 수집 {f['collected_at']})")
        print(f"    방송 전 마지막 편성표: {f['last_pre_air']} (방송 {f['gap_h']:.1f}시간 전)")
        for s in f["stale"]:
            print(f"    - {s['code']} @ {s['dropped_at']} 제외 관측")
            print(f"        편성표: {s['live_name']}")
            print(f"        기록  : {s['record_name']}")
        print()
    print(f"=== 판단 불가(방송 전 스냅샷 부족): {len(skipped)}건 ===")
    for k, d, why in skipped:
        print(f"  {k} {d}: {why}")


if __name__ == "__main__":
    main()
