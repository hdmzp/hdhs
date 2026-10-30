# 텔레그램 봇 (Cloudflare Worker) 설정

봇 버튼에 바로 답하려면 메시지를 받는 서버가 필요해서 Cloudflare Workers 무료 플랜을 쓴다.
코드는 `telegram-bot.js` 한 파일이고, 구독 설정은 Cloudflare KV에 저장된다.
아침 발송(`telegram-daily-food.yml`)은 Worker의 `/export`로 구독자 목록을 받아 간다.

## 처음 한 번만 하는 설정

1. **Cloudflare 가입**: https://dash.cloudflare.com/sign-up (무료)
2. **KV 만들기**: 왼쪽 메뉴 *Storage & Databases → KV* → *Create* → 이름 `hdhs-subs`
3. **Worker 만들기**: *Compute (Workers) → Workers & Pages → Create → Worker* →
   이름 `hdhs-bot` → *Deploy*
4. **코드 붙여넣기**: 만든 Worker에서 *Edit code* → 원래 코드를 지우고
   `worker/telegram-bot.js` 내용을 통째로 붙여넣기 → *Deploy*
5. **설정 넣기**: Worker → *Settings*
   - *Bindings → Add → KV namespace*: 변수 이름 `SUBS`, namespace `hdhs-subs`
   - *Variables and Secrets → Add*
     - `BOT_TOKEN` (Secret): BotFather의 봇 토큰 (GitHub Secrets `TELEGRAM_BOT_TOKEN`과 같은 값)
     - `EXPORT_KEY` (Secret): 아무 긴 문자열 (영문·숫자 30자 이상 추천)
     - `ADMIN_IDS` (Text): `8921978250` (관리자 텔레그램 ID, 여러 명이면 쉼표)
6. **연결**: 브라우저에서 `https://hdhs-bot.<계정>.workers.dev/setup?key=<EXPORT_KEY>` 열기
   → "✅ 텔레그램 webhook 연결 완료"가 나오면 끝
7. **GitHub에 주소·키 등록**: 저장소 *Settings → Secrets and variables → Actions*
   - *Variables* 탭: `SUBS_URL` = `https://hdhs-bot.<계정>.workers.dev`
   - *Secrets* 탭: `SUBS_KEY` = 5번의 `EXPORT_KEY`와 같은 값

## 코드를 고쳤을 때

`telegram-bot.js`를 다시 붙여넣고 *Deploy*. (6번 연결은 다시 안 해도 된다.
명령어 메뉴를 바꿨으면 6번 주소를 한 번 더 열기)

## 확인

- 봇에 `/start` → 버튼이 바로 와야 한다
- 관리자가 `/list` → 구독자 표
- Worker *Logs*에서 오류 확인
