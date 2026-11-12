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
from .schemas import Grade, Synthesis, Portrait, PaperInput, ReferenceDraft, DIMENSIONS, KINDS, ReferencePlan
from .paper import check_paper
from .reference_policy import reference_policy, check_reference_style, needs_reference_repair, reference_paper_spec, reference_payload, is_essay, ESSAY_MATERIAL_PRIORITY
from .grading_prompts import QUESTION_FIRST, GRADING_TASK, REFERENCE_FIELDS, REFERENCE_RULES, PLAN_RULES, SYNTHESIS_TASK, PLAN_TASK, PROMPT_VERSION, calibration, calibration_policy
from .portrait_summary import BRIEF_POLICY, NOTES_VERSION, readable_portrait, brief_points, rewrite_evidence
from .output_validation import validation_message
from .import_validation import FORMAT, normalize_import, import_errors

GRADING_POLICY = 'question-first-v1'
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
            if paper.get('materials_version') != 1 or paper.get('word_limits_version')!=2:
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
                if old.get('prompt')==q['prompt'] and old.get('paper_rows'):q['paper_rows']=old['paper_rows']
            paper.update(parsed, confirmed=False)
            paper = prepare_paper(paper)
            self.db.put('papers', paper_id, paper, paper['created_at'])
            self.finish(j)
        except Exception as exc:
            self.finish(j, public_error(exc))
    def reference_question(self,a,reference=None):
        snapshot=a['question_snapshot']
        q=model_question(snapshot)
        if not is_essay(q):return q
        priority=(reference or {}).get('material_priority',snapshot.get('reference_material_priority'))
        if priority is None:
            # Older attempts have no cross-question snapshot. Only use a current
            # paper whose essay prompt and exact material text still match.
            paper=self.db.get('papers',a.get('paper_id',''))
            if paper:
                paper=prepare_paper(paper)
                live=next((item for item in paper['questions'] if item.get('id')==a.get('question_id')),None)
                if live and live['prompt']==q['prompt'] and model_question(live)['materials']==q['materials']:
                    priority=dict(live['reference_material_priority'],source='current_paper') if live.get('reference_material_priority') else None
        q['reference_material_priority']=priority or dict(version=1,available=False,preferred_ids=[],secondary_ids=[],unresolved_prior_questions=[])
        return q
    def reference(self, draft, q, direction='initial', id=None, created_at=None):
        return dict(id=id or uid(), answer=draft['high_score_answer'],
                    model_self_check=draft.get('high_score_self_check'),
                    layout_check=check_paper(draft['high_score_answer'],reference_paper_spec(q)),
                    direction=direction, created_at=created_at or now(),
                    **({'plan':draft['high_score_plan']} if draft.get('high_score_plan') else {}),
                    **({'material_priority':q['reference_material_priority']} if is_essay(q) and q.get('reference_material_priority') else {}),
                    **({'paper_spec':reference_paper_spec(q)} if is_essay(q) else {}),
                    **({'style_check':style} if (style:=check_reference_style(draft['high_score_answer'],q)) else {}))
    def references(self, a, grade):
        if grade.get('references'):
            return grade['references']
        q = model_question(a['question_snapshot'])
        return [self.reference(grade['result'],q,id='original',created_at=a['created_at'])]
    async def repair_reference(self, provider, q, reference):
        if not needs_reference_repair(reference):
            return None
        try:
            raw = await self.ask(provider, '范文校正。以actual_check实测结果为准，保留正确观点，仅修所列问题；一次返回完整修订稿，不重新逐字计算原文。'+QUESTION_FIRST+REFERENCE_FIELDS+REFERENCE_RULES+reference_policy(q)+PLAN_RULES, {'question':q,'previous_reference':reference['answer'],'actual_check':reference['layout_check'], **reference_payload(q), **({'style_check':reference['style_check']} if reference.get('style_check') else {})})
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
            q = self.reference_question(a,previous if direction!='rewrite' else None)
            spec = reference_paper_spec(q)
            words = previous['layout_check']['word_count']
            ceiling = spec['word_max'] or spec['cells']
            target = min(ceiling,max(words+max(20,round(words*.15)),spec['word_min'])) if direction=='more' else max(spec['word_min'],min(ceiling,round(words*.82)))
            if direction=='rewrite':
                target=max(spec['word_min'],min(ceiling,words))
            task = '单独重写高分范文。用户认为上版字数少了，请适度补充有效要点与论证。' if direction=='more' else '单独重写高分范文。用户认为上版字数多了，请压缩冗余、合并不必要换行。'
            if direction=='rewrite':
                task='单独重写高分范文。按本题当前范文要求重新生成，字数以原题和纸面限制为准。'
            data={'question':q,'direction':direction, **reference_payload(q)}
            if direction!='rewrite':
                data.update(previous_reference=previous['answer'],previous_check=previous['layout_check'],target_word_count=target)
            raw = await self.ask(provider, task+QUESTION_FIRST+REFERENCE_FIELDS+REFERENCE_RULES+reference_policy(q)+PLAN_RULES, data)
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
    async def explain_reference(self,j,attempt_id,provider_id,reference_id):
        try:
            self.stage(j,'正在生成当前范文的构思说明')
            a=self.db.get('attempts',attempt_id)
            g=next(g for g in a['grades'] if g['provider_id']==provider_id and g['status']=='completed')
            provider=self.provider(provider_id)
            original=next(p for p in a['providers'] if p['id']==provider_id)
            if any(provider[k]!=original[k] for k in ('model','base_url','protocol')):
                raise ModelError('原范文模型配置已改变，请恢复该配置后再补充构思说明')
            versions=self.references(a,g)
            ref=next(r for r in versions if r['id']==reference_id)
            q=self.reference_question(a,ref)
            raw=await self.ask(provider,PLAN_TASK+(ESSAY_MATERIAL_PRIORITY if is_essay(q) else ''),{'question':q,'reference_answer':ref['answer']})
            plan=ReferencePlan.model_validate(raw).model_dump()
            ref.update(plan=plan,plan_updated_at=now())
            if getattr(raw,'usage',None):ref['plan_usage']=raw.usage
            g['references']=versions
            self.db.put('attempts',a['id'],a,a['created_at'])
            self.finish(j)
        except Exception as exc:self.finish(j,public_error(exc))
    def prepare_retry(self, a, value):
        """Refresh only requested failed slots, preserving completed model attribution."""
        safe = lambda p: {k:v for k,v in p.items() if k not in ('api_key','has_key')}
        failed = [p['id'] for p in a['providers'] if not any(g['provider_id']==p['id'] and g['status']=='completed' for g in a['grades'])]
        if value.provider_id is not None:
            if not any(p['id']==value.provider_id for p in a['providers']):
                raise ModelError('该教练未参与本次作答')
            if value.provider_id not in failed:
                raise ModelError('该教练已成功，无需重试')
        targets = [value.provider_id] if value.provider_id else [] if value.master_id else failed
        if value.replacement_provider_id and any(p['id']==value.replacement_provider_id and p['id']!=value.provider_id for p in a['providers']):
            raise ModelError('该接口已在本次批改中，请选择其他接口，避免重复计分')
        events = []
        retry_ids = []
        master_id = value.master_id or a['master']['id']
        for old_id in targets:
            old = next(p for p in a['providers'] if p['id']==old_id)
            replacement = safe(self.provider(value.replacement_provider_id or old_id))
            old_grade = next((g for g in a['grades'] if g['provider_id']==old_id), None)
            events.append(dict(role='coach',at=now(),previous=old,replacement=replacement,failed_grade=old_grade))
            a['providers'] = [replacement if p['id']==old_id else p for p in a['providers']]
            a['grades'] = [g for g in a['grades'] if g['provider_id']!=old_id]
            retry_ids.append(replacement['id'])
            if a['master']['id']==old_id and not value.master_id:
                master_id = replacement['id']
        # Explicit retry uses the current master configuration as well. Successful
        # coach snapshots remain untouched even if they share this configuration ID.
        master = self.db.provider(master_id, private=False)
        if value.master_id and not master:
            raise ModelError('所选汇总接口已删除，请重新选择')
        if master:
            master = safe(master)
            if master != a['master']:
                events.append(dict(role='master',at=now(),previous=a['master'],replacement=master,error=a.get('error')))
                a['master'] = master
        a.setdefault('retry_history',[]).extend(events)
        a.update(status='queued',retry_provider_ids=retry_ids,synthesis=None,error=None)
        return retry_ids
    async def grade(self, j, attempt_id, provider_ids=None):
        a = self.db.get('attempts', attempt_id)
        a.update(status='running', error=None, grading_policy=GRADING_POLICY, grading_stage='各教练批改中', summary_status='pending')
        self.db.put('attempts', a['id'], a, a['created_at'])
        self.stage(j, '各模型独立批改中')
        q = self.reference_question(a)
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
            g = dict(provider_id=pid, name=snapshot['name'], model=snapshot['model'], status='running', grading_policy=GRADING_POLICY, prompt_version=PROMPT_VERSION, queued_at=now())
            if policy:=calibration_policy(q):g['calibration_policy']=policy
            progress(g,'queued')
            try:
                p = self.provider(pid)
                # Never silently substitute a different model into an existing attempt.
                if (p['model'], p['base_url'], p['protocol']) != (snapshot['model'], snapshot['base_url'], snapshot['protocol']):
                    raise ModelError('接口配置已更新，请对该失败项单独重试以使用当前配置')
                raw = await self.ask(p, GRADING_TASK+REFERENCE_RULES+reference_policy(q)+PLAN_RULES, {'full_score':full, 'dimensions':DIMENSIONS, 'rubric': TASK_RUBRIC, 'grading_policy': GRADING_POLICY, 'calibration':calibration(q), 'question':q, 'answer':a['answer'], 'answer_layout':check_paper(a['answer'],q['paper_spec']), **reference_payload(q)}, on_start=lambda:progress(g,'requesting'))
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
                if needs_reference_repair(original_reference):
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
                        raise ModelError('汇总接口配置已更新，请重试汇总或更换汇总接口')
                    raw = await self.ask(master, SYNTHESIS_TASK, {'question':{k:v for k,v in q.items() if k!='reference_material_priority'},'answer':a['answer'],'answer_layout':check_paper(a['answer'],q['paper_spec']),'mean_score':a['mean_score'],'successful_count':len(good),'requested_count':len(a['providers']),**({'calibration':calibration(q)} if calibration_policy(q) else {}), 'reviews':[{k:g[k] for k in ('provider_id','name','model','status')} | {'result':{k:v for k,v in g['result'].items() if k not in ('high_score_answer','high_score_self_check','high_score_plan')}} for g in good]})
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
        portrait_needs_refresh = bool(portrait and portrait.get('notes_version') != NOTES_VERSION)
        return dict(total_attempts=len(attempts), graded_count=len(valid), paper_count=len(self.db.all('papers')), dimensions=dims, forecast=forecast, estimated_total=total, xp=xp, level=xp//100+1, level_progress=xp%100, timeline=timeline, signature=signature, portrait=portrait, portrait_needs_refresh=portrait_needs_refresh, portrait_stale=bool(portrait and portrait.get('signature') != signature), note='预估取各题型最近5次有效批改均分相加；六维分取全部有效作答的模型维度均分。重复练习、题目难度与模型偏差均会影响结果，不是官方成绩或排名。缺任一题型时不输出总分。')
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
                    history.append({'date':a['created_at'], 'kind':a['question_snapshot']['kind'],
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
                        part = await self.ask(provider, '历史分块分析：只保留关键变化、优势和短板，不列编号和分数，全文不超过500字。返回 {analysis,strengths,weaknesses,action_plan}，analysis为文字其余为字符串数组。保留本组记录的时间跨度、变化、共性不足、反例与不确定性；不能把本组说成全部历史。不虚构分数。只保留本组最重要的变化和训练重点。', {'records':batch})
                        summary = Portrait.model_validate(part).model_dump()
                        reduced.append({'record_count':sum(row.get('record_count',1) for row in batch),'summary':summary})
                    if len(dumps(reduced)) >= len(dumps(evidence)):
                        raise ModelError('历史分块摘要过长，无法继续压缩；数值统计已更新，请换用能遵循长度要求的主模型')
                    evidence, compressed = reduced, True
                raw = await self.ask(provider, '基于全部历史证据生成简短能力观察，'+BRIEF_POLICY+'返回 {analysis,strengths,weaknesses,action_plan}。只选最重要的一两项能力变化和下一步动作，不逐维解释、不逐题复盘。缺失题型不推断，不虚构排名或考试分数。所有有效练习都包含在历史摘要中，仅依据提供的证据判断。', {'statistics':{'dimensions':stats['dimensions'], 'forecast':stats['forecast']}, 'all_history':evidence, 'history_is_compressed':compressed, 'record_count':len(history)})
                try:
                    result = readable_portrait(raw)
                except ValidationError:
                    self.stage(j, '正在把观察重写为完整短句')
                    rewritten = await self.ask(provider, '重写能力观察：'+BRIEF_POLICY+'上次草稿违反短句或表达要求，请概括重写，不补充新事实。', {'draft':rewrite_evidence(raw)})
                    try:
                        result = readable_portrait(rewritten)
                    except ValidationError as exc:
                        raise ModelError('模型仍未生成清晰短句，未替换已有观察；请换用主模型后重新生成要点') from exc
                self.db.setting('portrait', dict(result=result, notes_version=NOTES_VERSION, signature=stats['signature'], model=provider['model'], name=provider['name'], updated_at=now(), history_count=len(history)))
                self.finish(j)
            except Exception as exc:
                self.finish(j, public_error(exc))
