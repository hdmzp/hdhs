# -*- coding: utf-8 -*-
"""
gs_fixed_programs.py
GS SHOP 모바일 "TV 대표 프로그램" 섹션에서 고정 편성 프로그램(진행자 쇼)
목록과, 각 프로그램이 다음 방송에서 소개할 상품까지 함께 수집한다.

(업데이트 - 2026-06, 전면 재작성: Selenium 제거)
이전 버전은 모바일 메인페이지(m.gsshop.com/index.gs)를 Selenium으로 열고
"TV" 탭을 자바스크립트로 클릭해 #tv-sect-pgm2 섹션이 나타나길 기다리는
방식이었다. 실제로 원본 HTML을 까본 결과, 메인페이지(index.gs)에는
대표프로그램 마크업이 처음부터 전혀 없고, GNB 데이터(헤더에 내려오는
gnbSectionList) 안에 "TV" 탭의 실제 목적지가 별도 경로로 박혀 있었다:

    {"id": 618, "name": "TV", "ajaxUrl": "/main/tvHighlight?mseq=W00618"}

즉 "TV" 메뉴는 같은 페이지 안에서 갱신되는 SPA 탭이 아니라, 처음부터
별도 페이지(/main/tvHighlight)로 통째로 이동하는 구조였다. 그 페이지를
requests로 직접 받아본 결과 정적 HTML 안에 대표프로그램 슬라이드
8개(.item-head, h3.ttl-lg, article.ban-item, article.prd-item 등)가
그대로 들어있는 것이 확인되어, Selenium 없이 이 페이지 하나만 받으면
충분하다. 마크업 구조 자체(셀렉터)는 이전 버전과 동일하게 유지한다.

== 출력 ==
homeshopping/fixed_programs/GS.json
{
  "company": "GS",
  "collectedAt": "...",
  "programs": [
    {
      "title": "프로그램명",
      "schedule_raw": "...",
      "desc": "...",
      "thumbnail": "https://...",
      "detail_link": "https://m.gsshop.com/...",
      "upcoming_products": [
        {"name": "...", "price": 12345, "image": "https://...", "link": "https://..."},
        ...
      ]
    },
    ...
  ]
}

== 사용법 ==
  pip install requests beautifulsoup4
  python gs_fixed_programs.py
"""

import os
import re
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
OUTPUT_DIR = os.path.join("homeshopping", "fixed_programs")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "GS.json")

# GNB의 "TV" 탭이 실제로 가리키는 경로. 메인페이지(index.gs)가 아니라
# 이 경로에 대표프로그램(#tv-sect-pgm2) 마크업이 정적으로 내려온다.
GS_TV_HIGHLIGHT_URL = "https://m.gsshop.com/main/tvHighlight?mseq=W00618"

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; SM-S918N) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)
REQUEST_TIMEOUT_SEC = 15


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": MOBILE_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://m.gsshop.com/index.gs",
    })
    return session


def parse_price(text: str):
    if not text:
        return None
    cleaned = re.sub(r'[^\d]', '', text)
    return int(cleaned) if cleaned else None


def to_https(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    return url


def to_absolute(url: str) -> str:
    if not url:
        return ""
    if url.startswith("/"):
        return "https://m.gsshop.com" + url
    return to_https(url)


def fetch_tv_highlight_html(session: requests.Session) -> str:
    resp = session.get(GS_TV_HIGHLIGHT_URL, timeout=REQUEST_TIMEOUT_SEC)
    resp.raise_for_status()
    return resp.text


def describe_markup(soup) -> None:
    """대표프로그램을 못 찾았을 때, 어떤 페이지가 내려왔는지 단서를 남긴다."""
    title = soup.select_one("title")
    print(f"  [GS] [진단] <title>: {title.get_text(strip=True) if title else '(없음)'}")

    section_ids = [
        el.get("id") for el in soup.select("[id]")
        if el.get("id") and re.search(r"(pgm|tv|program)", el.get("id"), re.I)
    ]
    print(f"  [GS] [진단] pgm/tv 관련 id: {section_ids[:15] or '(없음)'}")

    for sel in (".swiper-slide", ".item-head", "h3.ttl-lg",
                "article.ban-item", "article.prd-item"):
        print(f"  [GS] [진단] '{sel}' 개수: {len(soup.select(sel))}")


def parse_programs(html: str) -> list:
    programs = []
    soup = BeautifulSoup(html, "html.parser")

    section = soup.select_one("#tv-sect-pgm2")
    if not section:
        # 컨테이너 id만 바뀐 경우까지 죽지 않도록, 문서 전체에서 슬라이드를 찾는
        # 폴백을 시도한다. 아래 루프가 .item-head + 제목 없는 슬라이드는
        # 어차피 걸러내므로 무관한 슬라이드가 섞여도 결과에 들어오지 않는다.
        print("  [GS] [경고] '#tv-sect-pgm2' 컨테이너를 찾을 수 없습니다. "
              "문서 전체에서 대표프로그램 슬라이드를 찾아봅니다.")
        section = soup

    # 중복된 가짜 슬라이드(swiper-slide-duplicate) 제외하고 진짜 프로그램만
    slides = section.select(".swiper-slide:not(.swiper-slide-duplicate)")

    for slide in slides:
        head = slide.select_one(".item-head")
        if not head:
            continue

        title_tag = head.select_one("h3.ttl-lg")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            continue

        schedule_tag = head.select_one("sup.color-primary")
        schedule_raw = schedule_tag.get_text(strip=True) if schedule_tag else ""

        desc_tag = head.select_one("sub.desc-md")
        desc = desc_tag.get_text(separator=" ", strip=True) if desc_tag else ""

        ban_img_tag = slide.select_one("article.ban-item figure.ban-img img")
        thumbnail = to_https(ban_img_tag.get("src")) if ban_img_tag else ""

        ban_link_tag = slide.select_one("article.ban-item a.ban-link")
        detail_link = to_absolute(ban_link_tag.get("href")) if ban_link_tag else ""

        upcoming_products = []
        for prd in slide.select("article.prd-item"):
            name_tag = prd.select_one(".prd-name")
            name = name_tag.get_text(strip=True) if name_tag else ""
            if not name:
                continue

            price_tag = prd.select_one(".set-price strong")
            price = parse_price(price_tag.get_text(strip=True)) if price_tag else None

            img_tag = prd.select_one("figure.prd-img img")
            image = to_https(img_tag.get("src")) if img_tag else ""

            link_tag = prd.select_one("a.prd-link")
            link = to_absolute(link_tag.get("href")) if link_tag else ""

            upcoming_products.append({
                "name": name,
                "price": price,
                "image": image,
                "link": link,
            })

        programs.append({
            "title": title,
            "schedule_raw": schedule_raw,
            "desc": desc,
            "thumbnail": thumbnail,
            "detail_link": detail_link,
            "upcoming_products": upcoming_products,
        })

    return programs


def load_existing_programs() -> list:
    """이미 저장돼 있는 GS.json의 programs. 없거나 깨졌으면 빈 리스트."""
    try:
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        programs = data.get("programs")
        return programs if isinstance(programs, list) else []
    except (OSError, ValueError):
        return []


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("[GS] TV 대표프로그램 수집 중...")
    session = make_session()

    html = ""
    try:
        html = fetch_tv_highlight_html(session)
    except Exception as e:
        print(f"  [실패] {GS_TV_HIGHLIGHT_URL} 호출: {e}")

    programs = parse_programs(html) if html else []

    # 수집 0개인데 파일을 덮어쓰면 멀쩡하던 기존 데이터까지 날아가고,
    # 화면에서는 GS만 통째로 사라진다. 수집 실패는 데이터 유실 사유가 아니다.
    if not programs:
        if html:
            describe_markup(BeautifulSoup(html, "html.parser"))
        kept = load_existing_programs()
        if kept:
            print(f"\n[GS] 수집 0개 -> 기존 데이터 {len(kept)}개를 그대로 유지합니다 "
                  f"(덮어쓰지 않음): {OUTPUT_PATH}")
        else:
            print(f"\n[GS] 수집 0개, 유지할 기존 데이터도 없습니다: {OUTPUT_PATH}")
        sys.exit(1)

    payload = {
        "company": "GS",
        "collectedAt": datetime.now(KST).isoformat(),
        "programs": programs,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n총 {len(programs)}개 프로그램 수집 완료, 저장: {OUTPUT_PATH}")
    for p in programs:
        print(f"  [{p['title']}] {p['schedule_raw']} (소개상품 {len(p['upcoming_products'])}개)")


if __name__ == "__main__":
    main()
