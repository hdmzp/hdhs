# 카카오톡 식품방송 아침 알림

매일 아침 8:30(KST)에 오늘의 **건강식품/일반식품 홈쇼핑 방송 편성**과 날씨를
카카오톡 **나와의 채팅**으로 보내주는 자동화입니다.

```
07/08(수)
☀️ 날씨: 최저 25도, 최고 27도, 비 60%

오늘 진행예정인 식품 방송입니다

💊 건강식품
49회
01:00 롯데 프롬바이오 위엔 매스틱 24주분
02:10 현대 하루틴 프리미엄 리포좀 비타민C 프로 ...
...

🥩 일반식품
55회
01:00 공영 앞다리 순살 족발 8팩 별미 양념꼬리 2팩
...
```

> 카카오 텍스트 메시지는 1건당 200자 제한이 있어, 하루 100건 안팎의 편성이면
> 메시지 15~20건으로 나뉘어 순서대로 도착합니다. 회사를 줄이면 메시지 수도 줄어듭니다.

## 최초 설정 (1회)

### 1. 카카오 개발자 앱 만들기

[developers.kakao.com](https://developers.kakao.com) 접속 후:

1. **내 애플리케이션 → 애플리케이션 추가**
2. **앱 설정 > 플랫폼** → Web 플랫폼 등록, 사이트 도메인 `https://localhost`
3. **제품 설정 > 카카오 로그인** → 활성화 ON, Redirect URI `https://localhost` 등록
4. **제품 설정 > 카카오 로그인 > 동의항목** → `카카오톡 메시지 전송(talk_message)` 선택 동의로 설정
5. **앱 설정 > 앱 키** 에서 **REST API 키** 복사

> '나에게 보내기'는 무료이며 비즈니스 채널·검수 없이 바로 사용 가능합니다.

### 2. 리프레시 토큰 발급

로컬 PC에서:

```bash
python kakao/get_token.py
```

안내에 따라 REST API 키 입력 → 출력된 URL 브라우저에서 열어 동의 →
주소창의 `https://localhost/?code=...` 를 붙여넣으면 `refresh_token`이 출력됩니다.

### 3. GitHub Secrets 등록

저장소 → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | 값 |
|---|---|
| `KAKAO_REST_API_KEY` | REST API 키 |
| `KAKAO_REFRESH_TOKEN` | 위에서 발급한 refresh_token |
| `GH_PAT` (선택) | repo 권한 Personal Access Token — 리프레시 토큰 자동 갱신용 |

### 4. 테스트

**Actions → 카카오톡 식품방송 아침 알림 → Run workflow** 로 수동 실행해서
카카오톡이 오는지 확인하세요.

로컬에서 발송 없이 메시지 미리보기:

```bash
DRY_RUN=1 python kakao/send_daily_food.py
```

## 옵션 (저장소 Variables)

**Settings → Secrets and variables → Actions → Variables**:

| Variable | 설명 | 기본값 |
|---|---|---|
| `COMPANIES` | 포함할 회사 코드 (쉼표 구분). `CJ,GS,HD,LT,NS,HNS,PUBLIC,KTALPHA,SHINSEGAE,SHOPPINGNT,SKSTOA` 중 선택 | 전체 |
| `WEATHER_REGION` | 날씨 지역 (`seoul`, `busan`, `daegu`, `incheon`, `gwangju`, `daejeon`, `ulsan`, `sejong`) | `seoul` |

발송 시각을 바꾸려면 `.github/workflows/kakao-daily-food.yml` 의 cron을 수정하세요.
(UTC 기준이므로 KST에서 9시간을 빼야 합니다. 예: KST 08:30 → `30 23 * * *`)

## 토큰 유효기간 안내

- 리프레시 토큰은 **약 2개월** 유효하며, 매일 실행 시 만료 1개월 전부터 카카오가
  새 토큰을 내려줍니다.
- `GH_PAT` Secret을 등록해 두면 새 토큰이 내려올 때 워크플로우가
  `KAKAO_REFRESH_TOKEN` Secret을 자동 갱신합니다.
- `GH_PAT` 없이 쓰는 경우, 2개월마다 `get_token.py`로 재발급해서 Secret을
  수동 갱신해야 합니다. (만료되면 Actions 로그에 토큰 갱신 실패가 찍힙니다)

## 확장: 채널 친구들에게 보내기

지금 구조에서 `send_to_me()` 부분만 교체하면 됩니다. 채널(친구톡/알림톡) 발송은
카카오 공식 딜러사(솔라피, 알리고, NHN Cloud 등) 가입과 발신프로필 연동이 필요하고
건당 요금(약 8~25원)이 발생합니다. 메시지 생성 로직은 그대로 재사용 가능합니다.
