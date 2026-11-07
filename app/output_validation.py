"""Lossless compatibility for common model JSON shapes; never invent or rescale scores."""
import re

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

def text_list(value):
    if isinstance(value,str):return [line.strip() for line in value.splitlines() if line.strip()]
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
