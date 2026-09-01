# -*- coding: utf-8 -*-
"""
build_celeb_history.py
셀럽PGM 스크레이퍼(rehd/regs/relt/recj)가 만든 프로그램별 JSON은
"다음 방송" 기준으로 매번 덮어써서 지난 방송 데이터가 사라진다.
이 스크립트는 스크레이퍼 실행 직후에 돌면서 모든 셀럽PGM의 상품을
방송일자 기준 월(月) 파일에 누적 보존한다 -> 월 조회 기능의 데이터 소스.

== 출력 ==
homeshopping/representative_programs/history/{YYYY-MM}.json
{
  "month": "2026-07",
  "updated_at": "2026-07-17T03:05:00+09:00",
  "programs": [                       # 순서 = SOURCE_FILES 순서 (프런트 슬리서와 동일)
    {
      "program_key": "HD_OGS",
      "company": "HD", "tab_name": "오감쇼", "program_title": "오감쇼",
      "schedule_raw": "매주 화요일 19시 30분", "detail_link": "https://...",
      "broadcasts": [                 # 방송일 내림차순 = 최신 방송이 맨 위
        {"date": "2026-07-21", "label": "07/21(화) 19:30 방송",
         "collected_at": "...", "products": [...]},
        {"date": "2026-07-14", "label": "07/14(화) 19:30 방송", ...}
      ]
    },
    ...
  ]
}

== 누적 규칙 (방송 1회차의 3단계 수명) ==
방송 시작을 기준으로 기록을 딱 잘라 얼리면, "마지막 수집 ~ 방송 시작" 사이에
일어난 상품 제외/코드 변경이 영원히 반영되지 않는다. 실제로 2026-08-31
강주은 굿라이프(19:35)에서 이 사고가 났다 - 08:10 크론(실제 수집 10:22)이
잡은 8건이 그대로 확정됐는데, 방송 29분 전인 19:06 편성표 수집에서 이미
'농협 영암 햇 생 무화과'가 라인업에서 빠져 있었다. 실제 방송은 유러피안
데일리 베지믹스 / 프리메로 야생빌베리 / 더스텐 3개 브랜드만 진행됐는데도
무화과가 확정 기록에 남았다.
그래서 회차를 세 단계로 나눈다 (broadcast_phase()):

  before    방송 시작 전. 라인업이 계속 바뀌므로 최신 수집분으로 통째 교체.
  reconcile 방송 시작 ~ +RECONCILE_HOURS. 상품 단위로 '정정'을 반영한다
            (추가 / 제외 / 상품코드 변경). 방송 중·직후 수집분이 그 회차의
            실제 라인업을 가장 정확히 말해주는 구간이다.
  final     그 뒤. 확정 기록 - 무슨 일이 있어도 안 건드린다.

reconcile 구간에도 아무 수집분이나 받지는 않는다. 방송이 끝나면 사이트
라벨이 "8/22(토) 방송상품"처럼 시각 없는 잔여 표기로 바뀌면서 다음 회차
상품이 섞여 들어오는데(2026-08-22 왕영은의 톡투게더 08:20 방송 기록 3건이
같은 날 20:24 수집분 1건으로 교체되며 통째로 사라진 사고), 이런 수집분을
그대로 받으면 확정 기록이 날아간다. 그래서 두 겹으로 막는다:

  게이트1  새 수집분 라벨이 그 회차를 '시각까지' 특정해야 한다.
           (기존 기록 라벨의 HH:MM과 일치해야 함 - 잔여 표기는 시각이 없어
            여기서 걸린다)
  게이트2  기존 기록의 MIN_RETENTION 이상이 새 수집분에도 남아 있어야
           '제외'로 인정한다. 절반 넘게 사라지면 수집 실패/잔여 노출로 보고
           정정을 거부한다.

같은 상품인지는 상품코드(링크의 /item/{코드}) -> 상품명 순으로 본다.
코드만 바뀌고 이름이 같으면 '제외+추가'가 아니라 '상품코드 변경'으로 남긴다.
정정 내역은 방송 항목의 revisions[]에 최근 MAX_REVISIONS건까지 기록한다.

- 시작 시각은 기존 기록 라벨 -> 새 수집분 라벨 -> 편성문구(schedule_raw)
  순으로 찾고, 어디서도 못 읽으면 '이미 확정'으로 본다(보존 우선).
- 상품 라벨에서 월/일을 못 읽는 상품은 건너뛴다(어느 방송인지 알 수 없음).
- 라벨에 연도가 없으므로 "오늘과 가장 가까운 해석"으로 연도를 정한다
  (12월에 1/5 라벨 -> 내년, 1월에 12/28 라벨 -> 작년).

== 사용법 ==
  python fixed/build_celeb_history.py   (스크레이퍼들 실행 후)
"""

import os
import re
import json
from datetime import datetime, date, timezone, timedelta

KST = timezone(timedelta(hours=9))
SRC_DIR = os.path.join("homeshopping", "representative_programs")
HISTORY_DIR = os.path.join(SRC_DIR, "history")

# build_representative_programs.py의 SOURCE_FILES와 동일한 순서.
SOURCE_FILES = [
    "HD_HJM.json",
    "HD_OGS.json",
    "HD_WYE.json",   # 왕영은의 톡투게더
    "HD_CEK.json",   # 최은경쇼 (2026-08 신규)
    "GS_BJY.json",
    "GS_SYJ.json",
    "LT_CYR.json",
    "CJ_KJE.json",
    "CJ_CHJ.json",
    "CJ_KCO.json",
    "CJ_SIH.json",   # 소이현의 겟잇스타일
    "CJ_KSY.json",   # 김신영이 산다 (2026-08-18 론칭)
]

WEEKDAY_ABBR = ["월", "화", "수", "목", "금", "토", "일"]

# 방송 시작 후 이만큼은 '정정 창'으로 열어둔다. 방송 중·직후 수집분이 그
# 회차의 실제 라인업을 가장 정확히 말해준다(방송 직전에 빠진 상품이 여기서
# 걸러진다). 창을 닫은 뒤에는 확정 기록으로 보고 절대 안 건드린다.
# 12시간이면 저녁 방송(19:35)의 다음날 새벽 크론(03:00)까지 들어온다.
RECONCILE_HOURS = 12

# 정정 창 안이라도 기존 기록의 이 비율 이상이 새 수집분에 남아 있어야
# '제외'로 인정한다. 절반 넘게 사라졌으면 수집 실패나 다음 회차 잔여 노출로
# 보고 기존 기록을 지킨다.
MIN_RETENTION = 0.5

# 방송 항목당 보관할 정정 이력 건수 (최근 것부터)
MAX_REVISIONS = 5

# 회사별 broadcast_date_label 형식 (전부 월/일 포함, 연도 없음):
#   HD: "07/21(화) 19:30 방송" / "7/21(화) 방송상품"
#   GS: "7월 23일(목) 20:45 방송"
#   LT: "07/18 토요일 08:20"
#   CJ: "07/20(월) 19:35"
DATE_PATTERN = re.compile(r"(\d{1,2})\s*[/월]\s*(\d{1,2})")
TIME_PATTERN = re.compile(r"(\d{1,2}:\d{2})")

# 방송 시작 시각을 읽을 수 있는 표기들:
#   라벨 "08/22(토) 08:20 방송", 편성문구 "매주 토요일 08시 20분" / "매주 월요일 19시"
HM_PATTERNS = (
    re.compile(r"(\d{1,2}):(\d{2})"),
    re.compile(r"(\d{1,2})\s*시\s*(\d{1,2})\s*분"),
    re.compile(r"(\d{1,2})\s*시"),
)


def parse_hm(text):
    """문자열에서 (시, 분)을 뽑는다. 못 읽으면 None."""
    if not text:
        return None
    for pattern in HM_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.lastindex >= 2 else 0
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour, minute
    return None


def broadcast_start(date_iso: str, *time_hints):
    """방송 시작 시각(datetime). time_hints는 시각을 읽을 후보 문자열들
    (앞에 올수록 우선). 어디서도 못 읽으면 None."""
    try:
        brod_date = date.fromisoformat(date_iso)
    except (TypeError, ValueError):
        return None
    for hint in time_hints:
        hm = parse_hm(hint)
        if hm:
            return datetime(brod_date.year, brod_date.month, brod_date.day,
                            hm[0], hm[1], tzinfo=KST)
    return None


def broadcast_phase(date_iso: str, now: datetime, *time_hints) -> str:
    """회차의 수명 단계: 'before' | 'reconcile' | 'final'. (위 '누적 규칙' 참고)

    시작 시각을 어디서도 못 읽으면 날짜만으로 판단하되 보존 쪽을 택한다
    (오늘 이전이면 바로 final) - 라인업이 조금 낡는 것보다 확정 기록이
    날아가는 쪽이 훨씬 큰 손실이다."""
    try:
        brod_date = date.fromisoformat(date_iso)
    except (TypeError, ValueError):
        return "final"

    start = broadcast_start(date_iso, *time_hints)
    if start is None:
        return "before" if brod_date > now.date() else "final"
    if now < start:
        return "before"
    if now < start + timedelta(hours=RECONCILE_HOURS):
        return "reconcile"
    return "final"


def already_started(date_iso: str, now: datetime, *time_hints) -> bool:
    """해당 방송이 이미 시작됐는지. (check_scrape_health.py가 쓴다)"""
    return broadcast_phase(date_iso, now, *time_hints) != "before"


def record_is_final(date_iso: str, now: datetime, *time_hints) -> bool:
    """기록이 완전히 확정됐는지(정정 창까지 닫혔는지).

    '시작했다'와 '확정됐다'는 이제 다르다 - 시작 후 RECONCILE_HOURS 동안은
    상품 제외/코드 변경이 정상적으로 반영되므로, 그 구간의 건수 감소를
    사고로 봐선 안 된다."""
    return broadcast_phase(date_iso, now, *time_hints) == "final"


# ---- 상품 단위 정정(reconcile) ----

ITEM_CODE_PATTERN = re.compile(r"/item/(\d+)")


def product_code(product: dict) -> str:
    """판매 링크에서 상품코드를 뽑는다.
    (item.cjonstyle.com/item/{코드}, display.cjonstyle.com/p/item/{코드} 등)"""
    m = ITEM_CODE_PATTERN.search(product.get("link") or "")
    return m.group(1) if m else ""


def product_name_key(product: dict) -> str:
    """상품명 비교용 키(공백 제거 + 소문자). 코드가 바뀐 상품을 이어붙일 때 쓴다."""
    return re.sub(r"\s+", "", product.get("name") or "").lower()


def diff_products(kept: list, new: list):
    """기존 기록과 새 수집분을 상품 단위로 대조한다.
    같은 상품인지는 상품코드 -> 상품명 순으로 본다.

    반환: (added, removed, code_changed, matched)
      added        새로 붙은 상품 목록
      removed      라인업에서 빠진 상품 목록
      code_changed [{"name", "from", "to"}] - 이름은 같은데 코드만 바뀐 것
      matched      기존 기록 중 새 수집분에도 남아 있는 건수"""
    new_by_code, new_by_name = {}, {}
    for product in new:
        code, name_key = product_code(product), product_name_key(product)
        if code:
            new_by_code.setdefault(code, product)
        if name_key:
            new_by_name.setdefault(name_key, product)

    matched_ids = set()
    removed, code_changed, matched = [], [], 0
    for product in kept:
        code, name_key = product_code(product), product_name_key(product)
        hit = new_by_code.get(code) if code else None
        if hit is None and name_key:
            hit = new_by_name.get(name_key)
            new_code = product_code(hit) if hit is not None else ""
            if hit is not None and code and new_code and new_code != code:
                code_changed.append({"name": product.get("name") or "",
                                     "from": code, "to": new_code})
        if hit is None:
            removed.append(product)
        else:
            matched += 1
            matched_ids.add(id(hit))

    added = [p for p in new if id(p) not in matched_ids]
    return added, removed, code_changed, matched


def reconcile_broadcast(kept: dict, new: dict, now_iso: str):
    """정정 창 안에서 기존 기록을 새 수집분으로 정정한다.

    반환: (정정된 방송 항목 or None, 사람이 읽을 사유 문자열).
    None이면 기존 기록을 그대로 둔다."""
    kept_products = kept.get("products") or []
    new_products = new.get("products") or []

    # 게이트1: 새 수집분이 그 회차를 '시각까지' 특정해야 한다.
    #          방송이 끝나면 "8/22(토) 방송상품"처럼 시각 없는 잔여 표기로
    #          바뀌고 다음 회차 상품이 섞여 들어온다.
    kept_hm, new_hm = parse_hm(kept.get("label")), parse_hm(new.get("label"))
    if new_hm is None or kept_hm is None or new_hm != kept_hm:
        return None, "새 수집분이 회차를 시각까지 특정하지 못함(잔여 노출 의심)"
    if not new_products:
        return None, "새 수집분에 상품이 없음"

    added, removed, code_changed, matched = diff_products(kept_products, new_products)
    if not (added or removed or code_changed):
        return None, ""  # 변경 없음 - 매 실행 반복되는 정상 상황이라 조용히 넘어간다

    # 게이트2: 기존 기록의 절반 이상이 남아야 '제외'로 인정한다.
    if kept_products and matched / len(kept_products) < MIN_RETENTION:
        return None, (f"기존 {len(kept_products)}건 중 {matched}건만 남아 정정 거부"
                      f"(수집 실패/잔여 노출 의심)")

    revision = {"at": now_iso}
    if added:
        revision["added"] = [p.get("name") or "" for p in added]
    if removed:
        revision["removed"] = [p.get("name") or "" for p in removed]
    if code_changed:
        revision["code_changed"] = code_changed

    merged = dict(new)
    merged["label"] = kept.get("label") or new.get("label")
    merged["reconciled_at"] = now_iso
    merged["revisions"] = ((kept.get("revisions") or []) + [revision])[-MAX_REVISIONS:]

    parts = []
    if added:
        parts.append(f"추가 {len(added)}건")
    if removed:
        parts.append("제외 " + ", ".join(f"'{p.get('name') or ''}'" for p in removed))
    if code_changed:
        parts.append("코드변경 " + ", ".join(f"{c['from']}->{c['to']}" for c in code_changed))
    return merged, " / ".join(parts)


def parse_label_date(label: str, today: date):
    """라벨에서 (date, 'HH:MM' or None)을 뽑는다. 연도는 오늘과 가장
    가까운 해석을 택한다. 파싱 실패 시 (None, None)."""
    if not label:
        return None, None
    m = DATE_PATTERN.search(label)
    if not m:
        return None, None
    month, day = int(m.group(1)), int(m.group(2))

    best = None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            cand = date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((cand - today).days) < abs((best - today).days):
            best = cand
    if best is None:
        return None, None

    tm = TIME_PATTERN.search(label)
    return best, (tm.group(1) if tm else None)


def make_broadcast_label(brod_date: date, start_hm: str) -> str:
    label = f"{brod_date.month:02d}/{brod_date.day:02d}({WEEKDAY_ABBR[brod_date.weekday()]})"
    if start_hm:
        label += f" {start_hm}"
    return label + " 방송"


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[경고] {path} 읽기 실패: {e}")
        return None


def collect_current_broadcasts(today: date) -> dict:
    """프로그램별 JSON을 읽어 {program_key: {meta, {date_iso: broadcast}}}로 묶는다."""
    collected = {}
    now_iso = datetime.now(KST).isoformat()

    for filename in SOURCE_FILES:
        path = os.path.join(SRC_DIR, filename)
        if not os.path.isfile(path):
            continue
        data = load_json(path)
        if not data:
            continue

        program_key = filename[:-len(".json")]
        by_date = {}
        skipped = 0
        for product in data.get("products") or []:
            brod_date, start_hm = parse_label_date(product.get("broadcast_date_label"), today)
            if brod_date is None:
                skipped += 1
                continue
            key = brod_date.isoformat()
            entry = by_date.setdefault(key, {
                "date": key,
                "label": make_broadcast_label(brod_date, start_hm),
                "collected_at": now_iso,
                "products": [],
            })
            # 같은 날짜에 시각 있는 라벨과 없는 라벨이 섞여 들어오는 회사가 있다
            # (HD: "09/02(수) 19:30 방송" + "9/2(수) 방송상품"). 정정 게이트가
            # 라벨의 HH:MM으로 회차를 특정하므로, 시각이 있는 쪽을 라벨로 쓴다.
            if start_hm and not parse_hm(entry["label"]):
                entry["label"] = make_broadcast_label(brod_date, start_hm)
            entry["products"].append(product)

        if skipped:
            print(f"[경고] {program_key}: 날짜를 못 읽은 상품 {skipped}개 건너뜀")
        if not by_date:
            continue

        collected[program_key] = {
            "meta": {
                "program_key": program_key,
                "company": data.get("company", ""),
                "tab_name": data.get("tab_name", ""),
                "program_title": data.get("program_title", ""),
                "schedule_raw": data.get("schedule_raw", ""),
                "detail_link": data.get("detail_link", ""),
                "program_image": data.get("program_image", ""),
            },
            "broadcasts": by_date,
        }
    return collected


def merge_into_month(existing: dict, program_key: str, meta: dict,
                     new_broadcasts: dict, now: datetime):
    """월 파일의 프로그램 항목에 새 방송분을 병합한다.
    회차의 수명 단계(before/reconcile/final)에 따라 교체 / 정정 / 보존한다
    (위 '누적 규칙' 참고)."""
    programs = existing.setdefault("programs", [])
    prog = next((p for p in programs if p.get("program_key") == program_key), None)
    if prog is None:
        prog = {**meta, "broadcasts": []}
        programs.append(prog)
    else:
        # 편성/링크가 바뀌었을 수 있으니 메타는 항상 최신으로
        prog.update(meta)

    by_date = {b["date"]: b for b in prog.get("broadcasts") or []}
    for date_iso, broadcast in new_broadcasts.items():
        kept = by_date.get(date_iso)
        if kept is None:
            by_date[date_iso] = broadcast
            continue

        # 시작 시각은 기존 기록 라벨을 가장 신뢰한다. 방송이 끝나면 사이트
        # 라벨이 "8/22(토) 방송상품"처럼 시각 없는 잔여 표기로 바뀐다.
        phase = broadcast_phase(date_iso, now, kept.get("label"),
                                broadcast.get("label"), meta.get("schedule_raw"))
        kept_n = len(kept.get("products") or [])
        new_n = len(broadcast.get("products") or [])

        if phase == "before":
            # 방송 전 - 라인업이 계속 바뀐다. 최신 수집분으로 통째 교체.
            by_date[date_iso] = broadcast
            continue

        if phase == "reconcile":
            merged, note = reconcile_broadcast(kept, broadcast,
                                               broadcast.get("collected_at") or now.isoformat())
            if merged is not None:
                by_date[date_iso] = merged
                print(f"[정정] {program_key} {date_iso}: {note} "
                      f"({kept_n}건 -> {len(merged.get('products') or [])}건)")
            elif note:
                print(f"[보존] {program_key} {date_iso}: 정정 창이지만 {note} "
                      f"- 기존 기록 {kept_n}건 유지 (수집분 {new_n}건)")
            continue

        # final - 확정 기록. 무슨 일이 있어도 안 건드린다.
        if kept_n != new_n:  # 같은 건수면 조용히 넘어간다 (매 실행 반복되는 정상 상황)
            print(f"[보존] {program_key} {date_iso}: 정정 창이 닫힌 확정 기록이라 "
                  f"기존 {kept_n}건 유지 (수집분 {new_n}건 무시)")

    # 최신 방송이 맨 위로 오도록 내림차순 정렬
    prog["broadcasts"] = [by_date[k] for k in sorted(by_date, reverse=True)]


def main():
    if not os.path.isdir(SRC_DIR):
        print(f"[실패] 소스 디렉토리 없음: {SRC_DIR}")
        return
    os.makedirs(HISTORY_DIR, exist_ok=True)

    now = datetime.now(KST)
    today = now.date()
    collected = collect_current_broadcasts(today)
    if not collected:
        print("[경고] 누적할 셀럽PGM 데이터가 없음")
        return

    # 방송일이 속한 달 기준으로 월 파일에 나눠 담는다
    # (예: 7/31 수집분에 8/4 방송이 있으면 2026-08.json으로)
    months = {}
    for program_key, info in collected.items():
        for date_iso, broadcast in info["broadcasts"].items():
            ym = date_iso[:7]
            months.setdefault(ym, {}).setdefault(program_key, {})[date_iso] = broadcast

    for ym in sorted(months):
        path = os.path.join(HISTORY_DIR, f"{ym}.json")
        existing = (load_json(path) if os.path.isfile(path) else None) or {}
        existing["month"] = ym
        existing["updated_at"] = datetime.now(KST).isoformat()

        for program_key, new_broadcasts in months[ym].items():
            merge_into_month(existing, program_key, collected[program_key]["meta"],
                             new_broadcasts, now)

        # 프로그램 순서를 SOURCE_FILES 순서로 고정
        order = {f[:-len(".json")]: i for i, f in enumerate(SOURCE_FILES)}
        existing["programs"].sort(key=lambda p: order.get(p.get("program_key"), 99))

        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        total = sum(len(p["broadcasts"]) for p in existing["programs"])
        print(f"[성공] {path} 저장 (프로그램 {len(existing['programs'])}개, 누적 방송 {total}회)")


if __name__ == "__main__":
    main()
