import asyncio
import hashlib
import json
import math
import statistics
import uuid
from datetime import datetime, timezone
from pydantic import ValidationError
from .db import dumps
from .llm import call_model, ModelError, TruncatedOutputError
from .materials import prepare_paper, model_question
from .schemas import Grade, Synthesis, Portrait, PaperInput, ReferenceDraft, DIMENSIONS, KINDS
from .paper import check_paper
from .portrait_summary import BRIEF_POLICY, brief_portrait, brief_points
from .output_validation import validation_message
from .import_validation import FORMAT, normalize_import, import_errors

GRADING_POLICY = 'question-first-v1'
QUESTION_FIRST = '以question.prompt的完整题干为准，先辨明任务、作答对象、文体、内容范围、格式及字数，再从question.materials提炼可核验的评分要点，最后逐项对照用户answer。kind只表示20/30/50分训练档，不代表文体。题干未要求的标题、称呼、落款、日期不得强加；格式只在本题任务确需时评价。不得用其他题目、旧批改或固定文体模板替代本题。题干有歧义须说明不确定性，不能臆造缺失要求。'
TASK_RUBRIC = '从本题题干和材料制定评分细则，区分内容要点、分析论证、结构表达及本题确需的文体格式；每项扣分必须说明它对应的题干要求、材料依据和用户原答证据。'

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return uuid.uuid4().hex

def public_error(exc):
    if isinstance(exc, ModelError):
        return str(exc)
    if isinstance(exc, ValidationError):
        return validation_message(exc)
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
        for paper in self.db.all('papers'):
            if paper.get('materials_version') != 1:
                prepared = prepare_paper(paper)
                self.db.put('papers', prepared['id'], prepared, prepared['created_at'])
        for table in ('jobs', 'attempts'):
            for row in self.db.all(table):
                if row['status'] in ('queued', 'running'):
                    row['status'] = 'interrupted'
                    row['error'] = '服务重启中断了任务；已保留原答案与成功结果，可重试'
                    if table=='attempts':
                        for grade in row.get('grades',[]):
                            if grade.get('status')=='running':
                                grade.update(status='failed',stage='interrupted',error=row['error'],finished_at=now())
                            elif grade.get('stage')=='reference_repair':
                                grade.update(stage='completed',finished_at=now())
                                if grade.get('references'):grade['references'][-1]['repair_error']=row['error']
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
    async def ask(self, provider, task, data, on_start=None):
        async with self.semaphore:
            if on_start:on_start()
            return await call_model(provider, task, data)
    async def classify(self, j, paper_id, provider_id):
        try:
            self.stage(j, '大模型识别题型与来源')
            paper = self.db.get('papers', paper_id)
            provider = self.provider(provider_id)
            payload = {'filename':paper['filename'], 'text':paper['raw_text']}
            for attempt in range(2):
                try:
                    result = await self.ask(provider, FORMAT, payload)
                    parsed = normalize_import(result, paper)
                    break
                except (ValidationError, ModelError) as exc:
                    # Only repair malformed output; do not repeat timeouts/HTTP failures.
                    if isinstance(exc, ModelError) and 'JSON' not in str(exc):raise
                    detail = import_errors(exc)
                    if attempt:
                        raise ModelError('AI识别格式校正后仍未通过：'+detail+'。原试卷已保留，可重新识别或手动校对') from exc
                    self.stage(j, '识别结果格式校正中（最多一次）')
                    payload = {**payload, 'format_errors':detail,
                               'instruction':'上次输出未通过校验，请按指定JSON字段重新提取完整试卷。'}
            old_questions = {q['number']:q for q in paper['questions']}
            for q in parsed['questions']:
                old = old_questions.get(q['number'], {})
                q['id'] = old.get('id', uid())
                if old.get('prompt') == q['prompt'] and old.get('material_mode') in ('selected','all','custom'):
                    q.update({k:old[k] for k in ('material_mode','material_ids','custom_materials') if k in old})
            paper.update(parsed, confirmed=False)
            paper = prepare_paper(paper)
            self.db.put('papers', paper_id, paper, paper['created_at'])
            self.finish(j)
        except Exception as exc:
            self.finish(j, public_error(exc))
    def reference(self, draft, q, direction='initial', id=None, created_at=None):
        return dict(id=id or uid(), answer=draft['high_score_answer'],
                    model_self_check=draft.get('high_score_self_check'),
                    layout_check=check_paper(draft['high_score_answer'],q['paper_spec']),
                    direction=direction, created_at=created_at or now())
    def references(self, a, grade):
        if grade.get('references'):
            return grade['references']
        q = model_question(a['question_snapshot'])
        return [self.reference(grade['result'],q,id='original',created_at=a['created_at'])]
    async def repair_reference(self, provider, q, reference):
        if reference['layout_check']['valid']:
            return None
        try:
            raw = await self.ask(provider, '范文校正。'+QUESTION_FIRST+'只重写高分参考答案，不重新批改用户作答。返回 {high_score_answer,high_score_self_check:{word_count,used_rows,complies,format_note}}。先自行逐字计数并计算25格纸面占用，再返回纯文本范文。格式仅按本题题干所需设置，不强加标题、称呼、落款或日期，用全角空格表示必要缩进和对齐，不使用Markdown，不在范文末尾附字数说明。必须同时满足字数上下限和答题纸行数；消除多余空行，不能靠省略必要格式作弊。', {'question':q,'previous_answer':reference['answer'],'actual_check':reference['layout_check']})
            draft = ReferenceDraft.model_validate(raw).model_dump()
            return self.reference(draft,q,direction='auto_repair')
        except Exception as exc:
            reference['repair_error'] = public_error(exc)
            return None
    async def regenerate_reference(self, j, attempt_id, provider_id, direction, reference_id):
        try:
            self.stage(j,'正在单独重写高分范文，并检查字数与占格')
            a = self.db.get('attempts',attempt_id)
            g = next(g for g in a['grades'] if g['provider_id']==provider_id and g['status']=='completed')
            provider = self.provider(provider_id)
            original = next(p for p in a['providers'] if p['id']==provider_id)
            if any(provider[k]!=original[k] for k in ('model','base_url','protocol')):
                raise ModelError('模型配置已改变，不能把新模型的范文标成旧模型；请恢复原模型配置或重新作答')
            versions = self.references(a,g)
            previous = next((r for r in versions if r['id']==reference_id),None)
            if not previous:
                raise ModelError('所选范文版本不存在，请刷新后重试')
            q = model_question(a['question_snapshot'])
            spec = q['paper_spec']
            words = previous['layout_check']['word_count']
            ceiling = spec['word_max'] or spec['cells']
            target = min(ceiling,max(words+max(20,round(words*.15)),spec['word_min'])) if direction=='more' else max(spec['word_min'],min(ceiling,round(words*.82)))
            task = '单独重写高分范文。用户认为上版字数少了，请适度补充有效要点与论证。' if direction=='more' else '单独重写高分范文。用户认为上版字数多了，请压缩冗余、合并不必要换行。'
            raw = await self.ask(provider, task+QUESTION_FIRST+'只返回 {high_score_answer,high_score_self_check:{word_count,used_rows,complies,format_note}}，不重新评分、不输出对用户原答的批改。范文文体及格式只依本题任务，不强加题干未要求的标题、称呼、落款与日期，必要缩进使用实际全角空格；先逐字和逐行自检，必须同时满足字数与25格答题纸行数。返回纯文本，禁止Markdown及范文内的字数说明。', {'question':q,'previous_answer':previous['answer'],'previous_check':previous['layout_check'],'target_word_count':target,'direction':direction})
            candidate = self.reference(ReferenceDraft.model_validate(raw).model_dump(),q,direction)
            versions.append(candidate)
            repaired = await self.repair_reference(provider,q,candidate)
            if repaired:
                versions.append(repaired)
            # Grading and other reference jobs for the attempt are excluded by API guards.
            a = self.db.get('attempts',attempt_id)
            g = next(g for g in a['grades'] if g['provider_id']==provider_id)
            g['references'] = versions
            a['reference_updated_at'] = now()
            self.db.put('attempts',a['id'],a,a['created_at'])
            self.finish(j)
        except Exception as exc:
            self.finish(j,public_error(exc))
    async def grade(self, j, attempt_id, provider_ids=None):
        a = self.db.get('attempts', attempt_id)
        a.update(status='running', error=None, grading_policy=GRADING_POLICY, grading_stage='各教练批改中', summary_status='pending')
        self.db.put('attempts', a['id'], a, a['created_at'])
        self.stage(j, '各模型独立批改中')
        q = model_question(a['question_snapshot'])
        full = KINDS[q['kind']][1]
        repair_tasks=[]
        def save_grade(g):
            a['grades']=[x for x in a['grades'] if x['provider_id']!=g['provider_id']]+[g]
            self.db.put('attempts',a['id'],a,a['created_at'])
        def progress(g,stage):
            g.update(stage=stage,stage_started_at=now())
            if stage=='requesting':g['request_started_at']=g['stage_started_at']
            save_grade(g)
        async def repair_for_grade(provider,g,reference):
            try:
                repaired=await self.repair_reference(provider,q,reference)
                if repaired:g['references'].append(repaired)
            except asyncio.CancelledError:
                reference['repair_error']='服务中断了范文校正，初版已保留，可稍后单独重写范文'
                raise
            finally:
                g['finished_at']=now();progress(g,'completed')
        async def one(pid):
            if any(g['provider_id'] == pid and g['status'] == 'completed' for g in a['grades']):
                return
            snapshot = next(p for p in a['providers'] if p['id'] == pid)
            g = dict(provider_id=pid, name=snapshot['name'], model=snapshot['model'], status='running', grading_policy=GRADING_POLICY, queued_at=now())
            progress(g,'queued')
            try:
                p = self.provider(pid)
                # Never silently substitute a different model into an existing attempt.
                if (p['model'], p['base_url'], p['protocol']) != (snapshot['model'], snapshot['base_url'], snapshot['protocol']):
                    raise ModelError('模型配置已改变，请新建作答；不能将新模型混入旧批改')
                raw = await self.ask(p, '独立批改。审题分析与评语简明、去重，不复述大段材料；各列表通常2–4条，证据只摘必要短句，范文仍必须满足题干字数下限。审题分析task_analysis中，task为本题任务，genre为本题所需文体（无固定文体则明确说明），requirements逐条摘录或忠实概述题干要求，material_basis引用本题材料并说明对应要点。返回单个JSON对象 {task_analysis:{task,genre,requirements,material_basis},score,dimensions,strengths,weaknesses,suggestions,evidence,high_score_answer,high_score_self_check:{word_count,used_rows,complies,format_note}}。score 是本题训练满分内分数；dimensions 为六个指定维度各0–100。dimensions必须是六个指定中文名称为键、0–100数字为值的对象，不要嵌套说明；strengths、weaknesses、suggestions、evidence必须为字符串数组，前三项无内容可返回空数组，evidence须提供具体评分证据；high_score_answer必须为完整纯文本字符串；high_score_self_check 为前述自检对象；evidence 必须引用原答案并解释扣分。范文须自行逐字计数、自检文体格式与25格答题纸占用行数（paper_spec），同时满足两项上限。标题、称呼、落款、日期均计字，空格和换行损失占格；用实际全角空格表示缩进和对齐，范文纯文本，不要Markdown或字数说明。'+QUESTION_FIRST, {'full_score':full, 'dimensions':DIMENSIONS, 'rubric': TASK_RUBRIC, 'grading_policy': GRADING_POLICY, 'calibration':'优秀应满足题目核心要求且有充分材料或论证支撑；指出缺失与具体扣分理由，不因语言华丽忽略离题。自行拟定评分细则并保持一致；这不是官方阅卷。', 'question':q, 'answer':a['answer'], 'answer_layout':check_paper(a['answer'],q['paper_spec'])}, on_start=lambda:progress(g,'requesting'))
                g['response_received_at']=now()
                if getattr(raw,'usage',None):g['usage']=raw.usage
                progress(g,'validating')
                result = Grade.model_validate(raw)
                if result.score > full:
                    raise ModelError(f'模型分数超过本题 {full} 分满分，结果未计入均分')
                g.update(status='completed', result=result.model_dump())
                original_reference = self.reference(g['result'],q,id='original')
                g['references'] = [original_reference]
                g['grading_completed_at']=now();g['finished_at']=g['grading_completed_at']
                progress(g,'completed')
                if not original_reference['layout_check']['valid']:
                    g.pop('finished_at',None);progress(g,'reference_repair')
                    repair_tasks.append(asyncio.create_task(repair_for_grade(p,g,original_reference)))
            except Exception as exc:
                g.update(status='failed',error=public_error(exc),finished_at=now())
                if isinstance(exc,TruncatedOutputError):
                    g.update(error_code='output_truncated',token_limit=exc.token_limit,suggested_token_limit=exc.suggested_token_limit,usage=exc.usage)
                progress(g,'failed')
        try:
            targets=provider_ids if provider_ids is not None else [p['id'] for p in a['providers']]
            await asyncio.gather(*(one(pid) for pid in targets))
            good=[g for g in a['grades'] if g['status']=='completed']
            a['mean_score']=round(statistics.mean(g['result']['score'] for g in good),2) if good else None
            a['score_spread']=round(max(g['result']['score'] for g in good)-min(g['result']['score'] for g in good),2) if good else None
            self.db.put('attempts',a['id'],a,a['created_at'])
            async def summarize():
                self.stage(j,'主模型汇总中，范文校正并行执行')
                a.update(summary_status='running',grading_stage='主模型汇总中，已返回的评分可先阅读')
                self.db.put('attempts',a['id'],a,a['created_at'])
                try:
                    master=self.provider(a['master']['id'])
                    if any(master[k]!=a['master'][k] for k in ('model','base_url','protocol')):
                        raise ModelError('主模型配置已改变，请恢复原配置或重新提交')
                    raw = await self.ask(master, '汇总多位教练的批改，先以本题题干、材料和用户原答核验每位教练的审题与扣分依据；若有文体误判、强加格式或引用无关材料，须明确指出并排除其诊断影响，不把错误当共识。返回 {analysis,consensus,disagreements,action_plan}。analysis用300–600字给出关键诊断，其余为字符串数组。consensus和disagreements各最多5条，action_plan最多5条，每条只给一个具体动作，优先级从高到低，避免重复。识别共识和分歧，检验批评是否有原文证据，不盲信多数。均分已由程序计算，不改分。优先给出下一次能执行的修改动作。', {'question':q,'answer':a['answer'],'answer_layout':check_paper(a['answer'],q['paper_spec']),'mean_score':a['mean_score'],'successful_count':len(good),'requested_count':len(a['providers']),'reviews':[{k:g[k] for k in ('provider_id','name','model','status')} | {'result':{k:v for k,v in g['result'].items() if k not in ('high_score_answer','high_score_self_check')}} for g in good]})
                    a['synthesis']=Synthesis.model_validate(raw).model_dump();a['summary_status']='completed'
                except Exception as exc:
                    a.update(summary_status='failed',error=public_error(exc))
                if any(not task.done() for task in repair_tasks):
                    a['grading_stage']='评分与汇总已返回，正在完成范文校正'
                    self.stage(j,a['grading_stage'])
                self.db.put('attempts',a['id'],a,a['created_at'])
            if good:
                await asyncio.gather(summarize(),*repair_tasks)
                a['status']='summary_failed' if a['summary_status']=='failed' else 'completed' if len(good)==len(a['providers']) else 'partial'
            else:
                a.update(status='failed',error='所有批改模型均失败；原答案已存档，可重试')
            a['finished_at']=now();a['grading_stage']='批改完成';a.pop('retry_provider_ids',None)
            self.db.put('attempts',a['id'],a,a['created_at']);self.finish(j,a.get('error'))
            if good:
                profile_job=self.job('profile');self.launch(self.portrait(profile_job,a['master']['id']))
        except Exception as exc:
            a.update(status='failed',error=public_error(exc));a.pop('retry_provider_ids',None)
            self.db.put('attempts',a['id'],a,a['created_at']);self.finish(j,a['error'])
        finally:
            for task in repair_tasks:
                if not task.done():task.cancel()
            if repair_tasks:await asyncio.gather(*repair_tasks,return_exceptions=True)
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
        if portrait:
            portrait = dict(portrait, result=brief_portrait(portrait['result']))
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
                    history.append({'id':a['id'], 'date':a['created_at'], 'kind':a['question_snapshot']['kind'],
                                    'score':a['mean_score'], 'full_score':KINDS[a['question_snapshot']['kind']][1],
                                    'dimensions':{k:round(statistics.mean(g['result']['dimensions'][k] for g in good),1) for k in DIMENSIONS},
                                    'score_spread':a.get('score_spread'),
                                    'strengths':brief_points([t for g in good for t in g['result']['strengths']],2,80),
                                    'weaknesses':brief_points([t for g in good for t in g['result']['weaknesses']],3,80)})
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
                        part = await self.ask(provider, '历史分块分析：'+BRIEF_POLICY+'返回 {analysis,strengths,weaknesses,action_plan}，analysis为文字其余为字符串数组。保留本组记录的时间跨度、变化、共性不足、反例与不确定性；不能把本组说成全部历史。不虚构分数。只保留本组最重要的变化和训练重点。', {'records':batch})
                        summary = brief_portrait(Portrait.model_validate(part).model_dump())
                        reduced.append({'record_count':sum(row.get('record_count',1) for row in batch),'summary':summary})
                    if len(dumps(reduced)) >= len(dumps(evidence)):
                        raise ModelError('历史分块摘要过长，无法继续压缩；数值统计已更新，请换用能遵循长度要求的主模型')
                    evidence, compressed = reduced, True
                raw = await self.ask(provider, '基于全部历史证据生成简短能力观察，'+BRIEF_POLICY+'返回 {analysis,strengths,weaknesses,action_plan}。analysis 为文字，其余为字符串数组。解释进步与反复问题、缺失题型、样本偏差和跨模型差异，给出下一轮训练建议。不重算程序统计、不虚构排名、上岸概率或确定的考试分数。所有历史均含在记录中，单条为压缩摘要，不代表已阅读未提供的完整答案。', {'statistics':{k:v for k,v in stats.items() if k not in ('portrait','timeline')}, 'all_history':evidence, 'history_is_compressed':compressed, 'record_count':len(history)})
                result = brief_portrait(Portrait.model_validate(raw).model_dump())
                self.db.setting('portrait', dict(result=result, signature=stats['signature'], model=provider['model'], name=provider['name'], updated_at=now(), history_count=len(history)))
                self.finish(j)
            except Exception as exc:
                self.finish(j, public_error(exc))
