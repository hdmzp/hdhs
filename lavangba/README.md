# 라방바(live.ecomm-data.com) 11개사 방송/매출 자동 수집

로그인이 필요한 라방바 매출 데이터를, 로컬 PC의 전용 크롬 프로필 로그인 세션을 재사용해서
매일 자동으로 긁어온다. GS/CJ/현대/롯데/NS/공영/신세계/쇼핑엔티/SK스토아/홈앤쇼핑/KT알파
11개사 대상.

방송별 상품 시작/종료 시각은 이 저장소의 `homeshopping/{코드}_live/{YYYY-MM}.json`
(각 회사 자체 편성표 스크레이퍼 결과)을 그대로 fetch해서 씀 — 별도 입력 불필요.

## 설정 (최초 1회, PC 1대에서)

```powershell
cd lavangba
python -m venv venv
venv\Scripts\python.exe -m pip install playwright requests
venv\Scripts\python.exe -m playwright install chromium
venv\Scripts\python.exe login_setup.py   # 브라우저가 뜨면 라방바 로그인, 완료되면 터미널에서 Enter
```

## 매일 자동 실행 (Windows 작업 스케줄러)

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\run_scraper.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At 06:15
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 1) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "LavangbaScraper" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "라방바 11개사 방송/매출 자동 수집"
```

PC가 절전모드여도 깨워서 실행됨(`WakeToRun`). 완전 종료 상태면 못 돌고, 켜지면
`StartWhenAvailable`로 놓친 회차를 바로 실행한다.

결과는 `data/{날짜}.json` / `.tsv`, 실행 로그는 `logs/`에 쌓인다(30일 보관).

## 동작 원리 / 주의사항

- **headless로 돌리면 로그인 여부와 무관하게 매출액이 마스킹된다.** 라방바가
  User-Agent의 "HeadlessChrome" 표기를 감지하는 것으로 보임. 그래서 `headless=False`로
  띄우되 창을 화면 밖(`--window-position=-32000,-32000`)에 배치해서 화면엔 안 보이면서도
  "진짜 브라우저"로 인식되게 우회한다.
- API 호출은 쿠키를 꺼내 별도 `requests` 세션으로 쓰지 않고, 그 브라우저 페이지 안에서
  `fetch()`를 실행하는 방식(`page.evaluate`)으로 한다. 쿠키만 재사용하면 마스킹이 풀리지
  않는 걸 확인함.
- 상품 여러 개를 파는 "복합" 방송은 각 상품의 시계열(`sales_amt_rcd`)이 편성표 시간대
  구간에서 보인 활동 비중만큼 매출을 비례 배분한다(1등 구간이 전부 가져가는 방식이 아님).
  구간별 숫자는 최선 추정치이고, 방송 전체 합계는 항상 정확하다.
- 편성표 항목 ↔ 라방바 방송 매칭: 편성 항목에 저장된 `hsshow_id`(GS/기타 7사처럼
  라방바 기반으로 만든 편성표)로 1:1 정확 매칭을 우선하고, id가 없는 항목
  (HD/CJ/LT 자사몰 편성표, 과거 데이터)은 시간 겹침 + 제목 유사도로 **한 방송에만**
  배타 배정한다. SK스토아/신세계처럼 같은 시간대에 방송 2개가 병행 편성되는 채널에서
  두 방송이 같은 편성 항목을 중복으로 가져가 브랜드/카테고리/가격/링크가 뒤바뀌던
  문제 방지.
- `chrome_profile/`은 로그인 세션(쿠키)이 들어있으므로 **절대 커밋하지 말 것**
  (`.gitignore`에 이미 포함).
- 로그인 세션은 언젠가 만료될 수 있다. 만료되면 스크립트가 경고를 남기고
  조용히 종료된다(`logs/` 확인) — 이때 `login_setup.py`를 다시 실행하면 됨.
