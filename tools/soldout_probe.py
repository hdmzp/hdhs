# 임시: CJ '안 열림' 판정 4건 문맥 확인 (확인 후 삭제)
import re, requests
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
M = ("매진", "죄송합니다", "판매하지 않", "판매종료", "판매가 종료", "판매중지", "잠시 후", "판매 예정", "오픈 예정", "방송 중", "준비중")
for pid in ("2076669085", "2097035053", "2083670899", "2096851161"):
    for u in (f"https://item.cjonstyle.com/nfront/item/{pid}",):
        r = requests.get(u, headers={"User-Agent": UA, "Referer": "https://display.cjonstyle.com/p/item/"}, timeout=20)
        t = r.text
        title = re.search(r"<title>([^<]*)", t)
        print("=" * 80, pid, r.status_code, len(t), title.group(1).strip()[:50] if title else "")
        for m in M:
            for mm in list(re.finditer(re.escape(m), t))[:2]:
                ctx = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t[max(0, mm.start() - 120): mm.end() + 120]))
                print(f"  [{m}] ...{ctx.strip()[:200]}...")
