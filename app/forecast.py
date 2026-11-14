"""Manual, evidence-based score forecasts with a bounded locally prepared input."""
import hashlib
import json
import math
import statistics
from collections import Counter
from pydantic import BaseModel, Field, field_validator
from typing import Literal
from .schemas import KINDS, DIMENSIONS
from .output_validation import number, text_list

FORECAST_VERSION = 1
RECENT_ROWS = 6
FORECAST_TASK = ('预测浙江省考申论三题表现，仅基于给定历史评分与能力摘要。综合近期趋势、稳定性、六维能力、反复漏点、复练和模型分歧；均分仅作参考，不能直接当预测分，不因练习次数多而加分。'
 '同题复练降低证据独立性，模型偏差和跨省题目难度只能据证据谨慎判断，不当成官方校准。'
 '参考训练档：应用文17–19较弱、20–22中等、23–25中上、25以上极少；作文30一般、33二类起点、35中上、36–38高、38以上极少，非硬封顶。'
 'scores分别为summary满分20、practical满分30、essay满分50，至多1位小数；没有对应题型样本必须null，其他证据不足也可null。样本少或分歧大时降低confidence。'
 'analysis用2–3句说明预测依据、变化和不确定性，不逐题复盘、不抄统计表或内部编号。统计由程序给出，不重新计算均分、六维或总分、不虚构排名或上岸概率。必要判断后直接给出结果，不反复验算或模拟格纸。'
 '只返回JSON：{"scores":{"summary":null,"practical":null,"essay":null},"confidence":"low","analysis":"简短预测依据"}。confidence仅low/medium/high；程序校验并相加预测项，不必输出总分。')


class ForecastInput(BaseModel):
    provider_id: str = Field(min_length=1, max_length=100)


class ForecastResult(BaseModel):
    scores: dict[str, float | None]
    confidence: Literal['low','medium','high']
    analysis: str = Field(min_length=2, max_length=2400)

    @field_validator('scores', mode='before')
    @classmethod
    def numeric_scores(cls,value):
        if not isinstance(value,dict) or set(value)!=set(KINDS):raise ValueError('需提供三项预测分')
        result={}
        for kind,item in value.items():
            if item is None:result[kind]=None;continue
            parsed=number(item)
            if isinstance(parsed,bool) or not isinstance(parsed,(int,float)) or not math.isfinite(parsed) or not 0<=parsed<=KINDS[kind][1]:
                raise ValueError('预测分超出题型满分或不是有效数字')
            result[kind]=parsed
        return result

    @field_validator('confidence',mode='before')
    @classmethod
    def confidence_aliases(cls,value):
        return {'低':'low','较低':'low','中':'medium','中等':'medium','高':'high','较高':'high'}.get(value,value) if isinstance(value,str) else value

    @field_validator('analysis',mode='before')
    @classmethod
    def readable_analysis(cls,value):
        if isinstance(value,(list,dict)):
            lines=text_list(value)
            if isinstance(lines,list) and all(isinstance(line,str) for line in lines):return '\n'.join(lines)
        return value


def valid_attempts(attempts):
    return [a for a in attempts if a.get('mean_score') is not None and a.get('question_snapshot',{}).get('kind') in KINDS
            and any(g.get('status')=='completed' for g in a.get('grades',[]))]


def forecast_signature(attempts):
    """Fingerprint only forecast evidence; rewriting a model reference is irrelevant."""
    evidence=[]
    for a in sorted(valid_attempts(attempts),key=lambda a:(a['created_at'],a['id'])):
        q=a['question_snapshot']
        evidence.append([a['id'],a['created_at'],q.get('id',a.get('question_id')),q.get('prompt'),q.get('materials'),q.get('source'),
                         hashlib.sha256(a.get('answer','').encode()).hexdigest(),q['kind'],a['mean_score'],
                         [[g.get('provider_id'),g.get('model'),g.get('calibration_policy'),
                           {k:g['result'].get(k) for k in ('score','dimensions','strengths','weaknesses','suggestions')}]
                          for g in a['grades'] if g.get('status')=='completed']])
    return hashlib.sha256(json.dumps(evidence,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _short(text,limit=64):
    text=' '.join(str(text).split())
    return text if len(text)<=limit else text[:limit-1]+'…'


def _mean(values):return round(statistics.mean(values),2) if values else None


def _dimensions(rows):
    return [_mean([r['dimensions'][i] for r in rows]) for i in range(len(DIMENSIONS))] if rows else [None]*len(DIMENSIONS)


def _bin(rows):
    return dict(n=len(rows),period=[rows[0]['date'],rows[-1]['date']],score=_mean([r['score'] for r in rows]),dimensions=_dimensions(rows))


def forecast_evidence(attempts):
    """All attempts affect distributions/trends; only recent short evidence is sent raw."""
    attempts=sorted(valid_attempts(attempts),key=lambda a:(a['created_at'],a['id']))
    groups={}
    for kind,(label,full) in KINDS.items():
        rows=[];question_ids={};seen_answers=set();strengths=Counter();weaknesses=Counter();actions=Counter();offsets={};provinces=Counter()
        for a in attempts:
            q=a['question_snapshot']
            if q['kind']!=kind:continue
            good=[g for g in a['grades'] if g.get('status')=='completed']
            qkey=(a.get('question_id',q.get('id')),q.get('prompt',''),q.get('materials',''))
            if qkey not in question_ids:question_ids[qkey]=len(question_ids)+1
            answer_key=(question_ids[qkey],hashlib.sha256(a.get('answer','').encode()).hexdigest())
            repeated=answer_key in seen_answers;seen_answers.add(answer_key)
            positives=list(dict.fromkeys(_short(item) for g in good for item in g['result'].get('strengths',[]) if item.strip()))
            negatives=list(dict.fromkeys(_short(item) for g in good for item in g['result'].get('weaknesses',[]) if item.strip()))
            tips=list(dict.fromkeys(_short(item) for g in good for item in g['result'].get('suggestions',[]) if item.strip()))
            strengths.update(positives);weaknesses.update(negatives);actions.update(tips)
            scores=[g['result']['score'] for g in good]
            rows.append(dict(date=a['created_at'][:10],q=question_ids[qkey],same_answer=repeated,score=a['mean_score'],
                             dimensions=[_mean([g['result']['dimensions'][name] for g in good]) for name in DIMENSIONS],
                             spread=round(max(scores)-min(scores),2),strengths=positives[:1],weaknesses=negatives[:2],actions=tips[:1]))
            for g in good:
                key=_short(g.get('model',g.get('name','模型')),40)
                offsets.setdefault(key,[]).append((g['result']['score']-a['mean_score'])/full*100)
            province=q.get('source',{}).get('province')
            if province:provinces[_short(province,12)]+=1
        if not rows:
            groups[kind]=dict(n=0,full_score=full)
            continue
        values=[r['score'] for r in rows];recent=rows[-RECENT_ROWS:];older=rows[:-RECENT_ROWS]
        width=max(1,math.ceil(len(older)/4))
        groups[kind]=dict(n=len(rows),full_score=full,unique_questions=len(question_ids),same_answer_repeats=sum(r['same_answer'] for r in rows),
                         score_stats=dict(mean=_mean(values),median=statistics.median(values),minimum=min(values),maximum=max(values),
                                          sd=round(statistics.pstdev(values),2),recent_mean=_mean([r['score'] for r in recent])),
                         dimensions=_dimensions(rows),recent_dimensions=_dimensions(recent),
                         recurring_strengths=strengths.most_common(3),recurring_weaknesses=weaknesses.most_common(4),next_actions=actions.most_common(2),
                         provinces=provinces.most_common(6),rater_offsets=[[key,len(v),_mean(v)] for key,v in sorted(offsets.items(),key=lambda item:-len(item[1]))[:6]],
                         older_bins=[_bin(older[i:i+width]) for i in range(0,len(older),width)],recent=recent)
    return dict(version=FORECAST_VERSION,record_count=len(attempts),dimension_order=DIMENSIONS,
                note='全部有效练习进入统计与分期趋势；recent为各题型最近6次摘要，older_bins覆盖其余练习。rater_offsets是同答评分相对均分的百分点偏移，不是官方偏差校准。',groups=groups)
