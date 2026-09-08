# -*- coding: utf-8 -*-
"""
브랜드 정제/추론 로직 테스트 (pytest 없이 그냥 실행: python tools/test_brand_infer.py)

정정 제보로 드러난 오분류를 회귀 테스트로 고정한다.
  롯데 9/10 09:25 "포트메리온 뉴베리에이션 2인조 홈세트 10P"
  -> 브랜드가 "백화점가 46만원"으로 저장되고 카테고리도 '일반식품'으로 오분류
     (상품명 앞 대괄호 접두어를 그대로 브랜드로 썼기 때문)

네트워크 없이 롯데 API 응답을 흉내 낸 가짜 세션으로 수집 경로까지 검증한다.
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from infer_brand import infer_brand, is_marketing_copy, pick_brand_from_prefix
import lt_scraper

FAILS = []


def check(label, got, expected):
    ok = got == expected
    print(("  OK   " if ok else "  FAIL ") + f"{label}: {got!r}" + ("" if ok else f" (기대: {expected!r})"))
    if not ok:
        FAILS.append(label)


def test_marketing_copy():
    print("[1] 마케팅 카피 판별")
    for text in ["백화점가 106만원", "정상가 247,000원", "상시가 479,000원",
                 "런칭가 109,000원", "SALE", "기획특가", "1박스", "6개월",
                 "대용량", "단품", "화이트", "블랙", "4종 대용량세트",
                 "롯데 단독", "공식수입정품", "이태리 직수입"]:
        check(f"'{text}' 는 카피", is_marketing_copy(text), True)
    for text in ["포트메리온", "칼만", "코렐", "에스까다스포츠", "닥터오기덤",
                 "국내산 절단꽃게", "브라운", "린", "3.1 필립림 스튜디오"]:
        check(f"'{text}' 는 브랜드", is_marketing_copy(text), False)


def test_prefix_brand():
    print("[2] 대괄호 접두어에서 브랜드 고르기")
    check("안내문 접두어 건너뛰기",
          pick_brand_from_prefix("[백화점가 106만원][포트메리온] 뉴베리에이션 4인조 홈세트 23P"),
          "포트메리온")
    check("접두어가 없으면 본문에서 추론",
          pick_brand_from_prefix("[정상가 247,000원] 칼만 블랙 통5중 IH 저압냄비"),
          "칼만")
    check("색상 접두어는 브랜드 아님",
          pick_brand_from_prefix("[화이트] 무선 전동 그라인더 2.0 풀세트"), "")
    check("정상 접두어는 그대로",
          pick_brand_from_prefix("[아로마티카] 스파 샴푸 1등 패키지"), "아로마티카")


def test_infer_boundary():
    print("[3] 단어 중간 오매칭 방지")
    check("뉴베리에이션 -> 베리에 아님", infer_brand("뉴베리에이션 4인조 홈세트 23P"), "")
    check("에어프라이어 -> 프라이 아님", infer_brand("코렐 일렉 에어글라스 에어프라이어"), "코렐")
    check("프로폴리스 -> 폴리스 아님", infer_brand("프로폴리스 가글 오리지널 유자"), "")
    check("트라이탄보다 고트만",
          infer_brand("NEW 고트만 네오 크리스탈락 트라이탄 밀폐용기 세트"), "고트만")
    check("연도는 브랜드 아님", infer_brand("2026 아디다스 뉴 컴포트 드로즈 패키지"), "아디다스")
    print("[4] 기존 매칭 유지")
    check("붙여쓴 브랜드", infer_brand("닥터린콘드로이친MBP 초임계 비타민K2"), "닥터린")
    check("공백 차이", infer_brand("[LIVE]라이나 생명 The건강한치아보험V"), "라이나생명")
    check("공백 차이2", infer_brand("강릉 세인트존스 호텔"), "세인트존스호텔")
    check("맨 앞 브랜드", infer_brand("LG 통돌이 세탁기 T19MX7A 미드 블랙"), "LG(엘지)")


FAKE_PAYLOAD = {
    "body": {"prod": [{
        "sdate": "2026-09-10", "stime": "09:25", "etime": "11:35",
        "bdPrgmId": 111, "brand": "메종프라질",
        "name": "메종라귀올 커트러리 4인조 세트", "price_disc": "159,000",
        "linkInfo": "/goods/viewGoodsDetail.lotte?goods_no=1",
        "pgmMap": {"titNm": "더퍼스트 유난희"},
        "related": [
            {"name": "[백화점가 46만원][포트메리온] 뉴베리에이션 2인조 홈세트 10P",
             "goodsNo": "2", "price_disc": "359,000",
             "linkInfo": "/goods/viewGoodsDetail.lotte?goods_no=2"},
            {"name": "[화이트] 메종라귀올 커트러리 4인조 세트",
             "goodsNo": "3", "price_disc": "159,000",
             "linkInfo": "/goods/viewGoodsDetail.lotte?goods_no=3"},
        ],
    }]}
}


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return FAKE_PAYLOAD


def test_fetch_lotte():
    print("[5] 롯데 수집 경로 (가짜 응답)")
    original_get = requests.get
    requests.get = lambda *a, **k: FakeResponse()
    try:
        programs = lt_scraper.fetch_lotte("20260910", "2026-09-10", "scheduleLive")
    finally:
        requests.get = original_get

    brands = [p["brand"] for p in programs]
    check("브랜드 목록", brands, ["메종프라질", "포트메리온"])
    check("색상 중복 상품 제외", len(programs), 2)

    lt_scraper.add_categories(programs)
    portmeirion = programs[1]
    check("상품명 정제", portmeirion["product"], "뉴베리에이션 2인조 홈세트 10P")
    check("카테고리", portmeirion["category"], "리빙/주방")


if __name__ == "__main__":
    test_marketing_copy()
    test_prefix_brand()
    test_infer_boundary()
    test_fetch_lotte()
    print()
    if FAILS:
        print(f"실패 {len(FAILS)}건: {FAILS}")
        sys.exit(1)
    print("전부 통과")
