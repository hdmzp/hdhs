# -*- coding: utf-8 -*-
"""
CJ 고정PGM 키워드 테스트 (pytest 없이 그냥 실행:
python tools/test_cj_pgm_keywords.py)

지키려는 것:
  (A) 셀럽PGM 수집기(fixed/recj.py)에 등록된 프로그램은 편성표 수집
      (cj_scraper.CJ_PGM_KEYWORDS)에서도 반드시 고정PGM으로 인식될 것
  (B) 그 프로그램의 실제 편성표 프로그램명(homeshopping/fixed_programs/CJ.json의
      title)도 같은 키워드에 걸릴 것

왜 필요한가:
  CJ 편성표에 pgm이 안 붙으면 홈쇼핑 탭 뱃지만 빠지는 게 아니다.
  celeb_day_sweep.find_program_slots()가 편성표의 pgm으로 회차를 찾으므로
  셀럽PGM 탭의 '편성표 보강' 안전망까지 같이 죽는다.
  실제 사고: 2026-09-20 소이현의 겟잇스타일 20:30 회차 - pgmShop API가 그날
  회차를 아예 안 줬는데(09/21, 10/02만 줬다) 키워드에 "소이현"이 없어서
  편성표 보강도 못 돌았고, 홈쇼핑 탭 뱃지와 셀럽PGM 탭 회차가 통째로 비었다.
"""

import os
import ast
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_literal(relpath, name):
    """모듈을 import하지 않고 최상위 상수 값만 읽는다.
    cj_scraper는 categorize -> joblib(모델 의존성)까지 끌고 와서, 키워드 하나
    보자고 import하면 수집 환경 밖에서는 테스트가 아예 안 돈다."""
    with open(os.path.join(ROOT, relpath), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{relpath}에서 {name}을 못 찾음")


CJ_PGM_KEYWORDS = read_literal("cj_scraper.py", "CJ_PGM_KEYWORDS")
PROGRAMS = read_literal(os.path.join("fixed", "recj.py"), "PROGRAMS")

FAILURES = []


def check(label, got, want):
    if got == want:
        print(f"  OK   {label}")
    else:
        print(f"  FAIL {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


def is_fixed_pgm(pgm_nm):
    """cj_scraper.fetch_cj()의 판별식과 같은 식."""
    return any(k in pgm_nm for k in CJ_PGM_KEYWORDS)


def test_recj_programs_are_badged():
    """(A) recj.PROGRAMS의 프로그램명이 전부 고정PGM으로 인식돼야 한다."""
    print("[A] 셀럽PGM 등록 프로그램이 편성표 키워드에 걸리는가")
    for config in PROGRAMS:
        check(config["program_title"], is_fixed_pgm(config["program_title"]), True)


def test_fixed_program_titles_are_badged():
    """(B) 같은 프로그램의 실제 편성표 표기(fixed_programs/CJ.json)도 걸려야 한다.

    recj의 program_title은 우리가 붙인 이름이고, 편성표 pgmNm은 CJ 표기다
    ("소이현의 겟잇스타일"). 둘이 어긋나면 (A)만 통과하고 실제로는 안 걸린다.
    """
    print("[B] 실제 편성표 표기도 키워드에 걸리는가")
    path = os.path.join(ROOT, "homeshopping", "fixed_programs", "CJ.json")
    if not os.path.isfile(path):
        print("  SKIP fixed_programs/CJ.json 없음")
        return
    with open(path, encoding="utf-8") as f:
        titles = [p.get("title") or "" for p in json.load(f).get("programs") or []]

    for config in PROGRAMS:
        keywords = config.get("keywords") or ()
        matched = [t for t in titles if any(k in t for k in keywords)]
        if not matched:
            # 고정PGM 목록에 아직 안 올라온 신규 PGM일 수 있다 - (A)로 충분하다.
            print(f"  SKIP {config['program_title']}: 고정PGM 목록에 없음")
            continue
        for title in matched:
            check(f"{config['tab_name']} / {title}", is_fixed_pgm(title), True)


def main():
    test_recj_programs_are_badged()
    test_fixed_program_titles_are_badged()

    print()
    if FAILURES:
        print(f"실패 {len(FAILURES)}건: {', '.join(FAILURES)}")
        return 1
    print("모든 테스트 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
