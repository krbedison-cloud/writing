"""User preferences for short-comment exemplars, separate from grading criteria."""
import re
import unicodedata
from .paper import paper_spec, RULES

SHORT_GENRES = {'短评','简评','时评','简短评论','简短的评论','评论短文'}
GENRES = '|'.join(sorted(SHORT_GENRES | {'简报','提纲','建议书','倡议书','发言稿','讲话稿','公开信','一封信','通知','调研报告','工作报告','工作方案','宣传稿','申请书','汇报','议论文','议论性文章','文章'}, key=len, reverse=True))
TASK = re.compile(r'(?:撰写|拟写|写作|写)[^。；！？\n]{0,60}?(' + GENRES + ')')
EXPLICIT = re.compile(r'(?:文体|体裁)\s*(?:为|是|[:：])\s*[“「]?(' + '|'.join(SHORT_GENRES) + ')')

SHORT_COMMENT_POLICY = ('短评范文专用，不作原答额外扣分依据：总分总五段，开头亮总论点＋三个分论段＋结尾回扣提升。各分论段首句概括观点，三点紧扣本题、层次清楚不重复。'
 '案例仅据本题材料，适度概括主体—行动—意义/效果，保留关键做法、对象及真实因果；不照抄细节、不空泛、不虚构、不用材料外案例，不为凑三点重复或编例。'
 '案例不写具体数量、金额、比例或倍数，不能改成中文数字规避，不写XX占位符。段落用实际换行，无结构标签；标题等格式仅按题干设置，标题不算正文段。'
 '同时满足题干字数与25格纸面限制，不靠并段、删前置句或必要格式凑格；format_note一句自检五段、三个段首观点、案例合规和字数占格。')

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

ESSAY_REFERENCE_POLICY = ('大作文范文专用（原答按题干评分，不附加范文模板或字数扣分）：总分总，开头＋三个分论段＋结尾；确有必要可四个分论段，不以四段扣分。各分论段首句概括分论点，观点有层次递进。'
 '重在说理论证和评论，分析因果、机制与必要条件，案例辅助说理，不堆砌。论点准确与层次递进优先，在此基础上尽量利用题内材料；无法支撑论点的材料可舍弃，不求全覆盖、不为塞材料扭曲框架。材料充分时案例只用题内典型事例；仅当材料缺乏可用典型案例（如材料过短）且题干允许时，可少量补充知名、代表性且事实可靠的事例，不能确认的事实宁缺毋滥。构思说明须标明材料外补例及补充原因，不冒充题内材料。'
 '案例适度概括为主体、行动及意义或作用，不引用具体数量、比例、金额等数据，不改写数字规避，不空泛或虚构成效。'
 '范文严格1001–1200字，且写进25列×48行共1200格；标题、段首缩进和换行损失均占格。建议约1050–1120字留足格式空间，target_word_count仅参考。'
 '自检段首观点、论证与材料依据，将检查简述于format_note；不能合并分论段或删必要格式来凑格。')

def is_essay(question):
    if question.get('kind')!='essay' or is_short_comment(question):return False
    prompt=re.sub(r'\s+',' ',re.sub(r'《[^》]*》','',question.get('prompt','')))
    tasks=[m for m in TASK.finditer(prompt)
           if not re.search(r'(?:不要|不得|不能|不可|不宜|避免)\S{0,4}$',prompt[max(0,m.start()-8):m.start()])]
    return not tasks or tasks[-1][1] in ('议论文','议论性文章','文章')

def reference_policy(question):
    if is_short_comment(question):return SHORT_COMMENT_POLICY
    return ESSAY_REFERENCE_POLICY if is_essay(question) else ''

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

def check_reference_style(answer, question):
    essay=is_essay(question)
    if not essay and not is_short_comment(question):
        return None
    blocks = [line.strip() for line in answer.splitlines() if line.strip()]
    # A short standalone title may precede the five body paragraphs.
    counts=(5,6) if essay else (5,)
    body = blocks[1:] if len(blocks)-1 in counts and len(blocks[0])<=40 and not blocks[0].endswith(('。','；',';')) else blocks
    reasons=[]
    if len(body) not in counts:
        label='大作文正文应为5或6段（开头＋3或4个分论段＋结尾）' if essay else '短评正文应为5段（开头＋3个分论段＋结尾）'
        reasons.append(f'{label}，当前识别到{len(body)}段')
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
    return dict(policy='essay-v1' if essay else 'short-comment-v1',valid=not reasons,body_paragraphs=len(body),
                quantities=quantities,reasons=reasons)

def needs_reference_repair(reference):
    return not reference['layout_check']['valid'] or bool(reference.get('style_check') and not reference['style_check']['valid'])
