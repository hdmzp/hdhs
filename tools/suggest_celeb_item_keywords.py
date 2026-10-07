# -*- coding: utf-8 -*-
"""
suggest_celeb_item_keywords.py
셀럽PGM '특이사항'(겹치는 품목) 셀의 품목 키워드 사전
(fixed/celeb_item_keywords.json)을 관리할 때 쓰는 보조 스크립트.

  python tools/suggest_celeb_item_keywords.py            # 이번 달 겹침 + 키워드 후보
  python tools/suggest_celeb_item_keywords.py 2026-09    # 특정 달

1) 그 달의 겹치는 품목을 화면(index.html celBuildOverlaps)과 같은 규칙으로 출력
2) 셀럽PGM 누적 상품명에서 서로 다른 프로그램 2곳 이상에 나온 단어 중
   아직 사전에 없는 것을 후보로 출력
3) 후보 단어마다 홈쇼핑 탭 데이터(categorize.py 분류 결과)의 다수결 카테고리
   -> 사전에 넣을 때 category 값의 근거

사전 수정은 사람이 판단해서 한다 (자동으로 고치지 않는다).
"""

import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYWORDS_PATH = os.path.join(ROOT, "fixed", "celeb_item_keywords.json")
HISTORY_DIR = os.path.join(ROOT, "homeshopping", "representative_programs", "history")

# 품목이 아닌 단어 (단위·수식어·구성 표현). 후보 출력에서만 뺀다.
STOPWORDS = {
    "박스", "세트", "풀세트", "종세트", "구성", "더블구성", "패키지", "선물세트", "단품", "본품", "리필",
    "프리미엄", "유기농", "국산", "국내산", "직수입", "데일리", "올인원", "오리지널", "시그니처",
    "스페셜", "베스트", "최신상", "단독", "방송에서만", "대용량", "미니", "멀티", "플러스", "프로",
    "화이트", "블랙", "블루", "컬러", "개입", "개월분", "쇼핑백", "에코백", "파우치", "NEW",
}

CATEGORY_SHORT = {"건강식품": "식품", "일반식품": "식품", "리빙/주방": "리빙", "잡화/주얼리": "잡화"}


def load_keywords():
    with open(KEYWORDS_PATH, encoding="utf-8") as f:
        return json.load(f)["items"]


def load_hide_categories():
    """화면에서 숨기는 카테고리(패션). 품목 매칭은 그대로 하고 출력에서만 뺀다."""
    with open(KEYWORDS_PATH, encoding="utf-8") as f:
        return set(json.load(f).get("hide_categories", []))


def main_text(name: str) -> str:
    """본품 부분만 - 화면(index.html celItemMainText)과 같은 규칙.
    1) [..] (..) 안은 지운다: 구성 표기·색상 옵션·사은품이 들어간다
       ("롤팬 플러스(블랙/크림/그레이)"가 크림으로, "(+에센스크림)"이 크림으로 잡히는 것 방지)
       '+'로 자르기 전에 지워야 "[1+1] 콜라겐 토너패드"가 "[1"로 잘리지 않는다
    2) '+' 뒤(사은품/추가구성)는 버린다 - categorize._main_item_text와 같은 취지"""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", str(name or ""))
    return text.split("+")[0]


def match_item(name: str, items: list):
    text = main_text(name)
    for it in items:
        if any(x in text for x in it.get("exclude", [])):
            continue
        if any(x in text for x in it["match"]):
            return it
    return None


def load_history(ym: str):
    path = os.path.join(HISTORY_DIR, f"{ym}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_overlaps(hist: dict, items: list, hide=frozenset()):
    """품목 -> 회차 목록. 서로 다른 프로그램 2곳 이상인 품목만 돌려준다."""
    groups = defaultdict(dict)  # item -> {(program_key, date): entry}
    for prog in hist.get("programs", []):
        for b in prog.get("broadcasts", []):
            if b.get("off_air"):
                continue
            for p in b.get("products", []):
                it = match_item(p.get("name", ""), items)
                if not it or it["category"] in hide:
                    continue
                key = (prog["program_key"], b.get("date", ""))
                e = groups[it["item"]].setdefault(key, {
                    "company": prog.get("company", ""),
                    "program": prog.get("program_title") or prog.get("tab_name", ""),
                    "date": b.get("date", ""),
                    "brands": [],
                    "link": "",
                    "category": it["category"],
                })
                brand = (p.get("brand") or "").strip()
                if brand and not re.match(r"^(gs\s*shop|gs샵)$", brand, re.I) and brand not in e["brands"]:
                    e["brands"].append(brand)
                if not e["link"] and p.get("link"):
                    e["link"] = p["link"]
    out = []
    for item, entries in groups.items():
        programs = {k[0] for k in entries}
        if len(programs) < 2:
            continue
        rows = sorted(entries.values(), key=lambda e: e["date"])
        out.append((item, rows))
    out.sort(key=lambda x: (-len(x[1]), x[0]))
    return out


def all_celeb_products():
    for path in sorted(glob.glob(os.path.join(HISTORY_DIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            hist = json.load(f)
        for prog in hist.get("programs", []):
            for b in prog.get("broadcasts", []):
                for p in b.get("products", []):
                    yield prog["program_key"], p.get("name", "")


def homeshop_category_votes(words):
    votes = defaultdict(Counter)
    paths = glob.glob(os.path.join(ROOT, "homeshopping", "*_live", "*.json")) + \
        glob.glob(os.path.join(ROOT, "homeshopping", "*_data", "*.json"))
    for path in paths:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for day in (data.get("days") or {}).values():
            if not isinstance(day, list):
                continue
            for it in day:
                cat = it.get("category")
                if not cat:
                    continue
                text = main_text(it.get("product", ""))
                for w in words:
                    if w in text:
                        votes[w][CATEGORY_SHORT.get(cat, cat)] += 1
    return votes


def main():
    ym = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime("%Y-%m")
    items = load_keywords()

    hist = load_history(ym)
    print(f"== {ym} 겹치는 품목 ==")
    if not hist:
        print("  (월별 누적 파일 없음)")
    else:
        overlaps = build_overlaps(hist, items, load_hide_categories())
        if not overlaps:
            print("  없음")
        for item, rows in overlaps:
            parts = []
            for e in rows:
                md = f"{int(e['date'][5:7])}/{int(e['date'][8:10])}" if e["date"] else ""
                # 화면과 같은 규칙: GS 브랜드를 못 찾은 회차는 '확인↗'(화면에선 상품 페이지 링크)
                brand_text = "/".join(e["brands"]) or (f"확인↗ {e['link']}".strip() if e["company"] == "GS" else "")
                parts.append(" ".join(x for x in [e["company"], e["program"], md, brand_text] if x))
            print(f"  [{rows[0]['category']}] {item} {len(rows)}회 편성 ({', '.join(parts)})")

    # 후보: 서로 다른 프로그램 2곳 이상에 나온 단어 중 사전에 안 걸리는 것
    word_cnt, word_progs = Counter(), defaultdict(set)
    for program_key, name in all_celeb_products():
        if match_item(name, items):
            continue
        text = main_text(name)
        for w in set(re.findall(r"[가-힣]{2,}", text)):
            if w in STOPWORDS:
                continue
            word_cnt[w] += 1
            word_progs[w].add(program_key)
    cands = [w for w, _ in word_cnt.most_common() if len(word_progs[w]) >= 2][:40]
    votes = homeshop_category_votes(cands)
    print("\n== 사전에 없는 품목 후보 (사전에 안 걸린 상품 기준, 2개 프로그램 이상) ==")
    print("  단어 / 상품 수 / 프로그램 수 / 홈쇼핑 데이터 다수결 카테고리")
    for w in cands:
        top = ", ".join(f"{c} {n}" for c, n in votes[w].most_common(2)) or "-"
        print(f"  {w} / {word_cnt[w]} / {len(word_progs[w])} / {top}")


if __name__ == "__main__":
    main()
