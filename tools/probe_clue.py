# -*- coding: utf-8 -*-
"""[임시 조사용] v3 - 예고 제목 링크의 속성/주변 구조 확인"""
import re, sys
from bs4 import BeautifulSoup
sys.path.insert(0, ".")
import episode_clue_scraper as m
for series, epi in [("스타건강랭킹 넘버원", "63"), ("스타건강랭킹 넘버원", "64")]:
    html = m.fetch(f"{series} {epi}회")
    soup = BeautifulSoup(html, "html.parser")
    print("=" * 60, series, epi)
    for a in soup.find_all("a"):
        t = a.get_text(" ", strip=True)
        if "예고" in t and len(t) < 300:
            attrs = {k: (v if isinstance(v, str) else " ".join(v))[:120] for k, v in a.attrs.items() if k not in ("class", "style")}
            print("A:", t[:120]); print("   attrs:", attrs)
            node = a
            for lv in range(6):
                node = node.parent
                if node is None: break
                txt = node.get_text(" ", strip=True)
                print(f"   up{lv+1} <{node.name} {'.'.join(node.get('class', []))[:40]}> len={len(txt)} has_series={m.norm(series) in m.norm(txt)} :: {txt[:160]}")
    for mk in soup.find_all("mark"):
        p = mk.parent
        print("MARK in", p.name, ".".join(p.get("class", []))[:40], "::", p.get_text(" ", strip=True)[:150])
    # 전체 HTML에서 원문 제목 조각이 다른 곳(스크립트/속성)에 있는지
    for mt in re.finditer(r".{0,80}1위는 누구.{0,120}|.{0,60}실패하는 이유는.{0,120}", html):
        print("RAW:", mt.group(0).replace("\n", " ")[:220])
