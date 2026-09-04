# -*- coding: utf-8 -*-
"""
celeb_day_sweep.py
셀럽PGM 스크래퍼(regs/recj/relt)가 놓친 "방송 회차"를 편성표로 메운다.

== 왜 필요한가 ==
셀럽PGM은 한 프로그램이 하루에 두 번 방송하는 날이 있다.
  - 2026-09-08 오감쇼(HD)  : 08:15 / 19:30   <- 저녁 회차가 통째로 누락됐던 사고
  - 2026-09-05 최유라쇼(LT): 08:20 / 09:20 / 10:20
프로그램 상세페이지 API는 회사마다 "가장 가까운 회차 하나"만 주기도 하고
(HD pgm-comm), 여러 회차를 다 주기도 한다(LT). 어느 쪽이든 **편성표가
진실**이므로, 각 사 편성표(homeshopping/{사}_live/{YYYY-MM}.json - 이미
홈쇼핑 워크플로우가 하루 여러 번 수집해 둔 것)에서 그 프로그램의 방송을
훑어 **수집분에 없는 회차**를 채운다.

HD(rehd.py)는 편성표를 직접 훑는 방식으로 이미 바뀌었고, 이 모듈은
나머지 3사(GS/CJ/LT)가 같은 가정("어느 셀럽PGM이든 하루 2회 방송할 수
있다")을 갖게 해주는 공통 안전망이다.

== 반대편 문제: 한 방송이 여러 회차로 쪼개져 보이는 것 ==
롯데(LT)는 편성표도 상세페이지도 **한 방송을 상품 구간별로 쪼개서** 준다.
  2026-09-05 최유라쇼: 08:20~09:20 / 09:20~10:20 / 10:20~10:35
  (끊김 없이 이어지는 08:20~10:35 한 방송이다. 다른 3사는 방송당 구간 1개)
이걸 그대로 두면 화면에 회차 3개로 보인다. merge_continuous_slots()가
**이어지는 구간을 한 회차로 묶어** 첫 구간 시각을 그 방송의 라벨로 쓴다.
묶는 기준은 두 단계다:
  1순위 편성표의 종료시각 - 앞 구간 끝과 뒤 구간 시작이 붙어 있으면 한 방송
        (SEGMENT_GAP_MIN 분까지 허용)
  2순위 편성표에 아직 없는 날(편성표는 오늘~+5일뿐)은 시작시각 간격만 보고
        FALLBACK_GAP_MIN 분 이내면 한 방송으로 본다. 방송이 가까워져
        편성표에 들어오면 1순위로 정확히 다시 계산된다.
하루 2회 방송(오감쇼 08:15/19:30 = 675분 간격)은 어느 기준으로도 안 묶인다.
묶인 회차의 상품에는 원래 구간을 `segment_time`("08:20-09:20(60')")으로
남긴다 - 한 회차로 보되 어느 구간에 나온 상품인지는 알 수 있게 한다.
(구간이 하나뿐인 방송은 회차 라벨과 같은 말이라 안 붙인다)

== 규칙 ==
- 채우는 건 **회차 단위**다. 이미 수집된 회차(같은 날짜+시각)는 절대
  안 건드린다 - 편성표의 상품명은 화면 표시용으로 정제돼 있어서
  상세페이지 수집분과 섞으면 같은 상품이 두 줄로 보일 수 있다.
- **아직 시작 안 한 회차만** 채운다. 방송이 시작되면 상세페이지가 그
  회차를 목록에서 빼는데(GS), 편성표는 슬롯당 대표상품 1개만 실어서
  그걸 '빠진 회차'로 보고 채우면 20건짜리 회차가 1건으로 쪼그라든다
  (2026-09-04 소유진쇼 20:35 - 방송 12분 뒤 수집에서 실제로 발생).
  지난 회차는 build_celeb_history가 확정 기록으로 관리하므로 뒤늦게
  편성표로 새 기록을 만들지도 않는다.
- **스크래퍼가 상품을 하나도 못 가져온 프로그램은 건너뛴다.** 스크래퍼가
  죽은 걸 편성표로 덮어버리면 건전성 검사(check_scrape_health)가 실패를
  못 잡는다 - 이 레포가 제일 경계하는 '조용한 실패'다.

== 사용법 ==
    from celeb_day_sweep import supplement_missing_slots
    added = supplement_missing_slots("CJ", ["최화정쇼", "최화정"], products,
                                     label_fn=cj_label, link_fn=None)
"""

import os
import re
import json
from datetime import datetime, date, timedelta, timezone

KST = timezone(timedelta(hours=9))

# 편성표(라이브 방송)가 쌓이는 곳. 홈쇼핑 워크플로우가 어제~+5일을 매번 갱신한다.
LIVE_DIR_TEMPLATE = os.path.join("homeshopping", "{company}_live", "{ym}.json")

# 편성표에 들어있는 범위(오늘~+5일)만 볼 수 있다. 여유를 조금 더 둔다.
SWEEP_DAYS = 7

# 앞 구간 종료 ~ 뒤 구간 시작이 이만큼(분) 이내면 이어지는 한 방송으로 본다.
# 편성표는 보통 딱 붙여서 주지만(09:20 끝 -> 09:20 시작) 1~2분씩 어긋나는
# 경우가 있어 여유를 둔다.
SEGMENT_GAP_MIN = 10

# 편성표에 아직 없는 날(오늘+6일 이후)의 폴백 기준. 시작시각 간격이 이만큼
# 이내면 같은 방송의 구간으로 본다. 실측 근거: 최유라쇼 2026-09-10 회차가
# 19:35 / 21:45(130분 간격) 두 구간으로 왔다. 하루 2회 방송은 몇 시간씩
# 떨어져 있어(오감쇼 08:15/19:30 = 675분) 이 값에 안 걸린다.
FALLBACK_GAP_MIN = 150

WEEKDAY_ABBR = ["월", "화", "수", "목", "금", "토", "일"]
WEEKDAY_FULL = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

# 라벨에서 (월/일, 시각)을 읽는다. 회사별 표기가 제각각이라 느슨하게 본다.
#   HD "09/08(화) 19:30 방송" / GS "9월 8일(화) 20:35 방송"
#   LT "09/05 토요일 08:20"   / CJ "09/08(화) 18:30"
DATE_PATTERN = re.compile(r"(\d{1,2})\s*[/월]\s*(\d{1,2})")
TIME_PATTERN = re.compile(r"(\d{1,2}:\d{2})")


# ---- 회사별 방송회차 라벨 (각 사 스크래퍼가 쓰는 표기를 그대로 따라간다) ----
# 라벨 형식이 어긋나면 build_celeb_history의 정정 게이트(라벨의 HH:MM 일치)와
# 프런트의 회차 묶음이 깨진다.

def label_hd(d: date, hm: str) -> str:
    return f"{d.month:02d}/{d.day:02d}({WEEKDAY_ABBR[d.weekday()]}) {hm} 방송"


def label_gs(d: date, hm: str) -> str:
    return f"{d.month}월 {d.day}일({WEEKDAY_ABBR[d.weekday()]}) {hm} 방송"


def label_lt(d: date, hm: str) -> str:
    return f"{d.month:02d}/{d.day:02d} {WEEKDAY_FULL[d.weekday()]} {hm}"


def label_cj(d: date, hm: str) -> str:
    return f"{d.month:02d}/{d.day:02d}({WEEKDAY_ABBR[d.weekday()]}) {hm}"


LABEL_FNS = {"HD": label_hd, "GS": label_gs, "LT": label_lt, "CJ": label_cj}


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def title_matches(title: str, program_names) -> bool:
    """편성표의 프로그램명(pgm)이 이 셀럽PGM인지. 표기 흔들림
    ("왕영은의 톡 투게더" vs "왕영은의 톡투게더", "굿 라이프" vs "강주은 굿라이프")을
    견디도록 공백을 뺀 뒤 포함관계까지 허용한다."""
    compact_title = compact(title)
    if not compact_title:
        return False
    for name in program_names:
        compact_name = compact(name)
        if compact_name and (compact_title == compact_name
                             or compact_name in compact_title
                             or compact_title in compact_name):
            return True
    return False


def parse_label(label: str, today: date):
    """방송회차 라벨에서 (date, 'HH:MM' 또는 None). 연도는 오늘과 가장 가까운
    해석을 택한다(12월에 1/5 라벨 -> 내년). 못 읽으면 (None, None)."""
    m = DATE_PATTERN.search(label or "")
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
    tm = TIME_PATTERN.search(label or "")
    return best, (tm.group(1) if tm else None)


def load_live_days(company: str, days_ahead: int = SWEEP_DAYS) -> dict:
    """{'YYYY-MM-DD': [편성 항목...]} - 오늘~+days_ahead. 월 경계를 넘어가면
    두 월 파일을 다 읽는다. 파일이 없으면 그 달은 조용히 건너뛴다."""
    today = datetime.now(KST).date()
    wanted = [today + timedelta(days=i) for i in range(days_ahead + 1)]

    out = {}
    for ym in sorted({d.strftime("%Y-%m") for d in wanted}):
        path = LIVE_DIR_TEMPLATE.format(company=company, ym=ym)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                days = json.load(f).get("days", {}) or {}
        except (OSError, ValueError) as e:
            print(f"    -> [경고] 편성표({path}) 읽기 실패: {e}")
            continue
        for d in wanted:
            key = d.isoformat()
            if days.get(key):
                out[key] = days[key]
    return out


def find_program_slots(company: str, program_names, days_ahead: int = SWEEP_DAYS) -> dict:
    """편성표에서 이 프로그램의 방송 회차를 모두 찾는다.
    반환: {(날짜 'YYYY-MM-DD', 시작 'HH:MM'): [편성 항목...]}"""
    slots = {}
    for day, items in load_live_days(company, days_ahead).items():
        for item in items:
            start = item.get("start")
            if not start or not title_matches(item.get("pgm"), program_names):
                continue
            slots.setdefault((day, start), []).append(item)
    return slots


def to_product(item: dict) -> dict:
    """편성표 항목을 셀럽PGM 상품 스키마로. (broadcast_date_label은 호출부가 채움)"""
    return {
        "broadcast_date_label": None,
        "brand": item.get("brand") or "",
        "name": item.get("product") or "",
        "price": item.get("price") or None,
        # 편성표엔 상품 이미지가 없다. 프런트는 image가 없으면 그냥 안 쓴다.
        "image": None,
        "link": item.get("link") or None,
        "from_schedule": True,  # 편성표로 채운 회차라는 표시(디버깅용)
    }


def select_slots_by_starts(entries, starts) -> dict:
    """시작시각 목록으로 회차를 고른다. 편성표에 프로그램명이 비어 오는
    시간대가 있어(HD tv-list의 brodTitl) 이름으로 못 고를 때, 이름이 살아
    있는 다른 소스(로컬 편성)가 알려준 회차 시각으로 고르는 용도."""
    slots = {}
    for start, end, _title, payload in entries:
        if not start or start not in starts:
            continue
        slot = slots.setdefault(start, {"end": end, "items": []})
        if end and not slot["end"]:
            slot["end"] = end
        slot["items"].append(payload)
    return slots


def to_minutes(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def schedule_blocks(company: str, program_names, brod_date: date,
                    days_ahead: int = SWEEP_DAYS):
    """편성표에서 그 날 이 프로그램의 '방송 블록'(이어지는 구간 묶음)을 만든다.
    반환: [[시작 'HH:MM', ...], ...] - 블록별 구간 시작시각 목록.
    그 날 편성이 아직 없으면 빈 리스트."""
    segments = {}
    for (day, start), items in find_program_slots(company, program_names, days_ahead).items():
        if day != brod_date.isoformat():
            continue
        end = next((i.get("end") for i in items if i.get("end")), None)
        segments[start] = end

    blocks = []
    prev_end = None
    for start in sorted(segments):
        gap = None if prev_end is None else to_minutes(start) - to_minutes(prev_end)
        if blocks and gap is not None and gap <= SEGMENT_GAP_MIN:
            blocks[-1].append(start)
        else:
            blocks.append([start])
        prev_end = segments[start] or start
    return blocks


def format_segment(start: str, end) -> str:
    """구간 표기: "08:20-09:20(60')". 종료시각을 모르면 "10:10~"."""
    if not end:
        return f"{start}~"
    minutes = to_minutes(end) - to_minutes(start)
    if minutes <= 0:
        return f"{start}-{end}"
    return f"{start}-{end}({minutes}')"


def segment_ends(company: str, program_names, brod_date: date,
                 days_ahead: int = SWEEP_DAYS) -> dict:
    """편성표에서 그 날 이 프로그램의 {구간 시작: 구간 종료}."""
    ends = {}
    for (day, start), items in find_program_slots(company, program_names, days_ahead).items():
        if day != brod_date.isoformat():
            continue
        ends[start] = next((i.get("end") for i in items if i.get("end")), None)
    return ends


def merge_continuous_slots(company: str, program_names, products: list,
                           label_fn=None, days_ahead: int = SWEEP_DAYS) -> int:
    """이어지는 구간으로 쪼개져 들어온 회차를 한 방송으로 묶는다(제자리 수정).

    라벨만 첫 구간 시각으로 바꾸므로 상품은 하나도 잃지 않는다. 묶인 회차의
    상품에는 원래 구간을 segment_time으로 남긴다.
    반환: 라벨이 바뀐 상품 수. (규칙은 모듈 docstring 참고)"""
    if not products:
        return 0

    label_fn = label_fn or LABEL_FNS.get(company)
    if label_fn is None:
        raise ValueError(f"라벨 형식을 모르는 회사: {company}")

    today = datetime.now(KST).date()
    by_day = {}
    for product in products:
        d, hm = parse_label(product.get("broadcast_date_label"), today)
        if d and hm:
            by_day.setdefault(d, {}).setdefault(hm, []).append(product)

    changed = 0
    for brod_date, slots in by_day.items():
        if len(slots) < 2:
            continue

        blocks = schedule_blocks(company, program_names, brod_date, days_ahead)
        starts = sorted(slots)
        # 편성표에 있는 날은 종료시각으로 정확히 묶고, 없는 날은 시작시각
        # 간격만 보고 잠정으로 묶는다 (방송이 가까워지면 정확해진다).
        if blocks:
            start_to_block = {s: block[0] for block in blocks for s in block}
            ends = segment_ends(company, program_names, brod_date, days_ahead)
            source = "편성표"
        else:
            start_to_block = {}
            block_start = starts[0]
            for i, start in enumerate(starts):
                if i and to_minutes(start) - to_minutes(starts[i - 1]) > FALLBACK_GAP_MIN:
                    block_start = start
                start_to_block[start] = block_start
            # 종료시각을 모르니 "다음 구간 시작"을 끝으로 본다(마지막 구간은 미상).
            ends = {s: (starts[i + 1] if i + 1 < len(starts)
                        and start_to_block[starts[i + 1]] == start_to_block[s] else None)
                    for i, s in enumerate(starts)}
            source = "시작시각 간격"

        # 실제로 묶인 회차(구간 2개 이상)에만 구간 시간을 붙인다
        block_sizes = {}
        for start in starts:
            block_sizes[start_to_block[start]] = block_sizes.get(start_to_block[start], 0) + 1

        for start in starts:
            block_start = start_to_block.get(start)
            if not block_start:
                continue
            if block_sizes.get(block_start, 1) > 1:
                segment = format_segment(start, ends.get(start))
                for product in slots[start]:
                    product["segment_time"] = segment
            if block_start == start:
                continue
            label = label_fn(brod_date, block_start)
            print(f"    -> [회차 병합/{source}] {brod_date} {start} 구간을 "
                  f"{block_start} 방송으로 묶음 ({len(slots[start])}개 상품)")
            for product in slots[start]:
                product["broadcast_date_label"] = label
                changed += 1

    return changed


def supplement_missing_slots(company: str, program_names, products: list,
                             label_fn=None, days_ahead: int = SWEEP_DAYS) -> int:
    """products에 없는 방송 회차를 편성표에서 찾아 그 자리에서 추가한다.

    company      : "GS" | "CJ" | "LT" | "HD"
    program_names: 편성표 프로그램명과 대조할 이름들(프로그램명, 탭 이름 등)
    products     : 스크래퍼가 모은 상품 리스트 (제자리에서 확장된다)
    label_fn     : 방송회차 라벨 생성 함수. 기본은 회사 표준 형식.

    반환: 추가한 상품 수. (규칙은 모듈 docstring 참고)"""
    if not products:
        # 스크래퍼가 아무것도 못 가져온 상태 - 편성표로 덮으면 실패가 가려진다.
        return 0

    label_fn = label_fn or LABEL_FNS.get(company)
    if label_fn is None:
        raise ValueError(f"라벨 형식을 모르는 회사: {company}")

    now = datetime.now(KST)
    today = now.date()
    known = set()
    for p in products:
        d, hm = parse_label(p.get("broadcast_date_label"), today)
        if d and hm:
            known.add((d.isoformat(), hm))

    added = 0
    for (day, start), items in sorted(find_program_slots(company, program_names, days_ahead).items()):
        if (day, start) in known:
            continue  # 이미 수집된 회차는 안 건드린다
        try:
            brod_date = date.fromisoformat(day)
        except ValueError:
            continue
        # 이미 시작한 회차는 채우지 않는다 (지난 날짜 + 오늘의 지난 시각).
        # 방송이 시작되면 상세페이지가 그 회차를 목록에서 빼는데, 편성표는
        # 슬롯당 대표상품 1개뿐이라 채우면 오히려 회차가 쪼그라든다.
        hour, minute = int(start[:2]), int(start[3:5])
        if datetime(brod_date.year, brod_date.month, brod_date.day,
                    hour, minute, tzinfo=KST) <= now:
            continue

        label = label_fn(brod_date, start)
        print(f"    -> [편성표 보강] 수집분에 없는 회차 {label} - 상품 {len(items)}개 추가")
        for item in items:
            product = to_product(item)
            product["broadcast_date_label"] = label
            print(f"         · [{product['brand']}] {(product['name'] or '')[:40]}")
            products.append(product)
            added += 1

    return added
