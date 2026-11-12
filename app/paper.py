"""Deterministic 25-column answer-paper accounting, including formatting loss."""
import math
import re
import unicodedata

COLUMNS = 25
LEGACY_RULES = '每个汉字、字母、数字和标点占1格；空格和全角空格占格不计字；Tab占2格；换行耗尽本行剩余格；标题、称呼、落款、日期均计字且占格。段首缩进须用实际空格表示。'

RULES = '中文稿纸v2：汉字及全角标点通常1格；连续半角数字/英文字母（含数字中的小数点、百分号及序号1.）每2字符1格，单数尾字符占1格；短数字串/英文词整体换行。行末点号和后引号/右括号与末字合格，不放在自动换行的行首；前引号/左括号不孤立行末。成对破折号——、省略号……各占2格且不拆行。全角空格1格，Tab2格，换行耗尽本行余格，标题、称呼、落款均计入；字数仍逐字符计数，不能用合格规避字数上限。显式换行保留原文。'

def normalize_limit_numbers(text):
    text = unicodedata.normalize('NFKC',text)
    text = re.sub(r'(?<=\d)\s+(?=\d)','',text)
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

def word_requirement(text):
    text=normalize_limit_numbers(text)
    # A title's own limit must not become the answer's limit.
    text=re.sub(r'(?:标题|小标题|前置句)(?:字数)?\s*(?:不超过|不得超过|最多|限|控制在)?\s*\d{1,4}\s*字(?:以内|以下|左右)?','',text)
    number=r'(?<!\d)(\d{2,4})(?!\d)'
    ranged=re.search(number+r'\s*(?:字\s*)?[-—–~～至到]\s*(\d{2,4})\s*字',text)
    lo=int(ranged[1]) if ranged else None
    hi=int(ranged[2]) if ranged else None
    exclusive=False
    lower=re.search(r'(?:不少于|不低于|不得少于|不能少于|不可少于|至少|不小于)\s*'+number+r'\s*字',text)
    above=re.search(number+r'\s*字(?:以上|起)',text)
    if lower or above:lo=int((lower or above)[1])
    over=re.search(r'(?<!不)(?<!不得)(?<!不能)(?<!不可)(?:超过|大于|多于)\s*'+number+r'\s*字',text)
    if over:lo=int(over[1])+1
    upper=re.search(r'(?:不超过|不得超过|不能超过|不大于|不多于|最多|至多|字数(?:为|是|控制在|在)?[:：]?)\s*'+number+r'\s*字',text)
    inside=re.search(number+r'\s*字(?:以内|以下|为限|左右|上下)',text)
    strict=re.search(r'(?:不到|不足|不满|(?<!不)(?<!不得)(?<!不能)(?<!不可)少于|(?<!不)(?<!不得)(?<!不能)(?<!不可)小于)\s*'+number+r'\s*字',text)
    if upper or inside:hi=int((upper or inside)[1])
    if strict:hi=int(strict[1]);exclusive=True
    if hi is None and lo is None:
        bare=list(re.finditer(number+r'\s*字',text))
        if len(bare)==1:
            prefix=text[max(0,bare[0].start()-24):bare[0].start()]
            if len(text.strip())<=15 or re.search(r'字数|要求|(?:撰写|拟写|写|作答)[^。；\n]{0,18}$',prefix):hi=int(bare[0][1])
    return dict(word_min=lo,word_max=hi,max_exclusive=exclusive)

def normalize_question_limits(question):
    q=dict(question);limits=word_requirement(q.get('prompt',''))
    for field in ('word_min','word_max'):
        if limits[field] is not None:q[field]=limits[field]
    lo,hi=q.get('word_min',0),q.get('word_max',0)
    if limits['word_min'] is None and isinstance(lo,(int,float)) and isinstance(hi,(int,float)) and hi>0 and lo>hi:q['word_min']=0
    if limits['word_max'] is None and limits['word_min'] is not None and isinstance(hi,(int,float)) and 0<hi<limits['word_min']:q['word_max']=0
    return q

def paper_spec(question):
    question=normalize_question_limits(question)
    lo, hi = question.get('word_min', 0), question.get('word_max', 0)
    exclusive = bool(word_requirement(question.get('prompt',''))['max_exclusive'])
    maximum = hi - 1 if exclusive else hi
    rows = question.get('paper_rows') or max(1, math.ceil((hi or max(lo,{'summary':350,'practical':600,'essay':1200}.get(question.get('kind'),600)))/COLUMNS))
    return dict(layout_version=2, columns=COLUMNS, rows=rows, cells=COLUMNS*rows, word_min=lo,
                word_max=maximum, stated_word_max=hi, max_exclusive=exclusive, rules=RULES)

def zero_width(char):
    return unicodedata.category(char).startswith('M') or unicodedata.category(char)=='Cf'

def check_legacy(text, spec):
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


OPEN = '（〔［｛〈《「『【〖“‘([{'
CLOSE = '，。、；：！？）》」』】〕］｝〉〗”’)]},.!?;:'
RUN = re.compile(r'[A-Za-z0-9]+(?:[.:%/+-][A-Za-z0-9]+)*[.%]?')
TOKEN = re.compile(r'[A-Za-z0-9]+(?:[.:%/+-][A-Za-z0-9]+)*[.%]?|——|……|[\s\S]')

def check_paper(text, spec):
    if spec.get('layout_version',1) < 2:
        return check_legacy(text,spec)
    text=text.replace('\r\n','\n').replace('\r','\n')
    col=written=spaces=loss=0
    words=sum(not c.isspace() and not zero_width(c) for c in text)
    last=False
    for match in TOKEN.finditer(text):
        token=match[0]
        if token=='\n':
            loss+=spec['columns']-col;col=0;last=False;continue
        if len(token)==1 and zero_width(token):continue
        ascii_run=bool(RUN.fullmatch(token))
        width=math.ceil(len(token)/2) if ascii_run else 2 if token in ('——','……','\t') else 1
        if token in CLOSE and col==spec['columns'] and last:
            continue
        if (token in OPEN and col==spec['columns']-1) or (width<=spec['columns'] and col+width>spec['columns']):
            loss+=spec['columns']-col;col=0
        for _ in range(width):
            if col==spec['columns']:col=0
            col+=1
        written+=width
        if token.isspace():spaces+=width
        last=not token.isspace()
    occupied=written+loss;rows=math.ceil(occupied/spec['columns']) if occupied else 0
    reasons=[]
    if spec['word_min'] and words<spec['word_min']:reasons.append(f"字数不足：{words} / 至少 {spec['word_min']} 字")
    if spec['word_max'] and words>spec['word_max']:reasons.append(f"字数超限：{words} / 最多 {spec['word_max']} 字")
    if rows>spec['rows']:reasons.append(f"纸面超限：占用 {rows} / {spec['rows']} 行（含缩进和换行损失）")
    return dict(word_count=words,occupied_cells=occupied,used_rows=rows,whitespace_cells=spaces,line_break_loss=loss,formatting_cells=spaces+loss,remaining_cells=spec['cells']-occupied,overflow_cells=max(0,occupied-spec['cells']),valid=not reasons,reasons=reasons)
