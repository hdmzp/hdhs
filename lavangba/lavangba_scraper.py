"""
라방바(live.ecomm-data.com) 11개사 방송 스케줄 + 매출 자동 수집.

login_setup.py로 만들어둔 chrome_profile/ 의 로그인 세션을 재사용한다.

중요: hsshow/items API는 headless 브라우저로 호출하면 로그인 여부와 무관하게
매출액이 마스킹(null)돼서 나온다 (User-Agent에 "HeadlessChrome"이 찍히는 걸
라방바 쪽에서 감지하는 것으로 보임). 그래서 headless=False로 띄우되, 화면
밖(-32000,-32000)에 창을 배치해서 사용자 눈에는 안 보이면서도 "진짜 브라우저"로
인식되게 한다. API 호출은 requests가 아니라 그 페이지 안에서 fetch()를 실행하는
방식(page.evaluate)으로 한다 - 쿠키를 꺼내서 별도 세션으로 쓰면 마스킹이 풀리지 않았음.

- list_hs: 로그인 불필요(항상 마스킹된 공개 스케줄), 방송 목록/시작종료시각/item_cnt 조회 -> requests로 충분
- hsshow/items: 로그인 필요, 실제 매출액(sales_amt)/시계열(sales_amt_rcd) 조회 -> 반드시 브라우저 페이지 경유
- GitHub(hdmzp/hdhs) homeshopping/{코드}_live/{YYYY-MM}.json: 각 회사 자체 편성표
  (상품별 시작/종료시각) 자동 fetch. 복합 PGM(상품 여러개)일 때 라방바 아이템들의
  시계열을 이 편성표 시간대에 매칭해서 상품별 매출을 분리한다.

사용법:
    venv\\Scripts\\python.exe lavangba_scraper.py

로그인 세션이 만료된 상태면(매출액이 마스킹됨) 경고를 남기고 종료코드 1로 끝난다
(작업 스케줄러가 다음 회차에 재시도하도록).
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from playwright.sync_api import sync_playwright

# 콘솔/리다이렉트 인코딩이 cp949일 때 상품명의 특수문자(∙ 등)로 print가 죽는 것 방지
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

KST = timezone(timedelta(hours=9))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, "chrome_profile")
DATA_DIR = os.path.join(BASE_DIR, "data")

BROWSER_ARGS = [
    "--window-position=-32000,-32000",  # 화면 밖으로 배치 (headless 안 씀 -> 마스킹 회피용)
    "--window-size=1280,900",
    "--disable-blink-features=AutomationControlled",
]

# platform_id -> hdmzp/hdhs 저장소 homeshopping 폴더 코드
GITHUB_CODE = {
    "hs_gsshop": "GS",
    "hs_cjonstyle": "CJ",
    "hs_hmall": "HD",
    "hs_lotteimall": "LT",
    "hs_nsmall": "NS",
    "hs_gongyoung": "PUBLIC",
    "hs_shinsegae": "SHINSEGAE",
    "hs_shopntmall": "SHOPPINGNT",
    "hs_skstoa": "SKSTOA",
    "hs_hnsmall": "HNS",
    "hs_kshop": "KTALPHA",
}
# 제외: hs_hmallplus / hs_gsshopmyshop / hs_lotteimallonetv / hs_nsmallshopplus / hs_cjonstyleplus
# (TV 11개사 외 데이터홈쇼핑/플러스 채널 - 필요하면 위 맵에 추가)

API_HEADERS = {"content-type": "application/json", "domain": "ecomm-data.com"}


def launch_browser():
    if not os.path.isdir(PROFILE_DIR):
        raise RuntimeError("chrome_profile/ 이 없습니다. 먼저 login_setup.py 를 실행해서 로그인하세요.")
    pw = sync_playwright().start()
    try:
        context = pw.chromium.launch_persistent_context(PROFILE_DIR, headless=False, args=BROWSER_ARGS)
    except Exception as e:
        pw.stop()
        raise RuntimeError(f"프로필 실행 실패 (다른 프로세스가 같은 프로필을 쓰고 있을 수 있음): {e}")
    page = context.pages[0] if context.pages else context.new_page()
    for extra in context.pages[1:]:
        extra.close()
    page.goto("https://live.ecomm-data.com/", wait_until="domcontentloaded", timeout=60000)
    return pw, context, page


_FETCH_JS = """async ([url, options]) => {
  const res = await fetch(url, options);
  return {status: res.status, text: await res.text()};
}"""


def page_post_json(page, url, body):
    options = {"method": "POST", "headers": API_HEADERS, "body": json.dumps(body), "credentials": "include"}
    result = page.evaluate(_FETCH_JS, [url, options])
    if result["status"] >= 400:
        raise RuntimeError(f"HTTP {result['status']}: {result['text'][:200]}")
    return json.loads(result["text"])


def fetch_list_hs(date_str):
    yymmdd = date_str[2:]
    res = requests.post(
        "https://live.ecomm-data.com/api/schedule/list_hs",
        headers=API_HEADERS,
        json={"date": yymmdd},
        timeout=15,
    )
    res.raise_for_status()
    data = res.json()
    return [x for x in data.get("list", []) if x.get("platform_id") in GITHUB_CODE]


def fetch_items_all(page, hshow_id, expected_count=None):
    pg = 1
    size = 50
    all_items = []
    while True:
        data = page_post_json(
            page,
            "https://live.ecomm-data.com/api/hsshow/items",
            {"hsshow_id": hshow_id, "page": pg, "size": size, "order": ["sales_amt/desc"], "with_rcd": True},
        )
        items = data.get("items", [])
        all_items.extend(items)
        total_count = data.get("total_count")
        if total_count is None:
            total_count = expected_count or len(all_items)
        if len(items) < size or len(all_items) >= total_count:
            break
        pg += 1
    return all_items


def check_login(page, hshow_id):
    data = page_post_json(
        page,
        "https://live.ecomm-data.com/api/hsshow/items",
        {"hsshow_id": hshow_id, "page": 1, "size": 1, "order": ["sales_amt/desc"], "with_rcd": False},
    )
    items = data.get("items", [])
    return bool(items) and items[0].get("sales_amt") is not None


_github_cache = {}


def fetch_github_month(code, yyyy_mm):
    key = f"{code}|{yyyy_mm}"
    if key in _github_cache:
        return _github_cache[key]
    url = f"https://raw.githubusercontent.com/hdmzp/hdhs/main/homeshopping/{code}_live/{yyyy_mm}.json"
    data = None
    try:
        res = requests.get(url, timeout=15)
        if res.ok:
            data = res.json()
    except requests.RequestException:
        pass
    _github_cache[key] = data
    return data


def hs_to_datetime(s):
    return datetime(int(s[0:4]), int(s[4:6]), int(s[6:8]), int(s[8:10]), int(s[10:12]), tzinfo=KST)


def gh_entry_datetimes(date_hyphen, entry):
    y, m, d = (int(x) for x in date_hyphen.split("-"))
    sh, sm = (int(x) for x in entry["start"].split(":"))
    eh, em = (int(x) for x in entry["end"].split(":"))
    start = datetime(y, m, d, sh, sm, tzinfo=KST)
    end = datetime(y, m, d, eh, em, tzinfo=KST)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def format_amt(total):
    if total >= 100_000_000:
        return f"{total / 100_000_000:.2f}억"
    return f"{round(total / 10_000)}만"


def allocate_item_to_segments(item, segments):
    """세그먼트별로 item의 매출을 '해당 구간의 실제 판매 활동 비중'만큼 나눠서 분배한다.
    (1등 세그먼트가 전부 가져가는 방식이 아니라 비례배분 -> 대부분 0원으로 몰리는 문제 방지)
    활동이 전혀 없으면 구간 길이 비례로, 그것도 안 되면 균등 분배로 fallback."""
    rcd_raw = item.get("sales_amt_rcd") or ""
    rcd = []
    if rcd_raw:
        for x in rcd_raw.split(","):
            x = x.strip()
            try:
                rcd.append(int(x))
            except ValueError:
                rcd.append(0)

    sales_amt = item.get("sales_amt") or 0
    activity = {}
    total_activity = 0
    for seg in segments:
        s = sum(rcd[i] for i in range(seg["from"], min(seg["to"], len(rcd))) if i >= 0)
        s = max(s, 0)
        activity[seg["idx"]] = s
        total_activity += s

    if total_activity > 0:
        return {idx: sales_amt * a / total_activity for idx, a in activity.items()}

    total_duration = sum(max(0, seg["to"] - seg["from"]) for seg in segments)
    if total_duration > 0:
        return {seg["idx"]: sales_amt * max(0, seg["to"] - seg["from"]) / total_duration for seg in segments}

    share = sales_amt / len(segments) if segments else 0
    return {seg["idx"]: share for seg in segments}


def ymd_to_hyphen(date_str):
    return f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"


def kst_today():
    return datetime.now(KST).date()


def date_range(start_ymd, end_ymd):
    start = datetime.strptime(start_ymd, "%Y%m%d").date()
    end = datetime.strptime(end_ymd, "%Y%m%d").date()
    out, cur = [], start
    while cur <= end:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def scrape_dates(target_dates):
    pw, context, page = launch_browser()

    rows = []
    hshow_count = 0
    login_checked = False

    try:
        for date_str in target_dates:
            print(f"[list_hs] {date_str} 조회 중...")
            list_hs = fetch_list_hs(date_str)
            print(f"  -> 대상 11개사 방송 {len(list_hs)}건")
            if not list_hs:
                continue

            if not login_checked:
                if not check_login(page, list_hs[0]["hsshow_id"]):
                    print("[경고] 로그인 세션이 유효하지 않은 것으로 보입니다 (매출액이 마스킹됨). 이번 회차는 건너뜁니다.")
                    return None
                login_checked = True

            date_hyphen = ymd_to_hyphen(date_str)
            yyyy_mm = f"{date_str[0:4]}-{date_str[4:6]}"

            for hshow in list_hs:
                hshow_count += 1
                code = GITHUB_CODE[hshow["platform_id"]]
                channel_label = hshow.get("platform_name") or code

                month_data = fetch_github_month(code, yyyy_mm)
                day_entries = ((month_data or {}).get("days") or {}).get(date_hyphen, [])

                hshow_start = hs_to_datetime(hshow["hsshow_datetime_start"])
                hshow_end = hs_to_datetime(hshow["hsshow_datetime_end"])
                pgm_start_label = hshow_start.strftime("%H:%M")

                matched = []
                for e in day_entries:
                    s, en = gh_entry_datetimes(date_hyphen, e)
                    if s < hshow_end and en > hshow_start:
                        matched.append({"entry": e, "start": s, "end": en})
                matched.sort(key=lambda m: m["start"])

                print(f"[{channel_label} {date_str} {pgm_start_label}] item_cnt={hshow.get('item_cnt')} items 조회 중...")
                try:
                    items = fetch_items_all(page, hshow["hsshow_id"], hshow.get("item_cnt"))
                except Exception as e:
                    print(f"[오류] items 조회 실패: {hshow.get('hsshow_title')} - {e}")
                    items = []

                is_simple = len(items) <= 1
                cat_name = (hshow.get("cat") or {}).get("cat_name", "")
                pgm_end_label = hshow_end.strftime("%H:%M")
                duration_min = round((hshow_end - hshow_start).total_seconds() / 60)

                def base_row():
                    return {
                        "channel": channel_label,
                        "date": date_str,
                        "broadcast_start": pgm_start_label,
                        "broadcast_end": pgm_end_label,
                        "duration_min": duration_min,
                        "pgm_title": hshow.get("hsshow_title"),
                    }

                if is_simple:
                    # 하나의 상품(브랜드)만 파는 방송 -> 라방바가 이미 계산해준 매출액 그대로 사용
                    total = sum(it.get("sales_amt") or 0 for it in items)
                    best_gh = matched[0]["entry"] if matched else None
                    rows.append({
                        **base_row(),
                        "item_name": hshow.get("hsshow_title"),
                        "type": "단순",
                        "item_start": pgm_start_label,
                        "item_end": pgm_end_label,
                        "sales_amt": total,
                        "category": best_gh["category"] if best_gh else "",
                        "lavangba_category": (best_gh.get("lavangba_category") or cat_name) if best_gh else cat_name,
                    })
                    print(f"  단순 | {format_amt(total)}")
                else:
                    # 여러 상품을 파는 방송 -> 시계열(sales_amt_rcd)로 상품별 비중을 계산해서 집계.
                    # GitHub 편성표 세그먼트가 없으면 방송 전체를 세그먼트 1개로 보고 그냥 합산.
                    if len(matched) >= 2:
                        segments = [
                            {
                                "idx": i,
                                "from": max(0, round((m["start"] - hshow_start).total_seconds() / 60)),
                                "to": min(duration_min, round((m["end"] - hshow_start).total_seconds() / 60)),
                                "name": m["entry"]["product"],
                                "category": m["entry"].get("category", ""),
                                "lavangba_category": m["entry"].get("lavangba_category") or cat_name,
                            }
                            for i, m in enumerate(matched)
                        ]
                    else:
                        segments = [{
                            "idx": 0, "from": 0, "to": duration_min,
                            "name": hshow.get("hsshow_title"), "category": "", "lavangba_category": cat_name,
                        }]

                    seg_totals = {}
                    for it in items:
                        allocation = allocate_item_to_segments(it, segments)
                        for idx, amt in allocation.items():
                            seg_totals[idx] = seg_totals.get(idx, 0) + amt

                    for seg in segments:
                        total = round(seg_totals.get(seg["idx"], 0))
                        seg_start = hshow_start + timedelta(minutes=seg["from"])
                        seg_end = hshow_start + timedelta(minutes=seg["to"])
                        rows.append({
                            **base_row(),
                            "item_name": seg["name"],
                            "type": "복합",
                            "item_start": seg_start.strftime("%H:%M"),
                            "item_end": seg_end.strftime("%H:%M"),
                            "sales_amt": total,
                            "category": seg["category"],
                            "lavangba_category": seg["lavangba_category"],
                        })
                        print(f"  복합(시계열집계) {seg['name'][:20]} | {format_amt(total)}")

                time.sleep(0.25)
    finally:
        context.close()
        pw.stop()

    print(f"완료! 총 {len(rows)}행 ({hshow_count}개 방송)")
    return rows


def save_rows(rows, target_dates=None):
    # 여러 날짜를 한 번에 돌려도 날짜별 파일로 나눠서 저장한다 (data/YYYYMMDD.json / .tsv)
    os.makedirs(DATA_DIR, exist_ok=True)
    cols = ["channel", "date", "broadcast_start", "broadcast_end", "duration_min", "pgm_title",
            "item_name", "type", "item_start", "item_end", "sales_amt", "category", "lavangba_category"]

    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)

    for date_str, date_rows in sorted(by_date.items()):
        json_path = os.path.join(DATA_DIR, f"{date_str}.json")
        tsv_path = os.path.join(DATA_DIR, f"{date_str}.tsv")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(date_rows, f, ensure_ascii=False, indent=2)

        with open(tsv_path, "w", encoding="utf-8-sig") as f:
            f.write("\t".join(cols) + "\n")
            for r in date_rows:
                f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

        print(f"저장됨: {json_path} ({len(date_rows)}행)")


def main():
    yesterday = (kst_today() - timedelta(days=1)).strftime("%Y%m%d")
    target_dates = [yesterday]
    # 특정 날짜: target_dates = ["20260709"]
    # 기간: target_dates = date_range("20260701", "20260709")

    rows = scrape_dates(target_dates)
    if rows is None:
        sys.exit(1)  # 로그인 안 된 상태 -> 스케줄러가 다음 회차에 재시도
    save_rows(rows, target_dates)


if __name__ == "__main__":
    main()
