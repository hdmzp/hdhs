# -*- coding: utf-8 -*-
"""
이미 수집된 편성 JSON에서 브랜드 자리에 들어간 마케팅 카피를 정정한다.

배경:
  롯데 등 일부 편성은 브랜드 필드가 없어 상품명 앞 대괄호 접두어를 브랜드로
  쓰는데, 그 자리에 가격/구성/색상 안내문이 들어오는 경우가 있다.
    "[백화점가 106만원][포트메리온] 뉴베리에이션 4인조 홈세트 23P"
    -> 브랜드가 "백화점가 106만원"으로 저장됨
  수집기(lt_scraper.py, fixed/regs.py)는 infer_brand.is_marketing_copy로
  이 값을 걸러내도록 고쳤고, 이 스크립트는 이미 저장된 과거 데이터를 고친다.

동작:
  1) 브랜드가 마케팅 카피면 상품명에서 브랜드를 다시 추론 (실패 시 빈 값)
  2) 브랜드가 바뀐 항목은 카테고리도 다시 분류
  3) 브랜드 정정으로 완전히 똑같아진 중복 항목(시간/상품/가격 동일) 제거

사용법:
  python tools/fix_brand_copy.py            # 미리보기 (파일 안 건드림)
  python tools/fix_brand_copy.py --apply    # 실제 저장
  python tools/fix_brand_copy.py --apply --set "뉴베리에이션=포트메리온"
      상품명에 왼쪽 문구가 들어간 항목의 브랜드를 오른쪽 값으로 강제 지정
      (상품명 자체에 브랜드가 안 남아 추론이 불가능한 경우의 수동 정정용)
"""

import os
import sys
import json
import glob
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infer_brand import is_marketing_copy, infer_brand, extract_core_brand
from categorize import classify

# 상품명 앞 대괄호를 브랜드로 쓰는 롯데 편성이 기본 대상
DEFAULT_GLOB = "homeshopping/LT_*/*.json"


def resolve(brand, product, overrides):
    """정정된 브랜드 반환 (정정 대상 아니면 None)."""
    for keyword, forced in overrides:
        if keyword and keyword in (product or ""):
            return forced if forced != brand else None
    if not brand or not is_marketing_copy(brand):
        return None
    inferred = infer_brand(product)
    return extract_core_brand(inferred) if inferred else ""


def fix_file(path, overrides, apply_changes):
    data = json.load(open(path, encoding="utf-8"))
    days = data.get("days")
    if not isinstance(days, dict):
        return 0

    changed = 0
    for day, programs in days.items():
        if not isinstance(programs, list):
            continue
        day_changed = 0
        for p in programs:
            new_brand = resolve(p.get("brand", ""), p.get("product", ""), overrides)
            if new_brand is None:
                continue
            print(f"  {os.path.basename(path)} {day} "
                  f"[{p.get('brand')}] -> [{new_brand or '(없음)'}] {p.get('product', '')[:30]}")
            p["brand"] = new_brand
            p["category"] = classify(new_brand, p.get("product", ""))
            changed += 1
            day_changed += 1

        # 브랜드 정정으로 동일해진 중복 항목 제거 (색상 옵션별 중복 등).
        # 정정이 일어난 날짜에만 적용한다 - 원래부터 있던 동일 항목까지
        # 건드리지 않기 위해서.
        # 브랜드까지 같은 완전 중복 제거
        seen = set()
        deduped = []
        for p in programs:
            key = (p.get("start"), p.get("brand"), p.get("product"), p.get("price"))
            if key in seen:
                changed += 1
                continue
            seen.add(key)
            deduped.append(p)
        # 브랜드를 못 잡은 항목이, 같은 시간대 같은 상품명의 브랜드 있는 항목과
        # 겹치면 버린다 (색상/옵션만 다른 같은 상품이 상품코드별로 중복 편성된 것.
        # 편성표는 브랜드별 대표상품 1개만 싣는 게 원칙이라 가격이 달라도 중복)
        with_brand = {(p.get("start"), p.get("product")) for p in deduped if p.get("brand")}
        kept = []
        for p in deduped:
            if not p.get("brand") and (p.get("start"), p.get("product")) in with_brand:
                changed += 1
                continue
            kept.append(p)
        days[day] = kept

    if changed and apply_changes:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 파일에 저장")
    ap.add_argument("--set", action="append", default=[],
                    help='"상품명키워드=브랜드" 형태의 수동 정정')
    ap.add_argument("--glob", default=DEFAULT_GLOB, help="대상 파일 glob")
    args = ap.parse_args()

    overrides = []
    for item in args.set:
        keyword, _, forced = item.partition("=")
        overrides.append((keyword.strip(), forced.strip()))

    total = 0
    for path in sorted(glob.glob(args.glob)):
        total += fix_file(path, overrides, args.apply)

    print(f"\n정정 대상 {total}건" + ("" if args.apply else " (미리보기 - --apply 필요)"))


if __name__ == "__main__":
    main()
