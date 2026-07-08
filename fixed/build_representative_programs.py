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
{
  "collectedAt": "2026-07-04T12:00:00+09:00",
  "programs": [
    {company, tab_name, program_title, schedule_raw, detail_link, products: [...]},
    ...
  ]
}
"""

import os
import json
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
SRC_DIR = os.path.join("homeshopping", "representative_programs")
OUTPUT_PATH = os.path.join(SRC_DIR, "merged.json")

# 프런트 슬리서에 노출되는 순서 = 이 리스트 순서.
# 회사별 스크레이퍼(rehd.py/regs.py/recj.py/relt.py)의 output_file과
# 반드시 일치해야 한다. 아직 없는 파일(예: GS)은 자동으로 건너뛴다.
SOURCE_FILES = [
    "HD_HJM.json",
    "HD_OGS.json",
    "GS_BJY.json",   # 백지연 (regs.py 완성되면 생성됨)
    "GS_SYJ.json",   # 소유진 (regs.py 완성되면 생성됨)
    "LT_CYR.json",
    "CJ_KJE.json",
    "CJ_CHJ.json",
    "CJ_KCO.json",
]


def main():
    if not os.path.isdir(SRC_DIR):
        print(f"[실패] 소스 디렉토리 없음: {SRC_DIR}")
        return

    programs = []
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

        programs.append({
            "company": data.get("company", ""),
            "tab_name": data.get("tab_name", ""),
            "program_title": data.get("program_title", ""),
            "schedule_raw": data.get("schedule_raw", ""),
            "detail_link": data.get("detail_link", ""),
            "products": data.get("products", []) or [],
        })
        print(f"[포함] {filename} -> {data.get('tab_name')} (상품 {len(data.get('products') or [])}개)")

    merged = {
        "collectedAt": datetime.now(KST).isoformat(),
        "programs": programs,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"\n[성공] merged.json 저장 완료: {OUTPUT_PATH}")
    print(f"  - 총 프로그램 수: {len(programs)}개")
    print(f"  - 총 상품 수: {sum(len(p['products']) for p in programs)}개")


if __name__ == "__main__":
    main()
