# -*- coding: utf-8 -*-
"""
지난 편성 데이터의 '틀린 분류'만 새 기준으로 소급 정정한다 (2026-11-07 수립).

== 원칙 ==
- 새 분류가 '미분류'면 바꾸지 않는다 (미분류는 소급하지 않음 - 사람 지정).
- 상품명만 보고 바꾸지 않는다. 근거를 함께 본다:
  1) 라방바 분류값(lavangba_category) - 라방바 수집 데이터에 같은 상품(구매링크)이 있으면
     라방바 원문 상품명(사은품 '+' 구분이 살아 있음)으로 다시 분류하고, 라방바 분류와 대조한다.
  2) 사람이 정한 규칙(브랜드 강제, 브랜드+품목, 헤어기기=가전, 미용 확정 키워드)
- 편성 JSON의 product는 화면용으로 정제돼 '+'·증정 문구가 지워진 텍스트라 사은품 구분이 안 된다.
  그래서 라방바 원문이 없을 때는 보수적으로만 바꾼다.

== 바꾸는 조건 (new = 새 분류, old = 기존 분류) ==
  공통: new == old 또는 new == '미분류' 이면 유지
  A. 브랜드 강제 / 브랜드+품목 (brand_force, brand_split)
     - 라방바 분류가 있으면 new가 라방바와 맞을 때만, 없으면 이번에 지정한 브랜드·생로랑만 변경
  B. 라방바 원문이 있을 때 (원문으로 재분류)
     - hair_tool, beauty -> 변경 (식품 단어 + 확실한 미용 품목 = 미용)
     - 그 외(모델 등): new가 라방바 분류와 맞고 old가 안 맞을 때만      -> 변경
  C. 라방바 원문이 없을 때 (정제된 상품명으로 재분류)
     - hair_tool: old가 '미용'일 때만 (고데기를 미용으로 넣었던 것)     -> 변경
     - beauty:    old가 '일반식품'일 때만 (미용을 식품으로 넣었던 것)    -> 변경
     - 그 외: 유지 (근거 부족)

== 사용법 ==
  python tools/recategorize_retro.py            # 미리보기 (파일 안 바꿈)
  python tools/recategorize_retro.py --apply    # 적용
  python tools/recategorize_retro.py --apply --days 10   # 최근 10일만 (매일 새벽 category-correct.yml)
"""

import collections
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from categorize import UNCLASSIFIED, classify_explain  # noqa: E402

# 라방바 분류 -> 우리 분류 중 '맞다'고 볼 수 있는 것들
LAVANGBA_COMPAT = {
    "식품": {"일반식품", "건강식품"},
    "화장품/미용": {"미용"},
    "패션의류": {"의류", "언더웨어"},
    "패션잡화": {"잡화/주얼리"},
    "디지털/가전": {"가전"},
    "생활/건강": {"리빙/주방", "건강식품", "가전"},
    "가구/인테리어": {"리빙/주방"},
    "여가/생활편의": {"여행", "보험", "일반렌탈", "문화/스포츠"},
    "스포츠/레저": {"의류", "문화/스포츠", "잡화/주얼리"},
    "출산/육아": {"리빙/주방", "의류"},
    "도서": {"문화/스포츠"},
}


FASHION = {"의류", "잡화/주얼리", "언더웨어"}

# 사람이 품목 기준으로 못 박은 규칙 - 라방바 근거 없이도 소급한다 (발패치·풋패드 = 리빙/주방)
PERSON_ITEM_RE = re.compile(r"발\s?패치|풋\s?패드|발\s?패드")

# 라방바 근거 없이도 브랜드 강제 규칙을 소급해도 되는 브랜드 (2026-11-07 사람 지정분)
RETRO_TRUST_BRANDS = {"레쁠레뜨", "립밤", "Lipbbam", "듀얼소닉", "렌가드", "애디슨로스",
                      "벨라가든", "슈바넨가르텐", "김정문", "김정문알로에", "바이탈플랜트",
                      "헤이", "HAY", "헤이(HAY)", "뱅뱅", "푸마", "PUMA"}


def load_lavangba():
    """구매링크 -> [(라방바 분류, 라방바 원문 상품명, 브랜드), ...]
    같은 링크에 구성이 다른 방송(전고객 청소기 / 헤어드라이기 등)이 여러 개 붙을 수 있어 전부 모은다."""
    out = collections.defaultdict(list)
    for f in sorted(glob.glob(os.path.join(ROOT, "lavangba/data/*.json"))):
        with open(f, encoding="utf-8") as fp:
            data = json.load(fp)
        for items in data.values():
            for it in items:
                link = it.get("product_link")
                if link:
                    row = (it.get("lavangba_category") or "", it.get("item_name") or "", it.get("brand") or "")
                    if row not in out[link]:
                        out[link].append(row)
    return out


def _bigrams(text):
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def best_lavangba(product, rows, min_cover=0.7):
    """편성 상품명과 가장 비슷한 라방바 행. 편성 상품명 글자쌍의 min_cover 이상이
    라방바 원문에 들어 있어야 같은 상품으로 본다 (편성명은 정제돼 더 짧다)."""
    if not rows:
        return None
    pb = _bigrams(product)
    if not pb:
        return None
    best, best_cover = None, 0.0
    for row in rows:
        cover = len(pb & _bigrams(row[1])) / len(pb)
        if cover > best_cover:
            best, best_cover = row, cover
    return best if best_cover >= min_cover else None


def decide(brand, product, old, lv):
    """(새 분류, 근거 설명) 반환. 바꾸지 않으면 (None, 이유)."""
    lv_cat, lv_name = (lv[0], lv[1]) if lv else ("", "")
    text = lv_name or product
    new, src = classify_explain(brand, text)
    if not new or new == old or new == UNCLASSIFIED:
        return None, "same/unclassified"
    compat = LAVANGBA_COMPAT.get(lv_cat)
    # 미분류는 규칙·모델이 바뀌면 다시 판정한다 (새 결과가 미분류가 아니면 채택)
    # 단, 모델 재판정은 받지 않는다 - 편성 상품명은 화면용으로 정제돼 있어 모델 입력으로 부정확하고,
    # 미분류는 사람이 점검하는 목록이다. 규칙(브랜드 강제·키워드 등)으로 정해질 때만 채택.
    if old == UNCLASSIFIED:
        return (new, f"reeval_{src}") if src != "model" else (None, "unclassified-model")
    # 패션 잡화 키워드(백팩·로퍼·스카프 등)는 의류로 들어간 것만 바로잡는다
    # (편성 상품명은 사은품 구분이 지워져 있어, 미용 등 다른 분류에서 끌어오지 않는다)
    if src == "fashion" and old == "의류":
        return new, "fashion"
    # 사람이 직접 지정한 품목·브랜드 규칙은 라방바 분류보다 우선한다
    if new == "리빙/주방" and PERSON_ITEM_RE.search(text):
        return new, "person_item"
    if src == "brand_force" and brand in RETRO_TRUST_BRANDS:
        return new, "person_brand"
    if src in ("brand_force", "brand_split"):
        if old in FASHION and new in FASHION:
            return None, "fashion-internal"   # 예: FILA=의류 규칙이 FILA 운동화까지 의류로
        # 예전부터 있던 브랜드 강제 규칙은 그 브랜드의 다른 품목까지 끌고 간다
        # (예: '사임당'=리빙/주방 규칙이 사임당 떡까지 바꿈) -> 라방바가 받쳐줄 때만,
        # 또는 이번에 사람이 지정한 브랜드만 바꾼다.
        if compat:
            return (new, f"{src}+라방바분류({lv_cat})") if new in compat else (None, "lavangba-not-supporting")
        if src == "brand_split" or brand in RETRO_TRUST_BRANDS:
            return new, src
        return None, "no-evidence"
    if lv_name:
        if src == "hair_tool":
            return new, src + "+라방바원문"
        if src == "beauty":
            # 식품 단어 + 확실한 미용 품목 = 미용 (사람 지정). 먹는 제형은 규칙 쪽에서 이미 걸러짐.
            return new, src + "+라방바원문"
        # 라방바 분류는 방송 단위라 패션 방송에선 가방·신발도 '패션의류'로 붙는다
        # -> 의류/잡화/언더웨어끼리 바꾸는 건 라방바 근거로 하지 않는다
        if old in FASHION and new in FASHION:
            return None, "fashion-internal"
        if compat and new in compat and old not in compat:
            return new, f"{src}+라방바분류({lv_cat})"
        return None, "lavangba-not-supporting"
    if src == "hair_tool" and old == "미용":
        return new, src
    if src == "beauty" and old == "일반식품":
        return new, src
    return None, "no-evidence"


def iter_schedule_files():
    pats = ["homeshopping/*_live/*.json", "homeshopping/*_data/*.json", "homeshopping/NS_plus/*.json"]
    for pat in pats:
        for f in sorted(glob.glob(os.path.join(ROOT, pat))):
            yield f


def main(apply: bool, days: int = 0):
    """days > 0 이면 오늘(KST) 기준 최근 days일치 방송만 본다 (매일 새벽 자동 정정용)."""
    since = None
    if days > 0:
        import datetime as _dt
        today = (_dt.datetime.utcnow() + _dt.timedelta(hours=9)).date()
        since = (today - _dt.timedelta(days=days)).isoformat()
    lv_map = load_lavangba()
    changes = []
    reasons = collections.Counter()
    def _old_month(path):
        # 파일명이 YYYY-MM.json / YYYYMM.json 이면 since보다 이전 달 파일은 열지도 않는다
        m = re.search(r"(\d{4})-?(\d{2})\.json$", path)
        return bool(since and m and f"{m.group(1)}-{m.group(2)}" < since[:7])

    for f in iter_schedule_files():
        if _old_month(f):
            continue
        with open(f, encoding="utf-8") as fp:
            raw = fp.read()
        data = json.loads(raw)
        days_map = data.get("days") if isinstance(data, dict) else None
        if not isinstance(days_map, dict):
            continue
        touched = False
        for day, items in days_map.items():
            if since and day < since:
                continue
            for it in items:
                old = it.get("category")
                if old is None:
                    continue
                product = it.get("product") or ""
                lv = best_lavangba(product, lv_map.get(it.get("link")))
                new, why = decide(it.get("brand") or "", product, old, lv)
                if new is None:
                    reasons[why] += 1
                    continue
                changes.append((os.path.relpath(f, ROOT), day, it.get("brand") or "", it.get("product") or "", old, new, why))
                if apply:
                    it["category"] = new
                    touched = True
        if apply and touched:
            indent = 2 if raw.startswith("{\n  ") else None
            with open(f, "w", encoding="utf-8") as fp:
                fp.write(json.dumps(data, ensure_ascii=False, indent=indent) + ("\n" if raw.endswith("\n") else ""))

    # 라방바 데이터의 category(우리 분류 사본)도 같은 기준으로 맞춘다
    lv_changes = 0
    for f in sorted(glob.glob(os.path.join(ROOT, "lavangba/data/*.json"))):
        if _old_month(f):
            continue
        with open(f, encoding="utf-8") as fp:
            raw = fp.read()
        data = json.loads(raw)
        touched = False
        for day_key, items in data.items():
            if since and f"{day_key[:4]}-{day_key[4:6]}-{day_key[6:8]}" < since:
                continue
            for it in items:
                old = it.get("category")
                if not old:
                    continue  # 편성 매칭이 안 된 행은 원래 비어 있음 - 그대로 둔다
                lv = (it.get("lavangba_category") or "", it.get("item_name") or "", it.get("brand") or "")
                new, why = decide(it.get("brand") or "", it.get("item_name") or "", old, lv)
                if new is None:
                    continue
                lv_changes += 1
                changes.append((os.path.relpath(f, ROOT), it.get("date"), it.get("brand") or "", it.get("item_name") or "", old, new, why))
                if apply:
                    it["category"] = new
                    touched = True
        if apply and touched:
            with open(f, "w", encoding="utf-8") as fp:
                fp.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ("\n" if raw.endswith("\n") else ""))

    print(f"변경 {len(changes)}건 (그중 라방바 데이터 {lv_changes}건) / {'적용함' if apply else '미리보기'}")
    print("변경 방향:", collections.Counter((c[4], c[5]) for c in changes).most_common(15))
    print("근거:", collections.Counter(re.sub(r"\(.*\)", "", c[6]) for c in changes).most_common())
    print("유지 사유:", reasons.most_common())
    return changes


if __name__ == "__main__":
    n_days = 0
    if "--days" in sys.argv:
        n_days = int(sys.argv[sys.argv.index("--days") + 1])
    ch = main("--apply" in sys.argv, n_days)
    if "--list" in sys.argv:
        for c in ch:
            print(" | ".join(str(x) for x in c))
