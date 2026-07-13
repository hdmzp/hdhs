# -*- coding: utf-8 -*-
"""
mail2.py — RPA 실적분석 프로그램 (mail.py 확장판)

탭 구성 (2개)
  1) 식품사업부 Daily : mail.py가 제공하던 Daily Summary 전체 (RPA 1차+2차)
  2) 심화분석         : 기준연월/팀/브랜드 슬라이서 기반 심화 실적분석 (RPA 1차, 전체 기간)
                         - 기준연월 선택 (202501 ~ 최신월, 데이터 기준 자동 생성)
                         - 팀 슬라이서: 전체/건강식품1팀/건강식품2팀/일반식품1팀/일반식품2팀 (중복선택)
                         - 브랜드 슬라이서: 팀 선택 시에만 표시 (전체 선택 시 숨김, 중복선택)
                         - 협력사명 | 브랜드명 | 횟수 | 순달 | 공달 테이블
                         - 합산 목표/매출/공헌 기반 달성률 추이 꺾은선 그래프
                           · 순주문 효율 추이 (전체 순달 vs 생방 순달)
                           · 공헌 효율 추이   (전체 공달 vs 생방 공달)

참고 파일: C:/Users/Hhome/Downloads 의 RPA_총괄장(1차)/(2차) CSV
  - RPA 1차: PGM 총괄장 (기준일자 2025-01-01 ~ 현재, PGM 단위 실적/목표)
  - RPA 2차: 접수실적   (접수일자 기준 거래금액/공헌이익 등)
"""

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import threading
import pandas as pd
import os
import re
from datetime import datetime, timedelta
import matplotlib
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False


# ──────────────────────────────────────────────
# 공통 데이터 처리 로직
# ──────────────────────────────────────────────

def fmt_pct(val):
    if pd.isna(val):
        return "-"
    return f"{round(val)}%"


def sum_to_str(series):
    return round(series.sum() / 1e8, 1)


def calc_rate(df, nume_col, deno_col):
    # fillna(0): 목표누락PGM(순주문목표=0)의 실적값이 NaN이어도 집계에 포함
    nume = df[nume_col].fillna(0).sum()
    deno = df[deno_col].fillna(0).sum()
    return int(round(nume / deno * 100, 0)) if deno else 0


def contrib(df, teams, mode, subtract_fee=False):
    filtered_df = df[
        (df["사업부명"] == "식품사업부") &
        (df["팀명"].isin(teams)) &
        (df["순주문목표"] > 0) &
        (df["운영구분"] == mode)
    ]
    base = filtered_df["●순주문(생방)"].sum()
    if base == 0:
        return 0.0
    profit = filtered_df["●공헌(생방)"].sum()
    if subtract_fee:
        profit -= filtered_df["PGM수수료"].sum()
    return round(profit / base * 100, 0)


def count_time(df, day_type, hours):
    return sum((df["요일"].isin(day_type)) & (df["시간대"].isin(hours)))


def exclude_wang_rerun(df):
    """토요일 10·11시 왕영은의 톡 투게더 재방(순주문목표=0) 제외"""
    rerun_mask = (
        (df["PGM명"].astype(str).str.contains("왕영은의 톡 투게더", na=False)) &
        (df["순주문목표"] == 0) &
        (df["요일"] == "토") &
        (df["시간대"].isin([10, 11]))
    )
    return df[~rerun_mask]


FILE_DIR = r"C:/Users/Hhome/Downloads"
HL_RE = re.compile(r'(\[순[\d-]+%?\s*-\s*공[\d-]+%?\])')

# 협력사 통합 그룹 (키워드 포함 시 하나로 묶어 표시)
PARTNER_GROUPS = [
    {"keywords": ["에스엘바이오텍", "코스네이처"],  "display": "닥터린/뉴트리코어"},
    {"keywords": ["에스더포뮬러",   "큐어라벨"],    "display": "에스더포뮬러/큐어라벨"},
]

def normalize_partner(name):
    """협력사명 정규화: 그룹키워드 매칭 → 그룹명, 주식회사 → (주)"""
    s = str(name)
    for grp in PARTNER_GROUPS:
        if any(kw in s for kw in grp["keywords"]):
            return grp["display"]
    return s.replace("주식회사", "(주)")

# df_b는 파일에 따라 날짜 컬럼명이 다를 수 있어 후보를 모두 시도
DATE_COLS = ['기준일자', '접수일자', '방송일자', '일자', '날짜', '기준일', '방송날짜']


def find_latest_file(directory, pattern, target_date):
    matches = []
    for fname in os.listdir(directory):
        fpath = os.path.join(directory, fname)
        if os.path.isfile(fpath) and pattern in fname:
            ts = os.path.getctime(fpath)
            if datetime.fromtimestamp(ts).date() == target_date:
                matches.append((fpath, ts))
    if not matches:
        return None
    matches.sort(key=lambda x: x[1], reverse=True)
    return matches[0][0]


def read_csv_any_encoding(path):
    for enc in ('utf-8-sig', 'utf-8', 'cp949', 'euc-kr'):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            continue
    raise ValueError("인코딩 오류로 파일을 읽을 수 없습니다.")


PARSE_FORMATS = [
    '%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y%m%d',
    '%y-%m-%d', '%y/%m/%d', '%y.%m.%d', '%y%m%d',
    '%m/%d/%Y', '%d/%m/%Y',
]

def _try_parse(series):
    """여러 포맷을 순서대로 시도해 가장 많이 파싱되는 결과 반환"""
    s = series.astype(str).str.strip()
    # infer 먼저 시도 (pandas 2.0+ 은 infer_datetime_format 인자가 제거되어 기본 동작으로 대체)
    try:
        p = pd.to_datetime(s, errors='coerce', infer_datetime_format=True)
    except TypeError:
        p = pd.to_datetime(s, errors='coerce')
    if p.notna().sum() > len(s) * 0.5:
        return p
    # 명시적 포맷 순회
    for fmt in PARSE_FORMATS:
        try:
            p = pd.to_datetime(s, format=fmt, errors='coerce')
            if p.notna().sum() > len(s) * 0.5:
                return p
        except Exception:
            continue
    return p  # 그래도 실패면 마지막 결과 반환


def filter_month(df, ref_ym):
    for col in DATE_COLS:
        if col in df.columns:
            sample = df[col].dropna().astype(str).iloc[:2].tolist()
            parsed = _try_parse(df[col])
            mask = parsed.dt.strftime("%Y-%m") == ref_ym
            filtered = df[mask].copy()
            return filtered, col, sample
    return df.copy(), None, []


# ──────────────────────────────────────────────
# 식품사업부 Daily 보고서 (RPA 1차 + 2차)
# ──────────────────────────────────────────────

def build_report(df_a, df_b):
    ref_date = datetime.today() - timedelta(days=1)
    today_str = ref_date.strftime("%y/%m/%d")
    ref_ym    = ref_date.strftime("%Y-%m")
    month_label = ref_date.strftime("%y년 %m월")

    # ★ 해당 월만 필터 — 사용된 날짜 컬럼명도 반환해서 진단에 표시
    df_a, dcol_a, _ = filter_month(df_a, ref_ym)
    df_b, dcol_b, samp_b = filter_month(df_b, ref_ym)

    diag_a = f"1차: {dcol_a or '날짜컬럼없음'} ({len(df_a):,}행)"
    diag_b = f"2차: {dcol_b or '날짜컬럼없음'} ({len(df_b):,}행)"
    # 0행이면 샘플값 표시해서 원인 파악
    diag_warn = ""
    if dcol_b and len(df_b) == 0:
        diag_warn = f"  ⚠ 2차 필터 결과 0행 — 날짜 샘플: {samp_b}  (형식 확인 필요)"
    elif not dcol_b:
        diag_warn = "  ⚠ 2차 파일 날짜 컬럼 미확인 — 누적 집계일 수 있음"

    teams = ["건강식품1팀", "건강식품2팀", "일반식품1팀", "일반식품2팀"]

    lines = []
    lines.append(("title",   f"📩 식품사업부 Live방송 Daily Summary"))
    lines.append(("sub",     f"  {month_label} 누계 기준  |  -{today_str} 기준"))
    lines.append(("sub",     f"  [{diag_a}  /  {diag_b}]"))
    if diag_warn:
        lines.append(("warn", diag_warn))
    lines.append(("gap", ""))

    for team in teams:
        lines.append(("team",    f"☑️  {team}"))
        df_team_b    = df_b[df_b['팀명'] == team]
        df_team_live = df_team_b[df_team_b['생방비생방'] == '생방송']
        df_team_re   = df_team_b[df_team_b['생방비생방'] == '非생방송']

        gmv_total    = sum_to_str(df_team_b['거래금액'] + df_team_b['방송수수료'] + df_team_b['기타광고비'])
        profit_total = sum_to_str(df_team_b['공헌이익금액'])
        adv_total    = sum_to_str(df_team_b['방송수수료'] + df_team_b['기타광고비'])
        lines.append(("normal", f"  ▸ [접수]  매출 {gmv_total}억 / 공헌 {profit_total}억 / 광고비 {adv_total}억"))

        gmv_live    = sum_to_str(df_team_live['거래금액'] + df_team_live['방송수수료'] + df_team_live['기타광고비'])
        profit_live = sum_to_str(df_team_live['공헌이익금액'])
        adv_live    = sum_to_str(df_team_live['방송수수료'] + df_team_live['기타광고비'])
        lines.append(("normal", f"  ▸ [방송]  매출 {gmv_live}억 / 공헌 {profit_live}억 / 광고비 {adv_live}억"))

        gmv_re    = sum_to_str(df_team_re['거래금액'] + df_team_re['방송수수료'])
        profit_re = sum_to_str(df_team_re['공헌이익금액'])
        lines.append(("normal", f"  ▸ [미주]  매출 {gmv_re}억 / 공헌 {profit_re}억"))

        df_team_a    = df_a[(df_a['팀명'] == team) & (df_a['시간대구분'] != '심야')]
        total_order  = calc_rate(df_team_a, '●순주문(전체)', '순주문목표')
        total_profit = calc_rate(df_team_a, '●공헌(전체)',   '공헌목표')
        live_order   = calc_rate(df_team_a, '●순주문(생방)', '순주문목표')
        live_profit  = calc_rate(df_team_a, '●공헌(생방)',   '공헌목표')

        lines.append(("section", "  💡 달성률 요약"))
        lines.append(("hl_line", f"    • 전체   달성률:  [순{total_order}% - 공{total_profit}%]"))
        lines.append(("hl_line", f"    • 생방송 달성률:  [순{live_order}% - 공{live_profit}%]"))

        df_gnl = df_a[
            (df_a['팀명'] == team) &
            (df_a["시간대구분"] != "심야") &
            df_a["PGM명"].notna()
        ]

        df_no_wang = df_gnl[~df_gnl["PGM명"].str.contains("왕영은", na=False)]
        순_분자 = df_no_wang["●순주문(전체)"].sum()
        순_분모 = df_no_wang["순주문목표"].sum()
        공_분자 = df_no_wang["●공헌(전체)"].sum()
        공_분모 = df_no_wang["공헌목표"].sum()
        if 순_분모 > 0 and not pd.isna(순_분자) and 공_분모 > 0 and not pd.isna(공_분자):
            순달1 = int(round(순_분자 / 순_분모 * 100, 0))
            공달1 = int(round(공_분자 / 공_분모 * 100, 0))
            lines.append(("hl_line", f"    • 왕톡 제외 달성률:  [순{fmt_pct(순달1)} - 공{fmt_pct(공달1)}]"))

        exclude_kw = ['왕영은', '황정민', '오감쇼', '메종']
        df_no_fixed = df_gnl[~df_gnl["PGM명"].str.contains('|'.join(exclude_kw), na=False)]
        순_분자2 = df_no_fixed["●순주문(전체)"].sum()
        순_분모2 = df_no_fixed["순주문목표"].sum()
        공_분자2 = df_no_fixed["●공헌(전체)"].sum()
        공_분모2 = df_no_fixed["공헌목표"].sum()
        if 순_분모2 > 0 and not pd.isna(순_분자2) and 공_분모2 > 0 and not pd.isna(공_분자2):
            순달2 = int(round(순_분자2 / 순_분모2 * 100, 0))
            공달2 = int(round(공_분자2 / 공_분모2 * 100, 0))
            lines.append(("hl_line", f"    • 고정PGM 제외 달성률:  [순{fmt_pct(순달2)} - 공{fmt_pct(공달2)}]"))

        pgm_map = {'왕톡': '왕영은', '황정민쇼': '황정민', '오감쇼': '오감쇼', '메종': '메종'}
        df_team_valid = df_a[df_a['팀명'] == team]
        has_가중분 = '가중분' in df_team_valid.columns
        pgm_lines = []
        for label, keyword in pgm_map.items():
            df_pgm = df_team_valid[df_team_valid['PGM명'].str.contains(keyword, na=False)]
            if has_가중분:
                df_pgm = df_pgm[df_pgm['가중분'] > 0]
            count = df_pgm['브랜드명'].nunique()
            if count > 0:
                brands = df_pgm.sort_values('기준일자')['브랜드명'].dropna().unique()
                pgm_lines.append(f"    • {label}: {count}회  ({', '.join(brands)})")

        lines.append(("section", "  💡 고정PGM 진행 현황"))
        if pgm_lines:
            for pl in pgm_lines:
                lines.append(("normal", pl))
        else:
            lines.append(("normal", "    진행이력 없음"))
        lines.append(("gap", ""))

    # ── 고효율 PGM TOP5
    lines.append(("divider", "─" * 56))
    lines.append(("section", "📌 고효율 PGM TOP5   ※ 생방송 달성률 기준"))
    for label, team_list in [("건강식품1팀", ["건강식품1팀"]),
                              ("건강식품2팀", ["건강식품2팀"]),
                              ("일반식품1,2팀", ["일반식품1팀", "일반식품2팀"])]:
        lines.append(("subhead", f"  🟢 {label}"))
        df_eff = df_a[
            (df_a["팀명"].isin(team_list)) &
            (df_a["시간대구분"] != "심야")
        ]
        df_eff = df_eff[~df_eff['PGM명'].astype(str).str.contains('|'.join(exclude_kw), na=False)]
        df_eff = exclude_wang_rerun(df_eff)
        for _, row in df_eff.sort_values('●순달(생방)', ascending=False).head(5).iterrows():
            d = pd.to_datetime(row['기준일자']).strftime("%m/%d")
            h = int(float(row["시간대"])) if pd.notna(row["시간대"]) else 0
            s = round(row["●순달(생방)"], 0)
            p = round(row["●공달(생방)"], 0)
            lines.append(("normal", f"    • {d}({row['요일']}) {h:02d}시  {row['브랜드명']}  순{s} / 공{p}"))

    # ── 매출 TOP10
    lines.append(("gap", ""))
    lines.append(("section", "📌 매출 TOP10   ※ 접수 거래금액 기준"))
    for label, team_list in [("건강식품1팀", ["건강식품1팀"]),
                              ("건강식품2팀", ["건강식품2팀"]),
                              ("일반식품1,2팀", ["일반식품1팀", "일반식품2팀"])]:
        lines.append(("subhead", f"  🟢 {label}"))
        top_pre = df_b[df_b["팀명"].isin(team_list)].groupby("브랜드명")["거래금액"].sum().sort_values(ascending=False).head(10)
        items = [f"{brand} {int(val / 1e8)}억" for brand, val in top_pre.items()]
        lines.append(("normal", f"    {',  '.join(items)}"))

    # ── PGM달성률 컬럼 계산 ((순달+공달)/2)
    valid_teams = ["건강식품1팀", "건강식품2팀", "일반식품1팀", "일반식품2팀"]
    df_base = df_a[
        (df_a["팀명"].isin(valid_teams)) &
        (df_a["시간대구분"] != "심야")
    ].dropna(subset=["시간대"])
    df_base = exclude_wang_rerun(df_base).copy()

    if '●공달(전체)' in df_base.columns:
        df_base['_pgm달성률'] = (df_base['●순달(전체)'].fillna(0) + df_base['●공달(전체)'].fillna(0)) / 2
    else:
        공달_calc = df_base.apply(
            lambda r: r['●공헌(전체)'] / r['공헌목표'] * 100 if pd.notna(r.get('공헌목표')) and r.get('공헌목표', 0) > 0 else 0,
            axis=1
        )
        df_base['_pgm달성률'] = (df_base['●순달(전체)'].fillna(0) + 공달_calc.fillna(0)) / 2

    df_low30 = df_base[df_base["●순달(전체)"] < 30.0]
    df_p50   = df_base[df_base["_pgm달성률"]  < 50.0]

    # ── 순주문 30% 미만 — 팀별 분리
    lines.append(("gap", ""))
    lines.append(("section", f"📌 순주문 30% 미만 PGM  ({len(df_low30)}개)   ※ 전체 순달 기준(미주 포함)"))
    for t in ["건강식품1팀", "건강식품2팀", "일반식품1팀", "일반식품2팀"]:
        df_t = df_low30[df_low30["팀명"] == t]
        if df_t.empty:
            continue
        lines.append(("subhead", f"  🟢 {t}  ({len(df_t)}개)"))
        for _, r in df_t.iterrows():
            d = pd.to_datetime(r["기준일자"]).strftime("%m/%d")
            h = int(float(r["시간대"]))
            lines.append(("warn", f"    ⚠ {d}({r['요일']}) {h:02d}시  {r['브랜드명']}  {round(r['●순달(전체)'], 0)}%"))

    # ── PGM달성률 50% 미만 — 팀별 분리
    lines.append(("gap", ""))
    lines.append(("section", f"📌 PGM달성률 50% 미만  ({len(df_p50)}개)   ※ (순달+공달)÷2 기준"))
    for t in ["건강식품1팀", "건강식품2팀", "일반식품1팀", "일반식품2팀"]:
        df_t = df_p50[df_p50["팀명"] == t]
        if df_t.empty:
            continue
        lines.append(("subhead", f"  🟢 {t}  ({len(df_t)}개)"))
        for _, r in df_t.iterrows():
            d = pd.to_datetime(r["기준일자"]).strftime("%m/%d")
            h = int(float(r["시간대"]))
            pgm달 = round(r["_pgm달성률"], 0)
            순달  = round(r["●순달(전체)"], 0)
            lines.append(("warn_mid", f"    • {d}({r['요일']}) {h:02d}시  {r['브랜드명']}  P{int(pgm달)}%  (순{int(순달)}%)"))

    # ── 건강식품 협력사별 달성률 — 팀별 분리
    partner_col = next((c for c in ['협력사명'] if c in df_a.columns), None)
    brand_col   = '브랜드명' if '브랜드명' in df_a.columns else None

    if partner_col:
        lines.append(("gap", ""))
        lines.append(("section", f"📌 건강식품 협력사별 달성률"))
        for t in ["건강식품1팀", "건강식품2팀"]:
            df_t = df_a[
                (df_a["팀명"] == t) &
                (df_a["시간대구분"] != "심야") &
                (df_a["순주문목표"] > 0)
            ].copy()
            if df_t.empty:
                continue
            # 협력사명 정규화 (그룹화 + 주식회사 치환)
            df_t["_partner"] = df_t[partner_col].apply(normalize_partner)
            lines.append(("subhead", f"  🟢 {t}"))
            lines.append(("table_hdr", "    협력사명  |  브랜드명  |  횟수  |  순달  |  공달"))
            rows_out = []
            for grp_name, grp in df_t.groupby("_partner"):
                cnt    = len(grp)
                s달    = calc_rate(grp, '●순주문(전체)', '순주문목표')
                c달    = calc_rate(grp, '●공헌(전체)',   '공헌목표')
                brands = ""
                if brand_col:
                    brand_list = grp[brand_col].dropna().unique().tolist()
                    brands = ", ".join(str(b) for b in brand_list)
                rows_out.append({"name": grp_name, "brands": brands, "count": cnt, "순달": s달, "공달": c달})
            rows_out.sort(key=lambda x: x["순달"], reverse=True)
            for row in rows_out:
                lines.append(("partner_row", row))
            lines.append(("gap", ""))

    # ── 특이사항
    df_missing = df_a[
        (df_a["사업부명"] == "식품사업부") &
        (df_a["시간대구분"] != "심야") &
        (df_a["순주문목표"] == 0) &
        (df_a["PGM명"] != "왕영은의 톡 투게더")
    ]
    missing_count = len(df_missing)
    missing_items = []
    for _, r in df_missing.iterrows():
        d = pd.to_datetime(r["기준일자"]).strftime("%m/%d")
        h = int(float(r["시간대"])) if pd.notna(r.get("시간대")) else 0
        b = r.get("브랜드명", "")
        missing_items.append(f"{d} {h:02d}시 {b}")
    missing_detail = ",  ".join(missing_items) if missing_items else ""

    lines.append(("gap", ""))
    lines.append(("section", "📌 특이사항"))
    if missing_detail:
        lines.append(("normal", f"  • 목표 누락 PGM {missing_count}개  ({missing_detail}  — 누락 시 달성률 오차 발생 가능)"))
    else:
        lines.append(("normal", f"  • 목표 누락 PGM {missing_count}개  (누락 시 달성률 오차 발생 가능)"))
    건_정률 = int(round(contrib(df_a, ['건강식품1팀','건강식품2팀'], '정률'), 0))
    건_정액 = int(round(contrib(df_a, ['건강식품1팀','건강식품2팀'], '정액', True), 0))
    일_정률 = int(round(contrib(df_a, ['일반식품1팀','일반식품2팀'], '정률'), 0))
    일_정액 = int(round(contrib(df_a, ['일반식품1팀','일반식품2팀'], '정액', True), 0))
    lines.append(("normal", f"  • 건강식품  정률 평균 공헌율 {건_정률}% / 정액 {건_정액}%"))
    lines.append(("normal", f"  • 일반식품  정률 평균 공헌율 {일_정률}% / 정액 {일_정액}%"))

    # ── 시간대별 운영 빈도
    df_health = df_a[df_a["팀명"].isin(["건강식품1팀", "건강식품2팀"])].copy()
    df_health["요일"] = df_health["요일"].astype(str)
    weekdays = ['월', '화', '수', '목', '금']
    weekends = ['토', '일']
    wdc = {h: count_time(df_health, weekdays, [h]) for h in [6, 15, 16, 1]}
    wec = {h: count_time(df_health, weekends, [h]) for h in [6, 7, 8, 1]}
    lines.append(("normal", f"  • 건강식품 시간대별 운영 빈도"))
    lines.append(("normal", f"    [평일] 06시 {wdc[6]}회 / 15시 {wdc[15]}회 / 16시 {wdc[16]}회 / 01시 {wdc[1]}회"))
    lines.append(("normal", f"    [주말] 06시 {wec[6]}회 / 07시 {wec[7]}회 / 08시 {wec[8]}회 / 01시 {wec[1]}회"))

    # ── 상생방송
    df_sangsaeng = df_a[
        (df_a["사업부명"] == "식품담당") &
        (df_a["행사타이틀"].astype(str).str.contains("상생", na=False))
    ]
    total_s   = len(df_sangsaeng)
    regular_s = len(df_sangsaeng[df_sangsaeng["시간대구분"] != "심야"])
    night_s   = len(df_sangsaeng[df_sangsaeng["시간대구분"] == "심야"])
    lines.append(("normal", f"  • 상생방송 {total_s}회  (정규 {regular_s}회 / 심야 {night_s}회)"))

    return lines


# ──────────────────────────────────────────────
# 심화분석 로직 (RPA(1차) 파일 기반, 전체 기간)
# ──────────────────────────────────────────────

DEEP_TEAMS = ["건강식품1팀", "건강식품2팀", "일반식품1팀", "일반식품2팀"]
DEEP_NUM_COLS = ['순주문목표', '공헌목표',
                 '●순주문(전체)', '●순주문(생방)', '●공헌(전체)', '●공헌(생방)']

# 캡쳐 디자인 기반 색상 (연녹색 = 전체, 진녹색 = 생방)
DEEP_C_TOTAL_LINE = "#8fcdb2"
DEEP_C_TOTAL_FILL = "#c9e7d9"
DEEP_C_LIVE_LINE  = "#1e6f4c"
DEEP_C_LIVE_FILL  = "#5ea183"
DEEP_C_LABEL      = "#4a4f57"


def load_deep_df(path):
    """RPA(1차) 파일을 월 필터 없이 전체 기간(2025-01~)으로 로드 (심화분석용)"""
    df = read_csv_any_encoding(path)
    if '기준일자' not in df.columns:
        raise ValueError("파일에 '기준일자' 컬럼이 없습니다. RPA_총괄장(1차) 파일인지 확인해주세요.")
    df = df.copy()
    df['기준일자'] = _try_parse(df['기준일자'])
    df = df[df['기준일자'].notna()]
    for c in DEEP_NUM_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df[df['팀명'].isin(DEEP_TEAMS)].copy()
    df['YM'] = df['기준일자'].dt.strftime('%Y%m')
    return df


def deep_filter(df, teams, brands=None, exclude_night=True):
    """팀/브랜드 슬라이서 적용 (기본: 심야 제외 — Daily 달성률 요약과 동일 기준)"""
    d = df[df['팀명'].isin(teams)]
    if exclude_night and '시간대구분' in d.columns:
        d = d[d['시간대구분'] != '심야']
    if brands:
        d = d[d['브랜드명'].isin(brands)]
    return d


def deep_month_rates(df):
    """월별 합산 목표/매출/공헌 기반 달성률 4종 (전체/생방 × 순/공)"""
    out = []
    for ym in sorted(df['YM'].dropna().unique()):
        g = df[df['YM'] == ym]
        out.append({
            "ym": ym,
            "순달_전체": calc_rate(g, '●순주문(전체)', '순주문목표'),
            "순달_생방": calc_rate(g, '●순주문(생방)', '순주문목표'),
            "공달_전체": calc_rate(g, '●공헌(전체)',   '공헌목표'),
            "공달_생방": calc_rate(g, '●공헌(생방)',   '공헌목표'),
        })
    return out


def deep_partner_table(df_month):
    """협력사명 | 브랜드명 | 횟수 | 순달 | 공달  (순달 내림차순)"""
    d = df_month[df_month['순주문목표'] > 0].copy()
    if d.empty or '협력사명' not in d.columns:
        return []
    d['_partner'] = d['협력사명'].apply(normalize_partner)
    rows = []
    for (partner, brand), grp in d.groupby(['_partner', '브랜드명'], dropna=False):
        rows.append({
            "partner": partner,
            "brand": "" if pd.isna(brand) else str(brand),
            "count": len(grp),
            "순달": calc_rate(grp, '●순주문(전체)', '순주문목표'),
            "공달": calc_rate(grp, '●공헌(전체)',   '공헌목표'),
        })
    rows.sort(key=lambda x: x["순달"], reverse=True)
    return rows


def deep_month_summary(df_month):
    """선택 기준연월의 합산 실적 요약"""
    매출 = sum_to_str(df_month['●순주문(전체)'].fillna(0))
    공헌 = sum_to_str(df_month['●공헌(전체)'].fillna(0))
    return {
        "매출": 매출, "공헌": 공헌,
        "순달_전체": calc_rate(df_month, '●순주문(전체)', '순주문목표'),
        "공달_전체": calc_rate(df_month, '●공헌(전체)',   '공헌목표'),
        "순달_생방": calc_rate(df_month, '●순주문(생방)', '순주문목표'),
        "공달_생방": calc_rate(df_month, '●공헌(생방)',   '공헌목표'),
        "횟수": int((df_month['순주문목표'] > 0).sum()),
    }


def ym_label(ym):
    return f"{ym[2:4]}년{ym[4:6]}월"


# ──────────────────────────────────────────────
# 텍스트 위젯 렌더링
# ──────────────────────────────────────────────

def setup_tags(box):
    box.tag_configure("title",      font=("맑은 고딕", 14, "bold"), foreground="#0d2550",  spacing1=6,  spacing3=12)
    box.tag_configure("sub",        font=("맑은 고딕",  8),         foreground="#9098a3",  spacing3=10)
    box.tag_configure("team",       font=("맑은 고딕", 10, "bold"), foreground="#ffffff",  background="#3a6cf4", spacing1=12, spacing3=8, lmargin1=6, lmargin2=6)
    box.tag_configure("section",      font=("맑은 고딕",  9, "bold"), foreground="#1a3a6e",  spacing1=12, spacing3=5)
    box.tag_configure("section_box",  font=("맑은 고딕",  9, "bold"), foreground="#ffffff",  background="#26314a", spacing1=14, spacing3=8, lmargin1=6, lmargin2=6)
    box.tag_configure("subhead",      font=("맑은 고딕",  9, "bold"), foreground="#1f9d55",  spacing1=6,  spacing3=3)
    box.tag_configure("normal",       font=("맑은 고딕",  9),         foreground="#3a3f47",  spacing1=2,  spacing3=2,  spacing2=3)
    box.tag_configure("hl_base",      font=("맑은 고딕",  9),         foreground="#3a3f47",  spacing1=2,  spacing3=4,  spacing2=3)
    box.tag_configure("hl",           font=("맑은 고딕",  9, "bold"), foreground="#0860d6",  background="#e6f2ff", spacing1=2, spacing3=4)
    box.tag_configure("breakdown",    font=("맑은 고딕",  8),         foreground="#6b7280",  spacing1=2,  spacing3=2)
    box.tag_configure("warn",         font=("맑은 고딕",  9, "bold"), foreground="#d6311a",  spacing1=2,  spacing3=2)
    box.tag_configure("warn_mid",     font=("맑은 고딕",  9, "bold"), foreground="#d97c00",  spacing1=2,  spacing3=2)
    box.tag_configure("divider",      font=("맑은 고딕",  8),         foreground="#dde1e7",  spacing1=8,  spacing3=4)
    box.tag_configure("gap",          font=("맑은 고딕",  6))
    box.tag_configure("table_hdr",    font=("맑은 고딕",  8),         foreground="#9098a3",  spacing1=5,  spacing3=3)
    box.tag_configure("t_name",       font=("맑은 고딕",  9, "bold"), foreground="#1a1a2e")
    box.tag_configure("t_공달",       font=("맑은 고딕",  9, "bold"), foreground="#d6311a")
    box.tag_configure("t_공달_low",   font=("맑은 고딕",  9, "bold"), foreground="#d6311a",  underline=True)
    box.tag_configure("t_normal",     font=("맑은 고딕",  9),         foreground="#6b7280")


def insert_line(box, tag, text):
    if tag == "hl_line":
        parts = HL_RE.split(text)
        for part in parts:
            if HL_RE.match(part):
                box.insert("end", part, "hl")
            else:
                box.insert("end", part, "hl_base")
        box.insert("end", "\n")
    elif tag == "gap":
        box.insert("end", "\n", "gap")
    elif tag == "partner_row":
        d = text
        공달_tag = "t_공달_low" if d['공달'] < 85 else "t_공달"
        box.insert("end", "    • ")
        box.insert("end", f"{d['name']}", "t_name")
        if d.get("brands"):
            box.insert("end", f"  ({d['brands']})", "t_normal")
        box.insert("end", f"  {d['count']}회", "t_normal")
        box.insert("end", f"  순{d['순달']}%", "t_normal")
        box.insert("end", f"  공{d['공달']}%\n", 공달_tag)
    elif tag == "section":
        # 📌 헤더는 자동으로 검정 박스 스타일 적용
        actual_tag = "section_box" if text.lstrip().startswith("📌") else "section"
        box.insert("end", text + "\n", actual_tag)
    else:
        box.insert("end", text + "\n", tag)


# ──────────────────────────────────────────────
# GUI
# ──────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RPA 실적분석 프로그램")
        self.configure(bg="#eef1f6")
        self.minsize(820, 680)

        self.style = ttk.Style(self)
        try:
            self.style.theme_use('clam')
        except Exception:
            pass

        self._base_size = 9   # 모든 탭이 공유하는 글자 크기

        # 심화분석 테이블 스타일 (헤더: 남색 배경/흰 글씨)
        self.style.configure("Deep.Treeview", font=("맑은 고딕", 9), rowheight=26,
                              background="white", fieldbackground="white", borderwidth=0)
        self.style.configure("Deep.Treeview.Heading", font=("맑은 고딕", 9, "bold"),
                              background="#26314a", foreground="white", relief="flat")
        self.style.map("Deep.Treeview.Heading", background=[("active", "#26314a")])

        # ttk.Notebook의 "선택된 탭이 더 커 보이는" 테마 동작을 피하기 위해
        # 일반 tk.Button으로 직접 탭 바를 구현 (모든 버튼은 항상 동일한 크기)
        container = tk.Frame(self, bg="#eef1f6")
        container.pack(fill="both", expand=True, padx=10, pady=10)

        tab_bar = tk.Frame(container, bg="#eef1f6")
        tab_bar.pack(fill="x", side="top")

        body = tk.Frame(container, bg="#eef1f6")
        body.pack(fill="both", expand=True, pady=(8, 0))

        food_tab = tk.Frame(body, bg="#eef1f6")
        deep_tab = tk.Frame(body, bg="#eef1f6")
        for f in (food_tab, deep_tab):
            f.grid(row=0, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self._tab_btn = {}
        self._tab_frame = {"food": food_tab, "deep": deep_tab}

        def make_tab_button(key, text):
            btn = tk.Button(tab_bar, text=text, font=("맑은 고딕", 10, "bold"),
                             bd=0, relief="flat", padx=18, pady=10,
                             command=lambda: self._show_tab(key))
            btn.pack(side="left", padx=(0, 4))
            self._tab_btn[key] = btn

        make_tab_button("food", "식품사업부 Daily")
        make_tab_button("deep", "심화분석")

        self._build_food_tab(food_tab)
        self._build_deep_tab(deep_tab)

        self._show_tab("food")

    def _show_tab(self, key):
        for k, btn in self._tab_btn.items():
            active = (k == key)
            btn.configure(background="#3a6cf4" if active else "#dde2eb",
                          activebackground="#3a6cf4" if active else "#dde2eb",
                          foreground="#ffffff" if active else "#5a6472",
                          activeforeground="#ffffff" if active else "#5a6472")
        self._tab_frame[key].tkraise()

    # ── 식품사업부 탭 ─────────────────────────
    def _build_food_tab(self, root):
        pad = {"padx": 12, "pady": 7}

        tk.Label(root, text="📩 식품사업부 Live방송 Daily 알리미",
                 font=("맑은 고딕", 15, "bold"), bg="#eef1f6", fg="#10254f").grid(
            row=0, column=0, columnspan=3, pady=(18, 10))

        tk.Label(root, text="1차 파일", bg="#eef1f6", font=("맑은 고딕", 9)).grid(row=1, column=0, sticky="e", **pad)
        self.path_a = tk.StringVar()
        tk.Entry(root, textvariable=self.path_a, width=54, state="readonly").grid(row=1, column=1, sticky="ew", **pad)
        tk.Button(root, text="찾아보기", command=self._pick_a, width=8).grid(row=1, column=2, **pad)

        tk.Label(root, text="2차 파일", bg="#eef1f6", font=("맑은 고딕", 9)).grid(row=2, column=0, sticky="e", **pad)
        self.path_b = tk.StringVar()
        tk.Entry(root, textvariable=self.path_b, width=54, state="readonly").grid(row=2, column=1, sticky="ew", **pad)
        tk.Button(root, text="찾아보기", command=self._pick_b, width=8).grid(row=2, column=2, **pad)

        btn_frame = tk.Frame(root, bg="#eef1f6")
        btn_frame.grid(row=3, column=0, columnspan=3, pady=(8, 6))

        self.btn_run = tk.Button(btn_frame, text="  보고서 생성  ", font=("맑은 고딕", 10, "bold"),
                                 bg="#3a6cf4", fg="white", activebackground="#2756d1",
                                 bd=0, padx=16, pady=9, command=self._run)
        self.btn_run.pack(side="left", padx=6)

        self.btn_copy = tk.Button(btn_frame, text="  클립보드 복사  ", font=("맑은 고딕", 10),
                                  bg="#22a85a", fg="white", activebackground="#1a8247",
                                  bd=0, padx=16, pady=9, command=self._copy, state="disabled")
        self.btn_copy.pack(side="left", padx=6)

        self.btn_clear = tk.Button(btn_frame, text="  지우기  ", font=("맑은 고딕", 10),
                                   bg="#888", fg="white", activebackground="#666",
                                   bd=0, padx=16, pady=9, command=self._clear)
        self.btn_clear.pack(side="left", padx=6)

        # 글자 크기 조절
        tk.Label(btn_frame, text="  크기:", bg="#eef1f6", font=("맑은 고딕", 9)).pack(side="left")
        tk.Button(btn_frame, text="A+", font=("맑은 고딕", 9, "bold"),
                  bg="#ddd", fg="#333", bd=0, padx=6, pady=6,
                  command=lambda: self._set_font_size(1)).pack(side="left", padx=2)
        tk.Button(btn_frame, text="A-", font=("맑은 고딕", 9),
                  bg="#ddd", fg="#333", bd=0, padx=6, pady=6,
                  command=lambda: self._set_font_size(-1)).pack(side="left", padx=2)

        self.report_box = scrolledtext.ScrolledText(
            root, font=("맑은 고딕", 9), wrap="word",
            bg="white", fg="#1a1a2e", bd=0, relief="flat",
            highlightthickness=1, highlightbackground="#dde2eb", highlightcolor="#3a6cf4",
            padx=18, pady=16
        )
        self.report_box.grid(row=4, column=0, columnspan=3, sticky="nsew", padx=14, pady=(6, 10))
        self.report_box.configure(state="disabled")
        setup_tags(self.report_box)

        self.status = tk.StringVar(value="파일을 선택하고 보고서 생성 버튼을 클릭하세요.")
        tk.Label(root, textvariable=self.status, bg="#eef1f6",
                 fg="#666", font=("맑은 고딕", 8)).grid(row=5, column=0, columnspan=3, pady=(0, 8))

        root.columnconfigure(1, weight=1)
        root.rowconfigure(4, weight=1)
        self._report_lines = []
        self._auto_find()

    def _auto_find(self):
        today = datetime.today().date()
        p_a = find_latest_file(FILE_DIR, 'RPA_총괄장(1차)', today)
        p_b = find_latest_file(FILE_DIR, 'RPA_총괄장(2차)', today)
        found = []
        if p_a:
            self.path_a.set(p_a)
            found.append("1차")
        if p_b:
            self.path_b.set(p_b)
            found.append("2차")
        if found:
            self.status.set(f"오늘 파일 자동 감지: {', '.join(found)} ✅  — 보고서 생성 버튼을 클릭하세요.")
        else:
            self.status.set("오늘 생성된 파일을 찾지 못했습니다. 직접 선택해주세요.")

    def _pick_a(self):
        p = filedialog.askopenfilename(title="RPA_총괄장(1차) 파일 선택",
                                       filetypes=[("CSV", "*.csv"), ("모든 파일", "*.*")],
                                       initialdir=FILE_DIR)
        if p:
            self.path_a.set(p)

    def _pick_b(self):
        p = filedialog.askopenfilename(title="RPA_총괄장(2차) 파일 선택",
                                       filetypes=[("CSV", "*.csv"), ("모든 파일", "*.*")],
                                       initialdir=FILE_DIR)
        if p:
            self.path_b.set(p)

    def _render_report(self, lines):
        box = self.report_box
        box.configure(state="normal")
        box.delete("1.0", "end")
        for tag, text in lines:
            insert_line(box, tag, text)
        box.configure(state="disabled")

    def _set_font_size(self, delta):
        self._base_size = max(7, min(18, self._base_size + delta))
        s = self._base_size
        box = self.report_box
        box.configure(font=("맑은 고딕", s))
        box.tag_configure("title",      font=("맑은 고딕", s + 4, "bold"))
        box.tag_configure("sub",        font=("맑은 고딕", s - 1))
        box.tag_configure("team",       font=("맑은 고딕", s + 1, "bold"))
        box.tag_configure("section",    font=("맑은 고딕", s, "bold"))
        box.tag_configure("section_box",font=("맑은 고딕", s, "bold"))
        box.tag_configure("subhead",    font=("맑은 고딕", s, "bold"))
        box.tag_configure("normal",     font=("맑은 고딕", s))
        box.tag_configure("hl_base",    font=("맑은 고딕", s))
        box.tag_configure("hl",         font=("맑은 고딕", s, "bold"))
        box.tag_configure("warn",       font=("맑은 고딕", s, "bold"))
        box.tag_configure("warn_mid",   font=("맑은 고딕", s, "bold"))
        box.tag_configure("breakdown",  font=("맑은 고딕", s - 1))
        box.tag_configure("table_hdr",  font=("맑은 고딕", s - 1))
        box.tag_configure("t_name",     font=("맑은 고딕", s, "bold"))
        box.tag_configure("t_공달",     font=("맑은 고딕", s, "bold"))
        box.tag_configure("t_공달_low", font=("맑은 고딕", s, "bold"))
        box.tag_configure("t_normal",   font=("맑은 고딕", s))

        # 심화분석 테이블 폰트/행높이 일괄 조정
        self.style.configure("Deep.Treeview", font=("맑은 고딕", s), rowheight=s + 17)
        self.style.configure("Deep.Treeview.Heading", font=("맑은 고딕", s, "bold"))

        self.status.set(f"글자 크기: {s}pt")
        if hasattr(self, 'deep_status'):
            self.deep_status.set(f"글자 크기: {s}pt")

    def _clear(self):
        self.report_box.configure(state="normal")
        self.report_box.delete("1.0", "end")
        self.report_box.configure(state="disabled")
        self._report_lines = []
        self.btn_copy.configure(state="disabled")
        self.status.set("지웠습니다.")

    def _copy(self):
        plain_lines = []
        for tag, text in self._report_lines:
            if tag == "gap":
                plain_lines.append("")
            elif tag == "partner_row":
                d = text
                brands_str = f"  ({d['brands']})" if d.get("brands") else ""
                plain_lines.append(f"    • {d['name']}{brands_str}  {d['count']}회  순{d['순달']}%  공{d['공달']}%")
            elif text:
                plain_lines.append(text)
        self.clipboard_clear()
        self.clipboard_append("\n".join(plain_lines))
        self.status.set("✅ 클립보드에 복사됐습니다.")

    def _run(self):
        if not self.path_a.get() or not self.path_b.get():
            messagebox.showwarning("파일 미선택", "1차·2차 파일을 모두 선택해주세요.")
            return
        self.btn_run.configure(state="disabled")
        self.btn_copy.configure(state="disabled")
        self.status.set("처리 중...")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            df_a = read_csv_any_encoding(self.path_a.get())
            df_b = read_csv_any_encoding(self.path_b.get())
            lines = build_report(df_a, df_b)
            self._report_lines = lines
            self.after(0, self._render_report, lines)
            self.after(0, lambda: self.btn_copy.configure(state="normal"))
            self.after(0, lambda: self.status.set("✅ 보고서 생성 완료!"))
        except Exception as e:
            self.after(0, lambda: self.status.set("❌ 오류 발생"))
            self.after(0, lambda: messagebox.showerror("오류", str(e)))
        finally:
            self.after(0, lambda: self.btn_run.configure(state="normal"))

    # ── 심화분석 탭 ────────────────────────────
    def _build_deep_tab(self, root):
        pad = {"padx": 12, "pady": 5}

        tk.Label(root, text="🔎 식품사업부 심화분석",
                 font=("맑은 고딕", 15, "bold"), bg="#eef1f6", fg="#10254f").grid(
            row=0, column=0, columnspan=3, pady=(18, 8))

        tk.Label(root, text="RPA(1차) 파일", bg="#eef1f6", font=("맑은 고딕", 9)).grid(row=1, column=0, sticky="e", **pad)
        self.deep_path = tk.StringVar()
        tk.Entry(root, textvariable=self.deep_path, width=54, state="readonly").grid(row=1, column=1, sticky="ew", **pad)
        pick_frame = tk.Frame(root, bg="#eef1f6")
        pick_frame.grid(row=1, column=2, **pad)
        tk.Button(pick_frame, text="찾아보기", command=self._deep_pick, width=8).pack(side="left")
        self.deep_btn_load = tk.Button(pick_frame, text="분석 시작", font=("맑은 고딕", 9, "bold"),
                                       bg="#3a6cf4", fg="white", activebackground="#2756d1",
                                       bd=0, padx=10, pady=4, command=self._deep_run)
        self.deep_btn_load.pack(side="left", padx=(6, 0))

        # ── 슬라이서 영역 ──
        slicer = tk.Frame(root, bg="white", highlightthickness=1, highlightbackground="#dde2eb")
        slicer.grid(row=2, column=0, columnspan=3, sticky="ew", padx=14, pady=(4, 4))
        slicer.columnconfigure(1, weight=1)

        tk.Label(slicer, text="기준연월", font=("맑은 고딕", 9, "bold"),
                 bg="white", fg="#1a3a6e").grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(10, 4))
        self.deep_month_var = tk.StringVar()
        self.deep_month_cb = ttk.Combobox(slicer, textvariable=self.deep_month_var,
                                          state="readonly", width=10, font=("맑은 고딕", 9))
        self.deep_month_cb.grid(row=0, column=1, sticky="w", pady=(10, 4))
        self.deep_month_cb.bind("<<ComboboxSelected>>", lambda e: self._deep_render())
        tk.Label(slicer, text="※ 테이블/요약은 선택한 기준연월, 추이 그래프는 전체 기간 기준 (심야 제외)",
                 font=("맑은 고딕", 8), bg="white", fg="#9098a3").grid(
            row=0, column=2, sticky="e", padx=(0, 12), pady=(10, 4))

        tk.Label(slicer, text="팀 선택", font=("맑은 고딕", 9, "bold"),
                 bg="white", fg="#1a3a6e").grid(row=1, column=0, sticky="w", padx=(12, 8), pady=4)
        team_frame = tk.Frame(slicer, bg="white")
        team_frame.grid(row=1, column=1, columnspan=2, sticky="w", pady=4)
        self.deep_team_vars = {}
        for key in ["전체"] + DEEP_TEAMS:
            var = tk.BooleanVar(value=(key == "전체"))
            cb = tk.Checkbutton(team_frame, text=key, variable=var,
                                font=("맑은 고딕", 9), bg="white", activebackground="white",
                                command=lambda k=key: self._deep_on_team_toggle(k))
            cb.pack(side="left", padx=(0, 10))
            self.deep_team_vars[key] = var

        # 브랜드 슬라이서 — 팀을 선택했을 때만 표시 (전체 선택 시 숨김)
        self.deep_brand_frame = tk.Frame(slicer, bg="white")
        self.deep_brand_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=(12, 12), pady=(2, 10))
        tk.Label(self.deep_brand_frame, text="브랜드 선택", font=("맑은 고딕", 9, "bold"),
                 bg="white", fg="#1a3a6e").pack(side="left", anchor="n", padx=(0, 8))
        brand_box = tk.Frame(self.deep_brand_frame, bg="white")
        brand_box.pack(side="left", fill="x", expand=True)
        self.deep_brand_list = tk.Listbox(brand_box, selectmode="multiple", height=5,
                                          font=("맑은 고딕", 9), exportselection=False,
                                          bd=0, highlightthickness=1, highlightbackground="#dde2eb",
                                          selectbackground="#3a6cf4", selectforeground="white")
        brand_sb = tk.Scrollbar(brand_box, orient="vertical", command=self.deep_brand_list.yview)
        self.deep_brand_list.configure(yscrollcommand=brand_sb.set)
        self.deep_brand_list.pack(side="left", fill="x", expand=True)
        brand_sb.pack(side="left", fill="y")
        self.deep_brand_list.bind("<<ListboxSelect>>", lambda e: self._deep_render())
        tk.Label(self.deep_brand_frame, text="(클릭으로 중복선택/해제 · '(전체)' = 모든 브랜드)",
                 font=("맑은 고딕", 8), bg="white", fg="#9098a3").pack(side="left", anchor="n", padx=(8, 0))
        self.deep_brand_frame.grid_remove()   # 초기: 전체 선택 상태 → 숨김

        # ── 결과 영역 (스크롤) ──
        canvas = tk.Canvas(root, bg="#eef1f6", highlightthickness=0)
        vsb = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg="#eef1f6")
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)

        def _on_inner_configure(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(e):
            canvas.itemconfig(inner_id, width=e.width)

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        canvas.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=(14, 0), pady=(4, 8))
        vsb.grid(row=3, column=2, sticky="ns", pady=(4, 8))
        root.columnconfigure(1, weight=1)
        root.rowconfigure(3, weight=1)

        # 요약 카드
        sum_card = tk.Frame(inner, bg="white", highlightthickness=1, highlightbackground="#dde2eb")
        sum_card.pack(fill="x", padx=14, pady=(4, 10))
        self.deep_sum_title = tk.Label(sum_card, text="📌 선택 조건 요약", font=("맑은 고딕", 10, "bold"),
                                       bg="white", fg="#1a3a6e", anchor="w")
        self.deep_sum_title.pack(fill="x", padx=12, pady=(10, 2))
        self.deep_sum_label = tk.Label(sum_card, text="파일을 선택하고 [분석 시작]을 눌러주세요.",
                                       font=("맑은 고딕", 9), bg="white", fg="#5a6472",
                                       anchor="w", justify="left")
        self.deep_sum_label.pack(fill="x", padx=12, pady=(0, 10))

        # 협력사/브랜드 테이블
        tbl_card = tk.Frame(inner, bg="white", highlightthickness=1, highlightbackground="#dde2eb")
        tbl_card.pack(fill="x", padx=14, pady=(0, 10))
        self.deep_tbl_title = tk.Label(tbl_card, text="📌 협력사·브랜드별 달성률",
                                       font=("맑은 고딕", 10, "bold"), bg="white", fg="#1a3a6e", anchor="w")
        self.deep_tbl_title.pack(fill="x", padx=12, pady=(10, 4))
        cols = ["협력사명", "브랜드명", "횟수", "순달", "공달"]
        self.deep_tv = ttk.Treeview(tbl_card, columns=cols, show="headings",
                                    style="Deep.Treeview", height=1)
        for col, w in zip(cols, [220, 220, 70, 80, 80]):
            self.deep_tv.heading(col, text=col)
            self.deep_tv.column(col, width=w, anchor="center")
        self.deep_tv.pack(fill="x", padx=12, pady=(0, 12))
        self.deep_tv.tag_configure("hl_good", foreground="#1f9d55", font=("맑은 고딕", 9, "bold"))
        self.deep_tv.tag_configure("hl_bad",  foreground="#d6311a", font=("맑은 고딕", 9, "bold"))

        # 추이 그래프 카드 2개 (순주문 / 공헌)
        self.deep_chart_order_card = tk.Frame(inner, bg="white", highlightthickness=1,
                                              highlightbackground="#dde2eb")
        self.deep_chart_order_card.pack(fill="x", padx=14, pady=(0, 10))
        tk.Label(self.deep_chart_order_card, text="📈 순주문 효율 추이  (전체 순달 vs 생방 순달)",
                 font=("맑은 고딕", 10, "bold"), bg="white", fg="#1a3a6e",
                 anchor="w").pack(fill="x", padx=12, pady=(10, 2))
        self.deep_chart_order_body = tk.Frame(self.deep_chart_order_card, bg="white")
        self.deep_chart_order_body.pack(fill="x", padx=6, pady=(0, 8))

        self.deep_chart_profit_card = tk.Frame(inner, bg="white", highlightthickness=1,
                                               highlightbackground="#dde2eb")
        self.deep_chart_profit_card.pack(fill="x", padx=14, pady=(0, 10))
        tk.Label(self.deep_chart_profit_card, text="📈 공헌 효율 추이  (전체 공달 vs 생방 공달)",
                 font=("맑은 고딕", 10, "bold"), bg="white", fg="#1a3a6e",
                 anchor="w").pack(fill="x", padx=12, pady=(10, 2))
        self.deep_chart_profit_body = tk.Frame(self.deep_chart_profit_card, bg="white")
        self.deep_chart_profit_body.pack(fill="x", padx=6, pady=(0, 8))

        self._deep_chart_widgets = []

        self.deep_status = tk.StringVar(value="RPA(1차) 파일을 선택하고 [분석 시작]을 클릭하세요. (2025-01 ~ 최신월 전체 기간 사용)")
        tk.Label(root, textvariable=self.deep_status, bg="#eef1f6",
                 fg="#666", font=("맑은 고딕", 8), wraplength=720, justify="left").grid(
            row=4, column=0, columnspan=3, pady=(0, 8))

        self._deep_df = None
        self._deep_brands_cache = []
        self._deep_auto_find()

    def _deep_auto_find(self):
        today = datetime.today().date()
        p = find_latest_file(FILE_DIR, 'RPA_총괄장(1차)', today)
        if p:
            self.deep_path.set(p)
            self.deep_status.set("오늘 파일 자동 감지 ✅  — [분석 시작]을 클릭하세요.")

    def _deep_pick(self):
        p = filedialog.askopenfilename(title="RPA_총괄장(1차) 파일 선택",
                                       filetypes=[("CSV", "*.csv"), ("모든 파일", "*.*")],
                                       initialdir=FILE_DIR)
        if p:
            self.deep_path.set(p)

    def _deep_run(self):
        if not self.deep_path.get():
            messagebox.showwarning("파일 미선택", "RPA(1차) 파일을 선택해주세요.")
            return
        self.deep_btn_load.configure(state="disabled")
        self.deep_status.set("데이터 로딩 중...")
        threading.Thread(target=self._deep_worker, daemon=True).start()

    def _deep_worker(self):
        try:
            df = load_deep_df(self.deep_path.get())
            self._deep_df = df
            self.after(0, self._deep_after_load)
        except Exception as e:
            self.after(0, lambda: self.deep_status.set("❌ 오류 발생"))
            self.after(0, lambda: messagebox.showerror("오류", str(e)))
        finally:
            self.after(0, lambda: self.deep_btn_load.configure(state="normal"))

    def _deep_after_load(self):
        df = self._deep_df
        if df is None or df.empty:
            self.deep_status.set("⚠ 식품사업부 4개 팀 데이터가 없습니다. 파일을 확인해주세요.")
            return
        months = sorted(df['YM'].dropna().unique())
        if not months:
            self.deep_status.set("⚠ 기준일자에서 연월을 추출하지 못했습니다.")
            return
        self.deep_month_cb.configure(values=list(months))
        if self.deep_month_var.get() not in months:
            self.deep_month_var.set(months[-1])   # 기본: 최신월
        self._deep_refresh_brands()
        self.deep_status.set(f"✅ 로딩 완료 — 기간 {months[0]} ~ {months[-1]}, 총 {len(df):,}행")
        self._deep_render()

    def _deep_selected_teams(self):
        if self.deep_team_vars["전체"].get():
            return DEEP_TEAMS, True
        teams = [t for t in DEEP_TEAMS if self.deep_team_vars[t].get()]
        if not teams:
            return DEEP_TEAMS, True
        return teams, False

    def _deep_on_team_toggle(self, key):
        if key == "전체":
            if self.deep_team_vars["전체"].get():
                for t in DEEP_TEAMS:
                    self.deep_team_vars[t].set(False)
            else:
                # 전체 해제만 하고 팀 미선택이면 다시 전체로 복귀
                if not any(self.deep_team_vars[t].get() for t in DEEP_TEAMS):
                    self.deep_team_vars["전체"].set(True)
        else:
            if self.deep_team_vars[key].get():
                self.deep_team_vars["전체"].set(False)
            elif not any(self.deep_team_vars[t].get() for t in DEEP_TEAMS):
                self.deep_team_vars["전체"].set(True)
        self._deep_refresh_brands()
        self._deep_render()

    def _deep_refresh_brands(self):
        """팀 선택에 맞춰 브랜드 슬라이서 표시/목록 갱신 (전체 선택 시 숨김)"""
        teams, is_all = self._deep_selected_teams()
        if is_all:
            self.deep_brand_frame.grid_remove()
            return
        self.deep_brand_frame.grid()
        if self._deep_df is None:
            return
        d = self._deep_df[self._deep_df['팀명'].isin(teams)]
        brands = sorted(str(b) for b in d['브랜드명'].dropna().unique())
        items = ["(전체)"] + brands
        if items != self._deep_brands_cache:
            self._deep_brands_cache = items
            self.deep_brand_list.delete(0, "end")
            for b in items:
                self.deep_brand_list.insert("end", b)
            self.deep_brand_list.selection_set(0)

    def _deep_selected_brands(self):
        """None = 전체 브랜드, list = 선택 브랜드"""
        teams, is_all = self._deep_selected_teams()
        if is_all:
            return None
        sel = self.deep_brand_list.curselection()
        if not sel or 0 in sel:
            return None
        return [self.deep_brand_list.get(i) for i in sel]

    def _deep_render(self):
        if self._deep_df is None or self._deep_df.empty:
            return
        teams, is_all = self._deep_selected_teams()
        brands = self._deep_selected_brands()
        sel_ym = self.deep_month_var.get()

        d_all = deep_filter(self._deep_df, teams, brands)   # 전체 기간 (추이용)
        d_month = d_all[d_all['YM'] == sel_ym]              # 기준연월 (테이블/요약용)

        team_label = "전체" if is_all else ", ".join(teams)
        brand_label = "" if not brands else f"  |  브랜드: {', '.join(brands[:5])}{' 외' if len(brands) > 5 else ''}"

        # ── 요약
        self.deep_sum_title.configure(text=f"📌 선택 조건 요약  —  {sel_ym}  |  팀: {team_label}{brand_label}")
        if d_month.empty:
            self.deep_sum_label.configure(text="선택한 조건에 해당하는 데이터가 없습니다.")
        else:
            s = deep_month_summary(d_month)
            self.deep_sum_label.configure(
                text=f"방송 {s['횟수']}회   |   매출 {s['매출']}억 / 공헌 {s['공헌']}억   |   "
                     f"전체 [순{s['순달_전체']}% - 공{s['공달_전체']}%]   |   "
                     f"생방 [순{s['순달_생방']}% - 공{s['공달_생방']}%]   ※ 심야 제외")

        # ── 협력사·브랜드 테이블
        self.deep_tbl_title.configure(text=f"📌 협력사·브랜드별 달성률  ({sel_ym} 기준, 순달 내림차순)")
        rows = deep_partner_table(d_month)
        self.deep_tv.delete(*self.deep_tv.get_children())
        for i, r in enumerate(rows):
            if r["순달"] >= 100:
                tags = ("hl_good",)
            elif r["순달"] < 70:
                tags = ("hl_bad",)
            else:
                tags = ()
            self.deep_tv.insert("", "end", iid=str(i),
                                values=(r["partner"], r["brand"], f"{r['count']}회",
                                        f"{r['순달']}%", f"{r['공달']}%"),
                                tags=tags)
        self.deep_tv.configure(height=max(1, min(len(rows), 25)))

        # ── 추이 그래프 (전체 기간)
        trend = deep_month_rates(d_all)
        for w in self._deep_chart_widgets:
            w.destroy()
        self._deep_chart_widgets = []

        if trend:
            months = [t["ym"] for t in trend]
            w1 = self._deep_make_chart(self.deep_chart_order_body, months,
                                       [t["순달_전체"] for t in trend],
                                       [t["순달_생방"] for t in trend],
                                       "순달(전체)", "순달(생방)", sel_ym)
            w1.pack(fill="x", expand=True)
            self._deep_chart_widgets.append(w1)

            w2 = self._deep_make_chart(self.deep_chart_profit_body, months,
                                       [t["공달_전체"] for t in trend],
                                       [t["공달_생방"] for t in trend],
                                       "공달(전체)", "공달(생방)", sel_ym)
            w2.pack(fill="x", expand=True)
            self._deep_chart_widgets.append(w2)

    def _deep_make_chart(self, parent, months, total, live, name_total, name_live, sel_ym=None):
        """캡쳐 디자인 기반: 연녹색 면적(전체) + 진녹색 선(생방), 포인트 % 라벨"""
        n = len(months)
        fig_w = max(7.2, min(13.0, 1.05 * n + 2.2))
        fig = Figure(figsize=(fig_w, 3.0), dpi=90, facecolor="white")
        ax = fig.add_subplot(111)
        x = list(range(n))

        vals = [v for v in (total + live) if v is not None]
        lo = min(vals) if vals else 0
        hi = max(vals) if vals else 100
        ymin = max(0, int(lo // 10 * 10) - 10)
        ymax = int(-(-hi // 10) * 10) + 10

        # 전체: 연녹색 면적 + 선
        ax.fill_between(x, total, ymin, color=DEEP_C_TOTAL_FILL, alpha=0.9, zorder=1)
        ax.plot(x, total, color=DEEP_C_TOTAL_LINE, lw=2.0, zorder=2,
                solid_capstyle="round")
        # 생방: 진녹색 면적(반투명) + 굵은 선
        ax.fill_between(x, live, ymin, color=DEEP_C_LIVE_FILL, alpha=0.45, zorder=3)
        ax.plot(x, live, color=DEEP_C_LIVE_LINE, lw=2.6, zorder=4,
                solid_capstyle="round")

        # 포인트 % 라벨 (겹치면 생방 라벨을 아래로)
        for i, (tv, lv) in enumerate(zip(total, live)):
            ax.annotate(f"{tv}%", (i, tv), textcoords="offset points", xytext=(0, 8),
                        ha="center", fontsize=8.5, fontweight="bold", color=DEEP_C_LABEL, zorder=5)
            dy = -15 if abs(tv - lv) < (ymax - ymin) * 0.06 else 8
            ax.annotate(f"{lv}%", (i, lv), textcoords="offset points", xytext=(0, dy),
                        ha="center", fontsize=8.5, fontweight="bold", color=DEEP_C_LIVE_LINE, zorder=5)

        # 선택 기준연월 표시 (세로 점선)
        if sel_ym in months:
            xi = months.index(sel_ym)
            ax.axvline(xi, color="#b9c2ce", lw=0.9, ls="--", zorder=0)

        ax.set_xticks(x)
        ax.set_xticklabels([ym_label(m) for m in months], fontsize=8.5, color="#4a4f57")
        ax.set_ylim(ymin, ymax)
        yticks = list(range(ymin, ymax + 1, 20))
        ax.set_yticks(yticks)
        ax.set_yticklabels([f"{v}%" for v in yticks], fontsize=8.5, color="#4a4f57")
        ax.grid(axis="y", color="#e8ebee", ls=":", lw=0.9, zorder=0)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color("#d4d9df")
        ax.tick_params(length=0)
        ax.margins(x=0.02)

        # 범례 (캡쳐처럼 좌상단 · 마커)
        handles = [
            Line2D([0], [0], marker='o', color='none', markerfacecolor=DEEP_C_TOTAL_LINE,
                   markersize=9, label=name_total),
            Line2D([0], [0], marker='o', color='none', markerfacecolor=DEEP_C_LIVE_LINE,
                   markersize=9, label=name_live),
        ]
        ax.legend(handles=handles, loc="upper left", frameon=False, fontsize=9,
                  handletextpad=0.3, borderaxespad=0.1, ncol=2, bbox_to_anchor=(0, 1.16))

        fig.subplots_adjust(left=0.06, right=0.99, top=0.84, bottom=0.14)
        canvas_widget = FigureCanvasTkAgg(fig, master=parent)
        canvas_widget.draw()
        return canvas_widget.get_tk_widget()


if __name__ == "__main__":
    app = App()
    app.mainloop()
