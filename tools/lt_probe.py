# 임시: 롯데 편성 API 응답 확인용 (확인 후 삭제)
import json, requests
OLD_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
VARIANTS = {
    "old(스크래퍼 원본 헤더)": {"User-Agent": OLD_UA, "Referer": "https://www.lotteimall.com/main/viewMain.lotte"},
    "old+Accept/XHR": {"User-Agent": OLD_UA, "Referer": "https://www.lotteimall.com/main/viewMain.lotte",
                       "Accept": "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest"},
    "UA없음": {},
}
for name, h in VARIANTS.items():
    for ep in ("scheduleLive", "scheduleOne"):
        for d in ("20261102", "20261106"):
            u = f"https://www.lotteimall.com/main/{ep}.lotte?bdDate={d}&date={d[:4]}-{d[4:6]}-{d[6:]}"
            try:
                r = requests.get(u, headers=h, timeout=20)
                n = len(r.json()["body"]["prod"]) if r.status_code == 200 else "-"
                print(f"{name:28s} {ep:13s} {d} HTTP {r.status_code} prod={n}")
            except Exception as e:
                print(f"{name:28s} {ep:13s} {d} ERR {e}")

u = "https://m.lotteimall.com/main_rn/api/ston_gnbUnitApi.lotte?disp_no=5223317&tabNum=4"
j = requests.get(u, headers={"User-Agent": OLD_UA}, timeout=20).json()
print("gnbUnitApi units:")
for b in j["body"]:
    m = b.get("meta", {})
    print("  ", m.get("type"), "|", m.get("unitNm"), "|", list((b.get("data") or {}).keys())[:8])
