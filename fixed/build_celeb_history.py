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
    "CJ_DGG.json",   # 동가게 (셀럽PGM 편입 2026-10)
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

# 방송이 '끝난 뒤' 수집분에서 사라진 상품은 제외로 보지 않는다.
# 방송이 끝나면 사이트가 판매 종료된 상품(미리주문 등)을 페이지/편성표에서
# 내린다. 실측: 2026-10-03 동가게(08:20~10:20) 로보 3종(미리주문 케이프/코트)
# - 09:13 편성표엔 있었는데 방송 종료 후 10:52 페이지·11:32 편성표에서 사라졌고,
# 정정 창이 이걸 '라인업 제외'로 받아 60분 방송한 로보가 기록에서 지워졌다.
# 방송 전·중에 빠진 상품(2026-08-31 무화과 - 방송 중 20:10 수집에서 빠짐)은
# 지금처럼 제외한다.
# 종료 시각은 편성표({회사}_live)에서 읽고, 못 읽으면 시작 + 이 값(분)으로 본다.
DEFAULT_BROADCAST_MINUTES = 60
LIVE_PATH_TEMPLATE = os.path.join("homeshopping", "{company}_live", "{ym}.json")

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


_LIVE_DAY_CACHE = {}


def _live_day_entries(company: str, date_iso: str) -> list:
    """편성표({회사}_live/{YYYY-MM}.json)의 그날 항목들. 없으면 []."""
    key = (LIVE_PATH_TEMPLATE, company, date_iso)
    if key not in _LIVE_DAY_CACHE:
        path = LIVE_PATH_TEMPLATE.format(company=company, ym=date_iso[:7])
        entries = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                entries = (json.load(f).get("days") or {}).get(date_iso) or []
        except (OSError, ValueError):
            pass
        _LIVE_DAY_CACHE[key] = entries
    return _LIVE_DAY_CACHE[key]


def broadcast_end(company: str, date_iso: str, start: datetime, products=()):
    """방송 종료 시각(datetime). 읽는 순서:
      1. 상품의 segment_time("08:20-09:20(60')") 중 가장 늦은 끝
      2. 편성표({회사}_live)에서 그 시각에 시작하는 편성의 종료시각
         (같은 프로그램이 이어 붙은 구간은 끝까지 따라간다)
      3. 시작 + DEFAULT_BROADCAST_MINUTES"""
    if start is None:
        return None

    def at(hm_text):
        hm = parse_hm(hm_text)
        if not hm:
            return None
        end = start.replace(hour=hm[0], minute=hm[1])
        return end + timedelta(days=1) if end <= start else end

    ends = []
    for product in products or ():
        m = re.search(r"-\s*(\d{1,2}:\d{2})", product.get("segment_time") or "")
        if m and at(m.group(1)):
            ends.append(at(m.group(1)))
    if ends:
        return max(ends)

    entries = _live_day_entries(company, date_iso)
    start_hm = start.strftime("%H:%M")
    first = [e for e in entries if e.get("start") == start_hm and e.get("end")]
    if first:
        pgm = first[0].get("pgm") or ""
        end_hm = max(e["end"] for e in first)
        # 같은 프로그램이 구간을 쪼개 이어 붙인 경우 (09:20 끝 -> 09:20 시작)
        while pgm:
            nxt = [e for e in entries if e.get("start") == end_hm
                   and (e.get("pgm") or "") == pgm and e.get("end")]
            if not nxt:
                break
            end_hm = max(e["end"] for e in nxt)
        if at(end_hm):
            return at(end_hm)

    return start + timedelta(minutes=DEFAULT_BROADCAST_MINUTES)


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


def fold_time_unknown(products: list, extras: list) -> list:
    """방송 시각을 못 읽은 상품(extras)을 같은 날 시각 있는 회차(products)에 합친다.
    이미 있는 상품(코드 -> 이름 순 대조)은 건너뛰고, 새로 붙는 상품엔
    time_unknown=True를 달아 화면에 '시간확인필요' 배지를 띄운다.

    채널/프로그램 무관. 방송이 시작되면 상세페이지 라벨이 "9/28(월) 방송상품"처럼
    시각 없는 표기로 바뀌는데(HD), 그걸 별도 회차로 두면 같은 날 방송이
    '19:30 방송'과 '방송' 두 개로 갈라져 보였다 (2026-09-28 황정민쇼)."""
    codes = {product_code(p) for p in products} - {""}
    names = {product_name_key(p) for p in products} - {""}
    out = list(products)
    for p in extras:
        code, name = product_code(p), product_name_key(p)
        if (code and code in codes) or (name and name in names):
            continue
        out.append({**p, "time_unknown": True})
        codes.add(code)
        names.add(name)
    return out


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


def reconcile_broadcast(kept: dict, new: dict, now_iso: str, end_at: datetime = None):
    """정정 창 안에서 기존 기록을 새 수집분으로 정정한다.

    end_at: 방송 종료 시각. 새 수집분이 이 시각 이후에 수집됐으면, 거기서
    사라진 상품은 '제외'가 아니라 방송 후 판매 종료로 내려간 것으로 보고
    기록에 남긴다 (DEFAULT_BROADCAST_MINUTES 위 설명 - 2026-10-03 동가게 로보).

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

    kept_after_end = []
    if removed and end_at is not None:
        try:
            collected = datetime.fromisoformat(new.get("collected_at") or now_iso)
        except (TypeError, ValueError):
            collected = None
        if collected is not None and collected.tzinfo is None:
            collected = collected.replace(tzinfo=KST)
        if collected is not None and collected >= end_at:
            kept_after_end, removed = removed, []

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

    if kept_after_end:
        revision["kept_after_end"] = [p.get("name") or "" for p in kept_after_end]

    merged = dict(new)
    if kept_after_end:
        merged["products"] = list(new_products) + list(kept_after_end)
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
    if kept_after_end:
        parts.append(f"방송 종료 후 사라진 {len(kept_after_end)}건은 유지")
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


def broadcast_key(date_iso: str, start_hm) -> str:
    """방송 회차 식별키. 같은 날 2회 방송(2026-09-08 오감쇼 08:15/19:30)을
    따로 남기려면 날짜만으론 부족해서 시작시각까지 넣는다.
    시각을 못 읽은 회차는 예전처럼 날짜 단위('YYYY-MM-DD#')로 잡힌다."""
    return f"{date_iso}#{start_hm or ''}"


def broadcast_key_of(broadcast: dict) -> str:
    """이미 저장된 방송 항목의 식별키. 시각은 라벨에서 읽는다
    (기존 월 파일은 날짜만으로 저장돼 있어도 라벨에 시각이 있어 같은 키가 나온다)."""
    hm = parse_hm(broadcast.get("label"))
    return broadcast_key(broadcast.get("date") or "",
                         f"{hm[0]:02d}:{hm[1]:02d}" if hm else "")


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
        # 날짜 -> 시작시각('' = 시각 없는 라벨) -> 상품들
        # 같은 날 2회 방송하는 날이 있어서(2026-09-08 오감쇼 08:15/19:30)
        # 날짜만으로 묶으면 두 회차가 한 덩어리가 된다. 시작시각까지 나눈다.
        by_slot = {}
        skipped = 0
        for product in data.get("products") or []:
            brod_date, start_hm = parse_label_date(product.get("broadcast_date_label"), today)
            if brod_date is None:
                skipped += 1
                continue
            by_slot.setdefault(brod_date, {}).setdefault(start_hm or "", []).append(product)

        if skipped:
            print(f"[경고] {program_key}: 날짜를 못 읽은 상품 {skipped}개 건너뜀")

        by_date = {}
        for brod_date, slots in by_slot.items():
            timeless = slots.pop("", [])
            # 시각 있는 회차가 하나도 없으면 예전처럼 날짜 단위 한 덩어리로 둔다.
            if not slots:
                slots = {None: timeless}
                timeless = []
            for start_hm in sorted(slots):
                products = slots[start_hm]
                # 같은 날짜에 시각 있는 라벨과 없는 라벨이 섞여 들어오는 회사가 있다
                # (HD: "09/02(수) 19:30 방송" + "9/2(수) 방송상품"). 정정 게이트가
                # 라벨의 HH:MM으로 회차를 특정하므로, 시각 없는 상품은 그 날의
                # 첫 회차에 붙여 시각 있는 라벨을 쓰게 한다. 겹치는 상품은 빼고
                # 새로 붙는 상품은 '시간확인필요'로 표시한다.
                if timeless and start_hm == min(slots):
                    products = fold_time_unknown(products, timeless)
                by_date[broadcast_key(brod_date.isoformat(), start_hm)] = {
                    "date": brod_date.isoformat(),
                    "label": make_broadcast_label(brod_date, start_hm),
                    "collected_at": now_iso,
                    "products": products,
                }

        # 수집기가 '편성표에 그날 방송 없음'이라고 알려준 날짜는 휴방 회차로
        # 남긴다. 화면에서 그 주가 그냥 비어 보이는 것보다 '휴방'이라고 쓰는
        # 게 낫다 (2026-09 추석 주 - 최은경쇼 9/23 / 왕영은 9/26).
        # 같은 날짜에 실제 회차가 수집됐으면(편성표가 방송을 확인해 준 것)
        # 그쪽이 맞으므로 휴방을 만들지 않는다.
        for date_text in data.get("off_air_dates") or []:
            try:
                off_date = date.fromisoformat(date_text)
            except (TypeError, ValueError):
                continue
            if any(b.get("date") == date_text for b in by_date.values()):
                continue
            by_date[broadcast_key(date_text, None)] = {
                "date": date_text,
                "label": f"{off_date.month:02d}/{off_date.day:02d}"
                         f"({WEEKDAY_ABBR[off_date.weekday()]}) 휴방",
                "off_air": True,
                "collected_at": now_iso,
                "products": [],
            }

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

    by_date = {broadcast_key_of(b): b for b in prog.get("broadcasts") or []}

    # 회차 병합으로 '흡수된' 옛 회차만 지운다.
    # 최유라쇼가 한 방송을 구간별로 쪼개 주던 것을(09/05 08:20/09:20/10:20)
    # 한 회차(08:20)로 묶게 됐는데, 09:20/10:20 항목이 그대로 남아 화면에
    # 방송이 3회처럼 보였다.
    #
    # 단, "새 수집분에 없으면 지운다"로 하면 안 된다 - 수집이 한 회차를
    # 일시적으로 놓치면(HD 편성표의 brodTitl이 비어 오는 시간대가 있다)
    # 멀쩡한 기록이 날아간다. 실제로 2026-09-08 오감쇼 08:15 기록이 그렇게
    # 지워졌다. 그래서 옛 회차의 시작시각이 새 회차의 '방송 구간' 안에
    # 들어갈 때만(= 그 회차로 흡수된 게 확실할 때만) 지운다.
    # 구간은 상품의 segment_time("08:20-09:20(60')")에서 읽는다.
    # 정정 창(reconcile)/확정(final) 회차는 절대 안 건드린다.
    spans = {}   # 날짜 -> [(블록 시작 분, 블록 끝 분)]
    for broadcast in new_broadcasts.values():
        start_hm = parse_hm(broadcast.get("label"))
        if not start_hm:
            continue
        start_min = start_hm[0] * 60 + start_hm[1]
        end_min = start_min
        for product in broadcast.get("products") or []:
            segment = product.get("segment_time") or ""
            times = TIME_PATTERN.findall(segment)
            for t in times:
                hm = parse_hm(t)
                if hm:
                    end_min = max(end_min, hm[0] * 60 + hm[1])
        if end_min > start_min:
            spans.setdefault(broadcast.get("date"), []).append((start_min, end_min))

    for stale_key in [k for k in by_date if k not in new_broadcasts]:
        stale = by_date[stale_key]
        stale_hm = parse_hm(stale.get("label"))
        if not stale_hm:
            continue
        stale_min = stale_hm[0] * 60 + stale_hm[1]
        absorbed = any(start < stale_min <= end
                       for start, end in spans.get(stale.get("date"), []))
        if not absorbed:
            continue
        if broadcast_phase(stale.get("date"), now, stale.get("label"),
                           meta.get("schedule_raw")) != "before":
            continue
        print(f"[정리] {program_key} {stale.get('label')}: 새 회차의 방송 구간에 "
              f"흡수돼 제거 (상품 {len(stale.get('products') or [])}건)")
        del by_date[stale_key]

    # 시각 없는 회차('YYYY-MM-DD#')는 같은 날 시각 있는 회차가 있으면 거기로
    # 합친다 (fold_time_unknown). 새 수집분·기존 기록 둘 다. 채널/프로그램 무관.
    # 합칠 곳은 새 수집분의 회차를 먼저, 없으면 기존 기록을 쓴다. 기존 기록이
    # 확정(final)이면 새 수집분은 안 붙인다('확정 기록은 안 건드린다' 규칙).
    new_broadcasts = dict(new_broadcasts)

    def timed_sibling(date_iso):
        for pool in (new_broadcasts, by_date):
            keys = sorted(k for k, b in pool.items()
                          if k.startswith(f"{date_iso}#") and not k.endswith("#")
                          and not b.get("off_air"))
            if keys:
                return pool, keys[0]
        return None, None

    for key in [k for k, b in new_broadcasts.items() if k.endswith("#") and not b.get("off_air")]:
        date_iso = key[:-1]
        pool, sib = timed_sibling(date_iso)
        if not sib:
            continue
        target = pool[sib]
        extras = new_broadcasts[key].get("products") or []
        if pool is by_date and broadcast_phase(date_iso, now, target.get("label"),
                                               meta.get("schedule_raw")) == "final":
            print(f"[보존] {program_key} {date_iso}: 시각 없는 수집분 {len(extras)}건 - "
                  f"같은 날 {target.get('label')}이 확정 기록이라 안 붙임")
        else:
            before_n = len(target.get("products") or [])
            target["products"] = fold_time_unknown(target.get("products") or [], extras)
            print(f"[합침] {program_key} {date_iso}: 시각 없는 상품을 {target.get('label')}에 "
                  f"합침 (+{len(target['products']) - before_n}건 시간확인필요)")
        del new_broadcasts[key]

    for key in [k for k, b in by_date.items() if k.endswith("#") and not b.get("off_air")]:
        date_iso = key[:-1]
        pool, sib = timed_sibling(date_iso)
        if not sib:
            continue
        stale = by_date.pop(key)
        target = pool[sib]
        before_n = len(target.get("products") or [])
        target["products"] = fold_time_unknown(target.get("products") or [],
                                               stale.get("products") or [])
        print(f"[합침] {program_key} {date_iso}: 기존 '{stale.get('label')}' 기록을 "
              f"{target.get('label')}에 합침 (+{len(target['products']) - before_n}건 시간확인필요)")

    for slot_key, broadcast in new_broadcasts.items():
        date_iso = broadcast.get("date") or slot_key.split("#", 1)[0]
        kept = by_date.get(slot_key)
        if kept is None:
            by_date[slot_key] = broadcast
            continue

        # 사람이 '휴방'으로 못 박은 회차는 수집분으로 덮지 않는다.
        # 휴방인 날에도 상세페이지에 지난 회차 상품이 남아 있어서 수집분이
        # 계속 그 날짜를 주장한다 (2026-09-22 오감쇼 - 휴방인데 다이슨 V8이
        # '9/22 방송'으로 잡혔다). 편성표가 진실이고, 그 판단을 여기에
        # 기록해 둔 것이므로 수집분보다 우선한다.
        if kept.get("off_air"):
            new_n = len(broadcast.get("products") or [])
            if new_n:
                print(f"[보존] {program_key} {date_iso}: 휴방으로 기록된 회차라 "
                      f"수집분 {new_n}건 무시")
            continue

        # 시작 시각은 기존 기록 라벨을 가장 신뢰한다. 방송이 끝나면 사이트
        # 라벨이 "8/22(토) 방송상품"처럼 시각 없는 잔여 표기로 바뀐다.
        phase = broadcast_phase(date_iso, now, kept.get("label"),
                                broadcast.get("label"), meta.get("schedule_raw"))
        kept_n = len(kept.get("products") or [])
        new_n = len(broadcast.get("products") or [])

        if phase == "before":
            # 방송 전 - 라인업이 계속 바뀐다. 최신 수집분으로 통째 교체.
            by_date[slot_key] = broadcast
            continue

        if phase == "reconcile":
            start = broadcast_start(date_iso, kept.get("label"),
                                    broadcast.get("label"), meta.get("schedule_raw"))
            end_at = broadcast_end(program_key.split("_", 1)[0], date_iso, start,
                                   (kept.get("products") or []) + (broadcast.get("products") or []))
            merged, note = reconcile_broadcast(kept, broadcast,
                                               broadcast.get("collected_at") or now.isoformat(),
                                               end_at)
            if merged is not None:
                by_date[slot_key] = merged
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
        for slot_key, broadcast in info["broadcasts"].items():
            ym = (broadcast.get("date") or slot_key)[:7]
            months.setdefault(ym, {}).setdefault(program_key, {})[slot_key] = broadcast

    # 새 수집분이 없는 달/프로그램도 이번 달·지난달 파일은 한 번씩 훑어
    # 기존 기록 정리(시각 없는 회차 합치기 등)를 적용한다.
    this_ym = today.strftime("%Y-%m")
    prev_ym = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    cleanup_months = {ym for ym in (this_ym, prev_ym)
                      if os.path.isfile(os.path.join(HISTORY_DIR, f"{ym}.json"))}

    for ym in sorted(set(months) | cleanup_months):
        path = os.path.join(HISTORY_DIR, f"{ym}.json")
        existing = (load_json(path) if os.path.isfile(path) else None) or {}
        existing["month"] = ym
        existing["updated_at"] = datetime.now(KST).isoformat()

        month_new = months.get(ym, {})
        for program_key, new_broadcasts in month_new.items():
            merge_into_month(existing, program_key, collected[program_key]["meta"],
                             new_broadcasts, now)
        for prog in list(existing.get("programs") or []):
            if prog.get("program_key") not in month_new:
                merge_into_month(existing, prog.get("program_key"), {}, {}, now)

        # 프로그램 순서를 SOURCE_FILES 순서로 고정
        order = {f[:-len(".json")]: i for i, f in enumerate(SOURCE_FILES)}
        existing["programs"].sort(key=lambda p: order.get(p.get("program_key"), 99))

        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        total = sum(len(p["broadcasts"]) for p in existing["programs"])
        print(f"[성공] {path} 저장 (프로그램 {len(existing['programs'])}개, 누적 방송 {total}회)")


if __name__ == "__main__":
    main()
