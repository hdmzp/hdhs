# 텔레그램 알림 신청 봇 (Cloudflare Worker, 무료) 설정

1. 신청자가 봇에서 START → [셀럽PGM] [카테고리 편성] [둘 다]. 셀럽PGM 은 누르면 바로 신청, 카테고리는 패션/미용/식품/리빙 을 골라 [신청하기]
2. 관리자에게 "🆕 알림 신청" + [✅ 승인] [❌ 거절] 버튼
3. 승인하면 신청자에게 "내일 아침부터 보내드려요" - 아침 발송이 Worker 의 `/export` 로 승인된 사람을 받아 간다

4. **🔔 브랜드 알림** (`/brand` 또는 START → [🔔 브랜드 알림]): 브랜드명·상품명을 짧게 보내면 실제 4사 편성에 나온 브랜드 후보를 버튼으로 보여주고, 눌러서 등록한다 (여러 개 가능, 1인 20개까지).
   후보는 `homeshopping/brand_index.json`(편성 수집 때마다 `tools/build_brand_index.py` 가 갱신)에서 찾는다.
   승인 전인 사람은 첫 등록 때 한 번만 승인 요청, 승인된 사람은 승인 없이 바로. 아침 발송은 `notify/send_brand_alert.py` (그날 방송이 있는 사람에게만).
   검색으로도 안 이어지는 다른 이름(예: B&RC = 에이스바이옴)은 `notify/brand_alias.json` 에 적는다. 관리자 `/list` 맨 아래에 못 찾은 입력이 보인다.

명령: `/start` 신청·변경(변경도 승인 필요), `/brand` 브랜드 알림, `/status` 내 알림, `/stop` 해지,
관리자 `/list` 받는 사람·승인 대기, 관리자 `/test` 신청 흐름 혼자 미리 보기.
관리자 본인은 START 로 고르면 승인 없이 바로 적용된다.
코드는 `telegram-bot.js`, 배포 설정은 저장소 맨 위 `wrangler.jsonc` (ADMIN_ID, KV 저장공간).
받는 사람은 여기 한 곳에서만 관리한다 (관리자 본인도 START 로 골라야 받는다).

## 처음 한 번만 (10분)

1. **가입**: https://dash.cloudflare.com/sign-up (무료, 카드 등록 없음)
2. **Worker 만들기**: 왼쪽 메뉴 *Compute (Workers) → Workers & Pages* → *Create* →
   *Start with Hello World!* → 이름 `hdhs-bot` → *Deploy*
3. **코드 붙여넣기**: *Edit code* → 왼쪽 코드를 전부 지우고 `worker/telegram-bot.js` 내용을 통째로 붙여넣기
   → 오른쪽 위 *Deploy*
4. **설정 넣기**: Worker 화면 → *Settings* → *Variables and Secrets* → *Add*
   - `BOT_TOKEN` · 종류 *Secret* · 값: BotFather 봇 토큰 (GitHub Secrets 의 `TELEGRAM_BOT_TOKEN` 과 같은 값)
   - `ADMIN_ID` · 종류 *Text* · 값: `8921978250`
   - *Deploy* (또는 Save)
5. **연결**: Worker 화면 위에 보이는 주소(`https://hdhs-bot.<계정>.workers.dev`) 뒤에 `/setup` 을 붙여 브라우저로 열기
   → "✅ 연결 완료!" 가 나오면 끝

## 확인

- 다른 계정으로 봇에서 START → "어떤 알림을 받고 싶으세요?" 버튼이 바로 와야 한다
- 고르고 [신청하기] → 관리자에게 "🆕 알림 신청" + 승인·거절 버튼
- 관리자 본인이 START 를 누르면 "관리자 계정이에요" 라고 답한다. `/test` 를 보내면 신청자 화면부터 승인까지 혼자 확인할 수 있다 (테스트는 발송 목록에 안 들어간다)
- 답이 없으면 Worker → *Logs* 에서 오류 확인, 또는 `/setup` 을 다시 열어보기

## 코드를 고쳤을 때

Worker 가 GitHub 저장소와 연결돼 있으면 main 에 push 될 때마다 자동으로 다시 배포된다.
무엇을 배포할지는 저장소 맨 위 `wrangler.jsonc` 가 정한다 (이 파일이 없으면 저장소 전체가
웹사이트로 배포돼 봇 코드가 덮어써진다). 대시보드에서 직접 붙여넣은 코드는 다음 push 때 덮어써진다.
연결 안 된 Worker 라면 `telegram-bot.js` 를 다시 붙여넣고 *Deploy*. (`/setup` 은 다시 안 열어도 된다)

## 비용

Workers 무료 플랜은 하루 10만 번 요청까지 무료. 신청 봇은 하루 몇 번 쓰는 수준이라 돈 나갈 일 없다.

## 아침 발송 시계 (Cloudflare Cron)

GitHub 예약 실행은 3~6시간씩 늦게 돌아서, 아침 발송 시간은 봇이 잡는다 (`wrangler.jsonc` 의 `triggers.crons`).

| KST | 봇이 하는 일 |
|---|---|
| 05:30 | 셀럽PGM 수집(`scrape-celebpgm.yml`) 실행 요청 |
| 06:15 | 그 수집이 실패했거나 안 돌았으면 한 번 더 (성공했으면 건너뜀) |
| 07:00 | 아침 알림(`telegram-daily-food.yml`) 실행 요청 |
| 08:10 · 10:10 · 20:10 · 22:10 | 고정PGM·셀럽 대표프로그램 수집(`rep-pgm-scrape.yml`) 실행 요청 |
| 월요일 08:45 | 드라마·예능 편성 수집(`scrape-dramavariety.yml`) 실행 요청 |

- 문제가 생기면 관리자에게 텔레그램으로 알린다. 관리자가 봇에 `/check` 를 보내면 GitHub 토큰을 점검한다 (읽기 + 관리자에게만 셀럽PGM 테스트 발송).
- 예비: 알림은 GitHub 예약 KST 08:00 (07:00 알림이 나갔으면 봇의 `/sent` 를 보고 건너뛴다). 셀럽PGM 데이터는 같은 스크립트를 도는 rep-pgm-scrape.yml 이 낮에도 갱신한다 (두 워크플로는 concurrency 그룹으로 겹치지 않게 차례로 돈다).

### 처음 한 번: GITHUB_TOKEN 넣기

1. GitHub 오른쪽 위 프로필 → *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*
2. 이름 `hdhs-bot`, 만료는 원하는 기간 (만료되면 다시 만들어 넣어야 한다)
3. *Repository access*: Only select repositories → `hdmzp/hdhs`
4. *Permissions → Repository permissions → Actions*: **Read and write**
5. *Generate token* → 나온 값 복사
6. Cloudflare → `hdhs-bot` → *Settings → Variables and Secrets → Add* → 이름 `GITHUB_TOKEN`, 종류 *Secret*, 값 붙여넣기 → *Deploy*
