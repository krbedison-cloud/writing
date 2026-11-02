import asyncio
import hashlib
import json
import math
import statistics
import uuid
from datetime import datetime, timezone
from pydantic import ValidationError
from .db import dumps
from .llm import call_model, ModelError
from .schemas import Grade, Synthesis, Portrait, PaperInput, DIMENSIONS, KINDS

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return uuid.uuid4().hex

def public_error(exc):
    if isinstance(exc, ModelError):
        return str(exc)
    if isinstance(exc, ValidationError):
        return '模型输出未通过结构校验，请检查必填字段、分数与维度范围后重试'
    return '任务执行失败，原答案和已完成结果已保留；可重试'

class Service:
    def __init__(self, db):
        self.db = db
        self.tasks = set()
        self.semaphore = asyncio.Semaphore(4)
        self.profile_lock = asyncio.Lock()
    def launch(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
    def recover(self):
        for table in ('jobs', 'attempts'):
            for row in self.db.all(table):
                if row['status'] in ('queued', 'running'):
                    row['status'] = 'interrupted'
                    row['error'] = '服务重启中断了任务；已保留原答案与成功结果，可重试'
                    self.db.put(table, row['id'], row, row.get('created_at'))
    def job(self, kind, target=None):
        j = dict(id=uid(), kind=kind, target_id=target, status='queued', stage='等待执行', created_at=now())
        self.db.put('jobs', j['id'], j)
        return j
    def stage(self, j, text):
        j.update(status='running', stage=text)
        self.db.put('jobs', j['id'], j)
    def finish(self, j, error=None):
        j.update(status='failed' if error else 'completed', error=error, finished_at=now())
        self.db.put('jobs', j['id'], j)
    def provider(self, id):
        p = self.db.provider(id, private=True)
        if not p:
            raise ModelError('所选模型配置已删除，请重新配置')
        return p
    async def ask(self, provider, task, data):
        async with self.semaphore:
            return await call_model(provider, task, data)
    async def classify(self, j, paper_id, provider_id):
        try:
            self.stage(j, '大模型识别题型与来源')
            paper = self.db.get('papers', paper_id)
            result = await self.ask(self.provider(provider_id), '识别试卷：返回 {title,year,province,volume,materials,questions:[{number,kind,prompt,word_min,word_max,classification_note}]}。kind 仅 summary/practical/essay。按训练档：短概括与不超过400字的短简报=summary(20分)，应用文/500–600字短评=practical(30分)，长篇议论=essay(50分)。保留原文体和字数要求。来源不确定写待确认。完整摘录题干，不包含答题纸；只从提供文本取材。', {'filename':paper['filename'], 'text':paper['raw_text']})
            parsed = PaperInput.model_validate(result).model_dump()
            old_ids = {q['number']:q['id'] for q in paper['questions']}
            for q in parsed['questions']:
                q['id'] = old_ids.get(q['number'], uid())
            paper.update(parsed, confirmed=False)
            self.db.put('papers', paper_id, paper, paper['created_at'])
            self.finish(j)
        except Exception as exc:
            self.finish(j, public_error(exc))
    async def grade(self, j, attempt_id):
        a = self.db.get('attempts', attempt_id)
        a.update(status='running', error=None)
        self.db.put('attempts', a['id'], a, a['created_at'])
        self.stage(j, '各模型独立批改中')
        q = a['question_snapshot']
        full = KINDS[q['kind']][1]
        async def one(pid):
            if any(g['provider_id'] == pid and g['status'] == 'completed' for g in a['grades']):
                return
            snapshot = next(p for p in a['providers'] if p['id'] == pid)
            g = dict(provider_id=pid, name=snapshot['name'], model=snapshot['model'], status='failed')
            try:
                p = self.provider(pid)
                # Never silently substitute a different model into an existing attempt.
                if (p['model'], p['base_url'], p['protocol']) != (snapshot['model'], snapshot['base_url'], snapshot['protocol']):
                    raise ModelError('模型配置已改变，请新建作答；不能将新模型混入旧批改')
                raw = await self.ask(p, '独立批改。返回 {score,dimensions,strengths,weaknesses,suggestions,evidence,high_score_answer}。score 是本题训练满分内分数；dimensions 为六个指定维度各0–100。其余除范文外为字符串数组；evidence 必须引用原答案并解释扣分。范文遵循原题字数和文体。短简报虽在20分训练档仍按简报文体评价。', {'full_score':full, 'dimensions':DIMENSIONS, 'rubric': {'summary':'重点考察要点完整准确、分类概括与材料依据；短简报须同时保留标题和简报层次，禁止因训练档为概括题而忽略文体。', 'practical':'重点考察对象与目的、指定文体、材料转化与可执行性；短评考察观点鲜明及短篇论证。', 'essay':'重点考察中心论点、分论点与论证深度、事实材料与现实联系、全文结构和表达。'}[q['kind']], 'calibration':'优秀应满足题目核心要求且有充分材料或论证支撑；指出缺失与具体扣分理由，不因语言华丽忽略离题。自行拟定评分细则并保持一致；这不是官方阅卷。', 'question':q, 'answer':a['answer']})
                result = Grade.model_validate(raw)
                if result.score > full:
                    raise ModelError(f'模型分数超过本题 {full} 分满分，结果未计入均分')
                g.update(status='completed', result=result.model_dump())
            except Exception as exc:
                g['error'] = public_error(exc)
            a['grades'] = [x for x in a['grades'] if x['provider_id'] != pid] + [g]
            self.db.put('attempts', a['id'], a, a['created_at'])
        try:
            await asyncio.gather(*(one(p['id']) for p in a['providers']))
            good = [g for g in a['grades'] if g['status'] == 'completed']
            a['mean_score'] = round(statistics.mean(g['result']['score'] for g in good), 2) if good else None
            a['score_spread'] = round(max(g['result']['score'] for g in good) - min(g['result']['score'] for g in good), 2) if good else None
            self.db.put('attempts', a['id'], a, a['created_at'])
            if not good:
                a.update(status='failed', error='所有批改模型均失败；原答案已存档，可重试')
            else:
                self.stage(j, '主模型汇总诊断')
                try:
                    master = self.provider(a['master']['id'])
                    if (master['model'], master['base_url'], master['protocol']) != (a['master']['model'], a['master']['base_url'], a['master']['protocol']):
                        raise ModelError('主模型配置已改变，请恢复原配置或重新提交')
                    raw = await self.ask(master, '汇总多位教练的批改，返回 {analysis,consensus,disagreements,action_plan}。analysis为详细诊断文字，其余为字符串数组。识别共识和分歧，检验批评是否有原文证据，不盲信多数。均分已由程序计算，不改分。优先给出下一次能执行的修改动作。', {'question':q,'answer':a['answer'],'mean_score':a['mean_score'],'successful_count':len(good),'requested_count':len(a['providers']),'reviews':good})
                    a['synthesis'] = Synthesis.model_validate(raw).model_dump()
                    a['status'] = 'completed' if len(good) == len(a['providers']) else 'partial'
                except Exception as exc:
                    a.update(status='summary_failed', error=public_error(exc))
            a['finished_at'] = now()
            self.db.put('attempts', a['id'], a, a['created_at'])
            self.finish(j, a.get('error'))
            if good:
                profile_job = self.job('profile')
                self.launch(self.portrait(profile_job, a['master']['id']))
        except Exception as exc:
            a.update(status='failed', error=public_error(exc))
            self.db.put('attempts', a['id'], a, a['created_at'])
            self.finish(j, a['error'])
    def stats(self):
        attempts = self.db.all('attempts')
        valid = [a for a in attempts if a.get('mean_score') is not None]
        dims = {}
        for name in DIMENSIONS:
            values = [statistics.mean(g['result']['dimensions'][name] for g in a['grades'] if g['status'] == 'completed') for a in valid]
            dims[name] = round(statistics.mean(values), 1) if values else None
        forecast = {}
        for kind, (label, full) in KINDS.items():
            rows = [a for a in valid if a['question_snapshot']['kind'] == kind]
            recent = rows[:5]
            forecast[kind] = {'label':label, 'full_score':full, 'count':len(rows), 'recent_count':len(recent), 'score':round(statistics.mean(a['mean_score'] for a in recent), 1) if recent else None}
        complete = all(f['score'] is not None for f in forecast.values())
        total = round(sum(f['score'] for f in forecast.values()), 1) if complete else None
        seen = set()
        for a in valid:
            seen.add((a['question_id'], hashlib.sha256(a['answer'].encode()).hexdigest()))
        xp = len(seen) * 25
        timeline = [{'id':a['id'],'date':a['created_at'], 'score':round(a['mean_score']/KINDS[a['question_snapshot']['kind']][1]*100, 1),'kind':a['question_snapshot']['kind']} for a in reversed(valid)]
        signature = hashlib.sha256(dumps([(a['id'], a['mean_score'], a.get('finished_at')) for a in valid]).encode()).hexdigest()
        portrait = self.db.setting('portrait')
        return dict(total_attempts=len(attempts), graded_count=len(valid), paper_count=len(self.db.all('papers')), dimensions=dims, forecast=forecast, estimated_total=total, xp=xp, level=xp//100+1, level_progress=xp%100, timeline=timeline, signature=signature, portrait=portrait, portrait_stale=bool(portrait and portrait.get('signature') != signature), note='预估取各题型最近5次有效批改均分相加；六维分取全部有效作答的模型维度均分。重复练习、题目难度与模型偏差均会影响结果，不是官方成绩或排名。缺任一题型时不输出总分。')
    async def portrait(self, j, provider_id):
        async with self.profile_lock:
            try:
                self.stage(j, '基于全部练习记录更新能力画像')
                stats = self.stats()
                if not stats['graded_count']:
                    raise ModelError('请先完成至少一次有效批改')
                # Every valid attempt contributes both aggregate metrics and a compact evidence row.
                history = []
                for a in reversed(self.db.all('attempts')):
                    if a.get('mean_score') is None:
                        continue
                    good = [g for g in a['grades'] if g['status'] == 'completed']
                    history.append({'id':a['id'], 'date':a['created_at'], 'kind':a['question_snapshot']['kind'], 'score':a['mean_score'], 'full_score':KINDS[a['question_snapshot']['kind']][1], 'dimensions':{k:round(statistics.mean(g['result']['dimensions'][k] for g in good),1) for k in DIMENSIONS}, 'reviews':[{'name':g['name'],'model':g['model'],'score':g['result']['score'],'weaknesses':g['result']['weaknesses'][0][:180],'evidence':g['result']['evidence'][0][:200]} for g in good]})
                provider = self.provider(provider_id)
                evidence = history
                compressed = False
                # Reduce large histories in bounded batches instead of dropping older practice.
                while len(dumps(evidence)) > 65000:
                    batches, batch, size = [], [], 0
                    for row in evidence:
                        length = len(dumps(row))
                        if batch and size + length > 50000:
                            batches.append(batch)
                            batch, size = [], 0
                        batch.append(row)
                        size += length
                    if batch:
                        batches.append(batch)
                    reduced = []
                    for index, batch in enumerate(batches):
                        self.stage(j, f'历史较多，正在分析第 {index+1}/{len(batches)} 组证据')
                        part = await self.ask(provider, '历史分块分析：返回 {analysis,strengths,weaknesses,action_plan}，analysis为文字其余为字符串数组。保留本组记录的时间跨度、变化、共性不足、反例与不确定性；不能把本组说成全部历史。不虚构分数。控制文字总长在3000字内以便汇总。', {'records':batch})
                        summary = Portrait.model_validate(part).model_dump()
                        reduced.append({'record_count':sum(row.get('record_count',1) for row in batch),'summary':summary})
                    if len(dumps(reduced)) >= len(dumps(evidence)):
                        raise ModelError('历史分块摘要过长，无法继续压缩；数值统计已更新，请换用能遵循长度要求的主模型')
                    evidence, compressed = reduced, True
                raw = await self.ask(provider, '基于全部历史证据生成六维能力诊断，返回 {analysis,strengths,weaknesses,action_plan}。analysis 为文字，其余为字符串数组。解释进步与反复问题、缺失题型、样本偏差和跨模型差异，给出下一轮训练建议。不重算程序统计、不虚构排名、上岸概率或确定的考试分数。所有历史均含在记录中，单条为压缩摘要，不代表已阅读未提供的完整答案。', {'statistics':{k:v for k,v in stats.items() if k not in ('portrait','timeline')}, 'all_history':evidence, 'history_is_compressed':compressed, 'record_count':len(history)})
                result = Portrait.model_validate(raw).model_dump()
                self.db.setting('portrait', dict(result=result, signature=stats['signature'], model=provider['model'], name=provider['name'], updated_at=now(), history_count=len(history)))
                self.finish(j)
            except Exception as exc:
                self.finish(j, public_error(exc))
