# 임시: 셀럽PGM 상품페이지 매진 응답 형태 확인 (확인 후 삭제)
import json, re, requests
UAM = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
UAD = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
S = json.load(open("tools/soldout_probe_sample.json", encoding="utf-8"))
MARK = ["매진된 상품", "매진", "판매종료", "판매가 종료", "품절", "일시품절", "판매중지", "존재하지 않", "판매하지 않", "죄송합니다"]
for co, items in S.items():
    print("=" * 100, co)
    for date, link, name in items:
        for ua_l, ua in (("D", UAD),):
            try:
                r = requests.get(link, headers={"User-Agent": ua}, timeout=20)
                t = r.text
                hits = {m: t.count(m) for m in MARK if m in t}
                al = re.findall(r"alert\(\s*['\"]([^'\"]{0,80})", t)[:2]
                flags = re.findall(r'"(soldOutYn|soldoutYn|sellStatCd|saleStatCd|ordPsblYn|sellYn|goodsStatCd|saleStat\w*|sellStat\w*)"\s*:\s*"?([\w]{1,10})', t)[:6]
                print(f"{date} [{ua_l}] HTTP {r.status_code} len={len(t):7d} final={r.url[:85]}")
                print(f"     {name} | marks={hits} alert={al} flags={flags}")
            except Exception as e:
                print(f"{date} ERR {link[:80]} {e}")
