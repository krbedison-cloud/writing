import re
import subprocess
import tempfile
from pathlib import Path
import fitz
from .schemas import PaperInput, QuestionInput
from .materials import prepare_paper
from .paper import normalize_limit_numbers

PROVINCES = '浙江 江苏 安徽 福建 江西 山东 山西 陕西 河南 河北 湖北 湖南 广东 广西 四川 重庆 云南 贵州 海南 黑龙江 吉林 辽宁 内蒙古 宁夏 青海 甘肃 新疆 西藏 北京 天津 上海 台湾 香港 澳门'.split()

def extract_pdf(content: bytes) -> str:
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise ValueError("无法读取 PDF，请确认文件未损坏") from exc
    with doc:
        if doc.needs_pass:
            raise ValueError("请先解除 PDF 密码保护")
        if len(doc) > 80:
            raise ValueError("每份试卷最多 80 页，请拆分后导入")
        texts = []
        for page in doc:
            text = page.get_text()
            if len(re.findall(r'[\u4e00-\u9fffA-Za-z]', text)) < 25 and page.get_images():
                with tempfile.TemporaryDirectory() as td:
                    path = Path(td) / 'page.png'
                    page.get_pixmap(dpi=220).save(path)
                    try:
                        p = subprocess.run(['tesseract', str(path), 'stdout', '-l', 'chi_sim+eng', '--psm', '6'], capture_output=True, timeout=70)
                    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                        raise ValueError("扫描页 OCR 不可用或超时；Docker 镜像内置中文 OCR，也可先转换为文字型 PDF") from exc
                    if p.returncode:
                        raise ValueError("扫描页 OCR 识别失败，请使用清晰的 PDF")
                    text = p.stdout.decode('utf-8')
            texts.append(text)
        result = '\n'.join(texts)
        if len(result) > 160000:
            raise ValueError("提取文本超过 16 万字，请拆分试卷")
        if len(result.strip()) < 80:
            raise ValueError("未提取到足够文字，请检查 PDF 或先进行 OCR")
        return result

def word_limits(text):
    text = normalize_limit_numbers(text)
    ranged = re.search(r'(\d{2,4})\s*[-—–~～至到]\s*(\d{2,4})\s*字', text)
    if ranged:
        return int(ranged[1]), int(ranged[2])
    upper = re.search(r'(?:不超过|不足|不到|少于|不满|以内|最多|字数[为：:]?)\s*(\d{2,4})\s*字', text)
    inside = re.search(r'(\d{2,4})\s*字(?:以内|以下)',text)
    return (0,int(upper[1])) if upper else (0,int(inside[1])) if inside else (0,0)

def classify(prompt, index, count):
    lo, hi = word_limits(prompt)
    if lo >= 800 or hi >= 900 or re.search('议论性文章|议论文|大作文', prompt):
        return 'essay', '长篇议论性文章，按 50 分训练档'
    if (hi and hi <= 400) or (re.search('概括|概述|归纳', prompt) and not re.search('短评|倡议|讲话|发言|建议书', prompt)):
        return 'summary', '短概括/短简报按 20 分训练档；保留原文体要求'
    if re.search('简报|短评|应用文|公文|倡议|讲话|发言|宣传|建议书|汇报|公开信|提纲|一封信', prompt) or 400 < hi < 900:
        return 'practical', '应用文或短评按 30 分训练档'
    return 'summary', '规则识别不确定，请人工核对题型'

def parse_text(text, filename):
    lines = [x.strip() for x in text.splitlines()]
    lines = [x for x in lines if x and not re.match(r'^(?:·?\s*本试卷由|第\s*\d+\s*页[，,]|\d{4}年.*《申论》题)', x)]
    cleaned = '\n'.join(lines)
    sections = list(re.finditer(r'^作答要求\s*$', cleaned, re.M))
    split = sections[-1].start() if sections else -1
    body = cleaned[split:] if split >= 0 else cleaned
    body = re.split(r'^答题纸\s*$', body, flags=re.M)[0]
    matches = list(re.finditer(r'^(?:第\s*(?P<ordinal>[一二三四五六七八九十\d]+)\s*(?P<suffix>题|題|是(?=[ \t]*$))|(?P<list>[一二三四五六七八九十]+)[、.．])\s*', body, re.M))
    questions = []
    for i, m in enumerate(matches):
        prompt = body[m.end(): matches[i+1].start() if i+1 < len(matches) else len(body)].strip()
        if len(prompt) < 8:
            continue
        kind, note = classify(prompt, i, len(matches))
        lo, hi = word_limits(prompt)
        label = m.group('ordinal') or m.group('list')
        digits = {ch:n for n,ch in enumerate('零一二三四五六七八九')}
        if label.isdigit():
            number = int(label)
        elif '十' in label:
            a,b = label.split('十')
            number = digits.get(a,1)*10 + digits.get(b,0)
        else:
            number = digits.get(label,i+1)
        if m.group('suffix') == '是':
            note += '；疑似 OCR 题号误识别，请对照 PDF 核对'
        questions.append(QuestionInput(number=number, kind=kind, prompt=prompt, word_min=lo, word_max=hi, classification_note=note))
    # Unrecognised layouts remain editable; never silently invent questions.
    if not questions:
        questions = [QuestionInput(number=1, kind='summary', prompt='未能可靠识别题目，请从原文中粘贴题干与作答要求。', classification_note='待人工补充，暂不可批改')]
    meta = filename + '\n' + text[:250]
    yr = re.search(r'(20\d{2})年', meta)
    province = next((p for p in PROVINCES if p in meta), '待确认')
    vol = re.search(r'[（(]([^（）()\n]{1,12}卷)[）)]', meta)
    title = Path(filename).stem[:180]
    parsed = PaperInput(title=title, year=yr[1] if yr else '待确认', province=province,
                      volume=vol[1] if vol else '待确认', materials=cleaned[:split] if split >= 0 else cleaned,
                      questions=questions).model_dump()
    return prepare_paper(parsed)
