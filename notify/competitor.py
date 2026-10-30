# -*- coding: utf-8 -*-
"""
경쟁사 맞편성 · 신규 브랜드 (식품·패션·리빙·미용 리스트 맨 아래에 붙는다)

  🔁 맞편성 (같은 날 2개사 이상 방송한 브랜드)
  이지듀 · HD 06:15 / GS 15:30 / LT 07:15

  🆕 신규 브랜드 (최근 90일 4사 첫 방송)
  16:30 [CJ] 데비마이어 · 김치박스 퍼플

브랜드는 편성의 brand 칸 (비었거나 'GS SHOP' 같은 자리표시자면 상품명 첫 단어).
공백·대소문자를 무시하고 같은 브랜드로 본다. 표기가 다른 브랜드(BOBBI BROWN/바비브라운)는 못 묶는다.
회사는 리스트와 같다 (COMPANIES, 기본 HD,GS,CJ,LT). 시간대 필터는 안 건다 (하루 전체).
"""

import os
import re
import json
from datetime import timedelta

from tg import esc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW_LOOKBACK_DAYS = 90
PLACEHOLDER_BRANDS = {"", "GS SHOP", "GSSHOP", "1등급", "기타"}
COMPANY_NAMES = {"HD": "HD", "GS": "GS", "CJ": "CJ", "LT": "LT"}

_month_cache = {}


def _month(company, ym):
    key = (company, ym)
    if key not in _month_cache:
        path = os.path.join(ROOT, "homeshopping", company + "_live", ym + ".json")
        try:
            with open(path, encoding="utf-8") as f:
                _month_cache[key] = json.load(f).get("days", {})
        except (OSError, json.JSONDecodeError):
            _month_cache[key] = {}
    return _month_cache[key]


def day_items(company, day):
    return _month(company, day.strftime("%Y-%m")).get(day.strftime("%Y-%m-%d"), [])


def brand_of(item):
    brand = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", (item.get("brand") or "")).strip()
    if brand.upper().replace(" ", "") in {b.replace(" ", "") for b in PLACEHOLDER_BRANDS}:
        words = re.sub(r"\[[^\]]*\]|\([^)]*\)|[★☆■●◆▶♥]", " ", item.get("product") or "").split()
        brand = words[0] if words else ""
        # '26FW', '1+1' 같은 건 브랜드가 아니다
        if not re.search(r"[A-Za-z가-힣]{2,}", brand) or re.match(r"^\d", brand):
            return ""
    return brand


def brand_key(brand):
    return re.sub(r"\s+", "", brand).lower()


def _broadcasts(company, day, cats):
    """[(start, brand, product)] - 한 시작시각에 같은 브랜드는 한 번만."""
    seen, out = set(), []
    for it in day_items(company, day):
        if cats and it.get("category") not in cats:
            continue
        brand = brand_of(it)
        if not brand:
            continue
        k = (it.get("start", ""), brand_key(brand))
        if k in seen:
            continue
        seen.add(k)
        out.append((it.get("start", ""), brand, it.get("product") or ""))
    return out


def _seen_before(companies, day):
    keys = set()
    for i in range(1, NEW_LOOKBACK_DAYS + 1):
        d = day - timedelta(days=i)
        for co in companies:
            for it in day_items(co, d):
                b = brand_of(it)
                if b:
                    keys.add(brand_key(b))
    return keys


_seen_cache = {}


def competitor_lines(day, companies, cats):
    """리스트 맨 아래에 붙일 줄들. 둘 다 없으면 []."""
    companies = [c for c in ("HD", "GS", "CJ", "LT") if not companies or c in companies]
    by_brand = {}
    today = []
    for co in companies:
        for start, brand, product in _broadcasts(co, day, cats):
            entry = by_brand.setdefault(brand_key(brand), {"name": brand, "slots": {}})
            entry["slots"].setdefault(co, []).append(start)
            today.append((start, co, brand, product))

    lines = []
    multi = [e for e in by_brand.values() if len(e["slots"]) >= 2]
    multi.sort(key=lambda e: (-len(e["slots"]), min(min(v) for v in e["slots"].values())))
    if multi:
        lines += ["", "<b>🔁 맞편성</b> (같은 날 2개사 이상)"]
        for e in multi:
            parts = ["%s %s" % (COMPANY_NAMES.get(co, co), ", ".join(sorted(set(e["slots"][co]))))
                     for co in companies if co in e["slots"]]
            lines.append("%s · %s" % (esc(e["name"]), esc(" / ".join(parts))))

    key = (tuple(companies), day.strftime("%Y-%m-%d"))
    if key not in _seen_cache:
        _seen_cache[key] = _seen_before(companies, day)
    seen = _seen_cache[key]
    new, shown = [], set()
    for start, co, brand, product in sorted(today):
        k = brand_key(brand)
        # '바니스뉴욕 뷰티_웰니스' 처럼 이미 본 브랜드에 꼬리만 붙은 것도 본 걸로
        if k in seen or k in shown or any(len(s) >= 3 and k.startswith(s) for s in seen):
            continue
        shown.add(k)
        new.append("%s [%s] %s · %s" % (start, COMPANY_NAMES.get(co, co), esc(brand), esc(product[:30])))
    if new:
        lines += ["", "<b>🆕 신규 브랜드</b> (최근 %d일 4사 첫 방송)" % NEW_LOOKBACK_DAYS] + new
    return lines
