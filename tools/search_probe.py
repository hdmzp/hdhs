# 임시: 홈쇼핑 4사 검색 URL 확인용 2차 (확인 후 삭제)
import re, requests
from urllib.parse import quote
UAM = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
UAD = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
KW = "델시"

def show(label, u, ua):
    try:
        r = requests.get(u, headers={"User-Agent": ua}, timeout=20)
        t = re.search(r"<title>([^<]*)", r.text)
        print(f"[{label}] HTTP {r.status_code} kw={r.text.count(KW):4d} len={len(r.text):7d} -> {r.url[:120]} | {t.group(1).strip()[:30] if t else ''}")
        return r
    except Exception as e:
        print(f"[{label}] ERR {u[:100]} {e}")

# LT: 데스크톱 헤더 검색폼 input 이름, search.js 의 URL 조립
r = requests.get("https://www.lotteimall.com/main/viewMain.lotte", headers={"User-Agent": UAD}, timeout=20)
for fm in re.findall(r'<form id="frmHeaderSearch".*?</form>', r.text, re.S)[:1]:
    print("LT form inputs:", re.findall(r'<input[^>]*name="([^"]+)"', fm))
js = requests.get("https://www.lotteimall.com/common/js/search.js", headers={"User-Agent": UAD}, timeout=20).text
for m in sorted(set(re.findall(r'[^\n;]{0,80}searchMain\.lotte[^\n;]{0,120}', js)))[:12]:
    print("LT js:", m.strip()[:200])
# LT 모바일 사이트에서 검색 경로 찾기
rm = requests.get("https://m.lotteimall.com/main/viewMain.lotte", headers={"User-Agent": UAM}, timeout=20)
srcs = re.findall(r'<script[^>]+src="([^"]+)"', rm.text)
print("LT m scripts:", len(srcs))
for s in srcs[:40]:
    u = s if s.startswith("http") else ("https:" + s if s.startswith("//") else "https://m.lotteimall.com" + s)
    try:
        t = requests.get(u, headers={"User-Agent": UAM}, timeout=15).text
    except Exception:
        continue
    hits = sorted(set(re.findall(r'["\'](/[\w/]*search[\w/]*\.lotte[^"\']{0,60})["\']', t)))
    if hits: print("  ", u[-60:], hits[:8])

C = [
 ("HD-M", "https://www.hmall.com/md/dpl/search?searchTerm={q}", UAM),
 ("HD-D", "https://www.hmall.com/md/dpl/search?searchTerm={q}", UAD),
 ("HD-x", "https://www.hmall.com/md/dpl/search?searchTerm=zzqqxxnotexist", UAM),
 ("CJ1", "https://display.cjonstyle.com/m/search/searchAllList?k={q}", UAM),
 ("CJ2", "https://display.cjonstyle.com/m/search/searchAllList?searchKeyword={q}", UAM),
 ("CJ3", "https://display.cjonstyle.com/m/search/searchAllList?keyword={q}", UAM),
 ("CJ4", "https://display.cjonstyle.com/m/search/searchAllList?searchTerm={q}", UAM),
 ("CJ5", "https://display.cjonstyle.com/p/search/searchAllList?k={q}", UAD),
 ("LT-D1", "https://www.lotteimall.com/search/searchMain.lotte?headerQuery={q}", UAD),
 ("LT-D2", "https://www.lotteimall.com/search/searchMain.lotte?query={q}", UAD),
 ("LT-D3", "https://www.lotteimall.com/search/searchMain.lotte?searchKeyword={q}", UAD),
 ("LT-M1", "https://m.lotteimall.com/search/searchMain.lotte?headerQuery={q}", UAM),
 ("LT-M2", "https://m.lotteimall.com/search/m/searchMain.lotte?headerQuery={q}", UAM),
 ("LT-M3", "https://m.lotteimall.com/search/searchMain.lotte?query={q}", UAM),
]
for label, c, ua in C:
    r = show(label, c.format(q=quote(KW)), ua)
    if r is not None and label.startswith("CJ") and r.status_code == 200:
        print("     cj hints:", sorted(set(re.findall(r'(?:[?&]|["\'])(k|keyword|searchKeyword|searchTerm|query|q)["\']?\s*[:=]', r.text)))[:10])
