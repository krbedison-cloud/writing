"""Lossless compatibility for common model JSON shapes; never invent or rescale scores."""
import re
import math

NUMBER = re.compile(r'^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:分)?\s*$')

def number(value, maximum=None):
    if isinstance(value,dict):
        value=value.get('score',value.get('得分',value))
    if isinstance(value,bool):raise ValueError('布尔值不是分数')
    if isinstance(value,str):
        matched=NUMBER.fullmatch(value)
        if matched:return float(matched[1])
        if maximum is not None:
            fraction=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*',value)
            if fraction and float(fraction[2])==maximum:return float(fraction[1])
    return value

# Only known narrative shapes are rendered. Unknown objects still fail schema
# validation; no evidence, score or missing field is synthesized.
FEEDBACK_LABELS = {
    'quote':'原答引文','original':'原答','original_text':'原答引文','original_quote':'原答引文',
    'answer_quote':'原答引文','answer_excerpt':'原答引文','user_quote':'原答引文','user_text':'原答',
    'excerpt':'引文','text':'内容','content':'内容','point':'要点','criterion':'评分要点',
    'requirement':'题干要求','reason':'依据','analysis':'分析','explanation':'说明',
    'comment':'评语','feedback':'反馈','issue':'问题','problem':'问题','fact':'事实',
    'basis':'依据','evidence':'证据','deduction_reason':'扣分依据','loss_reason':'扣分依据',
    'suggestion':'建议','advice':'建议','action':'行动','usage':'用法',
    'deduction':'扣分','deduct':'扣分','points_deducted':'扣分','deducted_points':'扣分',
    'score':'得分','material':'材料','material_reference':'材料依据','material_basis':'材料依据',
}
FEEDBACK_LABELS.update({
    'citation':'引文','original_answer':'原答','original_excerpt':'原答引文','quoted_text':'引文',
    'answer_text':'原答','source_text':'材料引文','user_answer_excerpt':'原答引文',
    'observation':'观察','rationale':'依据','evaluation':'评价','deduction_explanation':'扣分依据',
})


def _feedback_key(key):
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])','_',key.strip()).lower().replace('-','_').replace(' ','_')


NARRATIVE_KEYS = set(FEEDBACK_LABELS)-{'deduction','deduct','points_deducted','deducted_points','score','material','material_reference'}
NARRATIVE_KEYS.update(('原文','原答','原句','引文','引用','原答案','原答引文','原答案片段','内容','要点','评分点','得分点',
                       '依据','得分依据','评分依据','扣分依据','分析','说明','评语','评价','问题','不足','失分点','原因','扣分原因','建议','改进建议','措施','原文片段','原文摘录','原答摘录','原答片段','答题原文','用户原文','用户答案',
                       '原答案引文','引用原文','证据','证据说明','得分理由','扣分理由','扣分点','问题说明','评分理由','扣分说明','具体问题','原答表述','对应要求','材料原文','材料要点'))


def _has_text(value,depth=0):
    if depth>4:return False
    if isinstance(value,str):return bool(value.strip())
    if isinstance(value,list):return any(_has_text(item,depth+1) for item in value)
    if isinstance(value,dict):return any(_has_text(item,depth+1) for item in value.values())
    return False


def _feedback_value(value,depth=0):
    if depth>4:return None
    if isinstance(value,str):return value
    if value is None:return '未提供'
    if isinstance(value,bool):return '是' if value else '否'
    if isinstance(value,int):return str(value)
    if isinstance(value,float):return str(value) if math.isfinite(value) else None
    if isinstance(value,list):
        parts=[_feedback_value(item,depth+1) for item in value]
        return '；'.join(parts) if all(part is not None for part in parts) else None
    if isinstance(value,dict) and all(isinstance(key,str) and key.strip() for key in value):
        parts=[]
        for key,item in value.items():
            text=_feedback_value(item,depth+1)
            if text is None:return None
            label=FEEDBACK_LABELS.get(_feedback_key(key),key)
            parts.append(label+'：'+text)
        return '；'.join(parts)
    return None


def feedback_text(value):
    if not isinstance(value,dict):return value
    known=any(isinstance(key,str) and _feedback_key(key) in NARRATIVE_KEYS and _has_text(item)
              for key,item in value.items())
    if not known:return value
    rendered=_feedback_value(value)
    return rendered if rendered is not None else value


def text_list(value):
    if isinstance(value,str):return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value,dict):
        rendered=feedback_text(value)
        if isinstance(rendered,str):return [rendered]
        # Some providers number text entries instead of using a JSON array.
        if value and all(isinstance(key,str) and re.fullmatch(r'[0-9一二三四五六七八九十]+',key) for key in value):
            return [key+'：'+item if isinstance(item,str) else feedback_text(item) for key,item in value.items()]
        return value
    if isinstance(value,list):return [feedback_text(item) for item in value if not (isinstance(item,str) and not item.strip())]
    return value

def dimensions(value):
    if isinstance(value,list):
        result={}
        for item in value:
            if not isinstance(item,dict):return value
            name=item.get('name',item.get('dimension'))
            if not isinstance(name,str) or name.strip() in result:return value
            result[name.strip()]=item
        value=result
    if isinstance(value,dict):
        result={}
        for key,item in value.items():
            name=key.strip() if isinstance(key,str) else key
            if name in result:return value
            result[name]=number(item,100)
        return result
    return value

LABELS={'score':'总分','dimensions':'六维分','high_score_answer':'高分答案',
        'strengths':'优点','weaknesses':'不足','suggestions':'改进建议','evidence':'评分证据',
        'analysis':'分析正文','consensus':'共识','disagreements':'分歧','action_plan':'训练建议',
        'high_score_plan':'构思说明','framework':'论点框架','progression':'层次递进','material_use':'材料运用说明',
        'task_analysis':'审题分析','high_score_self_check':'范文字数自检'}

def validation_message(error):
    messages=[]
    for item in error.errors(include_input=False,include_context=False,include_url=False):
        root=item['loc'][0] if item['loc'] else ''
        label=LABELS.get(root,'输出字段')
        kind=item['type']
        if root=='dimensions':reason='需提供六个指定中文维度，每项为0–100的有限数字'
        elif kind=='missing':reason='缺失必填字段'
        elif kind in ('float_parsing','float_type','finite_number','greater_than_equal','less_than_equal'):
            reason='需为范围内的有限数字'
        elif kind in ('list_type','string_type'):reason='需为文字列表' if kind=='list_type' else '需为纯文字'
        elif kind in ('too_short','string_too_short'):reason='内容为空或过短'
        elif kind=='too_long':reason='列表条数超过上限'
        elif kind=='string_too_long':reason='文字长度超过上限'
        else:reason='字段格式不符合要求'
        message=label+'：'+reason
        if message not in messages:messages.append(message)
        if len(messages)>=4:break
    return '模型输出格式有误：'+'；'.join(messages)+'。已保留原答案与成功结果，可重试失败项'
