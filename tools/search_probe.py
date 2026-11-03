# 임시: 홈쇼핑 4사 검색 URL 확인용 (확인 후 삭제)
import re, requests
from urllib.parse import quote
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
UAD = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
KW = "델시"
MAIN = ["https://www.hmall.com/", "https://m.gsshop.com/", "https://www.gsshop.com/", "https://display.cjonstyle.com/m/homeTab/main",
        "https://www.cjonstyle.com/", "https://www.lotteimall.com/main/viewMain.lotte", "https://m.lotteimall.com/main/viewMain.lotte"]
pat = re.compile(r"""["'(]((?:https?:)?//?[\w./-]*?(?:search|Search|srch)[\w./-]*?(?:\.\w+)?(?:\?[^"' )<>]{0,80})?)["')]""")
for u in MAIN:
    print("=" * 90); print("MAIN", u)
    for ua in (UA, UAD):
        try:
            r = requests.get(u, headers={"User-Agent": ua}, timeout=20)
            found = sorted(set(m for m in pat.findall(r.text) if len(m) < 140))
            print(f"  [{'M' if ua == UA else 'D'}] HTTP {r.status_code} {r.url} {len(r.text)}b")
            for f in found[:25]: print("     ", f)
            for m in re.findall(r"<form[^>]*>", r.text)[:5]: print("   form:", m[:200])
        except Exception as e:
            print("  ERR", e)

CANDS = [
  "https://www.hmall.com/md/dpl/search?searchTerm={q}",
  "https://www.hmall.com/pd/search/searchResult.do?searchTerm={q}",
  "https://www.hmall.com/m/search/searchResult.do?searchTerm={q}",
  "https://www.hmall.com/md/sea/search?searchTerm={q}",
  "https://m.gsshop.com/search/searchSect.gs?tq={q}",
  "https://www.gsshop.com/shop/search/main.gs?tq={q}",
  "https://display.cjonstyle.com/m/search?k={q}",
  "https://display.cjonstyle.com/p/search?k={q}",
  "https://www.cjonstyle.com/search?k={q}",
  "https://www.lotteimall.com/search/searchMain.lotte?headerQuery={q}",
  "https://m.lotteimall.com/search/searchMain.lotte?headerQuery={q}",
  "https://www.lotteimall.com/search/searchMain.lotte?searchKeyword={q}",
]
print("=" * 90)
for c in CANDS:
    u = c.format(q=quote(KW))
    try:
        r = requests.get(u, headers={"User-Agent": UA}, timeout=20, allow_redirects=True)
        t = re.sub(r"<[^>]+>", " ", r.text)
        title = re.search(r"<title>([^<]*)", r.text)
        print(f"HTTP {r.status_code} kw_in_html={r.text.count(KW):3d} len={len(r.text):7d} final={r.url[:110]} title={title.group(1).strip()[:40] if title else ''}")
    except Exception as e:
        print("ERR", u, e)
