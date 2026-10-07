# -*- coding: utf-8 -*-
"""[임시 조사용] 회차 검색 결과에서 단서가 왜 안 잡히는지 확인 (v2)"""
import re, sys, requests
from bs4 import BeautifulSoup
sys.path.insert(0, ".")
import episode_clue_scraper as m
for series, epi, d in [("스타건강랭킹 넘버원", "63", "2026-09-30"), ("스타건강랭킹 넘버원", "64", "2026-10-07")]:
    html = m.fetch(f"{series} {epi}회")
    soup = BeautifulSoup(html, "html.parser")
    print("=" * 80, series, epi, "len", len(html))
    print("parse_clues:", m.parse_clues(html, series, epi, d))
    ns = m.norm(series)
    for el in soup.find_all(True):
        t = m.clean(el.get_text(" ", strip=True))
        if f"{epi}회" in t and ns in m.norm(t) and len(t) < 400 and not any(ns in m.norm(c.get_text(" ", strip=True)) and f"{epi}회" in c.get_text(" ", strip=True) for c in el.find_all(True, recursive=False)):
            print(f"  <{el.name} class={'.'.join(el.get('class', []))[:50]}> ({len(t)}) {t[:220]}")
    for el in soup.find_all(string=re.compile("관절|예고")):
        p = el.parent
        print(f"  [str in <{p.name} {'.'.join(p.get('class', []))[:40]}> parent <{p.parent.name}>] {str(el).strip()[:150]}")
