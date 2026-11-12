"""Normalize common extraction JSON shapes without inventing questions or evidence."""
import re
from pydantic import ValidationError
from .schemas import PaperInput
from .pdf import classify, word_limits
from .materials import number_value

FORMAT = '''识别试卷：只返回JSON对象，字段严格使用英文键：
{"title":"试卷名称","year":"2026","province":"浙江","volume":"B卷","materials":"完整给定材料，保留材料编号与换行","questions":[{"number":1,"kind":"summary","prompt":"完整题干和作答要求","word_min":0,"word_max":300,"classification_note":"归类依据"}]}。
questions必须是数组，number为原题题号整数，year为字符串；word_min/word_max为整数，未规定填0，不要填null或“300字以内”。kind仅summary/practical/essay，表示20/30/50训练档：短概括/不超过400字短简报summary，应用文/500–600字短评practical，长篇议论essay，保留实际文体。来源不确定写待确认。完整摘录材料和每题题干，不包含答题纸。仅从提供文本取材，不编造材料归属；材料范围交由用户校对。'''
ALIASES={'概括题':'summary','概括':'summary','归纳概括':'summary','应用文':'practical','应用文题':'practical','公文题':'practical','应用文/公文题':'practical','大作文':'essay','大作文题':'essay','议论文':'essay'}

def normalize_import(value, paper):
    for key in ('paper','data','result'):
        if 'questions' not in value and isinstance(value.get(key),dict):
            value=value[key]
    result=dict(value)
    for key in ('title','year','province','volume','materials'):
        if result.get(key) is None:
            result[key]=paper.get(key,'待确认' if key!='materials' else '')
    if isinstance(result.get('year'),int):result['year']=str(result['year'])
    if isinstance(result.get('materials'),list) and all(isinstance(x,str) for x in result['materials']):
        result['materials']='\n\n'.join(result['materials'])
    questions=result.get('questions')
    if isinstance(questions,list):
        normalized=[]
        for q in questions:
            if not isinstance(q,dict):normalized.append(q);continue
            q=dict(q)
            # Extraction cannot approve the paper or silently choose evidence scope.
            for key in ('material_mode','material_ids','custom_materials'):q.pop(key,None)
            if isinstance(q.get('number'),str):
                label=re.fullmatch(r'(?:第)?([0-9０-９一二三四五六七八九十]+)(?:题)?',q['number'].strip())
                if label:q['number']=number_value(label[1])
            if isinstance(q.get('prompt'),list) and all(isinstance(x,str) for x in q['prompt']):
                q['prompt']='\n'.join(q['prompt'])
            kind=q.get('kind')
            if isinstance(kind,str):q['kind']=ALIASES.get(kind.strip(),kind.strip().lower())
            if q.get('kind') is None and isinstance(q.get('prompt'),str):
                q['kind'],_=classify(q['prompt'],0,len(questions))
            q['paper_rows']=0  # Only the user may set physical answer-sheet rows.
            for key in ('word_min','word_max','paper_rows'):
                v=q.get(key)
                if v is None:
                    limits=word_limits(q['prompt']) if isinstance(q.get('prompt'),str) else (0,0)
                    q[key]=limits[0 if key=='word_min' else 1] if key!='paper_rows' else 0
                elif isinstance(v,str):
                    m=re.fullmatch(r'\s*(\d+)\s*(?:字|行)?\s*',v)
                    if m:q[key]=int(m[1])
            if q.get('classification_note') is None:q['classification_note']='AI辅助识别，请校对'
            normalized.append(q)
        result['questions']=normalized
    result['confirmed']=False
    return PaperInput.model_validate(result).model_dump()


def import_errors(exc):
    if not isinstance(exc,ValidationError):return '未返回有效JSON对象'
    labels={'title':'试卷名称','year':'年份','province':'省份','volume':'卷别','materials':'给定材料','questions':'题目列表','number':'题号','kind':'训练题型','prompt':'完整题干','word_min':'最少字数','word_max':'最多字数','classification_note':'归类说明'}
    out=[]
    for item in exc.errors(include_input=False,include_context=False,include_url=False)[:6]:
        path=' / '.join('第'+str(x+1)+'项' if isinstance(x,int) else labels.get(x,x) for x in item['loc'])
        out.append(path+'：'+('缺失' if item['type']=='missing' else '内容或类型不符合要求'))
    return '；'.join(out)
