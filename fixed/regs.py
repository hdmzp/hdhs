# -*- coding: utf-8 -*-
"""
gs_bjy_representative.py
GS SHOP 대표 PGM '지금 백지연'의 '방송예정' 상품을 방송일시 단위로 정확히 그룹핑해서 수집한다.

== DOM 구조 (실측) ==
<section class="item-module__type4" data-mseq ...>       <- 방송 1타임 모듈
  <article class="ban-item" data-broaddate="20260716204500">...</article>   <- 배너(날짜 라벨), 형제 요소
  <section class="prd-swiper horizon" ...>
    <div class="swiper-wrapper">
      <div class="swiper-slide">
        <article class="prd-item ..." data-prdcd="...">
          ...
          <a class="prd-link" href="/prd/prd.gs?prdid=...">...</a>
        </article>
      </div>
    </div>
  </section>
</section>

시행착오 기록:
1차: h2.ttl 헤더 텍스트로 날짜 추적 -> 상품마다 다른 실제 방송일시를 뭉뚱그림 (부정확)
2차: ban-item이 prd-swiper를 감싸는 부모인 줄 알고 ban-item 내부에서만 prd-link를 찾음
     -> ban-item은 prd-swiper의 부모가 아니라 "형제"였음 (배너만 담고 있고 상품은 안 담음)
     -> 상품 0개 수집되는 버그
3차(현재): 부모 컨테이너 section.item-module__type4를 단위로 묶어서,
     그 안의 ban-item[data-broaddate]에서 날짜를, 그 안의 a.prd-link 전체에서 상품을 같이 추출.
     ban-item이 없는 item-module__type4(인기상품/스토리 등 다른 모듈)는 자동으로 스킵됨.

== 지난방송(놓친방송) 처리 ==
'놓친 방송 다시보기' 섹션은 id="LAST" 컨테이너로 감싸져 있어서 스캔 전에
decompose()로 통째로 제거함 (이중 안전장치로 텍스트 스톱 조건도 백업으로 유지).
"""

import os
import re
import json
import time
import requests
from datetime import datetime
from bs4 import BeautifulSoup

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

# 지난(놓친) 방송 섹션 백업 감지 키워드 (id="LAST" 제거가 실패했을 때만 작동)
PAST_SECTION_STOP_WORDS = ("지난", "놓친")

# 가격 텍스트 근처에서 흔히 쓰이는 라벨 (정규식 fallback용)
PRICE_LABEL_KEYWORDS = ("혜택가", "방송가", "판매가", "정상가")


def extract_gs_price(soup: BeautifulSoup, html: str):
    """상품 상세 페이지에서 가격을 여러 방식으로 시도해서 뽑아낸다.
    (brand/name과 마찬가지로 사이트 구조가 안 맞으면 실패할 수 있어서
    여러 단계로 폴백한다. 전부 실패하면 None을 반환하고 상위에서
    디버그 로그를 남긴다.)"""

    # 1) JSON-LD 구조화 데이터 (schema.org Product/offers.price) - 있으면 가장 정확
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (TypeError, ValueError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            offers = item.get("offers")
            offers_list = offers if isinstance(offers, list) else [offers]
            for offer in offers_list:
                if isinstance(offer, dict) and offer.get("price"):
                    try:
                        return int(float(str(offer["price"]).replace(",", "")))
                    except (TypeError, ValueError):
                        pass

    # 2) 메타 태그(product:price:amount / itemprop=price)
    meta_price = (
        soup.find("meta", attrs={"property": "product:price:amount"})
        or soup.find("meta", attrs={"itemprop": "price"})
    )
    if meta_price and meta_price.get("content"):
        try:
            return int(float(meta_price["content"].replace(",", "")))
        except (TypeError, ValueError):
            pass

    # 3) 흔히 쓰이는 가격 표시 셀렉터 후보들 (실측 전 추정치라 사이트 구조가
    #    바뀌면 안 맞을 수 있음 - 안 맞으면 아래 디버그 로그로 원인 확인)
    for sel in (".prd-price .num", ".price-area .num", "strong.price", ".sale-price",
                ".benefit-price .num", "#prdPrice", ".prd-price-wrap .num",
                ".price em", ".price strong"):
        tag = soup.select_one(sel)
        if tag:
            digits = re.sub(r"[^\d]", "", tag.get_text())
            if digits:
                return int(digits)

    # 4) 최후 fallback: "혜택가/판매가/정상가" 같은 라벨 주변 텍스트에서
    #    "숫자,숫자원" 패턴을 정규식으로 찾는다.
    for kw in PRICE_LABEL_KEYWORDS:
        idx = html.find(kw)
        if idx == -1:
            continue
        window = html[idx: idx + 200]
        m = re.search(r"(\d{1,3}(?:,\d{3})+)(?:\s|<[^>]*>)*원", window)
        if m:
            return int(m.group(1).replace(",", ""))

    return None


def parse_broaddate(raw: str) -> dict:
    """'20260716204500' -> {'label': '7월 16일(목) 20:45 방송', 'iso': '2026-07-16T20:45:00'}"""
    if not raw or len(raw) < 12:
        return {"label": "방송일시 미상", "iso": None}
    try:
        dt = datetime.strptime(raw[:14].ljust(14, "0"), "%Y%m%d%H%M%S")
        weekday = WEEKDAY_KR[dt.weekday()]
        label = f"{dt.month}월 {dt.day}일({weekday}) {dt.strftime('%H:%M')} 방송"
        return {"label": label, "iso": dt.isoformat()}
    except ValueError:
        return {"label": "방송일시 미상", "iso": None}


def fetch_gs_product_details_fixed(prd_id):
    """
    PC 버전(www.gsshop.com/prd/prd.gs)은 JSESSIONID 없이 접근하면 무조건
    "세션 만료" 에러 페이지로 리다이렉트되는 게 확인됨 (홈페이지를 먼저 들러도
    세션 쿠키가 안 잡힘 - JS로 세션을 만드는 방식이라 requests로는 통과 불가).
    반면 목록 자체는 m.gsshop.com에서 이미 잘 긁히고 있으므로, 상세페이지도
    PC 대신 모바일(m.gsshop.com)로 바꿔서 시도한다.
    """
    url = f"https://m.gsshop.com/prd/prd.gs?prdid={prd_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-S918N) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Referer": "https://m.gsshop.com/index.gs",
    }
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            price = extract_gs_price(soup, res.text)
            if price is None:
                print(f"       [디버그] prdid={prd_id}: 가격 추출 실패 (셀렉터/패턴이 실제 페이지와 안 맞을 수 있음)")

            og_title = soup.find("meta", property="og:title")

            if og_title and og_title.get("content"):
                full_title = og_title.get("content").strip()
                if full_title.startswith("[") and "]" in full_title:
                    brand = full_title[1:full_title.index("]")]
                    name = full_title[full_title.index("]") + 1:].strip()
                else:
                    brand = "GS SHOP"
                    name = full_title
                return brand, name, price

            name_tag = soup.select_one("p.prd-name, h2.prd-nm, .product_title, .pdp-tit, .prd-tit")
            name = name_tag.get_text(strip=True) if name_tag else None
            brand_tag = soup.select_one(".brand-name, .brand, .bnd-nm")
            brand = brand_tag.get_text(strip=True) if brand_tag else "GS SHOP"

            if name:
                return brand, name, price

            # og:title도, 백업 셀렉터도 다 실패한 경우 -> 실제로 뭐가 왔는지
            # <title> 태그를 그대로 출력해서 바로 원인을 알 수 있게 한다.
            title_tag = soup.find("title")
            actual_title = title_tag.get_text(strip=True) if title_tag else "(title 태그도 없음)"
            print(f"       [디버그] prdid={prd_id}: og:title/백업셀렉터 다 실패. "
                  f"실제 페이지 title='{actual_title}' (status={res.status_code}, len={len(res.text)})")
            return "GS SHOP", "상품명 정보 없음", price
        else:
            print(f"       [디버그] prdid={prd_id}: HTTP {res.status_code}")
    except Exception as e:
        print(f"       [디버그] prdid={prd_id}: 예외 발생 {e}")
    return "GS SHOP", "상품 상세 조회 실패", None


def crawl_gs_program(config: dict):
    tab_name = config["tab_name"]
    url = config["url"]
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-S918N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Referer": "https://m.gsshop.com/index.gs"
    }

    print(f"\n===== [{tab_name}] 수집 시작 =====")

    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        html = res.text
    except Exception as e:
        print(f"[실패] [{tab_name}] 페이지 접근 불가: {e}")
        return None

    soup = BeautifulSoup(html, "html.parser")

    # --- "놓친 방송 다시보기" 섹션을 스캔 전에 아예 잘라낸다 ---
    last_section = soup.find(id="LAST")
    if last_section:
        removed_title = last_section.find(["h2", "h3"])
        removed_title_text = removed_title.get_text(strip=True) if removed_title else "(제목 태그 못 찾음)"
        print(f"    -> [지난방송 섹션 제거] id=LAST 컨테이너 발견 -> '{removed_title_text}' 통째로 decompose()")
        last_section.decompose()
    else:
        print("    -> [경고] id=LAST 컨테이너를 못 찾음. 백업 텍스트 스톱 조건으로만 방어함.")
    # ---------------------------------------------------------------

    # --- 핵심: section.item-module__type4 (배너+상품 세트) 단위로 그룹핑 ---
    raw_target_products = []
    seen_ids = set()

    modules = soup.select("section.item-module__type4")
    print(f"    -> item-module__type4 총 {len(modules)}개 발견")

    matched_modules = 0
    for module in modules:
        ban_item = module.select_one("article.ban-item[data-broaddate]")
        if not ban_item:
            # 인기상품/스토리 등 날짜 배너가 없는 다른 종류의 모듈 -> 스킵
            continue

        matched_modules += 1
        broaddate_raw = ban_item.get("data-broaddate", "")
        date_info = parse_broaddate(broaddate_raw)
        date_label = date_info["label"]

        # 백업 방어: 혹시 이 모듈 안에 "지난"/"놓친" 텍스트가 섞여 있으면 통째로 스킵
        module_text = module.get_text(" ", strip=True)
        if any(w in module_text[:30] for w in PAST_SECTION_STOP_WORDS):
            print(f"    -> [스킵] '{date_label}' 모듈에서 지난/놓친 방송 텍스트 감지 -> 건너뜀")
            continue

        links = module.select("a.prd-link")
        print(f"    -> [방송 타임 진입]: {date_label} (raw={broaddate_raw}) - 상품 {len(links)}개")

        for link in links:
            href = link.get("href", "")
            match = re.search(r'prdid=(\d+)', href)
            if not match:
                continue

            prd_id = match.group(1)
            if prd_id in seen_ids:
                continue

            seen_ids.add(prd_id)
            raw_target_products.append({
                "prd_id": prd_id,
                "date_label": date_label,
                "broadcast_iso": date_info["iso"],
            })

    print(f"    -> ban-item[data-broaddate] 있는 모듈 {matched_modules}개 매칭됨")

    # --- 진짜 상품명/브랜드 매핑 진행 ---
    final_products = []
    total_count = len(raw_target_products)
    print(f"[{tab_name}] 스캔 완료. 방송예정 타겟 상품 총 {total_count}개 상세 수집 시작...")

    for idx, item in enumerate(raw_target_products, 1):
        prd_id = item["prd_id"]
        date_label = item["date_label"]

        brand, name, price = fetch_gs_product_details_fixed(prd_id)
        price_txt = f"{price:,}원" if price is not None else "가격 미확인"
        print(f"  ({idx}/{total_count}) [{date_label}] ID: {prd_id} -> [{brand}] {name[:15]}... / {price_txt}")

        time.sleep(0.2)
        final_products.append({
            "prd_id": prd_id,
            "broadcast_date_label": date_label,
            "broadcast_iso": item["broadcast_iso"],
            "brand": brand,
            "name": name,
            "price": price,
            "link": f"https://m.gsshop.com/prd/prd.gs?prdid={prd_id}"
        })

    return {
        "company": "GS",
        "tab_name": tab_name,
        "program_title": config["program_title"],
        "schedule_raw": config["schedule_raw"],
        "detail_link": url,
        "products": final_products,
    }


# ============ 여기에 프로그램 추가 ============
# homeshopping/fixed_programs/GS.json 에서 detail_link 확인 가능
PROGRAMS = [
    {
        "tab_name": "백지연",
        "program_title": "지금 백지연",
        "schedule_raw": "매주 목요일 저녁 8시 45분",
        "url": "https://m.gsshop.com/section/broad/specialPgm/13437?mseq=W00618-TV_PRO_BB-1",
        "output_file": "GS_BJY.json",
    },
    {
        "tab_name": "소유진",
        "program_title": "소유진쇼",
        "schedule_raw": "매주 금요일 저녁 8시 35분",
        "url": "https://m.gsshop.com/section/broad/specialPgm/13401?mseq=W00618-TV_PRO_BB-2",
        "output_file": "GS_SYJ.json",
    },
    # TODO: 더컬렉션/쇼미더트렌드 등 추가하려면 GS.json의 detail_link 참고
]
# ==============================================


def main():
    output_dir = os.path.join("homeshopping", "representative_programs")
    os.makedirs(output_dir, exist_ok=True)

    for config in PROGRAMS:
        result = crawl_gs_program(config)
        if not result:
            continue

        output_path = os.path.join(output_dir, config["output_file"])
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[성공] [{config['tab_name']}] 수집 및 저장 완료: {output_path}")
        print(f"  - 총 수집된 예고 상품 수: {len(result['products'])}개")


if __name__ == "__main__":
    main()