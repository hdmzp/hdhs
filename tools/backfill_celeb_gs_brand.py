# -*- coding: utf-8 -*-
"""
이미 쌓인 셀럽PGM 기록의 GS 상품에 브랜드를 소급해 채운다.

배경:
  GS 셀럽PGM(지금 백지연/소유진쇼)은 상품 상세 og:title이 "[GS SHOP] 상품명"
  꼴이라 전 상품 브랜드가 'GS SHOP' 자리표시자로 저장돼 왔다. 수집기
  (fixed/regs.py)는 fixed/celeb_brand.py로 상품명에서 브랜드를 추론하도록
  고쳤고, 이 스크립트는 이미 저장된 과거 기록에 같은 규칙을 한 번 적용한다.
  - 월 누적(history/{YYYY-MM}.json)은 확정 회차를 수집기가 다시 안 건드리므로
    소급은 이 스크립트로만 가능하다.
  - 프로그램별 결과 파일(GS_*.json)도 같이 고친다. 안 고치면 다음 수집 전까지
    build_celeb_history가 '방송 전' 회차를 자리표시자 그대로 다시 덮어쓴다.

동작:
  1) GS 프로그램의 상품 중 브랜드가 자리표시자(또는 빈 값)인 것만 상품명에서
     추론해 채운다. 못 찾으면 그대로 둔다.
  2) 상품 목록·수집 시각(updated_at/collected_at)·정정 이력(revisions)은 건드리지
     않는다 - 브랜드 칸을 채운 것이지 라인업이 바뀌거나 수집을 한 게 아니다.
     확정(final) 회차도 같은 이유로 대상에 넣는다.

사용법:
  python tools/backfill_celeb_gs_brand.py            # 미리보기 (파일 안 건드림)
  python tools/backfill_celeb_gs_brand.py --apply    # 실제 저장
"""

import os
import sys
import json
import glob
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "fixed"))

from celeb_brand import resolve_gs_brand  # noqa: E402

REP_DIR = os.path.join("homeshopping", "representative_programs")
DEFAULT_TARGETS = [
    os.path.join(REP_DIR, "history", "*.json"),
    os.path.join(REP_DIR, "GS_*.json"),
]


def iter_gs_products(data: dict):
    """파일 모양에 따라 GS 상품만 골라 돌려준다.
    - 월 누적:        {"programs": [{"company": "GS", "broadcasts": [{"products": [...]}]}]}
    - 프로그램별 결과: {"company": "GS", "products": [...]}"""
    if "programs" in data:
        for prog in data.get("programs") or []:
            if prog.get("company") != "GS":
                continue
            for broadcast in prog.get("broadcasts") or []:
                yield from (broadcast.get("products") or [])
    elif data.get("company") == "GS":
        yield from (data.get("products") or [])


def backfill_data(data: dict, log=None) -> int:
    """GS 상품의 자리표시자 브랜드를 상품명 추론으로 채운다. 반환: 바뀐 상품 수."""
    changed = 0
    for product in iter_gs_products(data):
        before = product.get("brand") or ""
        after = resolve_gs_brand(before, product.get("name") or "")
        if after == before:
            continue
        product["brand"] = after
        changed += 1
        if log:
            log(f"[{before}] -> [{after}] {(product.get('name') or '')[:40]}")
    return changed


def backfill_file(path: str, apply_changes: bool) -> int:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    filename = os.path.basename(path)
    changed = backfill_data(data, log=lambda line: print(f"  {filename} {line}"))
    if changed and apply_changes:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 파일에 저장")
    ap.add_argument("--glob", action="append", default=[],
                    help="대상 파일 glob (기본: history/*.json + GS_*.json)")
    args = ap.parse_args()

    total = 0
    for pattern in args.glob or DEFAULT_TARGETS:
        for path in sorted(glob.glob(pattern)):
            changed = backfill_file(path, args.apply)
            if changed:
                print(f"{os.path.basename(path)}: {changed}건")
            total += changed

    print(f"\n브랜드 채운 상품 {total}건" + ("" if args.apply else " (미리보기 - --apply 필요)"))


if __name__ == "__main__":
    main()
