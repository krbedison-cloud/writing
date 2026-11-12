"""Shared task prompts; keep scoring, reference writing and exact layout accounting distinct."""
import json

PROMPT_VERSION = 'efficient-v2'

QUESTION_FIRST = '以question.prompt和materials为准；kind只表示20/30/50分训练档，不代表文体。任务、对象、格式、字数按题干，不强加其他题模板；有歧义须说明。'
JSON_RULES = '仅返回一个完整JSON对象，不加代码块或说明；键和字符串用双引号，正文换行转义为\\n。示例为空结构，须据本题填实，列表均为字符串数组。'
SELF_CHECK_SHAPE = {'word_count':None,'used_rows':None,'complies':True,'format_note':''}
PLAN_SHAPE = {'framework':'','progression':'','material_use':[]}
REFERENCE_SHAPE = {'high_score_answer':'','high_score_plan':PLAN_SHAPE,'high_score_self_check':SELF_CHECK_SHAPE}
GRADE_JSON = json.dumps({
 'task_analysis':{'task':'','genre':'','requirements':[],'material_basis':[]},**REFERENCE_SHAPE,
 'score':0,'dimensions':{},'strengths':[],'weaknesses':[],'suggestions':[],'evidence':[]
},ensure_ascii=False,separators=(',',':'))
REFERENCE_JSON = json.dumps(REFERENCE_SHAPE,ensure_ascii=False,separators=(',',':'))
EFFICIENCY_RULES = '完成必要审题后直接形成一版成稿与结论；不枚举多套候选，不逐字列数、模拟格子或反复试排。保留关键分析，不为满足示例数值额外推演。'
GRADING_TASK = ('独立批改。'+JSON_RULES+GRADE_JSON+QUESTION_FIRST+
 '先独立构思范文，再评价answer；calibration只约束原答评分。score不超过full_score，dimensions按给定六维名称逐项返回0–100数字。'
 'task简述任务，genre为文体；requirements列硬性要求，material_basis列材料编号及要点，均用短句，不复述题干材料。'
 '优缺点、建议通常各2–4条，每条一句；同类合并但不能省略关键漏点。evidence不可空，短引原答并说明得失分依据，避免各字段重复长评。')
REFERENCE_FIELDS = '仅生成范文不评分。'+JSON_RULES+REFERENCE_JSON
REFERENCE_RULES = ('范文依本题题干、材料和专用规则独立构思；answer仅供批改，不作底稿、提纲或要点边界，不沿用错误遗漏，不刻意避开正确要点。'
 'previous_reference是模型旧范文，仅用于校正/增减篇幅。正文纯文本、实际换行、全角空格缩进，格式按题干。'
 '正文直接写主体、做法和成效，不说“材料中”“材料几中”“根据给定资料”等；材料编号只用于审题/构思说明。段落不加外包引号，正常引述保留。'
 '按reference_paper_spec（缺省question.paper_spec）一次自检文体、结构、材料和篇幅，format_note一句结论，complies如实判断。精确计字占格由程序校验；计数有把握填数字，否则填null，不猜数或反复重写。程序发现不合规最多反馈修订一次。')

DEFAULT_CALIBRATION = '按题干材料制定细则，优秀须切题且要点或论证充分；扣分须有材料与原答证据，不因辞藻忽略离题。非官方阅卷。'
PRACTICAL_CALIBRATION = ('应用文30分训练校准，仅评原答：依本题分配要点分，踩点给分，同义表达和有效抽象概括同等得分；重复不加分，同一缺陷不重复扣分。'
 '短评17–19分：结构基本对，语言不够简练准确、有要点缺失，按缺失程度裁量；20–22分：结构对、论点正确，能抽象概括材料案例，评论基本准确；'
 '23–25分：在中等基础上开头出彩、前置句准确对仗、典型案例覆盖充分恰当、评论切中要害、结尾升华到位。25分以上极少，须有原答突出证据。'
 '其他应用文按同等层次类推任务、信息、对象语气及格式，不强加短评框架。说明关键得失分和档次依据；不保底、不封顶、不统一折扣，不先定档倒推扣分，严重漏点或离题可低于17。')
ESSAY_CALIBRATION = ('大作文50分训练校准：30分非常一般，介于答非所问与论点准确；33分二类起点，论点准、框架合理、论述无明显瑕疵且自洽；35分二类中上；'
 '36–38分高水平，论点准确递进、论证严密深刻、表达简明规范、文笔更好；38分以上极罕见，须引原答证据。离题或严重缺陷低于30，不凑分；非保底或封顶，四个分论段不扣分。')


def calibration_policy(question):
    if question.get('kind')=='practical':return 'practical-v1'
    from .reference_policy import is_essay
    return 'essay-v1' if is_essay(question) else None


def calibration(question):
    return {'practical-v1':PRACTICAL_CALIBRATION,'essay-v1':ESSAY_CALIBRATION}.get(calibration_policy(question),DEFAULT_CALIBRATION)


PLAN_RULES = ('构思说明与范文分开：framework一句概括总分论点，progression一句说明递进与串联；material_use用“材料编号：用途、衔接或舍弃理由”的短句。'
 '同用途材料可合列编号，仍须覆盖各材料取舍；不复述原材料、范文或内部思考，不求全用。材料外补例须标明事实、用途及必要性。通常150–250字，小题更短，材料多可略增。')
SYNTHESIS_TASK = ('汇总多位教练的批改。'+JSON_RULES+'{"analysis":"","consensus":[],"disagreements":[],"action_plan":[]}。'
 '依本题题干、材料及原答核验审题和扣分证据，识别文体误判、强加格式、无关材料及评分校准偏差；排除错误诊断，不盲信多数、不修改程序均分。'
 'analysis通常150–250字指出关键不足；共识与分歧通常各1–3条，无则空，分歧引原答证据；建议2–4条，按优先级写下次可执行动作。同类合并，不重复长评。')
PLAN_TASK = ('范文构思讲解。'+JSON_RULES+json.dumps(PLAN_SHAPE,ensure_ascii=False,separators=(',',':'))+
 '仅解释给定reference_answer，不重写范文、不评分、不重新计算字数。'+PLAN_RULES)
