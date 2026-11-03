# 임시: 셀럽PGM 상품 매진 판별 2차 - pricewatch 수집기로 실제 데이터 확인 (확인 후 삭제)
import json, re, sys, requests
sys.path.insert(0, ".")
import pricewatch_scraper as pw
S = json.load(open("tools/soldout_probe_sample.json", encoding="utf-8"))
PID = {"HD": r"slitmCd=(\d+)", "GS": r"prdid=(\d+)", "CJ": r"/item/(\d+)", "LT": r"goods_no=(\d+)"}
H = {"User-Agent": pw.UA}
for co, items in S.items():
    print("=" * 100, co)
    for date, link, name in items:
        pid = re.search(PID[co], link).group(1)
        try:
            res = pw.FETCHERS[co](pid)
        except Exception as e:
            res = f"ERR {e}"
        extra = ""
        try:
            if co == "CJ":
                t = requests.get(f"https://item.cjonstyle.com/nfront/item/{pid}", headers=H, timeout=15).text
                extra = f"len={len(t)} " + str({m: t.count(m) for m in ("매진", "판매종료", "판매가 종료", "일시품절", "품절", "죄송") if m in t}) + " " + \
                        str(re.findall(r'"(\w*(?:[Ss]tat|[Ss]old|[Ss]ell)\w*)"\s*:\s*"?([\w가-힣]{1,12})', t)[:8])
            elif co == "HD":
                for tmpl in pw.HD_API_CANDIDATES:
                    r = requests.get(tmpl.format(pid=pid), headers={**H, "Referer": "https://www.hmall.com/"}, timeout=15)
                    if r.status_code == 200 and r.text.strip().startswith("{"):
                        extra = tmpl.split("?")[0][-40:] + " " + str(re.findall(r'"(\w*(?:[Ss]tat|[Ss]old|[Ss]ldout|[Ss]ell)\w*)"\s*:\s*"?([\w가-힣]{0,12})', r.text)[:10])
                        break
            elif co == "GS":
                t = requests.get(link, headers={"User-Agent": pw.GS_MOBILE_UA}, timeout=15).text
                extra = str(re.findall(r'"(\w*(?:[Ss]tat|[Ss]old|[Ss]ell|[Oo]rd)\w*)"\s*:\s*"?([\w가-힣]{0,12})', t)[:10])
        except Exception as e:
            extra = f"extra ERR {e}"
        r2 = res if not isinstance(res, dict) else {k: res[k] for k in ("price", "soldout", "src")}
        print(f"{date} {pid} {name[:22]} -> {r2}\n       {extra[:300]}")
