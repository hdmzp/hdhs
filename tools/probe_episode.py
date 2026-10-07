# -*- coding: utf-8 -*-
"""[임시 조사용] 네이버 검색에서 건강 프로그램 회차 소개글을 받을 수 있는지 확인한다."""
import re, sys, time, requests
from bs4 import BeautifulSoup

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
     "Accept-Language": "ko-KR,ko;q=0.9", "Referer": "https://www.naver.com/"}
QUERIES = sys.argv[1:] or [
    "건강한 집 2", "건강한 집 2 회차정보", "엄지의 제왕", "이토록 위대한 몸",
    "몸신의 탄생", "퍼펙트라이프", "천기누설", "알토란", "기분 좋은 날",
]
KEYS = re.compile(r"episode|turn|epi|broad|program|info_|desc|story|cm_|tv_|_tab", re.I)

for q in QUERIES:
    print("=" * 100); print("QUERY:", q)
    try:
        r = requests.get("https://search.naver.com/search.naver",
                         params={"where": "nexearch", "query": q}, headers=H, timeout=15)
        print("status", r.status_code, "len", len(r.text))
        soup = BeautifulSoup(r.text, "html.parser")
        # 1) 회차/정보 관련 class 이름 목록 (구조 파악용)
        classes = set()
        for el in soup.find_all(class_=True):
            for c in el.get("class", []):
                if KEYS.search(c): classes.add(c)
        print("classes:", " ".join(sorted(classes))[:1500])
        # 2) '회' 와 날짜가 들어간 긴 텍스트 블록 (회차 소개 후보)
        seen = set(); n = 0
        for el in soup.find_all(["li", "div", "p", "dd", "span", "a"]):
            t = el.get_text(" ", strip=True)
            if not (25 <= len(t) <= 400): continue
            if not re.search(r"\d+\s*회|\d{1,2}\.\d{1,2}\.", t): continue
            if t in seen: continue
            if any(t in s for s in seen): continue
            seen.add(t); n += 1
            print(f"  [{el.name}.{'.'.join(el.get('class', []))[:60]}] {t[:300]}")
            if n >= 15: break
    except Exception as e:
        print("ERROR", e)
    time.sleep(1.5)
