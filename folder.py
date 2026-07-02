import os
import shutil

# 1. 정리 대상 폴더
SOURCE_FOLDERS = [
    r"C:\Users\Hhome\Downloads",
    r"C:\Users\Hhome\Documents"
]

# 2. 이동 대상 폴더 (쉼표 누락 수정됨)
DEST_FOLDERS = {
    '추출데이터': r"C:\Users\Hhome\Desktop\(P)추출데이터",
    'HOMS': r"C:\Users\Hhome\Desktop\(P)HOMS",
    '영업계획': r"C:\Users\Hhome\Desktop\(P)영업계획",
    '일보': r"C:\Users\Hhome\Desktop\(P)일보",
    '이미지': r"C:\Users\Hhome\Desktop\(P)이미지",
    '팀사업부PGM': r"C:\Users\Hhome\Desktop\(P)팀사업부PGM",
    '상품기술서': r"C:\Users\Hhome\Desktop\(P)기술서,제안서",
    '예산목표': r"C:\Users\Hhome\Desktop\(P)예산목표",
    '민지': r"C:\Users\Hhome\Desktop\(MJ)개인자료_민지",
    'PBI': r"C:\Users\Hhome\Desktop\PBI"
}

# 3. 이동/삭제 조건
def get_destination(file_name, ext_lower):
    if ext_lower == ".tmp":
        return "DELETE"
    if any(kw in file_name for kw in ["undefined","PGM별 실적","방송편성희망안_","지난 방송 진행조회_","제목 없는","data",".RPA","선편성 캘린더",
                                     "LIVE방송PGM총괄장","비중 시뮬레이션", 
                                     "●2025.12.홈쇼핑.라이브전략팀.Live총괄장",
                                     "PGM별 실적 (카테고리별) ",
                                     "★2025.라이브방송.사업부PGM.취합","일자별 실적","주 편성표","PGM총괄장(25년)","PPL","ppl","구조", "행파사","현홈문고",
                                     "조직목록","이용자수","현금영수증","상품 후보군","감액목표원목표","방송코드별","정보고시","편성공유",
                                     "모니터링 현황","팀사업부pgm.취합","론칭캘린더","팀pgm 운영현황","사업부PGM결과","_상생 보규 전달",
                                     "상생방송","설 준비 현황","고정PGM편성표('","드라마예능편성표","슈퍼케어 리뷰","직매입 재고 소진안","황정민쇼 운영보고 1",
                                     "데이터방송 PGM목표","분단위목표","밀라노동계올림픽","밀라노", "(점프투파이썬)", "(안병주)","평가감액",
                                     "알부민","공적서 모음","구매고객 데이터","(관리용)","저효율 예상 기간 대응",
                                     "패션편성희망안", "19-21시대","건식 피피엘 편성표","고정PGM현황(","★황정민쇼", "4월이후_라이프사업부",

                                     "CJ","비전추진과제","★2025.오감쇼.운영리뷰_초안",
                                     "●2025.12.홈쇼핑.라이브전략팀.Live총괄장(-",
                                     "모니터링파일리부트(~05","행사기간비중","PGM별 실적 (카테고리","목표노출분",
                                     "옴부즈맨 프로그램","명함신청",
                                     "메종 개편","링크를_클릭하세요","위바이옴",
                                     "★리빙사업부","붙임1", "시뮬레이션", "사업부PGM.취합","hsshow_", "이월", "뷰티 협력사", "고정PGM편성표",
                                     "2025.12.홈쇼핑.라이브전략팀.Live총괄장",
                                     "PGM별 실적 (월별)", "PGM별 실적 (카테고리별)", "편성 달력", "연령별", "오감쇼 비교",
                                     "재방리스트","CJ재방", "CJ 재","뷰티 잡화","마시는하루견과","왕혜문 영양밥","목요일 저녁","업무분장(2026)",
                                     "주간 목표 설정", "data (", "방송의지목표_"] ):
        return "DELETE"
    if "건강식품 1팀 실적 취합양식 (" in file_name:
        return "DELETE"
    if "예상시뮬레이션" in file_name:
        return "DELETE"
    if ext_lower == ".exe":
        return "DELETE"
    if ext_lower == ".tmp":
        return "DELETE"
    if ext_lower == ".mp4":
        return "DELETE"
    if ext_lower in [".png", ".jpg", ".jpeg",".ico"]:
        return DEST_FOLDERS['이미지']
    if ext_lower == ".csv":
        return DEST_FOLDERS['추출데이터']
    if any(kw in file_name for kw in ["매체별 목표","매체별"]):
        return DEST_FOLDERS['예산목표']
    if any(kw in file_name for kw in ["기획pgm", "기획 pgm","기획PGM","사업부PGM","팀장PGM","사업부팀장"]):
        return DEST_FOLDERS['팀사업부PGM']
    if any(kw in file_name for kw in ["매출속보", "주 편성표","방송목표","의지목표 목록","시급조정표","주간재방", "일자별 편성_", "편성 희망안 작성", "방송상품 사전점검","편성상품 상세"]):
        return DEST_FOLDERS['HOMS']
    if any(kw in file_name for kw in ["영업계획","편성전략","왕톡.방송편성","경쟁사 월간"]):
        return DEST_FOLDERS['영업계획']
    if ext_lower in [".pdf", ".pptx"]:
        return DEST_FOLDERS['영업계획']
    if any(kw in file_name for kw in ["기술서","회의자료","제안서","제안"]):
        return DEST_FOLDERS['상품기술서']
    if any(kw in file_name for kw in ["Daily", "daily", "Report", "마감","값복사","연누계","영업관리팀","O2O"]):
        return DEST_FOLDERS['일보']
    if any(kw in file_name for kw in ["대학원","박민지","워케이션","민지","연말정산","소득","교통","기초통계"]):
        return DEST_FOLDERS['민지']
    if ext_lower in [".hwp",".hwpx"]:
        return DEST_FOLDERS['민지']
    if ext_lower in [".pbix",".webp",".crt"]:
        return DEST_FOLDERS['PBI']
    return None

# 4. 실행
for folder in SOURCE_FOLDERS:
    for filename in os.listdir(folder):
        file_path = os.path.join(folder, filename)
        if not os.path.isfile(file_path):
            continue  # 폴더는 무시

        name_lower = filename.lower()
        ext = os.path.splitext(filename)[1].lower()

        dest = get_destination(name_lower, ext)

        try:
            if dest == "DELETE":
                os.remove(file_path)
                print(f"🗑️ 삭제됨: {file_path}")
            elif dest:
                os.makedirs(dest, exist_ok=True)
                shutil.move(file_path, os.path.join(dest, filename))
                print(f"📂 이동됨: {filename} → {dest}")
            else:
                print(f"⚠️ 조건 미일치, 이동 안함: {filename}")
        except Exception as e:
            print(f"❌ 오류 ({filename}): {e}")
