"""Deterministic 25-column answer-paper accounting, including formatting loss."""
import math
import re
import unicodedata

COLUMNS = 25
RULES = '每个汉字、字母、数字和标点占1格；空格和全角空格占格不计字；Tab占2格；换行耗尽本行剩余格；标题、称呼、落款、日期均计字且占格。段首缩进须用实际空格表示。'

def normalize_limit_numbers(text):
    digits=dict(zip('零〇一二两三四五六七八九',[0,0,1,2,2,3,4,5,6,7,8,9]))
    def convert(match):
        label=match[0]
        if not any(ch in '十百千' for ch in label):
            return ''.join(str(digits[ch]) for ch in label)
        total,current=0,0
        for ch in label:
            if ch in digits:
                current=digits[ch]
            else:
                total+=(current or 1)*{'十':10,'百':100,'千':1000}[ch]
                current=0
        return str(total+current)
    return re.sub(r'[零〇一二两三四五六七八九十百千]+(?=\s*(?:字|[-—–~～至到]))',convert,text)

def paper_spec(question):
    prompt = normalize_limit_numbers(question.get('prompt', ''))
    lo, hi = question.get('word_min', 0), question.get('word_max', 0)
    ranged = re.search(r'(\d{2,4})\s*[-—–~～至到]\s*(\d{2,4})\s*字', prompt)
    if ranged:
        lo, hi = lo or int(ranged[1]), hi or int(ranged[2])
    strict = re.search(r'(?:不到|不足|少于|不满)\s*(\d{2,4})\s*字', prompt)
    upper = re.search(r'(?:不超过|最多|至多|字数(?:为|在|[:：])?)\s*(\d{2,4})\s*字|(?P<inside>\d{2,4})\s*字以内', prompt)
    if not hi:
        hi = int(strict[1]) if strict else int(upper[1] or upper['inside']) if upper else 0
    exclusive = bool(strict and int(strict[1]) == hi)
    maximum = hi - 1 if exclusive else hi
    rows = question.get('paper_rows') or max(1, math.ceil((hi or {'summary':350,'practical':600,'essay':1200}.get(question.get('kind'),600))/COLUMNS))
    return dict(columns=COLUMNS, rows=rows, cells=COLUMNS*rows, word_min=lo,
                word_max=maximum, stated_word_max=hi, max_exclusive=exclusive, rules=RULES)

def zero_width(char):
    return unicodedata.category(char).startswith('M') or unicodedata.category(char)=='Cf'

def check_paper(text, spec):
    text = text.replace('\r\n','\n').replace('\r','\n')
    row, col, words, written, whitespace, loss = 0, 0, 0, 0, 0, 0
    for char in text:
        if char == '\n':
            loss += spec['columns'] - col
            row, col = row+1, 0
        elif zero_width(char):
            continue
        else:
            width = 2 if char == '\t' else 1
            for _ in range(width):
                if col == spec['columns']:
                    row, col = row+1, 0
                col += 1
            written += width
            if char.isspace():
                whitespace += width
            else:
                words += 1
    occupied = written + loss
    rows = math.ceil(occupied/spec['columns']) if occupied else 0
    reasons = []
    if spec['word_min'] and words < spec['word_min']:
        reasons.append(f"字数不足：{words} / 至少 {spec['word_min']} 字")
    if spec['word_max'] and words > spec['word_max']:
        reasons.append(f"字数超限：{words} / 最多 {spec['word_max']} 字")
    if rows > spec['rows']:
        reasons.append(f"纸面超限：占用 {rows} / {spec['rows']} 行（含缩进和换行损失）")
    return dict(word_count=words, occupied_cells=occupied, used_rows=rows, whitespace_cells=whitespace,
                line_break_loss=loss, formatting_cells=whitespace+loss, remaining_cells=spec['cells']-occupied,
                overflow_cells=max(0,occupied-spec['cells']), valid=not reasons, reasons=reasons)
