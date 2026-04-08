"""
주차별 판매 추이 테이블 생성 스크립트

사용법:
    py make_trend_table.py                        # 기본 시트 사용
    py make_trend_table.py "4.15_소진체크"        # 시트 이름 지정
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import gspread

# ── 설정 ──────────────────────────────────────────
SPREADSHEET_ID  = '1CDsvq-f8i_H1Uo5IjncHkSZuCpx4b5oR2dcN774tuSY'
CREDENTIALS     = r'C:\Users\LUCALAB-LT2102\Desktop\★클로드\sheets-bot-490400-be2f963ececc.json'
DEFAULT_SHEET   = '2월 신상품 소진체크_0408'

CATEGORIES = [
    ('키스컷', [
        ('순한맛 응원',          0),
        ('매운맛 응원',          1),
        ('오늘의 일상기록',       2),
        ('오늘의 취미기록',       3),
        ('오늘의 말풍선',         4),
        ('끄적끄적 날짜&아이콘',  5),
    ]),
    ('기록책', [
        ('B7 데일리',    6),
        ('핸디 위클리',  7),
        ('핸디 프리노트',8),
    ]),
    ('핸디 멀티 파우치', [
        ('라이트핑크', 9),
    ]),
    ('양면 북밴드', [
        ('햄스터&도트',   10),
        ('기니피그&도트', 11),
        ('얼룩무늬&카우', 12),
        ('청사과&별',     13),
        ('수박&줄무늬',   14),
        ('별&무지개',     15),
    ]),
]
# ──────────────────────────────────────────────────


def find_total_table(all_vals):
    """'전체 채널 합산' 테이블의 주차 레이블과 데이터를 자동으로 찾아 반환"""
    header_row = None
    for i, row in enumerate(all_vals):
        # 헤더 행: B열='주차' 이고 C열에 상품명들이 있는 행
        if row[1] == '주차' and '순한맛 응원' in row:
            # 이 행이 전체 채널 합산 테이블의 헤더인지 확인 (위쪽에 '전체 채널 합산' 있는지)
            for look_back in range(1, 5):
                if i - look_back >= 0 and '전체 채널 합산' in all_vals[i - look_back][1]:
                    header_row = i
                    break

    if header_row is None:
        raise ValueError("'전체 채널 합산' 테이블을 찾을 수 없어요. 시트 구조를 확인해주세요.")

    # 헤더 다음 행부터 '합계' 행 전까지 = 주차별 데이터
    weeks_labels = []
    weeks_data   = []

    for i in range(header_row + 1, len(all_vals)):
        row = all_vals[i]
        label = row[1].strip()
        if label == '합계' or label == '':
            break
        # 컬럼 C~R (인덱스 2~17) = 16개 상품 수량
        vals = []
        for v in row[2:18]:
            clean = v.strip().replace(',', '')
            vals.append(int(clean) if clean.lstrip('-').isdigit() else 0)
        weeks_labels.append(label)
        weeks_data.append(vals)

    if not weeks_labels:
        raise ValueError("주차 데이터를 찾을 수 없어요.")

    print(f"  → 데이터 위치: {header_row+2}~{header_row+1+len(weeks_labels)}행, {len(weeks_labels)}주치")
    return weeks_labels, weeks_data


def find_start_row(all_vals):
    """트렌드 테이블을 그릴 시작 행 (마지막 데이터 행 + 3)"""
    last_row = 0
    for i, row in enumerate(all_vals):
        if any(v.strip() for v in row):
            last_row = i
    return last_row + 3  # 1-indexed + 공백 2줄


def cell_val(val, prev):
    if prev is None or prev == 0:
        return str(val)
    p = (val - prev) / prev * 100
    arrow = '\u25b2' if p > 0 else ('\u25bc' if p < 0 else '\u2192')
    return f'{val}\n{arrow}{abs(p):.0f}%'


BLACK = {'red': 0.0, 'green': 0.0, 'blue': 0.0}
RED   = {'red': 0.8, 'green': 0.0, 'blue': 0.0}   # 증가 → 빨간 글씨
BLUE  = {'red': 0.1, 'green': 0.2, 'blue': 0.8}   # 하락 → 파란 글씨


def get_fg(val, prev):
    if prev is None or prev == 0:
        return BLACK
    return RED if val > prev else (BLUE if val < prev else BLACK)


def req(sheet_id, r0, c0, c1, bg, bold=False, align='CENTER', wrap='WRAP', font_size=10, fg=None):
    fg_color = fg if fg is not None else BLACK
    return {'repeatCell': {
        'range': {'sheetId': sheet_id, 'startRowIndex': r0, 'endRowIndex': r0+1,
                  'startColumnIndex': c0, 'endColumnIndex': c1},
        'cell': {'userEnteredFormat': {
            'backgroundColor': bg,
            'textFormat': {'bold': bold, 'fontSize': font_size, 'foregroundColor': fg_color},
            'horizontalAlignment': align,
            'verticalAlignment': 'MIDDLE',
            'wrapStrategy': wrap,
        }},
        'fields': 'userEnteredFormat'}}


def build_table(weeks_labels, weeks_data):
    N = len(weeks_labels)
    table, row_info = [], []

    # 제목
    table.append(['전주 대비 판매 추이 (전채널 합산)'] + [''] * (N + 1))
    row_info.append(('title', None))

    # 헤더
    table.append(['구분', '상품'] + weeks_labels)
    row_info.append(('header', None))

    # 전체 합계
    totals = [sum(weeks_data[w]) for w in range(N)]
    table.append(['전체 합계', ''] + [cell_val(totals[w], totals[w-1] if w > 0 else None) for w in range(N)])
    row_info.append(('total', totals))

    table.append([''] * (N + 2))
    row_info.append(('blank', None))

    for cat_name, items in CATEGORIES:
        table.append([cat_name] + [''] * (N + 1))
        row_info.append(('cat', None))

        for opt_name, col in items:
            vals = [weeks_data[w][col] for w in range(N)]
            table.append(['', opt_name] + [cell_val(vals[w], vals[w-1] if w > 0 else None) for w in range(N)])
            row_info.append(('data', vals))

        sub = [sum(weeks_data[w][col] for _, col in items) for w in range(N)]
        table.append(['', '소계'] + [cell_val(sub[w], sub[w-1] if w > 0 else None) for w in range(N)])
        row_info.append(('sub', sub))

        table.append([''] * (N + 2))
        row_info.append(('blank', None))

    return table, row_info


def apply_formats(wb, sheet_id, START, row_info, N):
    W   = {'red': 1,    'green': 1,    'blue': 1   }
    GL  = {'red': 0.95, 'green': 0.95, 'blue': 0.95}
    GM  = {'red': 0.85, 'green': 0.85, 'blue': 0.85}
    TC  = {'red': 0.18, 'green': 0.45, 'blue': 0.18}
    CC  = {'red': 0.22, 'green': 0.42, 'blue': 0.62}
    TOT = {'red': 0.25, 'green': 0.55, 'blue': 0.25}
    end_col = 3 + N  # D부터 N주 끝까지

    requests = []
    for i, (rtype, data) in enumerate(row_info):
        r0 = START - 1 + i
        sid = sheet_id
        if rtype == 'title':
            requests.append(req(sid, r0, 1, end_col, TC, bold=True, font_size=11, wrap='OVERFLOW_CELL'))
        elif rtype == 'header':
            requests.append(req(sid, r0, 1, end_col, GM, bold=True, wrap='OVERFLOW_CELL'))
        elif rtype == 'total':
            requests.append(req(sid, r0, 1, 3, TOT, bold=True, align='LEFT', wrap='OVERFLOW_CELL'))
            for w in range(N):
                fg = get_fg(data[w], data[w-1] if w > 0 else None)
                requests.append(req(sid, r0, 3+w, 4+w, TOT, bold=True, fg=fg))
        elif rtype == 'cat':
            requests.append(req(sid, r0, 1, end_col, CC, bold=True, align='LEFT', wrap='OVERFLOW_CELL'))
        elif rtype == 'sub':
            requests.append(req(sid, r0, 1, 3, GM, bold=True, align='LEFT', wrap='OVERFLOW_CELL'))
            for w in range(N):
                fg = get_fg(data[w], data[w-1] if w > 0 else None)
                requests.append(req(sid, r0, 3+w, 4+w, GL, bold=True, fg=fg))
        elif rtype == 'data':
            requests.append(req(sid, r0, 1, 3, W, align='LEFT', wrap='OVERFLOW_CELL'))
            for w in range(N):
                fg = get_fg(data[w], data[w-1] if w > 0 else None)
                requests.append(req(sid, r0, 3+w, 4+w, W, fg=fg))

    # 행 높이
    for i, (rtype, _) in enumerate(row_info):
        h = 45 if rtype in ('data', 'sub', 'total') else (22 if rtype != 'blank' else 6)
        requests.append({'updateDimensionProperties': {
            'range': {'sheetId': sheet_id, 'dimension': 'ROWS',
                      'startIndex': START-1+i, 'endIndex': START+i},
            'properties': {'pixelSize': h}, 'fields': 'pixelSize'}})

    # 열 너비
    requests += [
        {'updateDimensionProperties': {'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'startIndex': 1, 'endIndex': 2}, 'properties': {'pixelSize': 100}, 'fields': 'pixelSize'}},
        {'updateDimensionProperties': {'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'startIndex': 2, 'endIndex': 3}, 'properties': {'pixelSize': 135}, 'fields': 'pixelSize'}},
        {'updateDimensionProperties': {'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'startIndex': 3, 'endIndex': 3+N}, 'properties': {'pixelSize': 82}, 'fields': 'pixelSize'}},
    ]

    wb.batch_update({'requests': requests})


def main():
    sheet_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SHEET
    print(f'시트: {sheet_name}')

    gc = gspread.service_account(filename=CREDENTIALS)
    wb = gc.open_by_key(SPREADSHEET_ID)
    ws = wb.worksheet(sheet_name)
    sheet_id = ws.id
    all_vals = ws.get_all_values()

    # 데이터 자동 감지
    print('데이터 위치 탐색 중...')
    weeks_labels, weeks_data = find_total_table(all_vals)
    N = len(weeks_labels)

    START = find_start_row(all_vals) + 1
    print(f'  → 트렌드 테이블 시작: {START}행')

    # 초기화
    wb.batch_update({'requests': [
        {'unmergeCells': {'range': {'sheetId': sheet_id, 'startRowIndex': START-1, 'endRowIndex': START+50, 'startColumnIndex': 1, 'endColumnIndex': 3+N+2}}},
        {'repeatCell': {
            'range': {'sheetId': sheet_id, 'startRowIndex': START-1, 'endRowIndex': START+50, 'startColumnIndex': 1, 'endColumnIndex': 3+N+2},
            'cell': {'userEnteredFormat': {'backgroundColor': {'red':1,'green':1,'blue':1}, 'textFormat': {'bold': False}, 'wrapStrategy': 'OVERFLOW_CELL'}},
            'fields': 'userEnteredFormat'}},
        {'updateCells': {
            'range': {'sheetId': sheet_id, 'startRowIndex': START-1, 'endRowIndex': START+50, 'startColumnIndex': 1, 'endColumnIndex': 3+N+2},
            'fields': 'userEnteredValue'}}
    ]})

    # 테이블 생성 및 입력
    table, row_info = build_table(weeks_labels, weeks_data)
    ws.update(values=table, range_name=f'B{START}')
    print(f'데이터 입력: {len(table)}행')

    # 서식 적용
    apply_formats(wb, sheet_id, START, row_info, N)
    print('완료!')


if __name__ == '__main__':
    main()
