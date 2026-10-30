# -*- coding: utf-8 -*-
"""
텔레그램으로 오늘의 건강프로그램 PPL 예고 발송 (식품 리스트를 받는 사람에게, 별도 메시지)

   💊 10/31(토) 건강프로그램 PPL 예고
   오늘 TV 건강프로그램이 다룰 성분이에요. ⭐ = 같은 시간대 홈쇼핑 같은 성분 방송

   08:55 [JTBC] 이토록 위대한 몸 60회
   🏷 관절 · 혈관
   "무릎 연골 지키는 ..."
   ⭐ 09:20 [CJ] 뉴트리원 · 초록입홍합 관절 ...

데이터
- data/episode_clues/{YYYY-MM}.json : 건강프로그램 본방 회차와 단서 (episode_clue_scraper.py)
- data/{YYYY-MM-DD}.json            : 지상파·종편 편성 (종료 시각)
- homeshopping/{회사}_live          : 같은 시간대 홈쇼핑 건강식품 방송

성분 판정은 사이트 '건강프로그램 추적'(index.html IN_TRK_INGR)과 같은 단어 묶음을 쓴다.
프로그램명이 들어간 단서(공식 회차정보·검색 제목)에 묶음 안 단어가 나오면 태그로 달고, 같은 시간대(TV 시작 ~ 종료 30분 뒤에 시작한)
홈쇼핑 건강식품 상품명에 같은 묶음 단어가 있으면 ⭐.

환경변수: TELEGRAM_BOT_TOKEN (필수) / COMPANIES / TARGET_DATE / DRY_RUN - send_daily_food.py 와 같음
"""

import os
import re
import json

from tg import esc, chunk_lines, broadcast
import send_daily_food as food

ROOT = food.ROOT
AFTER_MIN = 30  # index.html IN_TRK_AFTER_MIN
SNIPPET_MAX = 60

# index.html IN_TRK_INGR 과 같게 유지 (단, 문장 단서에 흔히 걸리는 '인지'는 뺌)
INGR = [
    ['위력', '위건강', '위의힘', '위장', '위엔', '위염', '위산', '매스틱', '속쓰림', '소화'],
    ['관절', '연골', '무릎', '난각막', '초록입홍합', 'msm', '보스웰리아', '글루코사민', '콘드로이친', '상어연골'],
    ['혈당', '바나바', '여주', '당조고추', '뽕잎', '당뇨'],
    ['갱년기', '갱춘기', '여성호르몬', 'err731', '석류', '회화나무'],
    ['눈건강', '루테인', '지아잔틴', '시력', '안구'],
    ['장건강', '유산균', '비피더스', '프로바이오틱스', '포스트바이오틱스', '장순환', '장독소', '변비', '식이섬유', '차전자피', '대장', '소화불량'],
    ['다이어트', '체지방', '내장지방', '뱃살', '비만', '가르시니아', '시클로덱스트린', '알파cd', '카테킨', '감량'],
    ['혈관', '혈행', '오메가3', '크릴오일', '나토키나제', '홍국', '콜레스테롤', '폴리코사놀', '중성지방'],
    ['간건강', '간기능', '밀크씨슬', '피로'],
    ['면역', '홍삼', '인삼', '인삼다당체', 'nk세포', '프로폴리스', '아연', '녹용', '침향', '공진단', '흑염소'],
    ['기억력', '치매', '뇌건강', '포스파티딜세린', '은행잎'],  # '인지'는 '무엇인지'에 걸려서 뺐다
    ['수면', '숙면', '멜라토닌', '테아닌'],
    ['피부', '콜라겐', '엘라스틴', '히알루론산', '주름'],
    ['전립선', '쏘팔메토', '남성건강'],
    ['뼈건강', '골밀도', '골강도', '골다공증', 'mbp', '칼슘', '비타민d', '마그네슘'],
    ['근육', '근감소', '근력', '단백질', '프로틴', '아미노산', '알부민'],
    ['기침', '호흡기', '기관지', '도라지', '배즙', '목건강'],
    ['혈압', '고혈압'],
    ['신맛', '식초', '비니거', '초산', '사과초모'],
    ['올리브오일', '올리브유', '폴리페놀'],
    ['효소', '캡슐레이션', '파로효소', '탄수화물'], ['비타민'], ['코엔자임', '코큐텐'], ['nmn'], ['글루타치온'], ['양배추'], ['노니'], ['새싹보리'],
    ['클로렐라', '스피루리나'], ['맥주효모', '비오틴', '탈모'], ['흑마늘'], ['생강'], ['레시틴'], ['감마리놀렌산', '달맞이꽃'],
]

norm = lambda s: re.sub(r"[^0-9A-Za-z가-힣]", "", str(s or "")).lower()


def to_min(t):
    m = re.match(r"^(\d{1,2}):(\d{2})", t or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def tags_of(text):
    """[(그룹 번호, 처음 나온 단어)]"""
    n = norm(text)
    out = []
    for gi, group in enumerate(INGR):
        hit = next((w for w in group if w in n), None)
        if hit:
            out.append((gi, hit))
    return out


def own_clues(ep):
    """프로그램명이 들어간 단서만 (편성 안내·동명 기사 같은 엉뚱한 검색 결과를 거른다)."""
    series = norm(ep.get("series"))
    texts = [ep["official"]] if ep.get("official") else []
    texts += [c for c in ep.get("clues", []) if series and series in norm(c)]
    return texts


def snippet(texts, words):
    """단어가 처음 나오는 곳 앞뒤로 잘라 보여준다."""
    for text in texts:
        text = " ".join(text.split())
        low = text.lower()
        pos = min((low.find(w) for w in words if low.find(w) >= 0), default=-1)
        if pos < 0:
            continue
        begin = max(0, pos - 20)
        cut = text[begin:begin + SNIPPET_MAX]
        return ("…" if begin else "") + cut + ("…" if begin + SNIPPET_MAX < len(text) else "")
    return ""


def tv_end(schedule, ep):
    """편성표에서 같은 채널·같은 시작 시각 방송의 종료 시각."""
    start = (ep.get("start") or "")[-5:]
    for p in (schedule or {}).get(ep.get("ch"), []):
        if p.get("start") == start:
            return p.get("end")
    return None


def health_food(day_str, month_str, companies):
    """[(시작 분, 표시 줄, 상품명 norm)] 오늘 4사 건강식품 방송 (한 방송 = 한 줄)."""
    out, seen = [], set()
    for co in companies:
        data = load_json(os.path.join(ROOT, "homeshopping", co + "_live", month_str + ".json")) or {}
        for it in data.get("days", {}).get(day_str, []):
            if it.get("category") != "건강식품":
                continue
            key = (co, it.get("start"))
            if key in seen:
                continue
            seen.add(key)
            product = food.clean_product(it.get("product") or "")
            brand = (it.get("brand") or "").strip()
            label = "%s [%s] %s" % (it.get("start"), food.COMPANY_NAMES.get(co, co),
                                    (brand + " · " if brand and not product.startswith(brand) else "") + product[:30])
            link = (it.get("link") or "").strip()
            out.append((to_min(it.get("start")), label, link, norm(brand + product)))
    return out


def build_lines(now, episodes, schedule, foods):
    lines = ["💊 %02d/%02d(%s) 건강프로그램 PPL 예고" % (now.month, now.day, food.WEEKDAY_KO[now.weekday()]),
             "오늘 TV 건강프로그램이 다룰 성분이에요. ⭐ = 같은 시간대 홈쇼핑 같은 성분 방송"]
    others = []
    for ep in episodes:
        start = (ep.get("start") or "")[-5:]
        title = "%s %s" % (ep.get("series", ""), (ep["epi"] + "회") if str(ep.get("epi", "")).isdigit() else "")
        head = "%s [%s] %s" % (start, ep.get("ch", ""), title.strip())
        texts = own_clues(ep)
        tags = tags_of(" ".join(texts))
        if not tags:
            others.append("%s %s" % (start, ep.get("series", "")))
            continue
        words = [w for gi, _ in tags for w in INGR[gi]]
        lines += ["", "<b>%s</b>" % esc(head), "🏷 " + esc(" · ".join(w for _, w in tags))]
        snip = snippet(texts, [w for _, w in tags])
        if snip:
            lines.append('"%s"' % esc(snip))
        s, e = to_min(start), to_min(tv_end(schedule, ep))
        if s is None:
            continue
        e = e if e is not None and e > s else s + 60
        for fm, label, link, pn in foods:
            if fm is not None and s <= fm <= e + AFTER_MIN and any(w in pn for w in words):
                text = '<a href="%s">%s</a>' % (esc(link), esc(label)) if link else esc(label)
                lines.append("⭐ " + text)
    if len(lines) == 2:
        return None
    if others:
        lines += ["", "단서 없는 회차: " + esc(", ".join(others))]
    return lines


def main():
    now = food.target_date()
    day_str, month_str = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    env = (os.environ.get("COMPANIES") or food.DEFAULT_COMPANIES).strip()
    companies = list(food.COMPANY_NAMES) if env.upper() == "ALL" else [c.strip() for c in env.split(",") if c.strip()]

    clues = load_json(os.path.join(ROOT, "data", "episode_clues", month_str + ".json")) or {}
    episodes = sorted((v for v in clues.values() if v.get("date") == day_str), key=lambda v: v.get("start", ""))
    if not episodes:
        print("%s 건강프로그램 회차 없음. 발송 생략." % day_str)
        return
    schedule = load_json(os.path.join(ROOT, "data", day_str + ".json"))
    lines = build_lines(now, episodes, schedule, health_food(day_str, month_str, companies))
    if not lines:
        print("%s 성분 단서가 있는 회차 없음. 발송 생략." % day_str)
        return
    chunks = chunk_lines(lines)
    print("회차 %d개 → 메시지 %d건" % (len(episodes), len(chunks)))
    if food.DRY_RUN:
        for c in chunks:
            print("\n-----\n" + c)
        return
    # 식품 리스트를 받는 사람에게 (봇 '일반식품·건강식품' 구독자 + 기존 수신자)
    broadcast(ROOT, chunks, topic="food")


if __name__ == "__main__":
    main()
