/**
 * hdhs 대시보드 '의견' 탭 백엔드 (이메일 전용, 시트 불필요)
 * ------------------------------------------------
 * 1) script.google.com 접속 → 새 프로젝트
 * 2) 기본 코드 지우고 이 파일 전체를 붙여넣기
 * 3) 아래 NOTIFY_EMAIL을 본인 Gmail 주소로 수정
 * 4) 배포 > 새 배포 > 유형: 웹 앱
 *      - 실행 계정: 나
 *      - 액세스 권한: 전체 허용(익명 포함)
 * 5) 배포 후 나오는 "웹 앱 URL"을 복사해서
 *    index.html의 FB_SCRIPT_URL에 붙여넣기
 * (코드를 수정하면 매번 "새 버전"으로 재배포해야 반영됩니다)
 */

const NOTIFY_EMAIL = 'mj2040354@gmail.com';

function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);

    MailApp.sendEmail({
      to: NOTIFY_EMAIL,
      subject: `[hdhs 의견] ${data.title || ''}`,
      body:
`제목: ${data.title || ''}
유형: ${data.type || ''}

내용:
${data.content || ''}

접수 시각: ${new Date().toLocaleString('ko-KR')}`
    });

    return ContentService
      .createTextOutput(JSON.stringify({ result: 'success' }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ result: 'error', message: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
