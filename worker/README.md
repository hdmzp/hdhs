# 텔레그램 알림 신청 봇 (Cloudflare Worker, 무료) 설정

1. 신청자가 봇에서 START → 받을 알림을 버튼으로 고르고 [신청하기]
2. 관리자에게 "🆕 알림 신청" + [✅ 승인] [❌ 거절] 버튼
3. 승인하면 신청자에게 "내일 아침부터 보내드려요" - 아침 발송이 Worker 의 `/export` 로 승인된 사람을 받아 간다

명령: `/start` 신청·변경(변경도 승인 필요), `/status` 내 알림, `/stop` 해지,
관리자 `/list` 받는 사람·승인 대기, 관리자 `/test` 신청 흐름 혼자 미리 보기.
관리자 본인은 START 로 고르면 승인 없이 바로 적용된다.
코드는 `telegram-bot.js`, 배포 설정은 저장소 맨 위 `wrangler.jsonc` (ADMIN_ID, KV 저장공간).
`notify/recipients.txt` 에 직접 적은 사람도 계속 받는다 (같은 ID 면 봇에서 고른 알림이 우선).

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

- 다른 계정으로 봇에서 START → 알림 고르는 버튼이 바로 와야 한다
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
