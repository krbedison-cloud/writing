"""User preferences for short-comment exemplars, separate from grading criteria."""
import re
import unicodedata
from .paper import paper_spec, RULES

SHORT_GENRES = {'短评','简评','时评','简短评论','简短的评论','评论短文'}
GENRES = '|'.join(sorted(SHORT_GENRES | {'简报','提纲','建议书','倡议书','发言稿','讲话稿','公开信','一封信','通知','调研报告','工作报告','工作方案','宣传稿','申请书','汇报','议论文','议论性文章','文章'}, key=len, reverse=True))
TASK = re.compile(r'(?:撰写|拟写|写作|写)[^。；！？\n]{0,60}?(' + GENRES + ')')
EXPLICIT = re.compile(r'(?:文体|体裁)\s*(?:为|是|[:：])\s*[“「]?(' + '|'.join(SHORT_GENRES) + ')')

SHORT_COMMENT_POLICY = ('短评范文专用，不作原答额外扣分依据：总分总五段，开头＋三个分论段＋结尾；各分论段首句概括观点，三点切题、有层次不重复。'
 '案例只据本题材料，不用材料外案例；概括主体—行动—意义/效果，保留关键做法、对象和因果，不空泛、不虚构、不为凑三点编例。'
 '不写具体数量、金额、比例、倍数，不换成中文数字规避，不写XX占位符。段落用换行，无结构标签；标题等仅按题干，标题不算正文段。'
 '满足字数与纸面限制，不靠并段、删前置句或必要格式凑格；自检五段、段首观点和案例合规。')

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

ESSAY_REFERENCE_POLICY = ('大作文范文专用，不作原答附加扣分标准：总分总，开头＋三个分论段＋结尾，必要可四个；各分论段有总结性前置句，论点准确与层次递进优先。'
 '重在说理论证和评论，分析因果、机制及条件，案例辅助、不堆砌。尽量利用题内材料；无法支撑论点的材料可舍弃，不求全覆盖、不扭曲框架。'
 '材料充分时案例仅用题内；仅当材料缺乏可用典型案例且题干允许时，可少量补充知名、代表性且事实可靠的事例，构思说明标明材料外补例及补充原因，不冒充题内材料。'
 '案例概括主体、行动和意义或作用，不用具体数量、比例、金额，不换写数字规避，不虚构成效。'
 '严格1001–1200字，写进25列×48行1200格，含标题、段首缩进及换行损失；目标约1050–1100字留格式余量，不把目标当硬性字数。'
 '一次自检段首观点、论证和材料依据；不并段或删必要格式凑格。')

ESSAY_MATERIAL_PRIORITY = ('同卷范文取材：reference_material_priority.available为true时，以preferred_ids（前题未用材料）为主要立意、框架及案例来源，secondary_ids（前题已用材料）尽量不用或降为辅助。'
 '仅当优先材料不足以完整准确支撑论证时适量补用，构思说明交代必要性；不为避重扭曲论点，不用材料外案例绕过可用题内材料。'
 '不改变题干范围或原答评分；题干明确必用材料优先。available为false或优先材料为空则正常取材；unresolved_prior_questions不可靠，不猜测排除。')

def is_essay(question):
    if question.get('kind')!='essay' or is_short_comment(question):return False
    prompt=re.sub(r'\s+',' ',re.sub(r'《[^》]*》','',question.get('prompt','')))
    tasks=[m for m in TASK.finditer(prompt)
           if not re.search(r'(?:不要|不得|不能|不可|不宜|避免)\S{0,4}$',prompt[max(0,m.start()-8):m.start()])]
    return not tasks or tasks[-1][1] in ('议论文','议论性文章','文章')

def reference_policy(question):
    if is_short_comment(question):return SHORT_COMMENT_POLICY
    return (ESSAY_REFERENCE_POLICY+(ESSAY_MATERIAL_PRIORITY if question.get('reference_material_priority') else '')) if is_essay(question) else ''

def reference_paper_spec(question):
    original=question.get('paper_spec') or paper_spec(question)
    if not is_essay(question):return original
    return dict(original,layout_version=2,columns=25,rows=48,cells=1200,word_min=1001,word_max=1200,
                stated_word_max=1200,max_exclusive=False,rules=RULES)

def reference_payload(question):
    if not is_essay(question):return {}
    # The long occupancy rules already occur once in question.paper_spec.rules.
    spec=reference_paper_spec(question)
    original=question.get('paper_spec') or paper_spec(question)
    return {'reference_paper_spec':{k:v for k,v in spec.items() if k!='rules' or spec['rules']!=original['rules']}}

NUMBER = r'(?:\d{1,18}(?:[,，]\d{3}){0,5}(?:\.\d{1,12})?|[零〇一二两三四五六七八九十百千万亿]{1,18})(?:多|余)?(?:[十百千万亿]{1,3})?(?:多|余)?'
UNITS = r'(?:个百分点|平方公里|平方米|立方米|公顷|公里|千米|万元|亿元|家|吨|人|名|户|件|亩|米|元|倍|%|个(?!方面|层面|维度|环节|步骤|方向|要点))'
QUANTITY = re.compile(r'(' + NUMBER + r')\s*(' + UNITS + r')|百分之[零〇一二两三四五六七八九十百\d.]+')

# Detect exam-source references, not words such as 原材料 or 新材料中心.
_SOURCE_NUMBER = r'(?:[0-9]+|[一二三四五六七八九十百零〇几]+)'
_SOURCE_NUMBERED = (r'(?:材料|资料)\s*[“「"（(]?\s*'+_SOURCE_NUMBER+
                    r'(?:\s*[、,，和及至到—-]\s*'+_SOURCE_NUMBER+r')*\s*[”」"）)]?'
                    r'(?=$|[\s，,。；;：:、“「]|中|里|的|所|显示|指出|提到|表明|介绍|描述|反映|为例|讲述|说明|体现)')
SOURCE_REFERENCE = re.compile(
    r'(?:给定|所给|上述|以上)(?:材料|资料)|'+_SOURCE_NUMBERED+
    r'|(?:(?<!\w)|(?:根据|依据|结合|从|在|例如|比如|正如|引用|参照|如))'
    r'(?:材料|资料)(?:中(?!心)|里|所述|所列|所举|显示|指出|提到|表明)')


def source_references(answer):
    text=unicodedata.normalize('NFKC',answer)
    titles=[match.span() for match in re.finditer(r'《[^》\n]*》',text)]
    return list(dict.fromkeys(match[0] for match in SOURCE_REFERENCE.finditer(text)
                              if not any(start<=match.start()<end for start,end in titles)))[:8]


def check_reference_style(answer, question):
    essay=is_essay(question)
    short=is_short_comment(question)
    mentions=source_references(answer)
    if not essay and not short and not mentions:
        return None
    blocks = [line.strip() for line in answer.splitlines() if line.strip()]
    # A short standalone title may precede the five body paragraphs.
    counts=(5,6) if essay else (5,)
    body = blocks[1:] if len(blocks)-1 in counts and len(blocks[0])<=40 and not blocks[0].endswith(('。','；',';')) else blocks
    reasons=[]
    if (essay or short) and len(body) not in counts:
        label='大作文正文应为5或6段（开头＋3或4个分论段＋结尾）' if essay else '短评正文应为5段（开头＋3个分论段＋结尾）'
        reasons.append(f'{label}，当前识别到{len(body)}段')
    quantities=[]
    scan=unicodedata.normalize('NFKC',answer)
    for idiom in ('千家万户','万家灯火','千人一面'):
        scan=scan.replace(idiom,'')
    for match in QUANTITY.finditer(scan) if essay or short else []:
        # “一家企业”“一个主体” are indefinite subjects, not quantitative case data.
        if match[1]=='一' and match[2] in ('家','个','人','名','户'):
            continue
        if match[0] not in quantities:quantities.append(match[0])
    if quantities:
        reasons.append('检测到具体数量表达：'+'、'.join(quantities[:5])+'；请概括案例的主体、行动和效果')
    if mentions:
        reasons.append('范文含试卷材料引用口吻：'+'、'.join(mentions)+'；请直接叙述主体、做法和成效，材料编号只放在审题或构思说明中')
    return dict(policy='essay-v1' if essay else 'short-comment-v1' if short else 'reference-prose-v1',valid=not reasons,body_paragraphs=len(body),
                quantities=quantities,source_mentions=mentions,reasons=reasons)

def needs_reference_repair(reference):
    return not reference['layout_check']['valid'] or bool(reference.get('style_check') and not reference['style_check']['valid'])
