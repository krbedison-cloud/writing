"""Readable profile notes; displayed sentences are never sliced to fit a limit."""
import re
from typing import Annotated
from pydantic import BaseModel, Field, field_validator
from .output_validation import text_list

NOTES_VERSION = 2
BRIEF_POLICY = '''只写给考生直接阅读的训练要点，不写统计报告或逐题复盘。
返回JSON {analysis,strengths,weaknesses,action_plan}：
analysis：一句完整结论，最多60字，直接说明当前最需要解决的能力问题或变化。
strengths：最多2条，每条最多40字；weaknesses：最多2条，每条最多40字。
action_plan：最多3条，每条最多50字，只写下一次作答能执行的一个动作。
全部用自然、直白的中文完整短句，句末用句号，全文尽量不超过260字。
禁止复述分数、均分、练习次数、试卷覆盖和六维统计；数字只供你判断，不要抄进结论。
禁止内部编号、题目缩写、英文字母、Markdown、公式、映射表术语、长清单和省略号。
不要让用户“重做某编号题”；直接说训练动作，例如“先圈出题干中的对象、任务和字数要求。”
不要复述以上示例作为用户问题，没有证据的优点或短板不要编造；证据不足可用一句话说明。
不要截断句子。较长内容必须重新概括成完整短句。'''

ShortPoint = Annotated[str, Field(min_length=2, max_length=40)]
ActionPoint = Annotated[str, Field(min_length=2, max_length=50)]

class ReadablePortrait(BaseModel):
    analysis: str = Field(min_length=2, max_length=60)
    strengths: list[ShortPoint] = Field(max_length=2)
    weaknesses: list[ShortPoint] = Field(max_length=2)
    action_plan: list[ActionPoint] = Field(min_length=1, max_length=3)

    @field_validator('strengths','weaknesses','action_plan',mode='before')
    @classmethod
    def list_text(cls,value):return text_list(value)

    @field_validator('analysis','strengths','weaknesses','action_plan')
    @classmethod
    def readable(cls,value,info):
        def sentence(text):
            text=re.sub(r'\s+',' ',text).strip()
            if re.search(r'[A-Za-z]|…|\.{3}|[#*`]|\d+\s*[/／]|\d+\.\d+',text):
                raise ValueError('请用无编号、无分数、无省略号的中文完整短句')
            if info.field_name!='action_plan' and re.search(r'\d',text):
                raise ValueError('观察中不要重复统计数字')
            if not text:raise ValueError('要点不能为空')
            return text if text[-1] in '。！？' else text+'。'
        if isinstance(value,list):return list(dict.fromkeys(sentence(item) for item in value))
        return sentence(value)

def readable_portrait(value):
    return ReadablePortrait.model_validate(value).model_dump()

def short_text(value, limit):
    """Bound input evidence only, never profile output."""
    text=re.sub(r'\s+',' ',value).strip()
    return text if len(text)<=limit else text[:limit-1].rstrip()+'…'

def brief_points(items, count=3, limit=60):
    result=[]
    for item in items:
        text=short_text(item,limit)
        if text and text not in result:result.append(text)
        if len(result)==count:break
    return result

def rewrite_evidence(value):
    # Limit the rewrite input; do not send all history a second time.
    draft={'analysis':str(value.get('analysis',''))[:2000]}
    for key in ('strengths','weaknesses','action_plan'):
        items=text_list(value.get(key,[]))
        draft[key]=[s[:500] for s in items[:8] if isinstance(s,str)] if isinstance(items,list) else []
    return draft
