"""
1회성 로그인 스크립트.

이 폴더에 전용 크롬 프로필(chrome_profile/)을 하나 만들어서 라방바 로그인 세션을
저장해둔다. lavangba_scraper.py는 headless로 이 프로필을 재사용해서 로그인 상태를 그대로 쓴다.

사용법:
    venv\\Scripts\\python.exe login_setup.py

브라우저 창이 뜨면 직접 live.ecomm-data.com에 로그인한 뒤, 이 터미널로 돌아와서
Enter를 누르면 세션이 저장되고 창이 닫힌다.

주의: 이 프로필은 자동화 전용으로만 쓰고, 평소 브라우징에 쓰는 크롬과는 분리해서 관리할 것.
스케줄러가 실행 중일 때 이 프로필로 크롬을 동시에 띄우면(중복 실행) 프로필 잠금으로 실패한다.
"""

import os
from playwright.sync_api import sync_playwright

PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile")

_CHECK_JS = """async () => {
  const today = new Date();
  today.setDate(today.getDate() - 1);
  const yymmdd = today.toISOString().slice(2,10).replace(/-/g,'');
  const listRes = await fetch('https://live.ecomm-data.com/api/schedule/list_hs', {
    method: 'POST',
    headers: {'content-type':'application/json','domain':'ecomm-data.com'},
    body: JSON.stringify({date: yymmdd}),
    credentials: 'omit'
  });
  const listData = await listRes.json();
  const list = listData.list || [];
  if (list.length === 0) return {ok: false, reason: 'no_shows'};
  const itemsRes = await fetch('https://live.ecomm-data.com/api/hsshow/items', {
    method: 'POST',
    headers: {'content-type':'application/json','domain':'ecomm-data.com'},
    body: JSON.stringify({hsshow_id: list[0].hsshow_id, page:1, size:1, order:['sales_amt/desc'], with_rcd:false}),
    credentials: 'include'
  });
  const itemsData = await itemsRes.json();
  const items = itemsData.items || [];
  if (items.length === 0) return {ok: false, reason: 'no_items'};
  return {ok: items[0].sales_amt !== null, reason: 'mask=' + itemsData.mask};
}"""


def is_logged_in(page):
    result = page.evaluate(_CHECK_JS)
    return result.get("ok"), result.get("reason")


def main():
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            viewport={"width": 1280, "height": 900},
        )
        # launch_persistent_context가 기본 빈 탭을 자동으로 하나 띄우므로, 새 탭을 추가로
        # 열지 않고 그 탭을 그대로 재사용한다 (탭 2개가 떠서 헷갈리는 것 방지).
        page = context.pages[0] if context.pages else context.new_page()
        for extra in context.pages[1:]:
            extra.close()
        page.bring_to_front()
        page.goto("https://live.ecomm-data.com/", wait_until="domcontentloaded", timeout=60000)
        print("브라우저 창에서 라방바에 로그인하세요.")
        while True:
            print("로그인을 마쳤으면 이 터미널로 돌아와서 Enter를 누르세요 (로그인 상태를 확인합니다).")
            input()
            try:
                ok, reason = is_logged_in(page)
            except Exception as e:
                print(f"[확인 실패] 확인 중 오류: {e} -- 다시 시도해주세요.")
                continue
            if ok:
                print("로그인 확인됨 (매출액 마스킹 해제 확인).")
                break
            print(f"[아직 로그인 안 된 것으로 보입니다: {reason}] 브라우저에서 로그인 상태(우측 상단)를 다시 확인하고 Enter를 눌러주세요.")
        context.close()
    print("완료. chrome_profile/ 에 로그인 세션이 저장되었습니다.")


if __name__ == "__main__":
    main()
