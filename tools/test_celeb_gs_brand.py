# -*- coding: utf-8 -*-
"""
셀럽PGM GS 브랜드 추론 테스트 (pytest 없이 그냥 실행:
python tools/test_celeb_gs_brand.py)

GS 상품 상세의 og:title은 "[GS SHOP] 상품명" 꼴이라 대괄호 안이 브랜드가
아니다. 그래서 GS 셀럽PGM(지금 백지연/소유진쇼)은 전 상품 브랜드가 'GS SHOP'
자리표시자로 저장됐고 화면은 그걸 숨겨 상품명만 보여줬다. 셀럽PGM 간 중복
브랜드 비교를 위해 자리표시자일 때만 상품명에서 브랜드를 추론하고, 못 찾으면
자리표시자를 그대로 둔다(지금처럼 상품명만 노출).

[2] 수집 경로 테스트는 fixed/regs.py를 읽으므로 bs4가 필요하다. 없으면 그
부분만 건너뛰고 알린다 (PYTHONPATH=<bs4 경로> python tools/test_celeb_gs_brand.py).
"""

import os
import sys
import json
import shutil
import tempfile
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "fixed"))

from celeb_brand import (  # noqa: E402
    GS_PLACEHOLDER, resolve_gs_brand, infer_celeb_brand, recompute_gs_brand)

_spec = importlib.util.spec_from_file_location(
    "_backfill", os.path.join(ROOT, "tools", "backfill_celeb_gs_brand.py"))
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)

FAILS = []


def check(label, got, expected):
    ok = got == expected
    print(("  OK   " if ok else "  FAIL ") + f"{label}: {got!r}" + ("" if ok else f" (기대: {expected!r})"))
    if not ok:
        FAILS.append(label)


# ---------------------------------------------------------------- [1] 규칙
def test_resolve():
    print("[1] 자리표시자일 때만 상품명에서 추론")
    check("사전에 있는 브랜드로 시작",
          resolve_gs_brand("GS SHOP", "정관장 홍삼정 센스 2박스(60포)"), "정관장")
    check("대괄호 안 브랜드",
          resolve_gs_brand("GS SHOP", "[미세스문] LK_코튼텐셀 침구 풀세트"), "미세스문")
    check("사전 표기의 괄호 부기는 떼고 저장",
          resolve_gs_brand("GS SHOP", "LG 통돌이 세탁기 T19MX7A 미드 블랙"), "LG")
    check("추론 실패 -> 자리표시자 유지",
          resolve_gs_brand("GS SHOP", "키직 26FW 베가스2 소가죽 스니커즈 여성용"), GS_PLACEHOLDER)
    check("공백/대소문자 다른 자리표시자도 인식",
          resolve_gs_brand("gs shop", "정관장 홍삼정 센스 2박스(60포)"), "정관장")
    check("이미 브랜드가 있으면 그대로",
          resolve_gs_brand("포트메리온", "정관장 홍삼정 센스 2박스(60포)"), "포트메리온")
    check("빈 브랜드 + 추론 실패 -> 빈 값 유지 (편성표로 채운 상품)",
          resolve_gs_brand("", "키직 26FW 아테네2 워킹화 여성용"), "")
    check("상품명 없음 -> 자리표시자 유지",
          resolve_gs_brand("GS SHOP", ""), GS_PLACEHOLDER)


def test_infer_rules():
    print("[1b] 추론 규칙: 맨 앞 브랜드 우선 / 단어 시작 위치만 / 맨 앞 대괄호 브랜드 / 표기 통합")
    cases = [
        ("맨 앞 브랜드 우선 (뒤에 붙은 사은품 브랜드가 더 길어도)",
         "비트 캡슐세제 몬스터팟 10X 삶음파워 유칼립투스러쉬 25입 파우치 2개 + 휴족시간 6매입 + 아이깨끗해 순 250ml용기",
         "비트"),
        ("안내 대괄호 뒤의 맨 앞 브랜드",
         "[소유진패키지]비트 캡슐세제 몬스터팟 10X 냄새케어 자스민웨이브 25입 용기 1개 + 아이깨끗해 용기1(+리필2)",
         "비트"),
        ("맨 앞의 긴 브랜드는 그대로",
         "오로 델 데시에르토 엑스트라버진 올리브오일 쿠파주 500ml*2병", "오로 델 데시에르토"),
        ("맨 앞이 아니어도 단어 시작이면 인정", "덴마크 스칸팬 프라이팬 4종+팬프로텍터 3종", "스칸팬"),
        ("뒤쪽에 있는 단어 시작 브랜드",
         "GS단독 한박스 더 한국인의 두유 무가당 국산 서리태 콩물두유 3박스(60팩)", "한국인의 두유"),
        ("단어 중간 매칭 거부 (14브릭스)",
         "오즈베 14브릭스 고당도 경북 햇 정품부사 로얄과 2kg(7~8과) 산지직송 사과", ""),
        ("맨 앞 대괄호 브랜드 (사전에 없음)", "[파이토리진] 유기농 야생 빌베리 6박스", "파이토리진"),
        ("맨 앞 대괄호 브랜드가 본문 브랜드보다 우선",
         "[HL사이언스] 닥터슈퍼칸 레이디 30캡슐*6박스", "HL사이언스"),
        ("대괄호 안에 사전 브랜드가 들어 있으면 그걸로", "[조선호텔김치] 조선호텔 포기김치 8kg", "조선호텔"),
        ("안내 대괄호는 건너뛰고 본문",
         "[GS단독] 정관장 홍삼진고 데일리스틱 6+4박스(300포) + 목어깨마사지기", "정관장"),
        ("'GS ONLY'는 브랜드 아님", "[GS ONLY]정관장 홍삼정 마일드 센스 3박스", "정관장"),
        ("프로그램명 대괄호는 브랜드 아님",
         "[소유진쇼] 코렐 일렉 에어글라스 에어프라이어 PRO 소쇼패키지", "코렐"),
        ("'공식'은 브랜드 아님, 샤크는 사전에 없음",
         "[공식] 샤크 무선청소기 파워클린 자동먼지비움 BU3641KR", ""),
        ("원산지 괄호는 브랜드 아님", "(국산)골프스킨 자외선차단 무봉제쿨마스크", "골프스킨"),
        ("안내 소괄호 건너뛰기", "(방송중에만)베키아에누보 샌드위치 총 21개입", "베키아에누보"),
        ("'올해단한번'은 브랜드 아님",
         "[올해단한번]  다비네스 리밸런싱샴푸3개+트리트먼트2개 단독구성", "다비네스"),
        ("표기 통합 VASAK -> 바삭", "VASAK 바삭 김부각 시그니처 연근참깨 김부각", "바삭"),
        ("사전에 없으면 빈 값", "키직 26FW 베가스2 소가죽 스니커즈 여성용", ""),
        ("브랜드 없는 상품", "국산 서리태 100% 무가당 국산 서리태 콩물두유 1박스(20팩)*3박스 총 60팩", ""),
    ]
    for label, name, want in cases:
        check(label, infer_celeb_brand(name), want)

    print("[1c] 소급 재계산: GS 기록의 브랜드는 전부 추론값이라 다시 계산한다")
    check("1단계 오인식 정정 (아이깨끗해 -> 비트)",
          recompute_gs_brand("아이깨끗해", "비트 캡슐세제 몬스터팟 10X 25입 파우치 2개 + 아이깨끗해 순 250ml용기"),
          "비트")
    check("오인식인데 대신할 브랜드가 없으면 자리표시자로 되돌림",
          recompute_gs_brand("브릭스", "오즈베 14브릭스 고당도 경북 햇 정품부사 로얄과 2kg"), GS_PLACEHOLDER)
    check("자리표시자 + 추론 실패 -> 자리표시자", recompute_gs_brand("GS SHOP", "키직 26FW 아테네2 워킹화"), GS_PLACEHOLDER)
    check("빈 브랜드(편성표로 채운 상품) + 추론 실패 -> 빈 값", recompute_gs_brand("", "키직 26FW 아테네2 워킹화"), "")
    check("빈 브랜드 + 추론 성공", recompute_gs_brand("", "정관장 홍삼정 센스 2박스(60포)"), "정관장")


# ---------------------------------------------------------------- [2] 수집 경로
FAKE_HTML = """<html><head>
<meta property="og:title" content="[GS SHOP] %s">
<script type="application/ld+json">{"@type":"Product","offers":{"price":"59000"}}</script>
</head><body></body></html>"""


class FakeResponse:
    status_code = 200

    def __init__(self, title):
        self.text = FAKE_HTML % title


def test_fetch_path():
    print("[2] regs.fetch_gs_product_details_fixed: og:title '[GS SHOP] 상품명' 에서 브랜드 추론")
    try:
        spec = importlib.util.spec_from_file_location(
            "_regs", os.path.join(ROOT, "fixed", "regs.py"))
        regs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(regs)
    except ImportError as e:
        print(f"  SKIP bs4/requests 없음 ({e}) - PYTHONPATH=<bs4 경로> 로 다시 실행")
        return

    def fake_get(url, headers=None, timeout=None):
        prd_id = url.rsplit("=", 1)[-1]
        return FakeResponse({
            "1": "정관장 홍삼정 센스 2박스(60포)",
            "2": "키직 26FW 베가스2 소가죽 스니커즈 여성용",
            "3": "[미세스문] LK_코튼텐셀 침구 풀세트",
        }[prd_id])

    original_get = regs.requests.get
    regs.requests.get = fake_get
    try:
        check("추론 성공", regs.fetch_gs_product_details_fixed("1"),
              ("정관장", "정관장 홍삼정 센스 2박스(60포)", 59000))
        check("추론 실패는 지금처럼 자리표시자 + 상품명",
              regs.fetch_gs_product_details_fixed("2"),
              ("GS SHOP", "키직 26FW 베가스2 소가죽 스니커즈 여성용", 59000))
        check("대괄호 안 브랜드는 상품명에 남기고 브랜드만 채움",
              regs.fetch_gs_product_details_fixed("3"),
              ("미세스문", "[미세스문] LK_코튼텐셀 침구 풀세트", 59000))
    finally:
        regs.requests.get = original_get


# ---------------------------------------------------------------- [3] 소급 적용
def gs_product(name, brand="GS SHOP"):
    return {"broadcast_date_label": "09/26(금) 20:35 방송", "brand": brand,
            "name": name, "price": None,
            "link": "https://m.gsshop.com/prd/prd.gs?prdid=1"}


def test_backfill_history():
    print("[3] 월 누적 파일: GS 프로그램의 자리표시자 브랜드만 채운다")
    data = {"month": "2026-09", "updated_at": "2026-09-30T03:00:00+09:00", "programs": [
        {"program_key": "GS_SYJ", "company": "GS", "broadcasts": [
            {"date": "2026-09-26", "label": "09/26(금) 20:35 방송", "products": [
                gs_product("정관장 홍삼정 센스 2박스(60포)"),
                gs_product("키직 26FW 베가스2 소가죽 스니커즈 여성용"),
                gs_product("[미세스문] LK_코튼텐셀 침구 풀세트", brand="미세스문"),
            ]},
            {"date": "2026-09-19", "label": "09/19(금) 휴방", "off_air": True, "products": []},
        ]},
        {"program_key": "CJ_CHJ", "company": "CJ", "broadcasts": [
            {"date": "2026-09-27", "label": "09/27(토) 08:20 방송", "products": [
                {"brand": "", "name": "정관장 홍삼정 에브리타임", "link": ""},
            ]},
        ]},
    ]}
    changed = backfill.backfill_data(data)
    products = data["programs"][0]["broadcasts"][0]["products"]
    check("바뀐 건수", changed, 1)
    check("추론 성공분 채움", products[0]["brand"], "정관장")
    check("추론 실패분은 자리표시자 유지", products[1]["brand"], "GS SHOP")
    check("이미 있는 브랜드는 그대로", products[2]["brand"], "미세스문")
    check("GS가 아닌 프로그램은 안 건드림",
          data["programs"][1]["broadcasts"][0]["products"][0]["brand"], "")
    check("수집 시각은 그대로 (수집한 척하지 않는다)",
          data["updated_at"], "2026-09-30T03:00:00+09:00")

    print("[3b] 월 누적 파일: 이미 채워진 추론 브랜드도 현재 규칙으로 다시 계산한다")
    data = {"programs": [{"program_key": "GS_SYJ", "company": "GS", "broadcasts": [
        {"date": "2026-08-01", "label": "08/01(금) 20:35 방송", "products": [
            gs_product("비트 캡슐세제 몬스터팟 10X 25입 파우치 2개 + 아이깨끗해 순 250ml용기", brand="아이깨끗해"),
            gs_product("오즈베 14브릭스 고당도 경북 햇 정품부사 로얄과 2kg", brand="브릭스"),
            gs_product("VASAK 바삭 김부각 시그니처 연근참깨 김부각", brand="VASAK"),
            gs_product("정관장 홍삼정 센스 2박스(60포)", brand="정관장"),
        ]}]}]}
    changed = backfill.backfill_data(data)
    products = data["programs"][0]["broadcasts"][0]["products"]
    check("바뀐 건수", changed, 3)
    check("정정된 브랜드", [p["brand"] for p in products], ["비트", "GS SHOP", "바삭", "정관장"])


def test_backfill_live_file():
    print("[4] 프로그램별 결과 파일(GS_*.json)도 같은 규칙")
    data = {"company": "GS", "tab_name": "소유진", "products": [
        gs_product("정관장 홍삼정 센스 2박스(60포)"),
        gs_product("키직 26FW 베가스2 소가죽 스니커즈 여성용"),
    ]}
    changed = backfill.backfill_data(data)
    check("바뀐 건수", changed, 1)
    check("브랜드", [p["brand"] for p in data["products"]], ["정관장", "GS SHOP"])

    print("[5] 파일 단위: 미리보기는 저장 안 함, --apply 만 저장")
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "GS_SYJ.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"company": "GS", "products": [gs_product("정관장 홍삼정 센스 2박스(60포)")]},
                      f, ensure_ascii=False)
        check("미리보기 건수", backfill.backfill_file(path, apply_changes=False), 1)
        check("미리보기는 파일 그대로",
              json.load(open(path, encoding="utf-8"))["products"][0]["brand"], "GS SHOP")
        check("적용 건수", backfill.backfill_file(path, apply_changes=True), 1)
        check("적용 후 파일",
              json.load(open(path, encoding="utf-8"))["products"][0]["brand"], "정관장")
        check("다시 돌리면 바뀔 게 없음", backfill.backfill_file(path, apply_changes=True), 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_resolve()
    test_infer_rules()
    test_fetch_path()
    test_backfill_history()
    test_backfill_live_file()
    print()
    if FAILS:
        print(f"실패 {len(FAILS)}건: " + ", ".join(FAILS))
        sys.exit(1)
    print("모두 통과")
