# -*- coding: utf-8 -*-
"""
build_representative_programs.py
rehd.py / regs.py / recj.py / relt.py 가 각각 만든
homeshopping/representative_programs/*.json 을
프런트(index.html의 대표PGM 탭)가 한 번에 fetch할 수 있는
merged.json 하나로 합친다.

fixed/build_fixed_pgm.py 와 동일한 역할이지만, 대표PGM은
"시간표 그리드"가 아니라 "프로그램별 이번 주 방송상품 리스트"라
슬롯 병합 없이 프로그램 단위 리스트를 그대로 이어붙이면 된다.

== 출력 ==
homeshopping/representative_programs/merged.json
homeshopping/representative_programs/status.json  (수집 성공/실패 표시용)
{
  "collectedAt": "2026-07-04T12:00:00+09:00",
  "programs": [
    {company, tab_name, program_title, schedule_raw, detail_link, products: [...]},
    ...
  ]
}

== 수집 실패 표시 (status.json) ==
스크레이퍼가 죽으면 그 회사 산출물은 **다시 쓰이지 않는다**. 그러면 화면엔
지난 수집분이 그대로 떠서 사람 눈에는 정상처럼 보인다 (2026-09-14 GS 수집기가
pandas 미설치로 매 실행 죽었는데 9/17 백지연 상품이 안 들어온 걸 사람이
발견하기까지 이틀이 걸렸다).

그래서 카드할인 탭(promotion_scraper.py)과 같은 방식으로, 프로그램별
수집 성공/실패를 status.json에 남겨 화면이 '수집실패' 뱃지를 띄우게 한다.
판정 기준은 건전성 검사(tools/check_scrape_health.py)와 같다:
**이번 실행 시작 시각(.scrape_run_mark) 이후에 그 파일이 다시 쓰였는가.**
상품이 0건이어도 파일이 다시 쓰였으면 성공이다 (휴방이면 0건이 정상이라
0건 자체를 실패로 보면 안 된다).

마지막 성공 시각(last_success)은 status.json에 누적한다. 실패한 프로그램은
직전 값을 그대로 물려받아 "언제 수집된 데이터를 보고 있는지" 알 수 있게 한다.
"""

import os
import json
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join("homeshopping", "representative_programs")
OUTPUT_PATH = os.path.join(SRC_DIR, "merged.json")
STATUS_PATH = os.path.join(SRC_DIR, "status.json")
# 수집 시작 직전에 check_scrape_health.py --mark가 찍는 기준 시각
MARK_PATH = os.path.join(ROOT, ".scrape_run_mark")

# 프런트 슬리서에 노출되는 순서 = 이 리스트 순서.
# 회사별 스크레이퍼(rehd.py/regs.py/recj.py/relt.py)의 output_file과
# 반드시 일치해야 한다. 아직 없는 파일(예: GS)은 자동으로 건너뛴다.
SOURCE_FILES = [
    "HD_HJM.json",
    "HD_OGS.json",
    "HD_WYE.json",   # 왕영은의 톡투게더
    "HD_CEK.json",   # 최은경쇼 (2026-08 신규)
    "GS_BJY.json",   # 백지연 (regs.py 완성되면 생성됨)
    "GS_SYJ.json",   # 소유진 (regs.py 완성되면 생성됨)
    "LT_CYR.json",
    "CJ_KJE.json",
    "CJ_CHJ.json",
    "CJ_KCO.json",
    "CJ_SIH.json",   # 소이현의 겟잇스타일
    "CJ_KSY.json",   # 김신영이 산다 (2026-08-18 론칭)
]


def read_mark_ts():
    """이번 실행 시작 시각(.scrape_run_mark의 mtime). 없으면 None.

    로컬에서 빌더만 돌릴 때는 마크가 없다. 그때는 판정을 포기하고
    모두 ok로 둔다 (없는 근거로 '수집실패'를 띄우면 더 나쁘다)."""
    try:
        return os.path.getmtime(MARK_PATH)
    except OSError:
        return None


def load_prev_status():
    """직전 status.json (실패한 프로그램의 last_success를 물려받는 데 쓴다)."""
    try:
        with open(STATUS_PATH, encoding="utf-8") as f:
            return (json.load(f) or {}).get("programs") or {}
    except (OSError, ValueError):
        return {}


def build_status(entries, mark_ts, now_iso):
    """{파일명: {status, last_success, products, reason}}

    entries: [(파일명, 파일경로, 상품수)] - 읽기에 성공한 산출물들
    """
    prev = load_prev_status()
    out = {}
    for filename, path, count in entries:
        before = prev.get(filename) or {}
        fresh = True
        if mark_ts is not None:
            try:
                fresh = os.path.getmtime(path) >= mark_ts
            except OSError:
                fresh = False
        if fresh:
            out[filename] = {
                "status": "ok",
                "last_success": now_iso,
                "products": count,
                "reason": "",
            }
        else:
            out[filename] = {
                "status": "failed",
                # 마지막으로 성공한 시각은 그대로 물려받는다
                "last_success": before.get("last_success", ""),
                "products": count,
                "reason": "이번 수집에서 갱신되지 않음 (스크레이퍼가 죽었거나 산출물을 못 씀)",
            }
    return out


def main():
    if not os.path.isdir(SRC_DIR):
        print(f"[실패] 소스 디렉토리 없음: {SRC_DIR}")
        return

    programs = []
    entries = []            # status.json 판정용 (파일명, 경로, 상품수)
    for filename in SOURCE_FILES:
        path = os.path.join(SRC_DIR, filename)
        if not os.path.isfile(path):
            print(f"[건너뜀] {filename} 없음 (아직 미수집 또는 스크레이퍼 미완성)")
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[경고] {filename} 읽기 실패: {e}")
            continue

        entries.append((filename, path, len(data.get("products") or [])))
        programs.append({
            "company": data.get("company", ""),
            "tab_name": data.get("tab_name", ""),
            "program_title": data.get("program_title", ""),
            "schedule_raw": data.get("schedule_raw", ""),
            "detail_link": data.get("detail_link", ""),
            "products": data.get("products", []) or [],
        })
        print(f"[포함] {filename} -> {data.get('tab_name')} (상품 {len(data.get('products') or [])}개)")

    now_iso = datetime.now(KST).isoformat()
    merged = {
        "collectedAt": now_iso,
        "programs": programs,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"\n[성공] merged.json 저장 완료: {OUTPUT_PATH}")
    print(f"  - 총 프로그램 수: {len(programs)}개")
    print(f"  - 총 상품 수: {sum(len(p['products']) for p in programs)}개")

    # --- 수집 성공/실패 표시 (화면의 '수집실패' 뱃지용) ---
    mark_ts = read_mark_ts()
    status = build_status(entries, mark_ts, now_iso)
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump({"checked_at": now_iso, "programs": status},
                  f, ensure_ascii=False, indent=2)

    failed = [name for name, v in status.items() if v["status"] == "failed"]
    if mark_ts is None:
        print(f"[상태] status.json 저장 (실행 마크가 없어 전부 ok로 기록)")
    elif failed:
        print(f"[상태] status.json 저장 - 수집실패 {len(failed)}건: {', '.join(failed)}")
    else:
        print(f"[상태] status.json 저장 - 전부 정상")


if __name__ == "__main__":
    main()
