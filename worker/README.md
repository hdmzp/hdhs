# 텔레그램 알림 신청 봇 (Cloudflare Worker, 무료) 설정

누가 봇에서 START 를 누르면 바로 "접수됐어요" 답장이 가고, 관리자에게 이름·ID 가 온다.
코드는 `telegram-bot.js` 한 파일. 받을 알림은 관리자가 `notify/recipients.txt` 에 적는다.

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

- 다른 계정으로 봇에서 START → 바로 "✅ 알림 신청이 접수됐어요" 답장
- 관리자에게 "🆕 알림 신청" + ID 가 옴 → 맨 아래 줄을 `notify/recipients.txt` 에 붙여넣고 받을 알림만 남기기
- 관리자 본인이 START 를 누르면 "관리자 계정이에요" 라고 답한다
- 답이 없으면 Worker → *Logs* 에서 오류 확인, 또는 `/setup` 을 다시 열어보기

## 코드를 고쳤을 때

Worker 가 GitHub 저장소와 연결돼 있으면 main 에 push 될 때마다 자동으로 다시 배포된다.
무엇을 배포할지는 저장소 맨 위 `wrangler.jsonc` 가 정한다 (이 파일이 없으면 저장소 전체가
웹사이트로 배포돼 봇 코드가 덮어써진다). 대시보드에서 직접 붙여넣은 코드는 다음 push 때 덮어써진다.
연결 안 된 Worker 라면 `telegram-bot.js` 를 다시 붙여넣고 *Deploy*. (`/setup` 은 다시 안 열어도 된다)

## 비용

Workers 무료 플랜은 하루 10만 번 요청까지 무료. 신청 봇은 하루 몇 번 쓰는 수준이라 돈 나갈 일 없다.
