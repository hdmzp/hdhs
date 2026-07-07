# 📺 시청환경 조회 — 프로젝트 전체 설명서

저장소: [`hdmzp/hdhs`](https://github.com/hdmzp/hdhs)
배포: `hdmzp.github.io/hdhs`

지상파·종편 편성표, 홈쇼핑 11개사(HD/GS/CJ/LT + 공영·홈앤·K쇼핑·신세계·NS·쇼핑엔티·SK스토아) 방송 편성·상품·가격, 고정PGM, 셀럽PGM, 드라마/예능 시청률, 홈쇼핑 랭킹, 서울 날씨를 매일 자동 수집해 보여주는 정적 웹 대시보드. 8개 탭 + 의견 수집 기능으로 구성.

---

## 1. 한눈에 보는 구조

```
hdhs/
├── index.html                       # 웹사이트 본체 (8개 탭, 전부 JSON fetch로 렌더링, GA4 연동)
│
├── .github/workflows/                # 자동화 파이프라인 (기능별로 분리된 9개 워크플로우)
│   ├── schedule.yml                    # 지상파·종편 편성표
│   ├── homeshopping.yml                # 홈쇼핑 4사(HD/GS/CJ/LT)
│   ├── etc-scrape.yml                  # 홈쇼핑 기타 7개사
│   ├── scrape-dramavariety.yml         # 드라마/예능 시청률
│   ├── scrape-fixed-pgm.yml            # 고정PGM (4사)
│   ├── scrape-celebpgm.yml             # 셀럽PGM 상품 데이터
│   ├── rep-pgm-scrape.yml              # 셀럽PGM 대표프로그램 메타(merge)
│   ├── scrape-ranking.yml              # 홈쇼핑 랭킹(18개 카테고리)
│   ├── weather.yml                     # 날씨(ASOS 과거 + 단기예보)
│   └── pages-deploy.yml                # 위 워크플로우들이 커밋 후 강제 트리거하는 배포 전용 워크플로우
│
├── naver_schedule_scraper.py        # [편성표] 지상파·종편 8채널 수집
├── hd_scraper.py / gs_scraper.py     # [홈쇼핑] 현대 / GS(라방바 경유)
├── cj_scraper.py / lt_scraper.py     # [홈쇼핑] CJ온스타일 / 롯데
├── etc_scraper.py                    # [홈쇼핑] 기타 7개사(공영/홈앤/K쇼핑/신세계/NS/쇼핑엔티/SK스토아, 라방바 경유)
├── weather.py                        # [날씨] ASOS 과거 + 단기예보
│
├── scraper/scrape_naver.py           # [드라마/예능] 시청률 편성 (Playwright)
├── scraper/naver_parser.py           # 위 스크립트의 파싱 로직 보조 모듈
├── scraper/ranking_scraper.py        # [랭킹] datahub.hsmoa.com 18개 카테고리 수집
│
├── fixed/                            # [고정PGM·셀럽PGM] 회사별 스크래퍼 + 병합 스크립트
│   ├── hd_fixed_programs.py / gs_fixed_programs.py
│   ├── cj_fixed_programs.py / lt_fixed_programs.py
│   ├── build_fixed_pgm.py            # 4사 결과 → homeshopping/fixed_programs/merged.json
│   ├── rehd.py / regs.py / recj.py / relt.py   # 셀럽PGM(8개 프로그램) 상품 수집 (rehd는 Playwright)
│   └── build_representative_programs.py        # 셀럽PGM 결과 → homeshopping/representative_programs/merged.json
│
├── categorize.py                     # 상품 카테고리 분류 (학습모델 호출)
├── infer_brand.py                    # 브랜드명 추론 (GS/CJ 등 브랜드 필드 공백 보강용)
├── clean_product.py                  # 상품명 화면표시용 정제
├── train_model.py                    # 분류모델 학습 스크립트
├── update_training_data.py           # 새로 라벨링한 데이터 병합
├── category_model.pkl                # 학습된 분류 모델 (TF-IDF + 로지스틱회귀)
├── training_data.xlsx                # 모델 학습용 원본 데이터
│
├── comtrack5.py / comtrack2.py / cloth.py   # 경쟁사 트래킹 분석 스크립트(로컬용, comptracker.xlsx 참조)
│
├── backup/feedback_apps_script_Code.gs      # 의견 탭 백엔드 (Google Apps Script, MailApp 발송)
│
├── data/{YYYY-MM-DD}.json                       # 편성표 결과 (날짜별 파일)
├── data/dramavariety/{주월요일}.json              # 드라마/예능 결과 (주 단위 파일)
├── data/ranking/{YYYY-MM-DD}.json, latest.json  # 랭킹 결과 (일 단위 + 최신 스냅샷)
├── homeshopping/{사}_{live|data|plus}/{YYYY-MM}.json  # 홈쇼핑 편성 결과 (월별 파일, 11개사)
├── homeshopping/fixed_programs/{사}.json, merged.json         # 고정PGM
├── homeshopping/representative_programs/{사}_{코드}.json, merged.json  # 셀럽PGM
└── weather/asos|forecast/...                    # 날씨 결과
```

---

## 2. 자동화 파이프라인

과거에는 `daily-scrape.yml` 하나로 전체를 순서대로 돌렸으나, 데이터가 늘면서 **기능별로 워크플로우를 분리**하고 크론 시각을 KST 기준으로 흩어 두었다(같은 시각에 여러 잡이 동시에 push해서 충돌하는 걸 줄이기 위함).

| 워크플로우 | 실행 시각(KST) | 대상 | 실패 허용 |
|---|---|---|---|
| `schedule.yml` | 05:00 | 지상파·종편 편성표 | - |
| `scrape-fixed-pgm.yml` | 04:30 | 고정PGM 4사 | 스크래퍼별 `continue-on-error` |
| `weather.yml` | 05:30 | 날씨(ASOS+단기예보) | - |
| `homeshopping.yml` | 05:50, 12:20 (하루 2회) | 홈쇼핑 4사(HD/GS/CJ/LT) | 스크래퍼별 `continue-on-error` |
| `etc-scrape.yml` | 06:10, 12:40 (하루 2회) | 홈쇼핑 기타 7개사 | `continue-on-error` |
| `scrape-ranking.yml` | 07:00 | 홈쇼핑 랭킹 18개 카테고리 | - |
| `rep-pgm-scrape.yml` | 08:10 | 셀럽PGM 대표프로그램 메타 병합 | 회사별 `continue-on-error` |
| `scrape-celebpgm.yml` | 03:00 | 셀럽PGM(8개 프로그램) 상품 데이터 | 스크립트별 `|| echo` |
| `scrape-dramavariety.yml` | 02:00, 08:30, 12:10, 21:00 (하루 4회) | 드라마/예능 시청률 | - |
| `pages-deploy.yml` | (push 또는 API 트리거 시) | GitHub Pages 배포 | 3회까지 자동 재시도 |

공통 사항:
- 실행 주체는 모두 `github-actions[bot]`, 데이터 변경이 있을 때만 커밋(`git diff --staged/cached --quiet ||`)해서 빈 커밋 방지
- **push 충돌 방지**: 최근 추가된 워크플로우(`scrape-dramavariety`, `scrape-fixed-pgm`, `scrape-ranking`, `rep-pgm-scrape`)는 `git pull --rebase` 후 재시도를 최대 5회까지 반복하는 루프를 둠. 반면 초기부터 있던 워크플로우(`schedule`, `homeshopping`, `etc-scrape`, `weather`)는 아직 단순 `git pull --rebase --autostash && git push` 1회뿐이라 동시 충돌 시 실패할 수 있음 — 통일 필요
- **Pages 배포 트리거 문제**: `github-actions[bot]` 계정의 push는 GitHub 정책상 다른 워크플로우를 재귀 트리거하지 않아, `pages-deploy.yml`이 데이터 갱신 커밋에 자동 반응하지 않는다. 그래서 각 스크래퍼 워크플로우가 커밋 후 `workflow_dispatch`를 API로 직접 호출해 배포를 강제로 큐에 넣는다(`scrape-celebpgm.yml`만 이 트리거 스텝이 빠져 있어, 셀럽PGM 상품만 갱신된 날은 배포가 안 될 수 있음)
- `pages-deploy.yml`은 GitHub Pages 배포가 일시적으로 실패(`Deployment failed, try again later.`)하는 경우를 대비해 최대 3회 자동 재시도

---

## 3. 데이터 수집기 상세

### 📅 편성표 — `naver_schedule_scraper.py`
- 대상: KBS1·KBS2·MBC·SBS(지상파), JTBC·MBN·TV조선·채널A(종편)
- 네이버 "{채널명} 편성표" 검색 위젯을 `requests`+`BeautifulSoup`로 정적 파싱(Playwright 불필요)
- 네이버가 두 가지 마크업(weekly-full / weekly-simple)을 랜덤하게 보여주므로 둘 다 처리하는 파서를 따로 둠 — full이 더 상세해서 우선 사용
- 검색 결과는 항상 "오늘 -1일 ~ +5일"의 7일 구간만 줘서, 그 범위에 한해 날짜별 파일(`data/{날짜}.json`)에 8채널 통합 저장
- 종료시각이 원본에 없어 "다음 프로그램 시작 = 이전 프로그램 종료"로 역산

### 🛒 홈쇼핑 — 4사(`hd/gs/cj/lt_scraper.py`) + 기타 7개사(`etc_scraper.py`)
전 11개사 모두 출력 스키마를 동일하게 맞춤:
```json
{
  "company": "HD", "broadcast": "live", "month": "2026-06",
  "days": { "2026-06-22": [ {"start":"08:00","end":"09:59","brand":"...","product":"...","price":39000,"link":"...","category":"가전"} ] }
}
```
- 공통 수집 범위: 오늘 -1일 ~ +5일. **과거 날짜가 이미 기록돼 있으면 건드리지 않고**, 오늘과 미래 날짜만 매번 새로 갱신(방송이 끝나며 정보가 보정되는 효과)
- 회사·방송유형별로 별도 월 파일 저장(`{사}_live`, `{사}_data`, NS는 `NS_plus` 추가)

| 회사 | 데이터 소스 | 특이사항 |
|---|---|---|
| **HD** (현대) | `hmall.com` 공개 API | dtv(데이터방송)는 종료시각이 끊겨있어 다음 방송 시작시각으로 보정 |
| **LT** (롯데) | `lotteimall.com` 공개 API | 비교적 단순한 구조 |
| **CJ** | `display.cjonstyle.com` API | API의 `brandName`이 거의 항상 비어서, itemCd로 `repBrandTag`라는 별도 REST 엔드포인트를 호출해 대표 브랜드를 보강. 실패 시 상품명 기반 추론으로 백업 |
| **GS** | gsshop.com 직접 차단(클라우드 IP 차단) → **라방바(`live.ecomm-data.com`) 경유**, 2단계 fetch: 1단계 목록 API로 `hsshow_id` 획득 → 2단계 `report/hsshow/{id}` 페이지의 `__NEXT_DATA__` JSON에서 가격·링크 추출. 브랜드 필드는 항상 빈 값이라 상품명에서 추론 |
| **공영·홈앤·K쇼핑·신세계·NS·쇼핑엔티·SK스토아** (기타 7개사) | 라방바(`live.ecomm-data.com`) API 경유, 회사별 `hs_*` 코드로 구분 | GS와 동일한 라방바 의존 구조를 공유해서, 라방바 사이트가 바뀌면 이 7개사와 GS가 한꺼번에 영향받음 |

### 🎬 드라마/예능 — `scraper/scrape_naver.py`
- Playwright(headless Chromium)로 네이버 "방영중한국드라마"/"방영예능" 위젯을 직접 조작해 수집
- "전체" 탭을 JS로 강제 추출/클릭(클릭 씹힘 방어), 페이지네이션 끝까지(최대 30페이지) 순회
- 시청률 최소 기준: 드라마(`MIN_RATING_DRAMA`) 5%↑, 예능(`MIN_RATING_VARIETY`) 1%↑ 만 채택 — 이 임계값과, 평점이 `None`으로 들어오는 케이스 처리 로직이 최근 특정 주(7/6주) 파일 미생성 이슈의 주요 의심 지점
- **저장 단위가 "주(월~일)"** — 수집된 모든 결과를 항상 "이번 주(KST 기준 오늘이 속한 주의 월요일 날짜)" 파일 하나에 무조건 병합. 동일 프로그램(`id`=분류+제목+채널)이 이미 있으면 요일만 합치고, 매일 실행해도 데이터가 쌓이기만 함
- 다음 주 월요일이 되면 새 파일이 자동 생성됨

### 🏷 고정PGM — `fixed/{사}_fixed_programs.py` + `fixed/build_fixed_pgm.py`
- 홈쇼핑 4사(HD/GS/CJ/LT)의 매주 반복되는 고정 편성 프로그램(요일·시간 고정)을 수집
- 회사별로 각각 `homeshopping/fixed_programs/{사}.json`에 저장 후, `build_fixed_pgm.py`가 하나의 `merged.json`으로 통합해 프론트가 참조

### ⭐ 셀럽PGM — `fixed/re{hd|gs|cj|lt}.py` + `fixed/build_representative_programs.py`
- 강주은 굿라이프(CJ)·오감쇼(HD)·더 김창옥 라이브(CJ)·최화정쇼(CJ)·황정민쇼(HD)·지금 백지연(GS)·최유라쇼(LT)·소유진쇼(GS) 등 8개 셀럽 호스트 프로그램의 회차별 판매 상품을 수집
- `rehd.py`만 Playwright 사용(나머지는 정적 파싱), 프로그램별 결과 파일(`{사}_{코드}.json`)을 `build_representative_programs.py`가 `merged.json`으로 통합

### 📊 홈쇼핑 랭킹 — `scraper/ranking_scraper.py`
- `datahub.hsmoa.com`에서 18개 카테고리별 인기 상품 랭킹을 수집
- 카테고리 호출 사이 0.6초 대기(`CATEGORY_REQUEST_DELAY`)로 과도한 요청 방지
- 직전 스냅샷(`link_cache.json` / 이전 날짜 파일)과 비교해 순위 변동을 계산, 인기/HOT/RISING 배지에 반영
- 날짜별 파일(`data/ranking/{날짜}.json`)과 최신 스냅샷(`latest.json`)을 함께 저장

### 🌤 날씨 — `weather.py`
- 서울 기준(위경도 격자 60,127 / 관측소ID 108)
- 과거(ASOS): 확정된 지난 달까지는 `weather/asos/{YYYY-MM}.json`에 한 번만 저장하고 재수집 안 함. 진행 중인 현재 달은 매일 1일~어제까지 통째로 재수집
- 미래(단기예보): `weather/forecast/latest.json`에 오늘~글피 매번 갱신
- 최초 실행 시 2023-01부터 백필

---

## 4. 상품 카테고리 분류 시스템

홈쇼핑 11개사 공통으로 쓰는 분류 파이프라인:

```
원본 (브랜드, 상품명)
  └→ ① 브랜드 완전일치 강제매핑(BRAND_FORCE_MAP, 삼성/LG 등 가전 브랜드) 우선 확정
  └→ ② 명확한 키워드 정규식(에어컨/세탁기/냉장고/TV 등) 즉시 확정
  └→ ③ 브랜드가 학습데이터에서 단일 카테고리로만 운영됐으면 그 카테고리로 확정
  └→ ④ 브랜드 비어있으면 infer_brand.py로 상품명에서 추론해 보강
  └→ ⑤ 그래도 안 잡히면 TF-IDF+로지스틱회귀 모델(category_model.pkl) 예측
  └→ 세분류를 그룹으로 통합 (가전=대형가전+소형가전+다이슨+로보락, 리빙/주방=주방용품+인테리어/침구+생활용품 등 GROUP_MAP 기준)
```

- **모델**: `train_model.py`로 `training_data.xlsx`(브랜드명/판매상품명/상품중분류명) 학습. char n-gram(2~5) TF-IDF + LogisticRegression, 검증 정확도 약 95%
- **학습데이터 갱신**: `update_training_data.py`로 새로 라벨링한 데이터를 기존 `training_data.xlsx`에 병합
- **브랜드 추론(`infer_brand.py`)**: GS·CJ·기타 7개사처럼 브랜드 필드가 비는 경우, 학습데이터의 브랜드 사전과 상품명을 매칭(긴 토큰 우선, 대괄호 안 텍스트도 보조 검사)해 추론. 화면 표시용 브랜드는 `resolve_display_brand()`로 정제(괄호 부기 제거 등)
- **상품명 정제(`clean_product.py`)**: 분류는 항상 원본 텍스트로 하고, 화면 표시 직전에만 마케팅 문구·가격강조·사은품 안내·용량 표기 등을 제거해 깔끔하게 보여줌

---

## 5. `index.html` — 프론트엔드 8개 탭

공통 구조: 모든 데이터는 fetch로 JSON을 불러와 그리드/표로 렌더링하는 정적 SPA 한 페이지. 데이터 fetch는 전부 `?t=${Date.now()}` 캐시버스팅 적용(브라우저가 옛 응답을 캐싱해 화면이 안 갱신되는 문제 방지). GA4(`G-EZ7V7Y9SFC`)로 `tab_view`, `filter_click`, `outbound_click`, `feedback_submit` 이벤트를 수집.

탭 순서(index.html 기준): **홈쇼핑 → 고정PGM → 셀럽PGM → 지상파·종편 → 드라마·예능 → 날씨 → 랭킹 → 의견**

### 🛒 홈쇼핑 탭 (기본 진입 탭)
- 일 단위 조회, 24시간 × 회사 그리드 (라이브방송/데이터방송 토글)
- **전체/정규/심야 시간대 필터** — 심야는 방송 시작시각 01:00~05:00 이전(1~4시)만 표시
- 카테고리 칩 클릭 시 리스트뷰로 전환되며 시간대 필터와 동시 적용
- **조회 가능 시작일: 2026-06-21**

### 🏷 고정PGM 탭
- 4사(HD/GS/CJ/LT) 고정 편성 프로그램을 요일별 그리드로 비교

### ⭐ 셀럽PGM 탭
- 8개 셀럽 프로그램(강주은/오감쇼/김창옥/최화정/황정민/백지연/최유라/소유진, 요일 순서로 정렬)을 **다중 선택해 동시 비교**하는 레이아웃
- 프로그램별 상품 데이터를 처음 열 때 한 번만 불러오는 캐시(`_celebrityTabLoaded`) 적용

### 📅 지상파·종편 탭
- 일 단위 조회, 24시간 × 8채널 그리드
- 지상파/종편 색상 범례, 현재 방송 중인 칸은 자동 스크롤로 보여줌
- **조회 가능 시작일: 2026-06-20**

### 🎬 드라마·예능 탭
- **주 단위(월~일) 조회**, 전체/드라마/예능/고시청률 필터
- 날짜 표시 형식: `2026-06-22 (월) ~ 2026-06-28 (일)`
- 달력으로 날짜를 고르면 해당 주의 월요일로 자동 스냅, 조회 컨트롤 고정(sticky)
- **조회 가능 시작 주차: 2026-06-22(월)부터**

### 🌤 날씨 탭
- 월 단위 캘린더, 최저/최고기온 + 강수(과거 mm / 예보 확률%)
- **작년 같은 날** 최저/최고기온을 함께 표시(과거 데이터 있을 때만)

### 📊 랭킹 탭
- 18개 카테고리(대/소분류) 선택형 주간 랭킹, 순위 변동 표시(▲▼)와 인기/HOT/RISING 배지
- 채널 색상 코딩 적용

### 💬 의견 탭
- 유형 선택(버그 신고/기능 제안/기타 의견) + 자유 텍스트 입력
- 백엔드는 스프레드시트 없이 **Google Apps Script(`backup/feedback_apps_script_Code.gs`)** 가 `MailApp`으로 바로 이메일 발송하는 구조. 제출 시 `feedback_submit` GA 이벤트 기록

---

## 6. 공통 UI 정책

- 전체 배경: 흰색(탭 바깥 영역까지 통일)
- 헤더: `📺 시청환경 조회`(부제목 없음)
- 날짜/주차 조회 컨트롤은 모든 탭에서 동일한 흰 탭 디자인(화살표 + 흰 박스 + 달력 아이콘)으로 통일
- 탭별 최소 조회 날짜 이전은 달력 비활성화 + 이전 버튼 비활성화 + 직접입력 시 자동 보정의 3중 방어

---

## 7. 알려진 이슈 / 한계

- **[진행 중] 7/6주 드라마·예능 JSON 미생성**: `MIN_RATING_DRAMA`/`MIN_RATING_VARIETY` 임계값 필터링과 `rating is None` 처리 로직을 의심하며 디버깅 중
- 네이버 편성표 위젯의 마크업이 두 버전으로 랜덤하게 바뀌어, 단순(simple) 버전이 뜨면 한 시간대의 프로그램 일부가 누락될 수 있음
- 모든 채널/회사의 종료시각은 원본에 없는 경우 "다음 프로그램 시작 = 이전 종료"로 역산한 추정값
- 두 파서 버전이 모두 실패하면 `data/_debug_fail_{채널명}.html`로 저장되고 그날 데이터에서 누락(구조 변경 감지용) — 셀럽PGM 쪽에도 `_debug_pgm_comm_*.json` 형태의 동일한 실패 스냅샷이 남음
- GS와 기타 7개사(총 8개사)가 모두 라방바(`live.ecomm-data.com`) 하나에 의존해서, 그 사이트 구조가 바뀌면 한 번에 다수 채널이 영향받는 단일 장애점
- 홈쇼핑/고정PGM 스크래퍼 대부분이 `continue-on-error`(또는 `|| echo`)라서 특정 사가 그날 실패해도 워크플로우 전체는 성공으로 표시됨 — 결과를 가끔 직접 확인할 필요
- `scrape-celebpgm.yml`에는 다른 워크플로우들과 달리 Pages 재배포 강제 트리거 스텝이 없어, 셀럽PGM 상품만 갱신된 날은 배포가 자동으로 안 될 수 있음
- 워크플로우별 push 재시도 로직이 통일돼 있지 않음(초기 워크플로우는 1회, 최근 워크플로우는 5회 재시도) — 통일 필요
