# 임시: 새 판정 기준(v2)을 실제 페이지로 검증 (확인 후 삭제)
import sys, os, json, random, re
sys.path.insert(0, "fixed"); sys.path.insert(0, ".")
import celeb_link_check as lc
random.seed(1)
out = {}
for f in ["homeshopping/representative_programs/history/2026-10.json", "homeshopping/representative_programs/history/2026-11.json"]:
    for p in json.load(open(f))["programs"]:
        for b in p["broadcasts"]:
            for x in b.get("products", []):
                if x.get("link"): out.setdefault(p["company"], []).append((b["date"], x["link"], x.get("name", "")[:24]))
cases = []
for co, l in out.items():
    past = [x for x in l if x[0] < "2026-11-03"]; fut = [x for x in l if x[0] > "2026-11-03"]
    for d, link, name in random.sample(past, min(8, len(past))) + fut[:2]:
        cases.append((co, lc.PID_RE[co].search(link).group(1), d, name))
cases += [("CJ", p, "flagged", "") for p in ("2096851161", "2076669085", "2097035053", "2083670899")]
import requests
for co, pid, d, name in cases:
    s = lc.check(co, pid)
    why = ""
    if s == "x":
        h = {"User-Agent": lc.pw.GS_MOBILE_UA if co == "GS" else lc.pw.UA, "Referer": "https://display.cjonstyle.com/p/item/"}
        t = requests.get(lc.RAW_URL[co].format(pid=pid), headers=h, timeout=15).text
        why = [p for p in lc.CLOSED_PHRASES if p in t] + lc.SOLDOUT_FLAG_RE.findall(t)
    print(f"{co} {pid} {d} {s:2s} {why} {name}")
