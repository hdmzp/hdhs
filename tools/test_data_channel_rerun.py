# -*- coding: utf-8 -*-
"""
데이터채널 재방송 제외 테스트 (pytest 없이 그냥 실행:
python tools/test_data_channel_rerun.py)

지키려는 것:
  (A) 데이터채널(TV+ 등)에서 같은 상품을 다시 트는 회차는 뺄 것
      -> 2026-10-22(목) 10:45 림잇티드 'T림잇티드 1부' (CJ TV+ 재방송)
  (B) 라이브 편성표에 같은 시각 방송이 있으면 본방이니 그대로 둘 것
  (C) 라이브 편성표에 없어도 데이터채널에 같은 상품이 없으면 그대로 둘 것
      -> 소이현의 겟잇스타일 19:00/20:00 모바일 라이브쇼
  (D) build_celeb_history가 이미 쌓인 재방송 회차를 지울 것
"""

import os
import sys
import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "fixed"))

import celeb_day_sweep as sweep  # noqa: E402
import build_celeb_history as bch  # noqa: E402

KST = timezone(timedelta(hours=9))
DAY = "2026-10-22"
CODE = "https://display.cjonstyle.com/p/item/2093724866?channelCode=30002002"


def setup(tmp):
    def write(kind, items):
        os.makedirs(os.path.join(tmp, f"CJ_{kind}"), exist_ok=True)
        with open(os.path.join(tmp, f"CJ_{kind}", "2026-10.json"), "w", encoding="utf-8") as f:
            json.dump({"days": {DAY: items}}, f, ensure_ascii=False)
    write("live", [
        {"start": "10:25", "end": "11:35", "product": "다른 방송"},
        {"start": "20:45", "end": "21:45", "product": "본방 상품", "link": ""},
    ])
    write("data", [
        {"start": "10:45", "end": "11:45", "product": "26FW 시티 라운지 셋업", "link": CODE},
        {"start": "20:45", "end": "21:45", "product": "본방 상품"},
        {"start": "19:00", "end": "20:00", "product": "데이터채널 다른 상품"},
    ])
    sweep.LIVE_DIR_TEMPLATE = os.path.join(tmp, "{company}_live", "{ym}.json")
    sweep.DATA_DIR_TEMPLATE = os.path.join(tmp, "{company}_data", "{ym}.json")
    sweep._MONTH_DAYS_CACHE.clear()


def main():
    tmp = tempfile.mkdtemp()
    failures = 0
    try:
        setup(tmp)

        products = [
            {"broadcast_date_label": "10/22(목) 10:45", "name": "26FW 시티 라운지 셋업 2종", "link": CODE},
            {"broadcast_date_label": "10/22(목) 10:45", "name": "26FW 컬러데님", "link": ""},
            {"broadcast_date_label": "10/22(목) 20:45", "name": "본방 상품", "link": ""},
            {"broadcast_date_label": "10/22(목) 19:00", "name": "라이브쇼 상품", "link": ""},
        ]
        removed = sweep.drop_data_channel_slots("CJ", products)
        left = sorted({p["broadcast_date_label"] for p in products})

        print("[A] 재방송 회차(10:45) 통째로 제외")
        ok = removed == 2 and "10/22(목) 10:45" not in left
        print("    OK" if ok else f"    FAIL removed={removed} left={left}")
        failures += not ok

        print("[B] 라이브에 같은 시각 방송이 있으면 유지 (20:45)")
        ok = "10/22(목) 20:45" in left
        print("    OK" if ok else f"    FAIL left={left}")
        failures += not ok

        print("[C] 데이터채널에 같은 상품이 없으면 유지 (19:00 라이브쇼)")
        ok = "10/22(목) 19:00" in left
        print("    OK" if ok else f"    FAIL left={left}")
        failures += not ok

        print("[D] history에 이미 쌓인 재방송 회차 제거 (확정 회차라도)")
        existing = {"programs": [{
            "program_key": "CJ_LMT", "company": "CJ",
            "broadcasts": [
                {"date": DAY, "label": "10/22(목) 10:45 방송",
                 "products": [{"name": "26FW 시티 라운지 셋업 2종", "link": CODE}]},
                {"date": DAY, "label": "10/22(목) 20:45 방송",
                 "products": [{"name": "본방 상품"}]},
            ]}]}
        bch.merge_into_month(existing, "CJ_LMT", {}, {}, datetime(2026, 10, 26, tzinfo=KST))
        labels = [b["label"] for b in existing["programs"][0]["broadcasts"]]
        ok = labels == ["10/22(목) 20:45 방송"]
        print("    OK" if ok else f"    FAIL labels={labels}")
        failures += not ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n전부 통과" if not failures else f"\n실패 {failures}건")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
