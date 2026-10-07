# -*- coding: utf-8 -*-
"""[임시 조사용] v4 - 개선된 parse_clues 실제 결과 확인"""
import sys, time
sys.path.insert(0, ".")
import episode_clue_scraper as m
for series, epi, d in [("스타건강랭킹 넘버원", "63", "2026-09-30"), ("스타건강랭킹 넘버원", "64", "2026-10-07"),
                       ("몸신의 탄생", "105", "2026-10-06"), ("퍼펙트 라이프", "302", "2026-10-07"),
                       ("건강한 집2", "120", "2026-09-22"), ("아이엠닥터", "75", "2026-10-04")]:
    print("=" * 50, series, epi)
    for c in m.parse_clues(m.fetch(f"{series} {epi}회"), series, epi, d):
        print("   ", c)
    time.sleep(2)
