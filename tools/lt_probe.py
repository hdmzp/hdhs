# 임시: 롯데 편성 API 응답 구조 확인용 (확인 후 삭제)
import json, requests
UA_M = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
H = {"User-Agent": UA_M, "Referer": "https://m.lotteimall.com/main/viewMain.lotte",
     "Accept": "application/json, text/plain, */*"}
URLS = [
    "https://m.lotteimall.com/main_rn/api/ston_gnbUnitApi.lotte?disp_no=5223317&tabNum=4",
    "https://m.lotteimall.com/main_rn/api/ston_gnbUnitApi.lotte?disp_no=5223317&tabNum=4&bdDate=20261103",
    "https://www.lotteimall.com/main/scheduleLive.lotte?bdDate=20261103&date=2026-11-03",
    "https://m.lotteimall.com/main/scheduleLive.lotte?bdDate=20261103&date=2026-11-03",
]

def shape(o, depth=0, path="$"):
    pad = "  " * depth
    if depth > 7:
        return
    if isinstance(o, dict):
        print(f"{pad}{path}: dict keys={list(o)[:40]}")
        for k, v in o.items():
            if isinstance(v, (dict, list)):
                shape(v, depth + 1, f"{path}.{k}")
    elif isinstance(o, list):
        print(f"{pad}{path}: list len={len(o)}")
        if o and isinstance(o[0], (dict, list)):
            shape(o[0], depth + 1, f"{path}[0]")

for u in URLS:
    print("=" * 100); print(u)
    try:
        r = requests.get(u, headers=H, timeout=20)
        print("HTTP", r.status_code, r.headers.get("content-type"), len(r.text))
        try:
            j = r.json()
            shape(j)
            print(json.dumps(j, ensure_ascii=False)[:6000])
        except ValueError:
            print(r.text[:3000])
    except Exception as e:
        print("ERR", e)
