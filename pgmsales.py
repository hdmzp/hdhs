import os
import pandas as pd
from datetime import datetime, timedelta
import warnings

# 경고 무시
warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')

def merge_broadcast_targets():
    # ==========================================
    # 1. 설정 (연도/월)
    # ==========================================
    TARGET_YEAR = 2026
    TARGET_MONTH = 7
    # ==========================================

    home_dir = os.path.expanduser("~")
    download_dir = os.path.join(home_dir, "Downloads") 
    today_str = datetime.now().strftime('%Y-%m-%d')
    year_month_str = f"{TARGET_YEAR}년 {TARGET_MONTH:02d}월"
    
    fixed_cols = None
    target_data_list = []
    weight_data_list = []
    
    print(f"\n[작업 시작] 1분 단위 행 생성 및 통합 중...")

    # =========================================================
    # [핵심 수정] 7월처럼 날짜가 긴 달을 위해 range를 7까지 늘리고, 
    # 파일이 없어도 break 대신 continue로 끝까지 탐색합니다.
    # =========================================================
    for week in range(0, 7): 
        file_prefix = f"방송의지목표 목록(TV)_{year_month_str} {week}주차"
        target_file = None
        
        # 다운로드 디렉토리가 존재하는지 안전하게 확인
        if not os.path.exists(download_dir):
            continue
            
        for f in os.listdir(download_dir):
            if f.startswith(file_prefix):
                fpath = os.path.join(download_dir, f)
                if datetime.fromtimestamp(os.path.getmtime(fpath)).strftime('%Y-%m-%d') == today_str:
                    target_file = fpath
                    break
        
        if target_file:
            try:
                df_raw = pd.read_excel(target_file, header=None, engine='openpyxl')
                dates = df_raw.iloc[0, 2::3].dropna().tolist()
                if fixed_cols is None:
                    fixed_cols = df_raw.iloc[3:, [0, 1]].reset_index(drop=True)
                    fixed_cols.columns = ['시작시분', '종료시분']
                
                target_data_list.append(df_raw.iloc[3:, 2::3].reset_index(drop=True).set_axis(dates, axis=1))
                weight_data_list.append(df_raw.iloc[3:, 4::3].reset_index(drop=True).set_axis(dates, axis=1))
                print(f"-> {week}주차 파일 로드 완료 ({len(dates)}일치 데이터)")
            except Exception as e:
                print(f"{week}주차 파일 읽기 오류: {e}")
        else:
            # 파일이 없더라도 break하지 않고 다음 주차를 계속 검색합니다.
            print(f"-> {week}주차 파일 없음 (오늘 생성된 파일이 없거나 파일명이 다를 수 있음)")
            continue

    if not target_data_list:
        print("취합할 파일을 찾지 못했습니다. 파일명과 파일 수정 날짜(오늘)를 확인해주세요.")
        return

    # 기존 데이터 통합
    final_target = pd.concat([fixed_cols] + target_data_list, axis=1)
    final_weight = pd.concat([fixed_cols] + weight_data_list, axis=1)

    # ---------------------------------------------------------
    # 05:20 ~ 05:59 까지 1분 단위 행 생성
    # ---------------------------------------------------------
    minute_rows = []
    start_time = datetime.strptime("05:20", "%H:%M")
    
    # 05:20부터 05:59까지 40개 행 생성
    for i in range(40):
        current = start_time + timedelta(minutes=i)
        time_str = current.strftime("%H:%M")
        row_data = final_target[final_target['시작시분'] == '05:20'].iloc[0].copy()
        row_data['시작시분'] = time_str
        row_data['종료시분'] = time_str 
        minute_rows.append(row_data)

    df_minutes_target = pd.DataFrame(minute_rows)
    
    minute_rows_w = []
    for i in range(40):
        current = start_time + timedelta(minutes=i)
        row_data_w = final_weight[final_weight['시작시분'] == '05:20'].iloc[0].copy()
        row_data_w['시작시분'] = current.strftime("%H:%M")
        row_data_w['종료시분'] = current.strftime("%H:%M")
        minute_rows_w.append(row_data_w)
    df_minutes_weight = pd.DataFrame(minute_rows_w)

    # 1분 단위 데이터 + 기존 데이터 결합
    final_target = pd.concat([df_minutes_target, final_target], ignore_index=True)
    final_weight = pd.concat([df_minutes_weight, final_weight], ignore_index=True)

    # 순번 재부여 및 숫자 포맷팅
    for df in [final_target, final_weight]:
        if '순번' in df.columns: df.drop(columns=['순번'], inplace=True)
        df.insert(0, '순번', range(1, len(df) + 1))
        cols = df.columns[3:]
        df[cols] = df[cols].apply(pd.to_numeric, errors='coerce')

    # 저장
    output_path = os.path.join(download_dir, f"방송의지목표_통합_{TARGET_YEAR}년{TARGET_MONTH:02d}월_합계.xlsx")
    
    try:
        with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
            final_target.to_excel(writer, sheet_name='순주문목표', index=False)
            final_weight.to_excel(writer, sheet_name='일자가중분', index=False)
            
            num_format = writer.book.add_format({'num_format': '0.00'})
            for sheet in writer.sheets.values():
                sheet.set_column(3, final_target.shape[1]-1, None, num_format)
        print(f"\n성공! 05:20~05:59 (1분 단위) 추가 및 통합 완료.")
        print(f"저장 위치: {output_path}")
    except PermissionError:
        print("엑셀 파일이 열려 있습니다. 파일을 닫고 다시 실행해주세요.")

if __name__ == "__main__":
    merge_broadcast_targets()