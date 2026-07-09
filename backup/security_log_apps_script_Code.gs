/**
 * hdhs 대시보드 '보안' 탭 접속 로그 백엔드 (구글 시트 누적 저장)
 * ------------------------------------------------
 * 1) 구글 스프레드시트를 하나 만든다.
 *    - 시트(하단 탭) 이름을 'log' 로 한다 (아래 SHEET_NAME과 동일해야 함).
 *    - 주소창의 .../d/여기값/edit 에서 "여기값"이 SPREADSHEET_ID.
 * 2) script.google.com → 새 프로젝트 → 기본 코드 지우고 이 파일 전체 붙여넣기.
 * 3) 아래 SPREADSHEET_ID 를 본인 시트 ID로 수정.
 * 4) 배포 > 새 배포 > 유형: 웹 앱
 *      - 실행 계정: 나
 *      - 액세스 권한: 전체 허용(익명 포함)
 * 5) 배포 후 나오는 "웹 앱 URL"을 복사해서
 *    index.html 의 SEC_LOG_URL 상수에 붙여넣기.
 * (코드를 수정하면 매번 "새 버전"으로 재배포해야 반영됩니다)
 *
 * ※ 참고: Apps Script 웹앱은 요청자의 실제 IP를 서버에서 얻을 수 없어서,
 *   IP는 브라우저(index.html)에서 api.ipify.org로 조회해 함께 보내온 값을 기록합니다.
 */

const SPREADSHEET_ID = '1_TwEbNyFKYiXHuFPCHx5EcqpU9EgbChQB7m3twu5QiA';
const SHEET_NAME = 'log';

function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);

    const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
    let sheet = ss.getSheetByName(SHEET_NAME);
    if (!sheet) {
      sheet = ss.insertSheet(SHEET_NAME);
    }
    // 헤더가 없으면 1행에 헤더 추가
    if (sheet.getLastRow() === 0) {
      sheet.appendRow(['접속시각', '사번', 'IP', '결과', 'UserAgent', '페이지']);
    }

    sheet.appendRow([
      new Date(),               // 서버 기준 접속 시각
      data.empNo || '',
      data.ip || '',
      data.result || '',        // success | fail
      data.userAgent || '',
      data.page || ''
    ]);

    return ContentService
      .createTextOutput(JSON.stringify({ result: 'success' }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ result: 'error', message: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
