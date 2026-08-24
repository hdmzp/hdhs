# 📺 시청환경 조회 — 프로젝트 설명

작성자: 👧MJ👧
저장소: [`hdmzp/hdhs`](https://github.com/hdmzp/hdhs)
배포: `hdmzp.github.io/hdhs`

지상파·종편 편성표, 홈쇼핑 11개사(HD/GS/CJ/LT + 공영·홈앤·K쇼핑·신세계·NS·쇼핑엔티·SK스토아) 방송편성·상품·가격, 고정PGM, 셀럽PGM, 드라마/예능 시청률, 홈쇼핑 랭킹, 서울 날씨를 매일 자동 수집해 보여주는 웹 대시보드. 8개 탭 + 의견 수집 기능으로 구성.

---

## 1. 한눈에 보는 구조

```
hdhs/
├── index.html                       # 웹사이트 본체 (8개 탭, 전부 JSON fetch로 렌더링, GA4 연동)
│
├── .github/workflows/                # 자동화 파이프라인 (기능별로 분리된 워크플로우)
│   ├── schedule.yml                    # 지상파·종편 편성표
│   ├── homeshopping.yml                # 홈쇼핑 4사(HD/GS/CJ/LT)
│   ├── etc-scrape.yml                  # 홈쇼핑 기타 7개사
│   ├── scrape-dramavariety.yml         # 드라마/예능 시청률
│   ├── scrape-fixed-pgm.yml            # 고정PGM (4사)
│   ├── scrape-celebpgm.yml             # 셀럽PGM 상품 데이터
│   ├── rep-pgm-scrape.yml              # 셀럽PGM 대표프로그램 메타(merge)
│   ├── scrape-ranking.yml              # 홈쇼핑 랭킹(18개 카테고리)
│   ├── lavangba.yml                    # 라방바 11개사 방송 데이터(v2, 로그인 불필요)
│   ├── weather.yml                     # 날씨(ASOS 과거 + 단기예보) + 공휴일 + 절기
│   └── pages-deploy.yml                # 위 워크플로우들이 커밋 후 강제 트리거하는 배포 전용 워크플로우
│
├── naver_schedule_scraper.py        # [편성표] 지상파·종편 8채널 수집
├── hd_scraper.py / gs_scraper.py     # [홈쇼핑] 현대 / GS(라방바 경유)
├── cj_scraper.py / lt_scraper.py     # [홈쇼핑] CJ온스타일 / 롯데
├── etc_scraper.py                    # [홈쇼핑] 기타 7개사(공영/홈앤/K쇼핑/신세계/NS/쇼핑엔티/SK스토아, 라방바 경유)
├── promotion_scraper.py              # [프로모션] 4사(HD/GS/CJ/LT) 카드할인 일정
├── lavangba_scraper_v2.py            # [라방바] 11개사 방송 데이터 수집 v2 (로그인 불필요, 매출 제외)
├── weather.py                        # [날씨] ASOS 과거 + 단기예보 + 공휴일 + 절기
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
├── homeshopping/promotions/card_discounts.json  # 프로모션(카드할인) 일정
├── lavangba/data/{YYYYMM}.json                  # 라방바 방송 데이터 (월별 파일, {"YYYYMMDD":[행...]})
├── homeshopping/fixed_programs/{사}.json, merged.json         # 고정PGM
├── homeshopping/representative_programs/{사}_{코드}.json, merged.json  # 셀럽PGM
└── weather/asos|forecast|holiday|term/...       # 날씨 결과 + 공휴일 + 절기
```

---

## 2. 자동화 파이프라인

과거에는 `daily-scrape.yml` 하나로 전체를 순서대로 돌렸으나, 데이터가 늘면서 **기능별로 워크플로우를 분리**하고 크론 시각을 KST 기준으로 흩어 두었다(같은 시각에 여러 잡이 동시에 push해서 충돌하는 걸 줄이기 위함).

| 워크플로우 | 실행 시각(KST) | 대상 | 실패 허용 |
|---|---|---|---|
| `schedule.yml` | 05:00 | 지상파·종편 편성표 | - |
| `scrape-fixed-pgm.yml` | 04:30 | 고정PGM 4사 | 스크래퍼별 `continue-on-error` + 건전성 검사(2-1) |
| `weather.yml` | 05:30 | 날씨(ASOS+단기예보)+공휴일+절기 | - |
| `homeshopping.yml` | 05:50, 12:20 (하루 2회) | 홈쇼핑 4사(HD/GS/CJ/LT) | 스크래퍼별 `continue-on-error` |
| `etc-scrape.yml` | 06:10, 12:40 (하루 2회) | 홈쇼핑 기타 7개사 | `continue-on-error` |
| `scrape-ranking.yml` | 07:00 | 홈쇼핑 랭킹 18개 카테고리 | - |
| `lavangba.yml` | 07:50 | 라방바 11개사 방송 데이터(v2) | `continue-on-error` |
| `promotion.yml` | 06:20, 12:50 (하루 2회) | 프로모션 카드할인 4사(HD/GS/CJ/LT) | 회사별 실패는 스크립트 내부에서 격리 |
| `rep-pgm-scrape.yml` | 08:10 | 셀럽PGM 대표프로그램 메타 병합 | 회사별 `continue-on-error` + 건전성 검사(2-1) |
| `scrape-celebpgm.yml` | 03:00 | 셀럽PGM(11개 프로그램) 상품 데이터 | 스크립트별 `|| echo` + 건전성 검사(2-1) |
| `scrape-dramavariety.yml` | 02:00, 08:30, 12:10, 21:00 (하루 4회) | 드라마/예능 시청률 | - |
| `pages-deploy.yml` | (push 또는 API 트리거 시) | GitHub Pages 배포 | 3회까지 자동 재시도 |

공통 사항:
- 실행 주체는 모두 `github-actions[bot]`, 데이터 변경이 있을 때만 커밋(`git diff --staged/cached --quiet ||`)해서 빈 커밋 방지
- **push 충돌 방지**: 최근 추가된 워크플로우(`scrape-dramavariety`, `scrape-fixed-pgm`, `scrape-ranking`, `rep-pgm-scrape`)는 `git pull --rebase` 후 재시도를 최대 5회까지 반복하는 루프를 둠. 반면 초기부터 있던 워크플로우(`schedule`, `homeshopping`, `etc-scrape`, `weather`)는 아직 단순 `git pull --rebase --autostash && git push` 1회뿐이라 동시 충돌 시 실패할 수 있음 — 통일 필요
- **Pages 배포 트리거 문제**: `github-actions[bot]` 계정의 push는 GitHub 정책상 다른 워크플로우를 재귀 트리거하지 않아, `pages-deploy.yml`이 데이터 갱신 커밋에 자동 반응하지 않는다. 그래서 각 스크래퍼 워크플로우가 커밋 후 `workflow_dispatch`를 API로 직접 호출해 배포를 강제로 큐에 넣는다(`scrape-celebpgm.yml`만 이 트리거 스텝이 빠져 있어, 셀럽PGM 상품만 갱신된 날은 배포가 안 될 수 있음)
- `pages-deploy.yml`은 GitHub Pages 배포가 일시적으로 실패(`Deployment failed, try again later.`)하는 경우를 대비해 최대 3회 자동 재시도

### 2-1. 수집 안전장치 (조용한 실패 방지)

`continue-on-error`/`|| echo`로 회사별 실패를 격리하면 워크플로우가 계속 초록불이라, 스크래퍼가 죽어도 아무도 모른 채 며칠이 지나간다(2026-08 CJ 셀럽PGM 사고: `recj.py`가 편성표 API의 `result: null`에 크래시하며 첫 프로그램에서 멈춰 CJ 4개 파일이 갱신되지 않았는데, 워크플로우는 매일 성공으로 표시됨). 그래서 방어를 세 층으로 둔다.

| 층 | 구현 | 막는 것 |
|---|---|---|
| ① 재시도 | `tools/scrape_guard.py` | 타임아웃·5xx·429·WAF의 HTML 응답 같은 **일시적** 실패. 지수 백오프(1→2→4초 + 지터), 429는 `Retry-After` 존중, 404/403은 재시도 없이 즉시 포기 |
| ② 산출물 검사 | `tools/check_scrape_health.py` | 재시도로도 안 되는 **진짜 장애**. 수집 직전 `--mark`로 시각을 찍고, 수집 후 산출물이 ⓐ존재하는지 ⓑJSON으로 읽히는지 ⓒ**이번 실행에서 다시 쓰였는지** ⓓ최소 건수를 넘는지 ⓔ직전 커밋 대비 40% 밑으로 급감하지 않았는지 ⓕ(celeb) `history/` 누적 파일에서 **이미 방송된 회차의 기록이 줄거나 사라지지 않았는지** 검사. 하나라도 걸리면 종료코드 1 |
| ③ 원인 요약 | `tools/run_step.py` | 로그를 뒤지는 수고. 스크래퍼를 `run_step.py`로 감싸 예외 종류·메시지·발생 위치(`파일:줄 (함수)`)를 잡아두고, 검사 실패 시 실행 로그 맨 아래에 **코드명 / 오류내용 / 수정권장사항**을 정리해 출력 |

핵심은 ⓒ다. 내용이 그대로여도 파일은 매번 다시 쓰이므로, 스크래퍼가 죽으면 그 회사 파일만 mtime이 안 바뀌어 즉시 잡힌다.

ⓕ는 결이 다르다 — 수집은 멀쩡한데 **누적 기록이 덮여 사라지는** 사고를 잡는다(위 2026-08-22 왕톡 사고는 per-program 산출물이 전부 정상이라 ⓐ~ⓔ 어디에도 안 걸렸다). 판정은 `build_celeb_history.py`의 `already_started()`를 그대로 import해서 쓴다 — 누적 규칙과 검사 기준이 갈라지면 검사가 무의미해진다.

검사는 커밋 **직전**에 돌리되 `continue-on-error: true`로 둔다 — 살아남은 회사 데이터는 커밋해서 살리고, 잡 실패 처리는 마지막 스텝에서 한다. 적용된 워크플로우는 `rep-pgm-scrape.yml`, `scrape-fixed-pgm.yml`, `scrape-celebpgm.yml` 3개.

검사 실패 시 실행 로그 마지막에 나오는 요약:

```
===== 원인·조치 요약 =====

▶ fixed/recj.py
  - AttributeError: 'NoneType' object has no attribute 'get' @ recj.py:175 (fetch_schedule_lineup)
    -> API가 해당 필드를 null로 준 경우 - 응답 접근부에 `or {}` 방어 추가
  - CJ_KJE.json - 이번 실행에서 갱신 안 됨 (마지막 기록 180분 전)
    -> fixed/recj.py 실행 로그에서 예외 확인 후 재실행
```

산출물 실패는 `PRODUCER` 매핑으로 담당 스크래퍼에 귀속되고, 권장사항은 예외 종류/실패 사유별로 `advise*()`가 만든다.

**텔레그램 알림은 두지 않는다.** 한때 붙였다가 걷어냈다 — 알림이 잡을 수 있는 범위(코드가 죽는 부류)가 실제로 놓치고 있던 범위(필드 결손 같은 조용한 누락)를 다 덮지 못해서다. 실패 감지는 Actions의 빨간불로만 한다.

```bash
# 로컬에서 수동 점검
python tools/check_scrape_health.py --mark      # 수집 직전
python tools/check_scrape_health.py --group celeb   # 또는 --group fixed
python tools/test_scrape_guard.py               # 재시도 로직 자체 테스트 (네트워크 불필요)
```

기대 산출물 목록·최소 건수는 `check_scrape_health.py`의 `SPECS`에 있다. **셀럽PGM/고정PGM에 프로그램을 추가하면 여기에도 추가해야** 그 프로그램의 수집 실패가 잡힌다.

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

### 📊 라방바 방송 데이터 — `lavangba_scraper_v2.py` (v2)

`index.html`의 **라방바 표**(`lavangba/data/{YYYYMM}.json`)를 만드는 수집기. 월별 파일에
`{"YYYYMMDD": [행...]}` 구조로 저장하고, 수집한 날짜만 갈아끼운다.

- 소스 ① 라방바 `api/schedule/list_hs` (공개 API) → 방송 목록·시작/종료시각·방송제목·라방바 대분류
- 소스 ② 이 저장소의 `homeshopping/{코드}_live/{YYYY-MM}.json` (각 사 편성표)
  → 상품명·브랜드·**판매가**·상품링크·카테고리. 저장소 안에서 돌 때는 로컬 파일을 그대로
  읽으므로, 같은 날 아침 편성 수집 워크플로우가 갱신한 최신본이 반영된다
- 편성 항목 ↔ 방송 매칭: 편성 항목에 `hsshow_id`가 있으면(GS/기타 7개사) 1:1 정확 매칭,
  없으면(HD/CJ/LT 자사몰 편성표) 시간 겹침 + 제목 유사도로 **한 방송에만** 배타 배정
  (같은 시간대 병행 편성 시 서로 상대 상품 정보를 가져가 오염되는 것 방지)

#### v1(비공개 저장소) 대비 달라진 점 — 2026-08 라방바 보안정책 변경

v1은 라방바 **로그인 세션**으로 매출 API(`hsshow/items`)를 호출해야 해서 집 PC의 크롬
프로필 + 작업 스케줄러로만 돌릴 수 있었다. 보안정책이 바뀌면서 그 경로가 막혀
(로그인해도 매출이 마스킹) 로그인 부분을 통째로 들어낸 v2로 교체했고, 로그인이
없어진 덕분에 **Actions 자동화가 가능해졌다**(`lavangba.yml`, KST 07:50).

| | v1 | v2 |
|---|---|---|
| 로그인/브라우저 | 필요 (Playwright + 로그인 세션) | 불필요 (requests만) |
| 총주문(`sales_amt`) | 라방바 실매출 | **수집 불가 → `"-"`** |
| 순주문 | 총주문 × 예상전환율 | 총주문이 없어 자동으로 `-` |
| 복합 PGM | 분당 매출 시계열로 상품별 분리(행 여러 개) | **방송 1건 = 1행(대표 단일코드)** |
| 판매가 | 편성표/라방바 | 편성표에서 그대로 수집 |
| 실행 | 집 PC 작업 스케줄러 | GitHub Actions |

- 복합 방송의 대표 상품은 **편성 노출시간이 가장 긴 항목**, 시간이 같으면(HD/CJ/LT
  편성표는 복합 방송의 모든 항목을 방송 전체 시간으로 기재) 방송 제목과 가장 비슷한 항목
- `item_start`/`item_end`/`노출분`은 방송 1건 = 1행이므로 **방송 전체 구간**
- JSON 필드명·구조는 v1과 동일 — `index.html`은 `lvFmtAmt`/`lvFmtPrice`에서 숫자 변환을
  먼저 해 `"-"`가 그대로 `-`로 표시되게만 보정했다. 2026-08-19 이전 데이터(실매출 포함)는
  그대로 남아있고 계속 정상 표시된다
- 목표(`target`, 분당목표 합산)는 v2에서 빠졌다. 비공개 저장소의 `pgmsales/*.enc`에
  의존하던 기능이라 자동화하면서 정리했고, 이전 데이터에 들어있는 `target` 값은 그대로 둔다
- 필요 패키지는 `requests` 하나 (로그인/암복호화 의존성 없음)

#### 수동 실행 / 백필

Actions → "라방바 방송 수집 (11개사)" → Run workflow 에서 `start_date`/`end_date`
(YYYYMMDD)를 넣으면 그 기간을 다시 수집해 덮어쓴다.

비워두면 `lavangba/data`의 월별 파일을 훑어서 **어제까지 30일 중 아직 없는 날짜만** 골라
수집한다. 마지막 날짜 이후만 보는 게 아니라 빠진 날짜 전부를 보기 때문에, 중간에 하루
실패해서 구멍이 나도(그날 행이 0개로 남은 경우 포함) 다음 실행 때 알아서 메워진다.
이미 있는 날짜는 건드리지 않고, 수집한 날짜만 월별 JSON에 덧씌운다.

로컬에서도 `python lavangba_scraper_v2.py 20260820`처럼 그대로 돌릴 수 있다.

### 🎬 드라마/예능 — `scraper/scrape_naver.py`
- Playwright(headless Chromium)로 네이버 "방영중한국드라마"/"방영예능" 위젯을 직접 조작해 수집
- "전체" 탭을 JS로 강제 추출/클릭(클릭 씹힘 방어), 페이지네이션 끝까지(최대 30페이지) 순회
- 시청률 최소 기준: 드라마(`MIN_RATING_DRAMA`) 5%↑, 예능(`MIN_RATING_VARIETY`) 1%↑ 만 채택 — 이 임계값과, 평점이 `None`으로 들어오는 케이스 처리 로직이 최근 특정 주(7/6주) 파일 미생성 이슈의 주요 의심 지점
- **저장 단위가 "주(월~일)"** — 수집된 모든 결과를 항상 "이번 주(KST 기준 오늘이 속한 주의 월요일 날짜)" 파일 하나에 무조건 병합. 동일 프로그램(`id`=분류+제목+채널)이 이미 있으면 요일만 합치고, 매일 실행해도 데이터가 쌓이기만 함
- 다음 주 월요일이 되면 새 파일이 자동 생성됨
- **회차(`episode`) 수집** — 카드 텍스트(제목 제외)에서 `N회`/`제 N회`/`N회차`/`첫 방송` 표기를 찾아 `episode`에 담고, 시청률과 마찬가지로 "가장 최근 방영 회차" 값이므로 요일 버킷(`ratingByDay[요일].episode`)에도 함께 저장한다. 네이버 위젯이 회차를 안 주는 마크업일 수도 있어 못 찾으면 `None`이며, 실행 로그(`[회차] ...`)에 추출 건수를 남겨 마크업이 바뀌어 0건이 되면 바로 알 수 있게 해둠
- **방영 기간 조회 — `lookup_air_periods()`** — 각 카드의 `link`(프로그램 네이버 정보 페이지)에 방영 기간이 표기된다. 방영 중이면 `2026.07.03. ~`, 종영했으면 `2026.07.03. ~ 2026.09.20.` 형태라 앞 날짜는 신규, 뒤 날짜는 종영 판정에 쓴다. `data/dramavariety/first_air_dates.json`에 캐시(실행당 최대 60건, 첫방송일 미확보 건 우선). 종영일까지 확보하면 더 조회하지 않고, 방영 중인 프로그램은 7일마다 다시 확인해 종영 여부를 갱신한다. ⚠️ 종영한 프로그램은 '방영중' 위젯에서 빠지므로, 조회 대상을 이번 수집분만이 아니라 저장된 과거 주차 데이터의 `link`에서도 끌어온다(`_stored_program_links`)
- **신규(New)·종영(End) 판정 — `recompute_new_flags()`** — 매 실행 마지막에 주차 파일 전체를 훑어 `isNew`/`newReason`/`isEnded`/`endDate`를 다시 계산(멱등, 값이 바뀐 파일만 재기록). 신규 우선순위는 ① `episode == 1`(→ `newReason: "episode"`), ② 캐시된 첫 방송일이 그 주(월~일)에 속함(→ `newReason: "firstAir"`). 종영은 캐시된 종영일이 그 주에 속하면 `isEnded`. ⚠️ "이력에 처음 등장" 방식은 쓰지 않는다 — 시청률 컷오프(드라마 5%/예능 1%) 미달로 초반 몇 주 데이터에 안 잡히다가 뒤늦게 컷오프를 넘은 프로그램(예: 전현무계획4 — 1회는 7/3인데 7/17에야 1.3%로 처음 등장)이 신규로 오탐되기 때문
- **수집 실패 감지 — `recent_collection_baseline()` + `warn_if_short()`** — 최근 완료된 4개 주차의 카테고리별 프로그램 수 중앙값을 기준선으로 삼아(진행 중인 주차는 제외, 과거의 부분 수집 주차에 끌려가지 않도록 중앙값 사용), 이번 수집이 기준선의 75% 미만이면 부분 수집으로 의심해 페이지를 다시 로드하고 재시도한다. 재시도 후에도 회복되지 않으면 `[⚠️ 수집 부족]` 경고를 로그에 남긴다 — 그 주에만 잡히는 시청률은 지나면 복구가 불가능하기 때문(네이버는 프로그램당 최신 회차만 노출)
- **중복 카드 통합 — `collapse_time_variants()`** — `id`에 시간이 포함돼 있어 네이버가 주중에 편성 시간 표기를 바꾸면 같은 회차가 두 장의 카드로 갈린다. 요일 구성이 완전히 같은 경우만 통합하고(아파트 토 10:40 / 일 10:30처럼 요일별로 시간이 다른 정상 편성은 유지), 이번 수집에서 확인된 시간을 대표로 남긴다
- **컷오프 미만 안내 — `newBelowCutoff`** — 컷오프 미만 카드도 수집은 해서 주차 파일의 `newBelowCutoff` 배열에 담아둔다(표에는 안 실림). `recompute_new_flags()`가 같은 기준으로 판정해 신규/종영이면 `isNew`/`isEnded`를 붙이고, 둘 다 아닌 것으로 확정되면 제거하며, 주중에 컷오프를 넘어 `programs`로 올라간 프로그램도 제거(그리드 배지가 대신 표시). 첫 방송일 미확인 항목은 판정 보류 상태로 유지
- 프론트(`index.html`)는 카드 제목 옆에 작은 `New`/`End` 배지를 그리고, 표 아래에 그 주의 **신규 편성**·**종영** 목록을 각각 요약한다. 목록에는 시청률 기준 이상(표에 실린 것)과 미만(`newBelowCutoff`)을 모두 넣고, 기준 미만 항목에만 `표 미표시` 표식을 단다. 클릭 시 네이버 검색결과로 이동

### 🏷 고정PGM — `fixed/{사}_fixed_programs.py` + `fixed/build_fixed_pgm.py`
- 홈쇼핑 4사(HD/GS/CJ/LT)의 매주 반복되는 고정 편성 프로그램(요일·시간 고정)을 수집
- 회사별로 각각 `homeshopping/fixed_programs/{사}.json`에 저장 후, `build_fixed_pgm.py`가 하나의 `merged.json`으로 통합해 프론트가 참조

### ⭐ 셀럽PGM — `fixed/re{hd|gs|cj|lt}.py` + `fixed/build_representative_programs.py`
- 강주은 굿라이프(CJ)·오감쇼(HD)·더 김창옥 라이브(CJ)·최화정쇼(CJ)·황정민쇼(HD)·지금 백지연(GS)·최유라쇼(LT)·소유진쇼(GS) 등 8개 셀럽 호스트 프로그램의 회차별 판매 상품을 수집
- `rehd.py`만 Playwright 사용(나머지는 정적 파싱), 프로그램별 결과 파일(`{사}_{코드}.json`)을 `build_representative_programs.py`가 `merged.json`으로 통합
- 프로그램별 결과 파일은 **"다음 방송" 기준으로 매번 덮어써지므로** 지난 회차가 사라진다. `fixed/build_celeb_history.py`가 수집 직후 돌면서 방송일 기준 월 파일(`representative_programs/history/{YYYY-MM}.json`)에 회차별로 누적한다 — 셀럽PGM 탭의 월 조회 데이터 소스
- **누적 규칙: 교체는 '방송 시작 전'에만.** 아직 시작 안 한 회차는 최신 수집분으로 교체하고(방송 전엔 라인업이 바뀐다), 이미 시작한 회차는 절대 덮어쓰지 않는다. 판정 기준이 날짜가 아니라 **시작 시각**인 게 핵심이다 — 날짜로만 보면 방송 당일 저녁 수집분이 그날 아침 방송의 확정 기록을 덮어쓴다(2026-08-22 왕영은의 톡투게더 08:20 방송 사고: 08:34에 정상 수집된 3건이, 같은 날 20:24 수집분의 다음 회차 잔여 노출 `8/22(토) 방송상품` 1건으로 교체되며 통째로 사라짐). 시작 시각은 `기존 기록 라벨 → 새 수집분 라벨 → schedule_raw` 순으로 찾고, 어디서도 못 읽으면 보존 쪽을 택한다

### 📊 홈쇼핑 랭킹 — `scraper/ranking_scraper.py`
- `datahub.hsmoa.com`에서 18개 카테고리별 인기 상품 랭킹을 수집
- 카테고리 호출 사이 0.6초 대기(`CATEGORY_REQUEST_DELAY`)로 과도한 요청 방지
- 직전 스냅샷(`link_cache.json` / 이전 날짜 파일)과 비교해 순위 변동을 계산, 인기/HOT/RISING 배지에 반영
- 날짜별 파일(`data/ranking/{날짜}.json`)과 최신 스냅샷(`latest.json`)을 함께 저장

### 💳 프로모션(카드할인) — `promotion_scraper.py`
- 홈쇼핑 4사(HD/GS/CJ/LT)의 날짜별 카드할인 일정(카드사/할인유형/할인율)을 수집해 `homeshopping/promotions/card_discounts.json` 하나에 저장
- 과거 날짜는 보존(이력 누적), 오늘 이후 날짜는 매 수집 시 통째로 교체. 실패한 회사는 기존 데이터 유지 + `status: "failed"` 표시
- 회사별 수집 방식 (4사 모두 구조 확정):
  - **HD**: `hmall.com/md/dpl/index`의 "한눈에 보는 카드 혜택" 스와이퍼. 같은 스와이퍼가 페이지에 따라 앵커명이 다름(`evnt_card_new`/`home_card`) - 둘 다 지원. 모바일 컨텍스트 + lazy 섹션이라 스크롤 도달 필요
  - **LT**: `lotteimall.com` 메인의 "카드 청구할인" `ul.cardbox` (Vue 렌더링 - Playwright)
  - **GS**: `m.gsshop.com` 메인의 "카드 혜택" 날짜 슬라이더 (`section.card-slider ul.card-detail-box`, `time.date` 빈값은 직전 날짜 상속, GS Pay 결합카드는 `card-gs-pay`로 판별). ⚠️ 처음에 쓰던 `event.gsshop.com/event/gs-pay-tip`은 월간 '안내' 페이지라 전체 일정이 오늘로 오탐돼 폐기
  - **CJ**: 혜택 홈탭(H00009)의 `li.item_card` 카드 목록 (`card_name`+`notify` 결합카드, `range`(~)=최대 할인율 → `max` 플래그). 날짜 없는 '현재 적용중' 혜택이라 오늘로 기록. 구조 변경 시 텍스트 휴리스틱 백업
- HD/LT/GS는 같은 날짜의 두 번째 카드부터 날짜 라벨이 없는 구조라 "직전 날짜 상속"으로 파싱
- 파싱 실패 시 `homeshopping/promotions/_debug_{사}.html` 스냅샷 저장 (PROMO_DEBUG=true면 성공해도 저장 - workflow_dispatch 입력으로 켤 수 있음)
- 회사 확장: 스크래퍼의 `COMPANIES` 리스트 + 프론트 `PM_COMPANIES`에 추가. NS(카드혜택 캘린더 table)가 다음 후보, KT는 이미지 배너뿐이라 보류

### 💰 가격추적 — `pricewatch_tracker.py` + `pricewatch_scraper.py`
- 4사 편성 데이터(최근 60일)에서 **3사 이상에 편성된 브랜드**를 자동 선정하고, 브랜드×회사별 최근 상품(최대 3개)의 상품페이지 판매가를 하루 2회(KST 10:40/16:40, `pricewatch.yml`) 수집
- 편성표에는 안 보이는 "방송 후 상시가 인하"와 구성(상품명) 변경을 잡는 것이 목적
- `pricewatch/tracked.json`(추적 대상) / `current.json`(최신 스냅샷) / `history/{YYYY-MM}.json`(변경 이벤트만 append — obs/price_drop/price_raise/name_change/sold_out/restock/new_product)
- 수집 폴백 체인: HD·CJ는 상세 API → HTML, LT는 HTML(www→m), GS는 `m.gsshop.com`(클라우드 IP 차단 시 최근 방송가 폴백, `src:"schedule"`로 구분). 같은 `src`끼리만 가격을 비교해 방송가↔상시가 혼동 오탐 방지, 1% 미만이면서 1,000원 미만 변동은 노이즈로 무시
- 프론트는 🔒보안 탭 로그인 후 "💰 가격추적" 서브탭 (브랜드×4사 표, 카테고리/4사공통 필터, 행 클릭 시 상품·이력)

### 🌤 날씨 — `weather.py`
- 서울 기준(위경도 격자 60,127 / 관측소ID 108)
- 과거(ASOS): 확정된 지난 달까지는 `weather/asos/{YYYY-MM}.json`에 한 번만 저장하고 재수집 안 함. 진행 중인 현재 달은 매일 1일~어제까지 통째로 재수집
- 미래(단기예보): `weather/forecast/latest.json`에 오늘~글피 매번 갱신
- 최초 실행 시 2023-01부터 백필
- **공휴일**: 한국천문연구원 특일 정보(`getRestDeInfo`)로 관공서 공휴일을 함께 수집해 `weather/holiday/{YYYY}.json`에 `{"2026-08-15": "광복절"}` 형태로 저장. 지역과 무관하므로 지역 디렉토리 없음
  - 대상: 2023년 ~ **내년**(연말에 다음 해 날짜를 조회해도 비지 않도록)
  - 지난 연도는 확정 과거라 건너뛰고, 올해·내년만 매일 재수집 후 병합 → 연중에 새로 지정되는 **임시공휴일**을 잡는다. 수집에 실패하면 그 지난 연도는 부분 데이터로 굳히지 않고 저장을 보류한다(다음 실행에서 재수집)
  - 키는 **`HOLIDAY_API_KEY` 시크릿**을 쓴다. 공공데이터포털은 서비스별로 활용신청이 따로라 기상청 키로 천문연 API를 부르면 403으로 막힌다. 시크릿이 없으면 `API_KEY`로 폴백
- **절기**: 24절기(`get24DivisionsInfo` — 입춘·입추·동지…)와 잡절(`getSundryDayInfo` — 초복·중복·말복·한식…)을 합쳐 `weather/term/{YYYY}.json`에 저장. 분류가 달라 엔드포인트가 나뉘어 있고, 쉬는 날이 아니라 `isHoliday=N`으로 오므로 공휴일과 달리 그 값으로 거르지 않는다
  - 공휴일과 저장 정책이 같아 `collect_yearly()` 한 함수를 조회 함수만 바꿔 재사용한다
  - 연 단위로 한 번에 조회해 평상시 하루 2회 요청(최초 백필 5회). `apis.data.go.kr` 접속이 아예 안 되면 첫 실패에서 바로 중단한다 — 요청마다 20초 연결 타임아웃이 쌓이면 워크플로우(`timeout-minutes: 20`)가 통째로 죽어 날씨 결과까지 커밋되지 못한다

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

탭 순서(index.html 기준): **홈쇼핑 → 고정PGM → 셀럽PGM → 편성맵 → 지상파·종편 → 드라마·예능 → 날씨 → 랭킹 → 카드할인 → 의견**

### 🛒 홈쇼핑 탭 (기본 진입 탭)
- 일 단위 조회, 24시간 × 회사 그리드 (라이브방송/데이터방송 토글)
- **전체/정규/심야 시간대 필터** — 심야는 방송 시작시각 01:00~05:00 이전(1~4시)만 표시
- 카테고리 칩 클릭 시 리스트뷰로 전환되며 시간대 필터와 동시 적용
- **조회 가능 시작일: 2026-06-21**

### 🏷 고정PGM 탭
- 4사(HD/GS/CJ/LT) 고정 편성 프로그램을 요일별 그리드로 비교

### ⭐ 셀럽PGM 탭
- 11개 셀럽 프로그램(황정민/강주은/오감쇼/김창옥/김신영/최화정/백지연/최유라/소유진/소이현/왕영은, 요일 순서로 정렬)을 **다중 선택해 동시 비교**하는 레이아웃
- 프로그램별 상품 데이터를 처음 열 때 한 번만 불러오는 캐시(`_celebrityTabLoaded`) 적용
- **신규 론칭 배지**: `CEL_LAUNCH_INFO`에 론칭일을 등록한 프로그램은 밴드 헤더 제목 옆에 배지가 붙는다. 론칭일 전에는 `론칭예정`(파랑), 론칭 후 `CEL_NEW_BADGE_DAYS`(60일) 동안 `NEW`(빨강), 그 뒤엔 자동으로 사라진다(날짜 기준 판단이라 배지를 지우러 다시 손댈 필요 없음). 현재 등록: `CJ_KSY.json`(김신영이 산다, 2026-08-18 론칭)

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
- **공휴일**은 요일과 무관하게 날짜를 빨간색으로 표시하고 옆에 이름을 붙이며 칸 배경을 옅은 붉은 톤으로 깐다(이름이 길면 말줄임 + `title` 툴팁). 오늘 강조가 공휴일 배경보다 우선
- **절기**(입추·초복 등)는 쉬는 날이 아니므로 같은 자리에 회색으로 표시해 공휴일 빨강에 묻히지 않게 위계를 준다
- 편성표 탭 상단 날씨 바에도 공휴일명을 덧붙인다. 예보 범위 밖 미래 날짜처럼 기온이 없는 날도 공휴일만 있으면 표시
- 홈쇼핑 탭 헤더의 날짜 왼쪽에도 같은 요약(최저/최고/강수 + 공휴일)을 표시. 클릭하면 날씨 탭으로 이동
- 달력은 **내년 12월까지** 넘길 수 있다(공휴일 수집 범위와 동일). 미래 달은 기온이 없지만 공휴일과 '작년 같은 날' 비교로 편성 계획에 쓴다

### 📊 랭킹 탭
- 18개 카테고리(대/소분류) 선택형 주간 랭킹, 순위 변동 표시(▲▼)와 인기/HOT/RISING 배지
- 채널 색상 코딩 적용

### 💳 카드할인 탭 (구 프로모션)
- "날짜 × 4사(HD/GS/CJ/LT)" 표로 오늘 이후의 카드할인 일정 표시, 오늘 행 하이라이트
- 카드사 브랜드 배경색 타일(카드명/할인율/할인유형, CJ 카드혜택 카드 디자인 참고), `~`=최대 할인율, 조건(`5만원이상` 등)은 작은 글씨로 부기
- 수집 실패한 회사는 헤더에 "수집실패" 뱃지 표시(마지막 성공 데이터는 유지)

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
- 홈쇼핑/고정PGM 스크래퍼 대부분이 `continue-on-error`(또는 `|| echo`)라서 특정 사가 그날 실패해도 워크플로우 전체는 성공으로 표시됨. 셀럽PGM·고정PGM 3개 워크플로우는 [2-1 수집 안전장치](#2-1-수집-안전장치-조용한-실패-방지)로 해결됨(검사 실패 시 잡 실패). **아직 미적용**: `homeshopping.yml`, `etc-scrape.yml`, `promotion.yml`, `scrape-ranking.yml` — 같은 방식으로 `SPECS`에 산출물을 추가하면 확장 가능
- `scrape-celebpgm.yml`에는 다른 워크플로우들과 달리 Pages 재배포 강제 트리거 스텝이 없어, 셀럽PGM 상품만 갱신된 날은 배포가 자동으로 안 될 수 있음
- 워크플로우별 push 재시도 로직이 통일돼 있지 않음(초기 워크플로우는 1회, 최근 워크플로우는 5회 재시도) — 통일 필요
