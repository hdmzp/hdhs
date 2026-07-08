# -*- coding: utf-8 -*-
"""
카카오 '나에게 보내기'용 리프레시 토큰 최초 발급 도우미 (로컬 PC에서 1회 실행)

== 사전 준비 (https://developers.kakao.com) ==
1. [내 애플리케이션] → 애플리케이션 추가
2. [앱 설정 > 플랫폼] → Web 플랫폼 등록 (사이트 도메인: https://localhost)
3. [제품 설정 > 카카오 로그인] → 활성화 ON,
   Redirect URI에 https://localhost 등록
4. [제품 설정 > 카카오 로그인 > 동의항목] →
   '카카오톡 메시지 전송(talk_message)' 을 '선택 동의'로 설정
5. [앱 설정 > 앱 키] 에서 REST API 키 확인

== 사용법 ==
  python kakao/get_token.py
  → 안내에 따라 REST API 키 입력 → 출력된 URL을 브라우저에서 열어 동의
  → 주소창의 https://localhost/?code=XXXX 전체(또는 code 값)를 붙여넣기
  → 출력된 refresh_token 을 GitHub Secrets 에 등록
"""

import json
import urllib.parse
import urllib.request

KAUTH = "https://kauth.kakao.com"
REDIRECT_URI = "https://localhost"


def main():
    rest_key = input("REST API 키: ").strip()

    auth_url = (
        KAUTH
        + "/oauth/authorize?"
        + urllib.parse.urlencode(
            {
                "client_id": rest_key,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": "talk_message",
            }
        )
    )
    print("\n1) 아래 URL을 브라우저에서 열고 카카오 계정으로 동의해 주세요:\n")
    print(auth_url)
    print(
        "\n2) 동의하면 https://localhost/?code=... 로 이동합니다."
        "\n   (페이지가 안 열려도 정상입니다. 주소창의 값만 필요합니다)\n"
    )
    raw = input("이동된 전체 URL 또는 code 값: ").strip()
    if "code=" in raw:
        code = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)["code"][0]
    else:
        code = raw

    data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": rest_key,
            "redirect_uri": REDIRECT_URI,
            "code": code,
        }
    ).encode()
    req = urllib.request.Request(KAUTH + "/oauth/token", data=data)
    with urllib.request.urlopen(req) as resp:
        body = json.load(resp)

    print("\n=== 발급 완료 ===")
    print("access_token  (유효 %s초):" % body.get("expires_in"), body.get("access_token"))
    print("refresh_token (유효 %s초, 약 2개월):" % body.get("refresh_token_expires_in"))
    print(body.get("refresh_token"))
    print(
        "\nGitHub 저장소 → Settings → Secrets and variables → Actions 에 등록:\n"
        "  KAKAO_REST_API_KEY  = (위에서 입력한 REST API 키)\n"
        "  KAKAO_REFRESH_TOKEN = (위 refresh_token 값)"
    )


if __name__ == "__main__":
    main()
