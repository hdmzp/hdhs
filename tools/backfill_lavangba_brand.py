# -*- coding: utf-8 -*-
"""
backfill_lavangba_brand.py
라방바 월 파일(lavangba/data/{YYYYMM}.json)의 모든 행에 화면용 필드를 (다시) 채운다.
  brand_display : 표시용 브랜드 - lavangba_scraper_v2.display_brand
  pgm           : 고정PGM 이름 - lavangba_scraper_v2.pgm_from_schedule
                  (HD/GS/CJ/LT 편성표 homeshopping/{코드}_live에서 방송 구간과 겹치는 pgm)

  python tools/backfill_lavangba_brand.py          # 미리보기 (채널별 채움 비율)
  python tools/backfill_lavangba_brand.py --apply  # 파일에 쓰기

몇 번 돌려도 결과가 같다 - 규칙/브랜드 사전이 바뀌면 다시 돌리면 된다.
brand_display·pgm 말고는 아무 필드도 안 건드린다.
"""

import glob
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lavangba_scraper_v2 import display_brand, pgm_from_schedule, _infer_brand  # noqa: E402


def main():
    apply = "--apply" in sys.argv
    if _infer_brand is None:
        print("infer_brand를 못 불러왔다 (pandas/openpyxl 필요) - 현대/CJ/롯데만 채워지니 중단")
        sys.exit(1)
    total, filled, pgm_cnt = Counter(), Counter(), Counter()
    for path in sorted(glob.glob(os.path.join(ROOT, "lavangba", "data", "*.json"))):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for rows in data.values():
            for r in rows:
                r["brand_display"] = display_brand(r.get("channel"), r.get("brand"), r.get("item_name"))
                total[r.get("channel")] += 1
                filled[r.get("channel")] += bool(r["brand_display"])
                r["pgm"] = pgm_from_schedule(r.get("channel"), str(r.get("date") or ""),
                                             r.get("broadcast_start"), r.get("broadcast_end"))
                if r["pgm"]:
                    pgm_cnt[r["pgm"]] += 1
        if apply:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            print(f"저장: {os.path.relpath(path, ROOT)}")
    for ch, n in total.most_common():
        print(f"  {ch}: 브랜드 {filled[ch]}/{n} ({filled[ch] / n:.0%})")
    print("  고정PGM:", ", ".join(f"{k} {v}" for k, v in pgm_cnt.most_common()))
    if not apply:
        print("(미리보기 - 쓰려면 --apply)")


if __name__ == "__main__":
    main()
