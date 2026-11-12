# -*- coding: utf-8 -*-
"""
브랜드 색인 만들기 -> homeshopping/brand_index.json

텔레그램 봇의 '🔔 브랜드 알림'에서 사용자가 입력한 말(브랜드명·상품명·다른 이름)로
실제 편성에 나온 브랜드 후보를 찾을 때 쓴다 (worker/telegram-bot.js 의 searchBrands).

  - 전체 홈쇼핑 11개 채널 *_live 편성 (4사 + NS·홈앤·공영·KT알파·신세계·쇼핑엔티·SK스토아), 최근 120일 ~ 이미 나온 앞으로의 편성
  - 브랜드마다 [이름, 방송 수, [회사], 대표 카테고리, [상품명 몇 개], [다른 이름]]
  - 다른 이름은 notify/brand_alias.json (손으로 적는 표 - 검색으로도 안 이어지는 것만)

홈쇼핑 편성 수집(homeshopping.yml) 때마다 다시 만든다. 1초도 안 걸리고 400KB 남짓.
"""

import os
import re
import json
import glob
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "homeshopping", "brand_index.json")
ALIAS = os.path.join(ROOT, "notify", "brand_alias.json")
COMPANIES = ["HD", "GS", "CJ", "LT", "NS", "HNS", "PUBLIC", "KTALPHA", "SHINSEGAE", "SHOPPINGNT", "SKSTOA"]
DAYS_BACK = 120
PRODUCTS_PER_BRAND = 8
KST = timezone(timedelta(hours=9))


def clean(name):
    name = re.split(r"[|ㅣ]\s*원산지", name or "")[0]
    name = re.sub(r"^[\s\-–·]+", "", name)
    return " ".join(name.split())[:30]


def load_alias():
    try:
        with open(ALIAS, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: [str(x) for x in v] for k, v in data.items() if not k.startswith("_") and isinstance(v, list)}


def main():
    since = (datetime.now(KST) - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    count = Counter()
    cos = defaultdict(set)
    cats = defaultdict(Counter)
    prods = defaultdict(Counter)
    for co in COMPANIES:
        for path in sorted(glob.glob(os.path.join(ROOT, "homeshopping", co + "_live", "*.json"))):
            if os.path.basename(path)[:7] < since[:7]:
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            for day, items in (data.get("days") or {}).items():
                if day < since:
                    continue
                seen = set()  # 한 방송(같은 시작 시각)의 구성상품 여러 줄은 한 번으로
                for it in items:
                    brand = (it.get("brand") or "").strip()
                    if not brand:
                        continue
                    if (brand, it.get("start")) not in seen:
                        seen.add((brand, it.get("start")))
                        count[brand] += 1
                    cos[brand].add(co)
                    cats[brand][it.get("category") or "미분류"] += 1
                    p = clean(it.get("product"))
                    if p:
                        prods[brand][p] += 1

    alias = load_alias()
    brands = []
    for b, n in count.most_common():
        brands.append([b, n, [c for c in COMPANIES if c in cos[b]], cats[b].most_common(1)[0][0],
                       [p for p, _ in prods[b].most_common(PRODUCTS_PER_BRAND)], alias.get(b, [])])
    # 데이터에 아직 없는 브랜드라도 별칭 표에 있으면 검색되게
    for b, names in alias.items():
        if b not in count:
            brands.append([b, 0, [], "", [], names])

    out = {"updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "since": since,
           "companies": COMPANIES, "brands": brands}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("브랜드 색인 %d개 → %s (%dKB)" % (len(brands), os.path.relpath(OUT, ROOT), os.path.getsize(OUT) // 1024))


if __name__ == "__main__":
    main()
