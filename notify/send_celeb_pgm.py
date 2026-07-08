# -*- coding: utf-8 -*-
"""
텔레그램 봇으로 셀럽PGM(대표 프로그램) 상품 리스트를 발송

homeshopping/representative_programs/{회사}_{셀럽}.json 을 읽어
아래 형식의 메시지를 만들어 보낸다.

   🌟 셀럽PGM 상품리스트

   🎤 최화정쇼 (CJ)
   07/08(수) 20:45

   [주영엔에스]
   상품명(판매페이지 링크)
   319,000원

   [브랜드]
   ...

셀럽PGM명·방송일시·브랜드명은 굵게, 상품명에는 판매 링크가 걸린다.

== 환경변수 ==
  TELEGRAM_BOT_TOKEN  (필수) BotFather 에서 발급받은 봇 토큰
  TELEGRAM_CHAT_ID    (선택) 수신처 (notify/recipients.txt 와 합집합)
  DRY_RUN             (선택) "1"이면 발송하지 않고 메시지만 출력
"""

import os
import json
import glob

from tg import esc, chunk_lines, broadcast

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"

# 표시 순서 (자사 우선)
COMPANY_ORDER = ["HD", "GS", "CJ", "LT"]


def load_programs():
    programs = []
    pattern = os.path.join(ROOT, "homeshopping", "representative_programs", "*.json")
    for path in sorted(glob.glob(pattern)):
        base = os.path.basename(path)
        if base.startswith("_debug") or base == "merged.json":
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("program_title") and data.get("products"):
            programs.append(data)
    order = {c: i for i, c in enumerate(COMPANY_ORDER)}
    programs.sort(key=lambda p: order.get(p.get("company", ""), 99))
    return programs


def format_price(price):
    try:
        return "{:,}원".format(int(price))
    except (TypeError, ValueError):
        return ""


def build_lines(programs):
    lines = ["<b>🌟 셀럽PGM 상품리스트</b>"]
    for pgm in programs:
        title = pgm.get("program_title", "")
        company = pgm.get("company", "")
        lines.append("")
        lines.append("🎤 <b>%s</b> (%s)" % (esc(title), esc(company)))

        # 방송일시(라벨)별로 상품을 묶어서 표시
        prev_label = None
        for item in pgm.get("products", []):
            label = (item.get("broadcast_date_label") or "").strip()
            if label != prev_label:
                lines.append("<b>%s</b>" % esc(label))
                lines.append("")
                prev_label = label
            brand = (item.get("brand") or "").strip()
            name = " ".join((item.get("name") or "").split())
            link = (item.get("link") or "").strip()
            price = format_price(item.get("price"))

            entry = []
            if brand:
                entry.append("<b>[%s]</b>" % esc(brand))
            entry.append('<a href="%s">%s</a>' % (esc(link), esc(name)) if link else esc(name))
            if price:
                entry.append(price)
            lines.append("\n".join(entry))
            lines.append("")  # 상품 사이 여백
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def main():
    programs = load_programs()
    if not programs:
        print("셀럽PGM 데이터가 없습니다. 발송 생략.")
        return
    lines = build_lines(programs)
    chunks = chunk_lines(lines)
    n_products = sum(len(p.get("products", [])) for p in programs)
    print("프로그램 %d개 / 상품 %d개 → 메시지 %d건" % (len(programs), n_products, len(chunks)))

    if DRY_RUN:
        for i, c in enumerate(chunks, 1):
            print("\n----- [%d/%d] (%d자) -----" % (i, len(chunks), len(c)))
            print(c)
        return

    broadcast(ROOT, chunks)


if __name__ == "__main__":
    main()
