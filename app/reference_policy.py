"""User preferences for short-comment exemplars, separate from grading criteria."""
import re
import unicodedata

SHORT_GENRES = {'短评','简评','时评','简短评论','简短的评论','评论短文'}
GENRES = '|'.join(sorted(SHORT_GENRES | {'简报','提纲','建议书','倡议书','发言稿','讲话稿','公开信','汇报','议论文','议论性文章','文章'}, key=len, reverse=True))
TASK = re.compile(r'(?:撰写|拟写|写作|写)[^。；！？\n]{0,60}?(' + GENRES + ')')
EXPLICIT = re.compile(r'(?:文体|体裁)\s*(?:为|是|[:：])\s*[“「]?(' + '|'.join(SHORT_GENRES) + ')')

SHORT_COMMENT_POLICY = '''短评高分范文专用要求（仅约束high_score_answer，不作为给用户原答额外扣分的规则）：
正文采用总分总，共五个自然段：开头点明议题并亮出总论点；正文三个分论段；结尾回扣总论点、总结提升。
每个分论段必须以一句总结性的前置句开篇，先明确本段分论点，再展开论证；三个分论点紧扣本题、层次清晰、不重复。
案例引用不要出现具体数据，例如1000家、一万吨、比例、金额、倍数等，不能把阿拉伯数字改成中文数字规避。
案例须依据本题所选材料适度抽象，写清“什么主体做了什么事，带来了什么意义或效果”。保留关键做法、必要对象和真实因果，既不照抄细节，也不能过度概括成空泛口号，更不能虚构主体、行动或成效。
不要在范文里写“XX主体做XX事”的占位符，应填入材料支持的真实概括；不为凑三个分论点编造案例或重复同一段内容。
段落用实际换行分开，不写“开头、分论点一、正文、结尾”等结构标签；标题只按原题要求设置，标题不计入五段正文。
控制各段长度，必须同时满足原题字数与25格纸面行数，不能靠合并三个分论段、删掉前置句或保留具体案例数据来压缩字数。
生成后在high_score_self_check.format_note中简要自检：五段、三个段首观点、案例主体行动效果及适度概括、无具体数据，并检查分段和标题带来的占格。'''

def is_short_comment(question):
    # Source material and training buckets cannot establish the requested genre.
    prompt = re.sub(r'《[^》]*》', '', question.get('prompt',''))
    prompt = re.sub(r'\s+', ' ', prompt)
    if EXPLICIT.search(prompt):return True
    tasks = [m for m in TASK.finditer(prompt)
             if not re.search(r'(?:不要|不得|不能|不可|不宜|避免)\S{0,4}$',prompt[max(0,m.start()-8):m.start()])]
    if tasks:
        return tasks[-1][1] in SHORT_GENRES
    return bool(re.match(r'^(?:短评|简评|时评)[（(:：]',prompt))

def reference_policy(question):
    return SHORT_COMMENT_POLICY if is_short_comment(question) else ''

NUMBER = r'(?:\d{1,18}(?:[,，]\d{3}){0,5}(?:\.\d{1,12})?|[零〇一二两三四五六七八九十百千万亿]{1,18})(?:多|余)?(?:[十百千万亿]{1,3})?(?:多|余)?'
UNITS = r'(?:个百分点|平方公里|平方米|立方米|公顷|公里|千米|万元|亿元|家|吨|人|名|户|件|亩|米|元|倍|%|个(?!方面|层面|维度|环节|步骤|方向|要点))'
QUANTITY = re.compile(r'(' + NUMBER + r')\s*(' + UNITS + ')|百分之[零〇一二两三四五六七八九十百\d.]+')

def check_reference_style(answer, question):
    if not is_short_comment(question):
        return None
    blocks = [line.strip() for line in answer.splitlines() if line.strip()]
    # A short standalone title may precede the five body paragraphs.
    body = blocks[1:] if len(blocks)==6 and len(blocks[0])<=40 and not blocks[0].endswith(('。','；',';')) else blocks
    reasons=[]
    if len(body)!=5:
        reasons.append(f'短评正文应为5段（开头＋3个分论段＋结尾），当前识别到{len(body)}段')
    quantities=[]
    scan=unicodedata.normalize('NFKC',answer)
    for idiom in ('千家万户','万家灯火','千人一面'):
        scan=scan.replace(idiom,'')
    for match in QUANTITY.finditer(scan):
        # “一家企业”“一个主体” are indefinite subjects, not quantitative case data.
        if match[1]=='一' and match[2] in ('家','个','人','名','户'):
            continue
        if match[0] not in quantities:quantities.append(match[0])
    if quantities:
        reasons.append('检测到具体数量表达：'+'、'.join(quantities[:5])+'；请概括案例的主体、行动和效果')
    return dict(policy='short-comment-v1',valid=not reasons,body_paragraphs=len(body),
                quantities=quantities,reasons=reasons)

def needs_reference_repair(reference):
    return not reference['layout_check']['valid'] or bool(reference.get('style_check') and not reference['style_check']['valid'])
