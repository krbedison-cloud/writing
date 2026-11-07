"""Compact shared prompts. Score calibration is sent once in its own field."""
QUESTION_FIRST = '以question.prompt审题，依question.materials核验原答；kind只表示20/30/50分训练档，不代表文体。任务、对象、内容、格式和字数按题干，不强加标题、称呼、落款、日期；不套其他题模板，歧义须说明。'
GRADING_TASK = ('独立批改。返回JSON：'
 '{task_analysis:{task,genre,requirements,material_basis},score,dimensions,strengths,weaknesses,suggestions,evidence,high_score_answer,high_score_plan:{framework,progression,material_use},high_score_self_check:{word_count,used_rows,complies,format_note}}。'
 'task/genre为题干任务/文体，requirements/material_basis为要求/材料依据。所有列表为字符串数组，评语通常2–4条，无优缺点或建议时可空；evidence不可空，须引用原答解释评价。'
 'score按full_score及calibration；dimensions用给定六维中文名为键、0–100数字为值，不嵌套说明。评语简明去重，范文完整纯文本，不用Markdown或字数附注。'+QUESTION_FIRST)
REFERENCE_FIELDS = '仅重写范文不评分，返回{high_score_answer,high_score_plan:{framework,progression,material_use},high_score_self_check:{word_count,used_rows,complies,format_note}}。范文纯文本，无Markdown或字数附注。'
REFERENCE_RULES = ('范文按reference_paper_spec（缺省用question.paper_spec）做一次字数及占格自检，不反复试算；rules同名字段优先，否则用question.paper_spec.rules。'
 '标题标点计字，空格、缩进、换行损失计格，缩进用全角空格；须同时满足字数上下限与纸面容量，format_note只写一句自检结论。')
DEFAULT_CALIBRATION = '依据本题制定细则，优秀须切题且有充分材料或论证支撑；具体说明缺失和扣分，不因语言华丽忽略离题。非官方阅卷。'
ESSAY_CALIBRATION = ('大作文50分训练校准（非保底或封顶）：30分非常一般，介于答非所问与论点准确；33分二类起点，论点准确、框架基本合理、论述无明显瑕疵且自洽，无功无过；35分二类中上；'
 '36–38分高水平，论点准确并递进、论证严密深刻，表达简明准确、用词规范，文笔优于二类；38分以上实战极罕见，须逐项引用原答证明突出表现。'
 '离题或严重缺陷应低于30，不凑分；三个或四个分论段均可，不因四段扣分。')

def calibration(question):
    from .reference_policy import is_essay
    return ESSAY_CALIBRATION if is_essay(question) else DEFAULT_CALIBRATION

PLAN_RULES = ('面向读者的简要构思讲解：framework概括总论点与分论点框架，progression解释各论点为何这样递进、串联；material_use是字符串数组，逐条用“材料编号：用于哪一论点、如何衔接”说明本题每份材料的用法，舍弃则说明理由，材料无编号可按主题标识。'
 '大作文若必要补充材料外案例，另列“材料外补例：事实、用途及必要性”。不强求每份材料入文，不复述材料原文或范文，不输出内部思考过程。框架和递进各1–2句，材料每条一句，通常共200–400字，小题更短，材料多可略延长。说明不混入范文，不计入其字数和占格。')
