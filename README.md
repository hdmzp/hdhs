# 지상파·종편 방송 편성표

네이버 검색 'TV 편성표' 위젯을 매일 자동으로 크롤링해서,
시간(행) × 채널(열) 그리드로 보여주는 정적 웹사이트.

## 구조

```
.
├── index.html                      # 웹사이트 본체 (data/*.json을 읽어서 그리드로 표시)
├── naver_schedule_scraper.py       # 크롤러 (8개 채널 x 7일치 편성표 수집)
├── data/
│   └── 2026-06-21.json             # 날짜별 편성표 데이터 (크롤러가 매일 추가)
└── .github/workflows/
    └── daily-scrape.yml            # 매일 자동 실행 + 자동 커밋
```

## 채널 (8개)

- 지상파: KBS1, KBS2, MBC, SBS
- 종합편성: JTBC, MBN, TV조선, 채널A

## 동작 방식

1. `daily-scrape.yml`이 매일 한국시간 06:00에 자동 실행
2. `naver_schedule_scraper.py`가 8개 채널을 검색해서 각각 "오늘 -1일 ~ +5일"
   (7일치) 편성표를 가져옴
3. 날짜별로 `data/{YYYY-MM-DD}.json` 파일에 저장 (8개 채널 통합)
4. 매일 실행되므로 같은 날짜가 여러 번 갱신되며, 방송 종료 후 보정된
   최신 데이터로 점점 더 정확해짐
5. 과거 날짜 파일은 덮어쓰지 않는 한 계속 남아있어 자동으로 누적 보관됨

## GitHub Pages로 배포하기

1. 저장소 Settings → Pages → Source를 "Deploy from a branch"로 설정,
   브랜치는 main(또는 master), 폴더는 `/ (root)` 선택
2. 몇 분 후 `https://{사용자명}.github.io/{저장소명}/` 에서 접속 가능
3. `index.html`은 `data/` 폴더의 JSON을 fetch로 읽으므로,
   Actions가 새 날짜를 추가할 때마다 자동으로 조회 가능한 날짜 범위가 늘어남

## 수동 실행 / 테스트

저장소의 Actions 탭 → "Daily TV Schedule Scrape" → "Run workflow" 버튼으로
스케줄과 무관하게 즉시 한 번 실행해볼 수 있음.

## 알려진 한계

- 네이버 위젯이 마크업을 두 가지 버전(weekly-full / weekly-simple)으로
  번갈아 보여줄 수 있음. 크롤러는 둘 다 처리하지만, simple 버전이 뜨면
  한 시간대의 프로그램이 일부 누락될 수 있음(대표 프로그램 1개만 제공됨)
- 종료시각이 원본에 없어서 "다음 프로그램 시작 = 이전 프로그램 종료"로
  역산함. 따라서 실제 방송 종료시각과 약간 다를 수 있음
- 두 파서 버전 모두 실패하면 해당 채널은 `data/_debug_fail_{채널명}.html`로
  저장되고 그날 데이터에서 누락됨 (구조 변경 감지용)
