import asyncio
import threading
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
import fitz
import httpx
from fastapi.testclient import TestClient
from app.main import create_app
from app.pdf import parse_text, extract_pdf
from app.schemas import DIMENSIONS, Grade
from app.llm import call_model, ModelError, parse_json

TEXT = '''2026年浙江省公考《申论》题（A卷）
给定材料
材料1
本地产业通过技术创新与绿色发展提高竞争优势。
作答要求
第一题
根据资料，以发展变化为题写一份简报。（20分）
要求：全面准确，字数不超过350字。
第二题
写一则短评。（30分）
要求：观点鲜明，字数不超过600字。
第三题
围绕发展优势写一篇议论性文章。（50分）
字数1000-1200字。
答题纸
第一大题
60字'''

def pdf_bytes():
    d=fitz.open();p=d.new_page();p.insert_text((30,40), 'A real PDF document with a sufficient amount of text to test extraction of study material. '*4)
    content=d.tobytes();d.close();return content

def grade(score=15):
    return dict(score=score,dimensions={k:70 for k in DIMENSIONS},strengths=['观点清晰'],weaknesses=['要点不完整'],suggestions=['补充材料中的关键措施'],evidence=['原文“加强建设”过于笼统'],high_score_answer='参考范文：围绕核心问题分类概述措施，紧扣给定材料。')

async def fake_model(p,task,payload):
    await asyncio.sleep(.005)
    if task.startswith('独立批改'):
        if p['model']=='bad':raise ModelError('接口返回 HTTP 429；额度不足')
        return grade(payload['full_score']-4 if p['model']=='good' else payload['full_score']-6)
    if task.startswith('汇总'):
        return dict(analysis='根据多位教练的评价，原答案具有明确观点，但材料运用仍需加强。',consensus=['材料要点遗漏'],disagreements=[],action_plan=['下一次先列出要点再成文'])
    if task.startswith(('基于全部','历史分块')):
        return dict(analysis='综合所有历史练习，当前材料运用还有提升空间；缺失题型不能推断。',strengths=['表达清楚'],weaknesses=['要点不完整'],action_plan=['练习分类概括'])
    if task.startswith('识别试卷'):
        return parse_text(TEXT,'2026年浙江省公考《申论》题（A卷）.pdf')
    return {'ok':True}

class ParserTests(unittest.TestCase):
    def test_training_types_and_metadata(self):
        p=parse_text(TEXT,'2026年浙江省公考《申论》题（A卷）.pdf')
        self.assertEqual([q['kind'] for q in p['questions']],['summary','practical','essay'])
        self.assertEqual([q['word_max'] for q in p['questions']],[350,600,1200])
        self.assertEqual(p['questions'][2]['word_min'],1000)
        self.assertEqual((p['year'],p['province'],p['volume']),('2026','浙江','A卷'))
        self.assertNotIn('答题纸',p['questions'][-1]['prompt'])
        self.assertIn('材料1',p['materials'])
    def test_ocr_heading_and_missing_numbers(self):
        p=parse_text(TEXT.replace('第一题','第一是'),'扫描.pdf')
        self.assertEqual([q['number'] for q in p['questions']],[1,2,3])
        self.assertIn('OCR',p['questions'][0]['classification_note'])
        p=parse_text('作答要求\n第二题\n请写一则600字短评。\n第三题\n写一篇1000-1200字议论性文章。','扫描.pdf')
        self.assertEqual([q['number'] for q in p['questions']],[2,3])
    def test_unknown_layout_requires_confirmation(self):
        p=parse_text('这是无题号的原始试卷，不能猜测题目。','未知.pdf')
        self.assertFalse(p['confirmed']);self.assertEqual(p['year'],'待确认')
        self.assertIn('未能可靠识别',p['questions'][0]['prompt'])
    def test_pdf_and_bad_file(self):
        self.assertIn('real PDF',extract_pdf(pdf_bytes()))
        with self.assertRaises(ValueError):extract_pdf(b'not pdf')
    def test_grade_rejects_wrong_dimensions_and_nan(self):
        for item in [grade(float('nan')),dict(grade(),dimensions={'审题立意':101})]:
            with self.assertRaises(ValueError):Grade.model_validate(item)
    def test_json_fences(self):
        self.assertEqual(parse_json('```json\n{"ok":true}\n```'),{'ok':True})
        for raw in ['[]','not json']:
            with self.assertRaises(ModelError):parse_json(raw)

class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app(self.tmp.name)
        self.client=TestClient(self.app);self.client.__enter__()
        self.patcher=patch('app.service.call_model',side_effect=fake_model);self.mock=self.patcher.start()
        self.provider=self.add_model('good')
        self.master=self.provider['id']
    def tearDown(self):
        self.client.__exit__(None,None,None);self.patcher.stop();self.tmp.cleanup()
    def add_model(self,model):
        r=self.client.post('/api/providers',json={'name':'教练 '+model,'protocol':'openai','base_url':'http://127.0.0.1:7777/v1','model':model,'api_key':'secret-example-do-not-leak'})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()
    def make_paper(self):
        r=self.client.post('/api/papers/import',files={'file':('exam.pdf',pdf_bytes(),'application/pdf')})
        self.assertEqual(r.status_code,200,r.text)
        id=r.json()['id'];p=parse_text(TEXT,'2026年浙江省公考《申论》题（A卷）.pdf');p['confirmed']=True
        r=self.client.put('/api/papers/'+id,json=p);self.assertEqual(r.status_code,200,r.text)
        return r.json()
    def wait_job(self,id):
        for _ in range(300):
            j=self.client.get('/api/jobs/'+id).json()
            if j['status'] not in ('queued','running'):return j
            time.sleep(.01)
        self.fail('job timeout')
    def submit(self,p,index=0,models=None):
        r=self.client.post('/api/attempts',json={'question_id':p['questions'][index]['id'],'answer':'加强基础建设，推动绿色创新与产业发展。','provider_ids':models or [self.master],'master_id':self.master})
        self.assertEqual(r.status_code,200,r.text)
        j=self.wait_job(r.json()['job_id'])
        return self.client.get('/api/attempts/'+r.json()['attempt_id']).json()
    def test_450_character_upgrade_repairs_current_paper_without_rewriting_history(self):
        import copy
        p=self.make_paper();q=p['questions'][1]
        q.update(number=3,prompt='根据材料1，提出改进工作建议，要求不超过450字。',word_max=600,paper_rows=0)
        p['questions']=[q];p.update(title='2026安徽B卷',province='安徽',volume='B卷')
        saved=self.client.put('/api/papers/'+p['id'],json=p).json()
        self.assertEqual((saved['questions'][0]['word_max'],saved['questions'][0]['paper_spec']['cells']),(450,450))
        historical=self.submit(saved);self.assertEqual(historical['status'],'completed')
        # Simulate an old installation with stale capacity in both question and cached spec.
        db=self.app.state.service.db;legacy=copy.deepcopy(saved);legacy.pop('word_limits_version',None)
        legacy['questions'][0].update(word_max=600,paper_spec=dict(q['paper_spec'],rows=24,cells=600))
        old_attempt=db.get('attempts',historical['id'])
        old_attempt['question_snapshot'].update(word_max=600,paper_spec=copy.deepcopy(legacy['questions'][0]['paper_spec']))
        old_attempt['paper_spec']=copy.deepcopy(legacy['questions'][0]['paper_spec'])
        db.put('attempts',historical['id'],old_attempt,old_attempt['created_at'])
        db.put('papers',legacy['id'],legacy,legacy['created_at'])
        self.app.state.service.recover()
        current=self.client.get('/api/papers/'+p['id']).json()
        self.assertEqual((current['questions'][0]['word_max'],current['questions'][0]['paper_spec']['rows']),(450,18))
        self.assertEqual(db.get('attempts',historical['id']),old_attempt)
        self.assertEqual(self.app.state.service.provider(self.master)['api_key'],'secret-example-do-not-leak')
        a=self.submit(current);self.assertEqual(a['status'],'completed')
        self.assertEqual(a['paper_spec']['cells'],450)
        grading=[c.args[2] for c in self.mock.call_args_list if c.args[1].startswith('独立批改')][-1]
        self.assertEqual((grading['question']['word_max'],grading['question']['paper_spec']['cells']),(450,450))

    def test_question_scoped_api_and_model_payloads(self):
        from test_materials import MATERIALS
        p=self.make_paper();p['materials']=MATERIALS
        p['questions'][0]['prompt']='根据资料3，写一份简报，字数不超过350字。'
        p['questions'][1]['prompt']='根据资料8、9，写一则短评，不超过600字。'
        p['questions'][2]['prompt']='结合给定资料，写一篇1000-1200字的议论性文章。'
        p['questions'][2]['material_mode']='all'
        p=self.client.put('/api/papers/'+p['id'],json=p).json()
        self.assertEqual([q['material_scope']['matched_ids'] for q in p['questions']],[[3],[8,9],list(range(1,10))])
        focused=self.client.get('/api/papers/'+p['id']+'/questions/'+p['questions'][0]['id']).json()
        self.assertNotIn('PASSAGE_1_ONLY',json.dumps(focused))
        a=self.submit(p)
        self.assertIn('PASSAGE_3_ONLY',a['question_snapshot']['materials'])
        self.assertNotIn('PASSAGE_1_ONLY',a['question_snapshot']['materials'])
        calls=[c for c in self.mock.call_args_list if c.args[1].startswith(('独立批改','汇总'))]
        self.assertEqual(len(calls),2)
        for call in calls:
            payload=call.args[2]
            self.assertIn('PASSAGE_3_ONLY',payload['question']['materials'])
            self.assertNotIn('PASSAGE_1_ONLY',json.dumps(payload))
            self.assertNotIn(p['questions'][1]['prompt'],json.dumps(payload,ensure_ascii=False))
            self.assertNotIn('custom_materials',payload['question'])
        summary=calls[-1].args[2]
        self.assertNotIn('high_score_answer',summary['reviews'][0]['result'])
        self.assertIn('high_score_answer',a['grades'][0]['result'])
        # Editing the source paper later must not change the submitted evidence snapshot.
        original=a['question_snapshot']['materials']
        p['questions'][0].update(material_mode='custom',custom_materials='新的人工校对材料，具有不同内容。')
        p=self.client.put('/api/papers/'+p['id'],json=p).json()
        self.assertEqual(p['questions'][0]['materials'],'新的人工校对材料，具有不同内容。')
        self.assertEqual(self.client.get('/api/attempts/'+a['id']).json()['question_snapshot']['materials'],original)
    def test_grading_uses_question_requirements_instead_of_training_genre(self):
        p=self.make_paper()
        prompts=[('summary','根据资料1，概括当地产业发展的主要变化。不超过350字。'),
                 ('summary','根据资料1，分析创新带来竞争优势的原因。不超过350字。'),
                 ('summary','根据资料1，以产业变化为题写一份简报。不超过350字。'),
                 ('practical','根据资料1，拟写一份发言提纲，600字以内。'),
                 ('practical','根据资料1，写一则短评，自拟题目，不超过600字。'),
                 ('essay','结合资料1，围绕创新发展自拟题目写一篇议论性文章，1000-1200字。')]
        for kind,prompt in prompts:
            p['questions'][0].update(kind=kind,prompt=prompt)
            p=self.client.put('/api/papers/'+p['id'],json=p).json()
            before=len(self.mock.call_args_list);a=self.submit(p)
            calls=[c for c in self.mock.call_args_list[before:] if c.args[1].startswith('独立批改')]
            self.assertEqual(len(calls),1)
            task,data=calls[0].args[1:]
            self.assertNotIn('简报',task);self.assertNotIn('简报',data['rubric'])
            self.assertEqual(data['question']['prompt'],prompt)
            self.assertEqual(data['question']['materials'],p['questions'][0]['materials'])
            self.assertEqual(data['answer'],a['answer'])
            self.assertIn('task_analysis',task);self.assertIn('kind只表示20/30/50分训练档',task)
            self.assertEqual(a['grading_policy'],'question-first-v1')
        for call in self.mock.call_args_list:
            if call.args[1].startswith('汇总'):
                self.assertIn('文体误判',call.args[1])

    def test_highlights_persist_colors_and_reject_stale_or_invalid_anchors(self):
        from app.annotations import canonical_text
        p=self.make_paper();q=p['questions'][0]
        canonical=canonical_text(q['materials'])
        path='/api/papers/'+p['id']+'/questions/'+q['id']+'/highlights'
        data=dict(signature=q['material_signature'],start=0,end=5,quote=canonical[:5])
        r=self.client.post(path,json=data);self.assertEqual(r.status_code,200,r.text)
        h=r.json();self.assertEqual(h['color'],'#ffff00')
        self.client.patch('/api/highlights/'+h['id'],json={'color':'#12AB34'})
        self.assertEqual(self.client.get(path).json()['highlights'][0]['color'],'#12ab34')
        self.assertEqual(len(self.client.get('/api/export').json()['highlights']),1)
        changed=dict(data,quote='错误文字')
        self.assertEqual(self.client.post(path,json=changed).status_code,400)
        self.assertEqual(self.client.post(path,json=dict(data,color='red;bad')).status_code,422)
        p['materials']='材料1\n已经改变的资料内容。'
        self.client.put('/api/papers/'+p['id'],json=p)
        current=self.client.get(path).json()
        self.assertEqual(current['highlights'],[]);self.assertEqual(current['outdated_count'],1)
        self.assertEqual(self.client.post(path,json=data).status_code,409)
        self.client.delete('/api/highlights/'+h['id'])
        self.assertEqual(self.client.get(path).json()['outdated_count'],0)
    def test_short_comment_policy_applies_to_initial_repair_and_both_rewrites(self):
        from test_reference_policy import COMMENT
        from app.reference_policy import SHORT_COMMENT_POLICY
        async def comment_model(provider,task,data):
            if task.startswith('独立批改') and '短评' in data['question']['prompt']:
                result=grade(26);result['high_score_answer']='当地1000家企业加强创新，提高产品质量，为发展注入动力。'
                return result
            if task.startswith(('范文校正','单独重写高分范文')):
                return {'high_score_answer':COMMENT}
            return await fake_model(provider,task,data)
        p=self.make_paper()
        with patch('app.service.call_model',side_effect=comment_model) as mock:
            a=self.submit(p,1)
            self.assertEqual(a['status'],'completed')
            g=a['grades'][0];self.assertEqual(g['result']['score'],26)
            self.assertTrue(g['references'][0]['layout_check']['valid'])
            self.assertFalse(g['references'][0]['style_check']['valid'])
            self.assertEqual(g['references'][-1]['answer'],COMMENT)
            self.assertTrue(g['references'][-1]['style_check']['valid'])
            for direction in ('more','less','rewrite'):
                source=self.client.get('/api/attempts/'+a['id']).json()['grades'][0]['references'][-1]['id']
                j=self.client.post('/api/attempts/'+a['id']+'/references',json={'provider_id':self.master,'direction':direction,'reference_id':source}).json()
                self.assertEqual(self.wait_job(j['id'])['status'],'completed')
            relevant=[c for c in mock.call_args_list if c.args[1].startswith(('独立批改','范文校正','单独重写高分范文'))]
            self.assertEqual(len(relevant),5)
            for call in relevant:
                self.assertIn(SHORT_COMMENT_POLICY,call.args[1])
                self.assertEqual(call.args[2]['question']['materials'],p['questions'][1]['materials'])
                from app.grading_prompts import REFERENCE_RULES
                self.assertEqual(call.args[1].count(REFERENCE_RULES),1)
                if not call.args[1].startswith('独立批改'):
                    self.assertNotIn('answer',call.args[2])
                    self.assertNotIn('previous_answer',call.args[2])
                    if call.args[2].get('direction')=='rewrite':
                        self.assertNotIn('previous_reference',call.args[2])
                        self.assertNotIn('previous_check',call.args[2])
                        self.assertNotIn('target_word_count',call.args[2])
                    else:self.assertIn('previous_reference',call.args[2])
            self.assertNotIn(SHORT_COMMENT_POLICY,next(c.args[1] for c in mock.call_args_list if c.args[1].startswith('汇总')))
        updated=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(updated['answer'],a['answer'])
        self.assertEqual(updated['mean_score'],a['mean_score'])
        self.assertEqual(updated['grades'][0]['result'],g['result'])
        self.assertTrue(updated['grades'][0]['references'][-1]['style_check']['valid'])

    def test_short_comment_failed_style_repair_is_bounded_and_keeps_score(self):
        bad='当地1000家企业加强创新，提高产品质量，为发展注入动力。'
        async def invalid_comment(provider,task,data):
            if task.startswith('独立批改'):
                result=grade(26);result['high_score_answer']=bad;return result
            if task.startswith('范文校正'):return {'high_score_answer':bad}
            return await fake_model(provider,task,data)
        p=self.make_paper()
        with patch('app.service.call_model',side_effect=invalid_comment) as mock:
            a=self.submit(p,1)
        self.assertEqual(a['status'],'completed');self.assertEqual(a['mean_score'],26)
        self.assertEqual(len(a['grades'][0]['references']),2)
        self.assertFalse(a['grades'][0]['references'][-1]['style_check']['valid'])
        self.assertEqual(sum(c.args[1].startswith('范文校正') for c in mock.call_args_list),1)

    def test_essay_calibration_reference_limits_and_all_rewrite_paths(self):
        from test_essay_policy import essay_fixture
        from app.grading_prompts import ESSAY_CALIBRATION
        from app.reference_policy import ESSAY_REFERENCE_POLICY
        initial=essay_fixture(size=236)
        async def essay_model(provider,task,data):
            if task.startswith('独立批改'):
                result=grade(35);result['high_score_answer']=initial;return result
            if task.startswith(('范文校正','单独重写高分范文')):
                return {'high_score_answer':essay_fixture(6)}
            return await fake_model(provider,task,data)
        p=self.make_paper()
        with patch('app.service.call_model',side_effect=essay_model) as mock:
            a=self.submit(p,2)
            self.assertEqual(a['mean_score'],35)
            g=a['grades'][0];self.assertEqual(g['calibration_policy'],'essay-v1')
            self.assertFalse(g['references'][0]['layout_check']['valid'])
            self.assertTrue(g['references'][-1]['layout_check']['valid'])
            self.assertEqual(g['references'][-1]['style_check']['body_paragraphs'],6)
            self.assertEqual(g['references'][-1]['paper_spec']['word_min'],1001)
            for direction in ('more','less','rewrite'):
                source=self.client.get('/api/attempts/'+a['id']).json()['grades'][0]['references'][-1]['id']
                j=self.client.post('/api/attempts/'+a['id']+'/references',json={'provider_id':self.master,'direction':direction,'reference_id':source}).json()
                self.assertEqual(self.wait_job(j['id'])['status'],'completed')
            calls=[c for c in mock.call_args_list if c.args[1].startswith(('独立批改','范文校正','单独重写高分范文'))]
            self.assertEqual(len(calls),5)
            for c in calls:
                task,data=c.args[1:]
                self.assertEqual(task.count(ESSAY_REFERENCE_POLICY),1)
                self.assertNotIn(ESSAY_CALIBRATION,task)
                self.assertEqual(data['reference_paper_spec']['word_min'],1001)
                self.assertEqual(data['reference_paper_spec']['cells'],1200)
                self.assertNotIn('rules',data['reference_paper_spec'])
                self.assertEqual(data['question']['materials'],p['questions'][2]['materials'])
            grading=next(c.args[2] for c in calls if c.args[1].startswith('独立批改'))
            self.assertEqual(grading['calibration'],ESSAY_CALIBRATION)
            self.assertEqual(grading['question']['paper_spec']['word_min'],1000)
            summary=next(c.args[2] for c in mock.call_args_list if c.args[1].startswith('汇总'))
            self.assertEqual(summary['calibration'],ESSAY_CALIBRATION)
        updated=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(updated['grades'][0]['result'],g['result'])
        self.assertEqual(updated['answer'],a['answer'])

    def test_essay_calibration_does_not_impose_38_as_a_hard_cap(self):
        async def high(provider,task,data):
            if task.startswith('独立批改'):return grade(39)
            return await fake_model(provider,task,data)
        with patch('app.service.call_model',side_effect=high):a=self.submit(self.make_paper(),2)
        self.assertEqual(a['mean_score'],39)
        self.assertEqual(a['grades'][0]['result']['score'],39)

    def test_essay_material_priorities_survive_source_edits_and_all_reference_paths(self):
        from test_materials import MATERIALS
        from test_essay_policy import essay_fixture
        from test_reference_plan import PLAN
        from app.reference_policy import ESSAY_MATERIAL_PRIORITY
        p=self.make_paper();p['materials']=MATERIALS
        p['questions'][0]['prompt']='根据资料1、2，概括措施。'
        p['questions'][1]['prompt']='根据资料3、4，写一则短评。'
        p['questions'][2].update(material_mode='all')
        p=self.client.put('/api/papers/'+p['id'],json=p).json()
        async def model(provider,task,data):
            if task.startswith('独立批改'):
                result=grade(35);result['high_score_answer']=essay_fixture(size=236);return result
            if task.startswith(('范文校正','单独重写高分范文')):return dict(high_score_answer=essay_fixture(6),high_score_plan=PLAN)
            if task.startswith('范文构思讲解'):return PLAN
            return await fake_model(provider,task,data)
        with patch('app.service.call_model',side_effect=model) as mock:
            a=self.submit(p,2)
            priority=a['question_snapshot']['reference_material_priority']
            self.assertEqual(priority['preferred_ids'],list(range(5,10)))
            self.assertEqual(priority['secondary_ids'],[1,2,3,4])
            p['questions'][0]['prompt']='根据资料5、6，概括措施。'
            p=self.client.put('/api/papers/'+p['id'],json=p).json()
            self.assertNotEqual(p['questions'][2]['reference_material_priority'],priority)
            for direction in ('rewrite','more','less'):
                job=self.client.post('/api/attempts/'+a['id']+'/references',json={'provider_id':self.master,'direction':direction}).json()
                self.assertEqual(self.wait_job(job['id'])['status'],'completed')
            job=self.client.post('/api/attempts/'+a['id']+'/reference-plan',json={'provider_id':self.master}).json()
            self.assertEqual(self.wait_job(job['id'])['status'],'completed')
        for call in mock.call_args_list:
            task,data=call.args[1:]
            if task.startswith(('独立批改','范文校正','单独重写高分范文','范文构思讲解')):
                self.assertEqual(data['question']['reference_material_priority'],priority)
                self.assertEqual(data['question']['materials'],MATERIALS)
                self.assertEqual(task.count(ESSAY_MATERIAL_PRIORITY),1)
                self.assertNotIn(p['questions'][0]['prompt'],json.dumps(data,ensure_ascii=False))
            elif task.startswith('汇总'):
                self.assertNotIn('reference_material_priority',data['question'])
                self.assertNotIn(ESSAY_MATERIAL_PRIORITY,task)
        updated=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(updated['question_snapshot'],a['question_snapshot'])
        self.assertEqual(updated['mean_score'],35);self.assertEqual(updated['answer'],a['answer'])
        self.assertTrue(all(r['material_priority']==priority for r in updated['grades'][0]['references']))

    def test_legacy_essay_priority_uses_only_matching_current_paper_without_rewriting_history(self):
        from test_materials import MATERIALS
        from test_essay_policy import essay_fixture
        p=self.make_paper();p['materials']=MATERIALS
        p['questions'][0]['prompt']='根据资料1、2，概括措施。'
        p['questions'][1]['prompt']='根据资料3、4，写一则短评。'
        p['questions'][2].update(material_mode='all')
        p=self.client.put('/api/papers/'+p['id'],json=p).json()
        a=self.submit(p,2);db=self.app.state.service.db
        old=db.get('attempts',a['id']);old['question_snapshot'].pop('reference_material_priority')
        for g in old['grades']:
            for ref in g['references']:ref.pop('material_priority',None)
        db.put('attempts',old['id'],old,old['created_at'])
        seen=[]
        async def model(provider,task,data):
            if task.startswith('单独重写高分范文'):
                seen.append(data['question']['reference_material_priority'])
                return dict(high_score_answer=essay_fixture())
            return await fake_model(provider,task,data)
        with patch('app.service.call_model',side_effect=model):
            for changed in (False,True):
                if changed:
                    p['materials']=MATERIALS+'\n材料10\n源试卷新增的材料。'
                    self.client.put('/api/papers/'+p['id'],json=p)
                job=self.client.post('/api/attempts/'+a['id']+'/references',json={'provider_id':self.master,'direction':'rewrite'}).json()
                self.assertEqual(self.wait_job(job['id'])['status'],'completed')
        self.assertEqual(seen[0]['preferred_ids'],list(range(5,10)))
        self.assertEqual(seen[0]['source'],'current_paper')
        self.assertFalse(seen[1]['available'])
        updated=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(updated['question_snapshot'],old['question_snapshot'])
        self.assertEqual(updated['mean_score'],old['mean_score'])
        self.assertEqual(updated['grades'][0]['result'],old['grades'][0]['result'])
        self.assertNotIn('material_priority',updated['grades'][0]['references'][0])

    def test_practical_calibration_reaches_each_coach_and_summary_for_each_genre(self):
        from app.grading_prompts import PRACTICAL_CALIBRATION, DEFAULT_CALIBRATION
        from test_reference_policy import COMMENT
        other=self.add_model('other')
        p=self.make_paper()
        async def practical_model(provider,task,data):
            if task.startswith('独立批改'):
                result=grade(21 if provider['id']==self.master else 22)
                result['high_score_answer']=COMMENT
                return result
            return await fake_model(provider,task,data)
        for prompt in ['根据资料1写一则短评，不超过600字。','根据资料1拟写一份工作通知，不超过600字。','根据资料1拟写一份发言提纲，不超过600字。']:
            with self.subTest(prompt=prompt):
                p['questions'][1]['prompt']=prompt
                p=self.client.put('/api/papers/'+p['id'],json=p).json()
                with patch('app.service.call_model',side_effect=practical_model) as mock:
                    a=self.submit(p,1,[self.master,other['id']])
                self.assertEqual(a['status'],'completed');self.assertEqual(a['mean_score'],21.5)
                self.assertEqual({g['calibration_policy'] for g in a['grades']},{'practical-v1'})
                grading=[c for c in mock.call_args_list if c.args[1].startswith('独立批改')]
                summaries=[c for c in mock.call_args_list if c.args[1].startswith('汇总')]
                self.assertEqual((len(grading),len(summaries)),(2,1))
                for call in grading+summaries:
                    task,data=call.args[1:]
                    self.assertEqual(data['calibration'],PRACTICAL_CALIBRATION)
                    self.assertEqual(data['question']['prompt'],prompt)
                    self.assertEqual(data['question']['materials'],a['question_snapshot']['materials'])
                    self.assertEqual(data['answer'],a['answer'])
                    self.assertNotIn(PRACTICAL_CALIBRATION,task)
                    self.assertEqual(json.dumps(data,ensure_ascii=False).count(PRACTICAL_CALIBRATION),1)
                self.assertTrue(all(c.args[2]['full_score']==30 for c in grading))
        a=self.submit(p,0)
        self.assertNotIn('calibration_policy',a['grades'][0])
        summary_calls=[c for c in self.mock.call_args_list if c.args[1].startswith('汇总')]
        self.assertNotIn('calibration',summary_calls[-1].args[2])
        grade_calls=[c for c in self.mock.call_args_list if c.args[1].startswith('独立批改')]
        self.assertEqual(grade_calls[-1].args[2]['calibration'],DEFAULT_CALIBRATION)

    def test_practical_calibration_has_no_cap_floor_or_legacy_score_migration(self):
        p=self.make_paper()
        old=None
        for score in (15,27.5):
            async def scored(provider,task,data):
                if task.startswith('独立批改'):return grade(score)
                return await fake_model(provider,task,data)
            with patch('app.service.call_model',side_effect=scored):a=self.submit(p,1)
            self.assertEqual(a['mean_score'],score)
            self.assertEqual(a['grades'][0]['result']['score'],score)
            if old is None:
                old=self.app.state.service.db.get('attempts',a['id'])
                old['grades'][0].pop('calibration_policy')
                self.app.state.service.db.put('attempts',old['id'],old,old['created_at'])
        stored=self.client.get('/api/attempts/'+old['id']).json()
        self.assertEqual(stored['mean_score'],15)
        self.assertEqual(stored['grades'][0]['result'],old['grades'][0]['result'])
        self.assertNotIn('calibration_policy',stored['grades'][0])
        self.assertEqual(stored['answer'],old['answer'])

    def test_practical_retry_calibrates_failed_coach_without_relabeling_success(self):
        from app.grading_prompts import PRACTICAL_CALIBRATION
        failed=self.add_model('bad')
        a=self.submit(self.make_paper(),1,[self.master,failed['id']])
        self.assertEqual(a['status'],'partial')
        saved=self.app.state.service.db.get('attempts',a['id'])
        success=next(g for g in saved['grades'] if g['status']=='completed')
        success.pop('calibration_policy')
        self.app.state.service.db.put('attempts',a['id'],saved,saved['created_at'])
        async def retry_model(provider,task,data):
            if task.startswith('独立批改'):return grade(22)
            return await fake_model(provider,task,data)
        with patch('app.service.call_model',side_effect=retry_model) as mock:
            r=self.client.post('/api/attempts/'+a['id']+'/retry',json={'provider_id':failed['id']})
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(self.wait_job(r.json()['job_id'])['status'],'completed')
        updated=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(updated['status'],'completed');self.assertEqual(updated['mean_score'],24)
        kept=next(g for g in updated['grades'] if g['provider_id']==self.master)
        self.assertEqual(kept['result'],success['result'])
        self.assertNotIn('calibration_policy',kept)
        retried=next(g for g in updated['grades'] if g['provider_id']==failed['id'])
        self.assertEqual(retried['calibration_policy'],'practical-v1')
        calls=[c for c in mock.call_args_list if c.args[1].startswith('独立批改')]
        self.assertEqual(len(calls),1);self.assertEqual(calls[0].args[0]['id'],failed['id'])
        self.assertEqual(calls[0].args[2]['calibration'],PRACTICAL_CALIBRATION)
        summary=next(c.args[2] for c in mock.call_args_list if c.args[1].startswith('汇总'))
        self.assertEqual(summary['calibration'],PRACTICAL_CALIBRATION)

    def test_reference_plans_stay_with_their_version_and_out_of_summary(self):
        from test_reference_plan import PLAN
        from test_essay_policy import essay_fixture
        async def with_plan(provider,task,data):
            if task.startswith('独立批改'):
                result=grade(35);result.update(high_score_answer=essay_fixture(),high_score_plan=PLAN);return result
            if task.startswith('单独重写高分范文'):
                return dict(high_score_answer=essay_fixture(6),high_score_plan=dict(PLAN,framework='这是四个分论段的新框架。'))
            return await fake_model(provider,task,data)
        p=self.make_paper()
        with patch('app.service.call_model',side_effect=with_plan) as mock:
            a=self.submit(p,2);ref=a['grades'][0]['references'][0]
            self.assertEqual(ref['plan'],PLAN)
            self.assertTrue(ref['layout_check']['valid']);self.assertGreater(ref['layout_check']['word_count'],1000)
            summary=next(c.args[2] for c in mock.call_args_list if c.args[1].startswith('汇总'))
            self.assertNotIn('high_score_plan',summary['reviews'][0]['result'])
            j=self.client.post('/api/attempts/'+a['id']+'/references',json={'provider_id':self.master,'direction':'rewrite'}).json()
            self.assertEqual(self.wait_job(j['id'])['status'],'completed')
        updated=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(updated['grades'][0]['references'][0]['plan'],PLAN)
        self.assertEqual(updated['grades'][0]['references'][-1]['plan']['framework'],'这是四个分论段的新框架。')
        self.assertEqual(updated['grades'][0]['result'],a['grades'][0]['result'])

    def test_plan_only_generation_preserves_answer_score_versions_and_layout(self):
        from test_reference_plan import PLAN
        p=self.make_paper();a=self.submit(p)
        calls=[]
        async def plan_only(provider,task,data):
            calls.append((task,data));return PLAN
        path='/api/attempts/'+a['id']+'/reference-plan'
        with patch('app.service.call_model',side_effect=plan_only):
            j=self.client.post(path,json={'provider_id':self.master,'reference_id':'original'}).json()
            self.assertEqual(self.wait_job(j['id'])['status'],'completed')
        updated=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(len(calls),1)
        self.assertTrue(calls[0][0].startswith('范文构思讲解'))
        self.assertEqual(calls[0][1]['question']['materials'],a['question_snapshot']['materials'])
        self.assertNotIn('answer',calls[0][1]);self.assertNotIn('reviews',calls[0][1])
        self.assertEqual(updated['answer'],a['answer']);self.assertEqual(updated['mean_score'],a['mean_score'])
        self.assertEqual(updated['grades'][0]['result'],a['grades'][0]['result'])
        ref=updated['grades'][0]['references'][0];original=a['grades'][0]['references'][0]
        self.assertEqual(ref['answer'],original['answer']);self.assertEqual(ref['layout_check'],original['layout_check'])
        self.assertEqual(ref['plan'],PLAN)
        with patch('app.service.call_model',side_effect=ModelError('说明接口暂不可用')):
            j=self.client.post(path,json={'provider_id':self.master}).json()
            self.assertEqual(self.wait_job(j['id'])['status'],'failed')
        preserved=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(preserved['grades'],updated['grades'])
        self.assertEqual(self.client.post(path,json={'provider_id':self.master,'reference_id':'missing'}).status_code,400)
        busy=self.app.state.service.job('reference',a['id'])
        self.assertEqual(self.client.post(path,json={'provider_id':self.master}).status_code,409)
        self.app.state.service.finish(busy)

    def test_reference_regeneration_keeps_scores_answers_and_old_versions(self):
        p=self.make_paper();a=self.submit(p)
        original=a['grades'][0]['result']['high_score_answer'];signature=self.client.get('/api/profile').json()['signature']
        async def reference_model(provider,task,data):
            if task.startswith('单独重写高分范文'):
                answer='　　'+'甲'*(280 if data['direction']=='more' else 240)
                return dict(high_score_answer=answer,high_score_self_check=dict(word_count=len(answer.strip()),used_rows=12 if data['direction']=='more' else 10,complies=True,format_note='已检查缩进与行数'))
            return await fake_model(provider,task,data)
        with patch('app.service.call_model',side_effect=reference_model):
            for direction in ['more','less']:
                current=self.client.get('/api/attempts/'+a['id']).json()
                source=current['grades'][0]['references'][-1]['id']
                r=self.client.post('/api/attempts/'+a['id']+'/references',json={'provider_id':self.master,'direction':direction,'reference_id':source})
                self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.wait_job(r.json()['id'])['status'],'completed')
        updated=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(updated['mean_score'],a['mean_score'])
        self.assertEqual(updated['finished_at'],a['finished_at']);self.assertEqual(updated['answer'],a['answer'])
        self.assertEqual(updated['grades'][0]['result']['high_score_answer'],original)
        refs=updated['grades'][0]['references'];self.assertEqual(len(refs),3)
        self.assertTrue(refs[-1]['layout_check']['valid'])
        self.assertEqual(self.client.get('/api/profile').json()['signature'],signature)

    def test_fresh_reference_request_excludes_original_answer_and_all_previous_draft_content(self):
        from app.grading_prompts import REFERENCE_RULES,GRADING_TASK
        from test_reference_plan import PLAN
        old_draft='旧范文草稿：OLD_REFERENCE_ONLY_MARKER 这是模型先前输出的文章。'
        async def model(provider,task,data):
            if task.startswith('独立批改'):
                result=grade(16);result['high_score_answer']=old_draft;return result
            if task.startswith('单独重写高分范文'):
                self.assertEqual(set(data),{'question','direction'})
                self.assertNotIn(old_draft,json.dumps(data,ensure_ascii=False))
                self.assertNotIn('加强基础建设，推动绿色创新与产业发展。',json.dumps(data,ensure_ascii=False))
                return dict(high_score_answer='新范文依据题干材料独立概括创新与绿色发展的做法。',high_score_plan=PLAN)
            return await fake_model(provider,task,data)
        p=self.make_paper()
        with patch('app.service.call_model',side_effect=model) as mock:
            a=self.submit(p)
            signature=self.client.get('/api/profile').json()['signature']
            initial=[c for c in mock.call_args_list if c.args[1].startswith('独立批改')]
            self.assertEqual(len(initial),1)
            self.assertEqual(initial[0].args[2]['answer'],a['answer'])
            self.assertIn(REFERENCE_RULES,initial[0].args[1])
            from app.grading_prompts import GRADE_JSON, REFERENCE_JSON
            shape=json.loads(GRADE_JSON)
            self.assertLess(list(shape).index('high_score_answer'),list(shape).index('score'))
            self.assertEqual(set(json.loads(REFERENCE_JSON)),{'high_score_answer','high_score_self_check','high_score_plan'})
            job=self.client.post('/api/attempts/'+a['id']+'/references',json={'provider_id':self.master,'direction':'rewrite'}).json()
            self.assertEqual(self.wait_job(job['id'])['status'],'completed')
        calls=[c for c in mock.call_args_list if c.args[1].startswith('单独重写高分范文')]
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0].args[2]['question']['materials'],a['question_snapshot']['materials'])
        updated=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(updated['answer'],a['answer']);self.assertEqual(updated['mean_score'],16)
        self.assertEqual(updated['grades'][0]['result'],a['grades'][0]['result'])
        refs=updated['grades'][0]['references'];self.assertEqual(len(refs),2)
        self.assertEqual(refs[0]['answer'],old_draft)
        self.assertEqual(refs[-1]['plan'],PLAN)
        self.assertEqual(self.client.get('/api/profile').json()['signature'],signature)
    def test_invalid_reference_is_repaired_once_without_changing_grade(self):
        p=self.make_paper()
        async def responder(provider,task,data):
            if task.startswith('独立批改'):
                raw=grade(16);raw['high_score_answer']='甲\n'*20;return raw
            if task.startswith('范文校正'):
                return dict(high_score_answer='　　'+'甲'*280,high_score_self_check=dict(word_count=280,used_rows=12,complies=True,format_note='两格缩进，12行'))
            return await fake_model(provider,task,data)
        with patch('app.service.call_model',side_effect=responder) as mock:
            a=self.submit(p)
        self.assertEqual(a['status'],'completed');self.assertEqual(a['mean_score'],16)
        refs=a['grades'][0]['references'];self.assertEqual(len(refs),2)
        self.assertFalse(refs[0]['layout_check']['valid']);self.assertTrue(refs[1]['layout_check']['valid'])
        self.assertEqual(sum(c.args[1].startswith('范文校正') for c in mock.call_args_list),1)
        summary=next(c.args[2] for c in mock.call_args_list if c.args[1].startswith('汇总'))
        self.assertNotIn('references',summary['reviews'][0]);self.assertNotIn('high_score_answer',summary['reviews'][0]['result'])
    def test_reference_jobs_are_serialized_and_failures_preserve_original(self):
        p=self.make_paper();a=self.submit(p)
        body={'provider_id':self.master,'direction':'less','reference_id':'original'}
        j=self.app.state.service.job('reference',a['id'])
        self.assertEqual(self.client.post('/api/attempts/'+a['id']+'/references',json=body).status_code,409)
        self.assertEqual(self.client.post('/api/attempts/'+a['id']+'/retry').status_code,409)
        self.app.state.service.finish(j)
        async def unavailable(provider,task,data):
            if task.startswith('单独重写'):raise ModelError('接口返回 HTTP 429')
            return await fake_model(provider,task,data)
        with patch('app.service.call_model',side_effect=unavailable):
            job=self.client.post('/api/attempts/'+a['id']+'/references',json=body).json()
            self.assertEqual(self.wait_job(job['id'])['status'],'failed')
        current=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(current['grades'][0]['references'],a['grades'][0]['references'])
        self.assertEqual(current['mean_score'],a['mean_score'])
    def test_unresolved_reference_overflow_is_visible_and_retry_is_bounded(self):
        p=self.make_paper();a=self.submit(p)
        async def bad_layout(provider,task,data):
            if task.startswith(('单独重写','范文校正')):
                return {'high_score_answer':'甲\n'*20,'high_score_self_check':{'word_count':20,'used_rows':1,'complies':True}}
            return await fake_model(provider,task,data)
        with patch('app.service.call_model',side_effect=bad_layout) as mock:
            j=self.client.post('/api/attempts/'+a['id']+'/references',json={'provider_id':self.master,'direction':'less'}).json()
            self.assertEqual(self.wait_job(j['id'])['status'],'completed')
        current=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(current['mean_score'],a['mean_score'])
        self.assertFalse(current['grades'][0]['references'][-1]['layout_check']['valid'])
        self.assertEqual(sum(c.args[1].startswith(('单独重写','范文校正')) for c in mock.call_args_list),2)
    def test_highlight_offsets_count_unicode_characters_and_survive_restart(self):
        from app.annotations import canonical_text
        p=self.make_paper();p['materials']='材料1\n甲😀乙 空格　末尾'
        p=self.client.put('/api/papers/'+p['id'],json=p).json();q=p['questions'][0]
        canonical=canonical_text(q['materials']);start=canonical.index('😀')
        body=dict(signature=q['material_signature'],start=start,end=start+2,quote='😀乙')
        r=self.client.post('/api/papers/'+p['id']+'/questions/'+q['id']+'/highlights',json=body)
        self.assertEqual(r.status_code,200,r.text)
        other=create_app(self.tmp.name)
        stored=other.state.service.db.rows('SELECT data FROM highlights WHERE id=?',(r.json()['id'],))
        self.assertEqual(json.loads(stored[0]['data'])['quote'],'😀乙')
    def test_legacy_report_adds_local_paper_checks_without_model_calls_or_rewriting(self):
        p=self.make_paper();a=self.submit(p)
        for j in self.client.get('/api/jobs').json():
            if j['kind']=='profile':self.wait_job(j['id'])
        original=self.app.state.service.db.get('attempts',a['id'])
        original.pop('answer_layout',None);original['question_snapshot'].pop('paper_spec',None)
        for g in original['grades']:
            g.pop('references',None);g['result'].pop('high_score_self_check',None)
        self.app.state.service.db.put('attempts',original['id'],original,original['created_at'])
        with patch('app.service.call_model',side_effect=AssertionError('Viewing history must not call a model')):
            response=self.client.get('/api/attempts/'+a['id'])
        self.assertEqual(response.status_code,200,response.text)
        restored=response.json();self.assertEqual(restored['paper_spec']['rows'],14)
        self.assertEqual(restored['mean_score'],original['mean_score'])
        self.assertEqual(restored['grades'][0]['references'][0]['id'],'original')
        self.assertEqual(self.app.state.service.db.get('attempts',a['id']),original)
    def test_import_confirmation_and_source_download(self):
        r=self.client.post('/api/papers/import',files={'file':('exam.pdf',pdf_bytes(),'application/pdf')}).json()
        self.assertFalse(r['confirmed'])
        rejected=self.client.post('/api/attempts',json={'question_id':r['questions'][0]['id'],'answer':'答案','provider_ids':[self.master],'master_id':self.master})
        self.assertEqual(rejected.status_code,400)
        self.assertTrue(self.client.get('/api/papers/'+r['id']+'/pdf').content.startswith(b'%PDF-'))
    def test_full_grade_history_and_forecast(self):
        p=self.make_paper();other=self.add_model('other')
        a=self.submit(p,models=[self.master,other['id']])
        self.assertEqual(a['status'],'completed');self.assertEqual(a['mean_score'],15)
        self.assertEqual(len(a['grades']),2);self.assertIn('high_score_answer',a['grades'][0]['result'])
        self.assertEqual(a['answer'],'加强基础建设，推动绿色创新与产业发展。')
        self.assertIsNone(self.client.get('/api/profile').json()['estimated_total'])
        self.submit(p,1);self.submit(p,2)
        s=self.client.get('/api/profile').json();self.assertEqual(s['estimated_total'],87)
        self.assertEqual(s['graded_count'],3);self.assertEqual(s['xp'],75)
        self.assertEqual(s['dimensions']['审题立意'],70)
        # Repeated exact answer contributes to statistics but cannot farm XP.
        self.submit(p)
        self.assertEqual(self.client.get('/api/profile').json()['xp'],75)
        exported=self.client.get('/api/export').json();self.assertEqual(len(exported['attempts']),4)
        self.assertNotIn('secret-example',json.dumps(exported))
    def test_partial_failure_and_retry_only_failed(self):
        p=self.make_paper();bad=self.add_model('bad')
        a=self.submit(p,models=[self.master,bad['id']]);self.assertEqual(a['status'],'partial');self.assertEqual(a['mean_score'],16)
        good_calls=sum(1 for c in self.mock.call_args_list if c.args[0]['model']=='good' and c.args[1].startswith('独立'))
        result=self.client.post('/api/attempts/'+a['id']+'/retry').json();self.wait_job(result['job_id'])
        self.assertEqual(good_calls,sum(1 for c in self.mock.call_args_list if c.args[0]['model']=='good' and c.args[1].startswith('独立')))
    def test_single_coach_retry_preserves_all_other_results(self):
        p=self.make_paper();first=self.add_model('bad');second=self.add_model('bad-two')
        recovered=set();seen=[]
        async def selective(provider,task,payload):
            if task.startswith('独立批改'):
                seen.append((provider['id'],provider.get('timeout_seconds')))
                if provider['model'].startswith('bad') and provider['id'] not in recovered:
                    raise ModelError('接口超时，已保留作答，可重试')
                if provider['id'] in recovered:return grade(18)
            return await fake_model(dict(provider,model='good') if provider['id'] in recovered else provider,task,payload)
        with patch('app.service.call_model',side_effect=selective):
            a=self.submit(p,models=[self.master,first['id'],second['id']])
            self.assertEqual(a['status'],'partial')
            unchanged={g['provider_id']:g for g in a['grades'] if g['provider_id']!=first['id']}
            path='/api/attempts/'+a['id']+'/retry'
            self.assertEqual(self.client.post(path,json={'provider_id':self.master}).status_code,400)
            self.assertEqual(self.client.post(path,json={'provider_id':'unrelated-coach'}).status_code,400)
            config=dict(first,timeout_seconds=1200,api_key='')
            self.assertEqual(self.client.put('/api/providers/'+first['id'],json=config).status_code,200)
            before=len(seen);recovered.add(first['id'])
            result=self.client.post(path,json={'provider_id':first['id']})
            self.assertEqual(result.status_code,200,result.text);result=result.json()
            self.wait_job(result['job_id'])
            updated=self.client.get('/api/attempts/'+a['id']).json()
            self.assertEqual(seen[before:],[(first['id'],1200)])
            for g in updated['grades']:
                if g['provider_id'] in unchanged:self.assertEqual(g,unchanged[g['provider_id']])
            self.assertEqual(updated['status'],'partial')
            self.assertEqual(updated['mean_score'],17)
            self.assertIsNotNone(updated['synthesis'])
            self.assertEqual(updated['answer'],a['answer'])
            self.assertEqual(updated['question_snapshot'],a['question_snapshot'])
            self.assertNotIn('retry_provider_ids',updated)
            updated['status']='running';self.app.state.service.db.put('attempts',a['id'],updated,updated['created_at'])
            self.assertEqual(self.client.post(path,json={'provider_id':second['id']}).status_code,409)

    def test_retry_uses_edited_failed_config_without_regrading_success(self):
        p=self.make_paper();bad=self.add_model('bad')
        a=self.submit(p,models=[self.master,bad['id']]);self.assertEqual(a['status'],'partial')
        kept=next(g for g in a['grades'] if g['provider_id']==self.master)
        changed=dict(bad,name='新教练',protocol='anthropic',base_url='https://replacement.example/v1',model='new-model',api_key='replacement-secret')
        self.assertEqual(self.client.put('/api/providers/'+bad['id'],json=changed).status_code,200)
        before=len(self.mock.call_args_list)
        r=self.client.post('/api/attempts/'+a['id']+'/retry',json={'provider_id':bad['id']});self.assertEqual(r.status_code,200,r.text)
        self.wait_job(r.json()['job_id'])
        result=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(next(g for g in result['grades'] if g['provider_id']==self.master),kept)
        new=next(g for g in result['grades'] if g['provider_id']==bad['id'])
        self.assertEqual((new['name'],new['model']),('新教练','new-model'))
        calls=[c for c in self.mock.call_args_list[before:] if c.args[1].startswith('独立批改')]
        self.assertEqual(len(calls),1);self.assertEqual(calls[0].args[0]['protocol'],'anthropic')
        self.assertEqual(calls[0].args[0]['base_url'],changed['base_url'])
        self.assertEqual(result['retry_history'][0]['previous']['model'],'bad')
        self.assertEqual(result['retry_history'][0]['failed_grade']['status'],'failed')
        self.assertEqual(result['answer'],a['answer']);self.assertEqual(result['question_snapshot'],a['question_snapshot'])
        exported=self.client.get('/api/export').text
        self.assertNotIn('replacement-secret',exported);self.assertNotIn('secret-example',exported)

    def test_replacement_after_deleted_failed_provider_also_replaces_master(self):
        p=self.make_paper();bad=self.add_model('bad');replacement=self.add_model('other')
        async def quota(provider,task,payload):
            if provider['id']==bad['id']:raise ModelError('接口额度已用完')
            return await fake_model(provider,task,payload)
        with patch('app.service.call_model',side_effect=quota) as mock:
            r=self.client.post('/api/attempts',json=dict(question_id=p['questions'][0]['id'],answer='测试原答案，围绕材料概括措施。',provider_ids=[self.master,bad['id']],master_id=bad['id'])).json()
            self.wait_job(r['job_id']);a=self.client.get('/api/attempts/'+r['attempt_id']).json()
            self.assertEqual(a['status'],'summary_failed')
            kept=next(g for g in a['grades'] if g['provider_id']==self.master)
            self.client.delete('/api/providers/'+bad['id'])
            before=len(mock.call_args_list)
            r=self.client.post('/api/attempts/'+a['id']+'/retry',json={'provider_id':bad['id'],'replacement_provider_id':replacement['id']})
            self.assertEqual(r.status_code,200,r.text);self.wait_job(r.json()['job_id'])
            result=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(result['status'],'completed');self.assertEqual(result['mean_score'],15)
        self.assertEqual(result['master']['id'],replacement['id'])
        self.assertEqual([g['provider_id'] for g in result['grades'] if g['provider_id']==bad['id']],[])
        self.assertEqual(next(g for g in result['grades'] if g['provider_id']==self.master),kept)
        self.assertEqual([c.args[0]['id'] for c in mock.call_args_list[before:] if c.args[1].startswith('独立批改')],[replacement['id']])
        self.assertEqual(next(c.args[0]['id'] for c in mock.call_args_list[before:] if c.args[1].startswith('汇总')),replacement['id'])
        self.assertTrue(any(e['role']=='master' for e in result['retry_history']))

    def test_replacement_rejects_success_duplicate_and_invalid_selection_atomically(self):
        p=self.make_paper();bad=self.add_model('bad');new=self.add_model('other')
        a=self.submit(p,models=[self.master,bad['id']]);path='/api/attempts/'+a['id']+'/retry'
        for payload,status in [({'replacement_provider_id':new['id']},422),({'provider_id':self.master,'replacement_provider_id':new['id']},400),({'provider_id':bad['id'],'replacement_provider_id':self.master},400),({'provider_id':bad['id'],'replacement_provider_id':'missing'},400),({'provider_id':bad['id'],'replacement_provider_id':new['id'],'master_id':'missing'},400)]:
            r=self.client.post(path,json=payload);self.assertEqual(r.status_code,status,r.text)
            updated=self.client.get('/api/attempts/'+a['id']).json()
            self.assertEqual(updated['providers'],a['providers']);self.assertEqual(updated['grades'],a['grades'])
            self.assertNotIn('retry_history',updated)

    def test_replacing_failed_summary_does_not_call_any_coach(self):
        p=self.make_paper();other=self.add_model('other')
        async def summary_quota(provider,task,payload):
            if task.startswith('汇总') and provider['id']==self.master:raise ModelError('额度用完')
            return await fake_model(provider,task,payload)
        with patch('app.service.call_model',side_effect=summary_quota) as mock:
            a=self.submit(p);self.assertEqual(a['status'],'summary_failed')
            before=len(mock.call_args_list)
            r=self.client.post('/api/attempts/'+a['id']+'/retry',json={'master_id':other['id']});self.assertEqual(r.status_code,200,r.text)
            self.wait_job(r.json()['job_id'])
            updated=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(updated['status'],'completed');self.assertEqual(updated['grades'],a['grades'])
        self.assertEqual(updated['master']['id'],other['id'])
        self.assertFalse(any(c.args[1].startswith('独立批改') for c in mock.call_args_list[before:]))
        self.assertEqual(updated['mean_score'],a['mean_score'])

    def test_summary_retry_refreshes_edited_master_without_relabeling_success(self):
        p=self.make_paper()
        async def summary_quota(provider,task,payload):
            if task.startswith('汇总') and provider['model']=='good':raise ModelError('额度用完')
            return await fake_model(provider,task,payload)
        with patch('app.service.call_model',side_effect=summary_quota):
            a=self.submit(p);self.assertEqual(a['status'],'summary_failed')
            config=dict(self.provider,name='新汇总模型',model='other',api_key='')
            self.assertEqual(self.client.put('/api/providers/'+self.master,json=config).status_code,200)
            r=self.client.post('/api/attempts/'+a['id']+'/retry');self.wait_job(r.json()['job_id'])
            updated=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(updated['status'],'completed')
        self.assertEqual(updated['grades'],a['grades'])
        self.assertEqual(updated['providers'],a['providers'])
        self.assertEqual(updated['master']['model'],'other')

    def test_model_shape_compatibility_and_field_specific_failure(self):
        p=self.make_paper()
        async def compatible(provider,task,data):
            if task.startswith('独立批改'):
                result=grade();result.update(score='16分',task_analysis={'task':'不完整审题'},strengths='观点清晰',weaknesses=[])
                result['dimensions']={key:{'score':'70分'} for key in DIMENSIONS}
                return result
            return await fake_model(provider,task,data)
        with patch('app.service.call_model',side_effect=compatible):a=self.submit(p)
        self.assertEqual(a['status'],'completed');self.assertEqual(a['mean_score'],16)
        self.assertIsNone(a['grades'][0]['result']['task_analysis'])
        async def invalid(provider,task,data):
            if task.startswith('独立批改'):
                result=grade();result.pop('evidence');return result
            return await fake_model(provider,task,data)
        with patch('app.service.call_model',side_effect=invalid):b=self.submit(p)
        self.assertEqual(b['status'],'failed');self.assertIsNone(b['mean_score'])
        self.assertIn('评分证据：缺失必填字段',b['grades'][0]['error'])

    def test_score_and_summary_are_readable_before_slow_reference_repair(self):
        p=self.make_paper();release=threading.Event()
        async def delayed(provider,task,data):
            if task.startswith('独立批改'):
                value=grade(16);value['high_score_answer']='甲\n'*20;return value
            if task.startswith('范文校正'):
                while not release.is_set():await asyncio.sleep(.01)
                return {'high_score_answer':'　　'+'甲'*260}
            return await fake_model(provider,task,data)
        with patch('app.service.call_model',side_effect=delayed):
            result=self.client.post('/api/attempts',json=dict(question_id=p['questions'][0]['id'],answer='测试原答案。',provider_ids=[self.master],master_id=self.master)).json()
            try:
                for _ in range(200):
                    current=self.client.get('/api/attempts/'+result['attempt_id']).json()
                    if current.get('synthesis'):break
                    time.sleep(.01)
                self.assertEqual(current['status'],'running')
                self.assertIsNotNone(current['synthesis']);self.assertEqual(current['mean_score'],16)
                g=current['grades'][0];self.assertEqual(g['status'],'completed');self.assertEqual(g['stage'],'reference_repair')
                self.assertEqual(len(g['references']),1);self.assertEqual(g['result']['score'],16)
                self.assertIn('request_started_at',g);self.assertIn('response_received_at',g)
            finally:release.set()
            self.wait_job(result['job_id'])
            finished=self.client.get('/api/attempts/'+result['attempt_id']).json()
            self.assertEqual(finished['status'],'completed');self.assertEqual(len(finished['grades'][0]['references']),2)
            self.assertEqual(finished['grades'][0]['stage'],'completed')
    def test_progress_distinguishes_semaphore_queue_from_api_wait(self):
        p=self.make_paper();models=[self.master]+[self.add_model('coach-'+str(i))['id'] for i in range(4)]
        release=threading.Event();started=[]
        async def blocked(provider,task,data):
            if task.startswith('独立批改'):
                started.append(provider['id'])
                while not release.is_set():await asyncio.sleep(.01)
            return await fake_model(provider,task,data)
        with patch('app.service.call_model',side_effect=blocked):
            result=self.client.post('/api/attempts',json=dict(question_id=p['questions'][0]['id'],answer='测试排队状态。',provider_ids=models,master_id=self.master)).json()
            try:
                for _ in range(200):
                    current=self.client.get('/api/attempts/'+result['attempt_id']).json()
                    if len(current['grades'])==5:break
                    time.sleep(.01)
                self.assertEqual(len(started),4)
                self.assertEqual(sum(g['stage']=='queued' for g in current['grades']),1)
                self.assertEqual(sum(g['stage']=='requesting' for g in current['grades']),4)
                queued=next(g for g in current['grades'] if g['stage']=='queued')
                self.assertNotIn('request_started_at',queued)
            finally:release.set()
            self.wait_job(result['job_id'])
            self.assertEqual(len(started),5)
    def test_truncation_exposes_retry_budget_without_fake_score(self):
        from app.llm import TruncatedOutputError
        p=self.make_paper()
        async def truncated(provider,task,data):
            if task.startswith('独立批改'):raise TruncatedOutputError(6000,{'output_tokens':6000,'reasoning_tokens':5900})
            return await fake_model(provider,task,data)
        with patch('app.service.call_model',side_effect=truncated):a=self.submit(p)
        g=a['grades'][0]
        self.assertEqual(g['stage'],'failed');self.assertEqual(g['error_code'],'output_truncated')
        self.assertEqual(g['token_limit'],6000);self.assertEqual(g['suggested_token_limit'],32768)
        self.assertEqual(g['usage']['reasoning_tokens'],5900);self.assertIsNone(a['mean_score'])

    def test_all_fail_no_fake_zero_score(self):
        p=self.make_paper();bad=self.add_model('bad');a=self.submit(p,models=[bad['id']])
        self.assertEqual(a['status'],'failed');self.assertIsNone(a['mean_score'])
        self.assertEqual(self.client.get('/api/profile').json()['graded_count'],0)
    def test_out_of_range_score_not_counted(self):
        p=self.make_paper()
        async def invalid(p,t,data):return grade(21)
        with patch('app.service.call_model',side_effect=invalid):a=self.submit(p)
        self.assertEqual(a['status'],'failed');self.assertIsNone(a['mean_score'])
    def test_snapshot_immutable_on_paper_edit(self):
        p=self.make_paper();a=self.submit(p);p['questions'][0]['prompt']='已经改变的另一份题干。';p['year']='2025'
        self.client.put('/api/papers/'+p['id'],json=p)
        old=self.client.get('/api/attempts/'+a['id']).json()
        self.assertIn('简报',old['question_snapshot']['prompt']);self.assertEqual(old['question_snapshot']['source']['year'],'2026')
    def test_keys_encrypted_and_blank_update_preserves(self):
        text=self.client.get('/api/providers').text;self.assertNotIn('secret-example',text)
        raw=self.app.state.service.db.rows('SELECT secret FROM providers')[0]['secret'];self.assertNotIn('secret-example',raw)
        body={'name':'改名','protocol':'openai','base_url':'http://127.0.0.1:7777/v1','model':'good','api_key':''}
        self.client.put('/api/providers/'+self.master,json=body)
        self.assertTrue(self.client.get('/api/providers').json()[0]['has_key'])
        self.assertEqual(self.app.state.service.db.provider(self.master,True)['api_key'],'secret-example-do-not-leak')
    def test_ai_classification(self):
        p=self.make_paper();j=self.client.post('/api/papers/'+p['id']+'/classify',json={'provider_id':self.master}).json();self.assertEqual(self.wait_job(j['id'])['status'],'completed')
        new=self.client.get('/api/papers/'+p['id']).json();self.assertFalse(new['confirmed']);self.assertEqual(len(new['questions']),3)
        self.assertEqual(new['questions'][0]['id'],p['questions'][0]['id'])
    def test_summary_only_retry_preserves_coach_grades(self):
        summary_calls=0
        async def summarize(p,task,payload):
            nonlocal summary_calls
            if task.startswith('汇总'):
                summary_calls+=1
                return dict(analysis='依据题干核对原答，下一次应补充材料中的具体举措。',consensus=[],disagreements=[],action_plan=['具体修改建议']*(65 if summary_calls==1 else 12))
            return await fake_model(p,task,payload)
        self.mock.side_effect=summarize
        p=self.make_paper();a=self.submit(p)
        self.assertEqual(a['status'],'summary_failed')
        grades=a['grades']
        count=sum(c.args[1].startswith('独立批改') for c in self.mock.call_args_list)
        j=self.client.post('/api/attempts/'+a['id']+'/retry').json();self.wait_job(j['job_id'])
        updated=self.client.get('/api/attempts/'+a['id']).json()
        self.assertEqual(updated['status'],'completed')
        self.assertEqual(updated['grades'],grades)
        self.assertEqual(updated['mean_score'],a['mean_score'])
        self.assertEqual(len(updated['synthesis']['action_plan']),12)
        self.assertEqual(sum(c.args[1].startswith('独立批改') for c in self.mock.call_args_list),count)

    def test_legacy_profile_requests_rewrite_and_preserves_original(self):
        result=dict(analysis='这是旧版长期观察…',strengths=['旧优势'],weaknesses=['旧短板'],action_plan=['旧训练'])
        db=self.app.state.service.db
        db.setting('portrait',dict(result=result,signature='old',name='教练',model='good',history_count=1,updated_at='2026-01-01'))
        profile=self.client.get('/api/profile').json()
        self.assertTrue(profile['portrait_needs_refresh'])
        self.assertEqual(profile['portrait']['result'],result)
        self.assertEqual(db.setting('portrait')['result'],result)

    def test_profile_rewrites_bad_prose_once_and_preserves_previous_on_failure(self):
        p=self.make_paper();self.submit(p)
        for j in self.client.get('/api/jobs').json():
            if j['kind']=='profile':self.wait_job(j['id'])
        bad=dict(analysis='基于7条记录，六维均分83.7。',strengths=['f8农业题审题95。'],weaknesses=['材料运用不充分…'],action_plan=['建立材料段落与答案要点映射表。'*20])
        good=dict(analysis='先补齐材料要点，再精简表达。',strengths=['分条作答比较清楚。'],weaknesses=['具体措施仍有遗漏。'],action_plan=['列出各段材料的措施，写完后逐项核对。'])
        async def rewritten(provider,task,payload):
            if task.startswith('基于全部'):return bad
            if task.startswith('重写能力观察'):return good
            return await fake_model(provider,task,payload)
        self.mock.side_effect=rewritten
        j=self.client.post('/api/profile/refresh',json={'provider_id':self.master}).json()
        self.assertEqual(self.wait_job(j['id'])['status'],'completed')
        calls=[c for c in self.mock.call_args_list if c.args[1].startswith('重写能力观察')]
        self.assertEqual(len(calls),1);self.assertEqual(set(calls[0].args[2]),{'draft'})
        profile=self.client.get('/api/profile').json()
        self.assertFalse(profile['portrait_needs_refresh'])
        self.assertEqual(profile['portrait']['result'],good)
        previous=self.app.state.service.db.setting('portrait')
        async def still_bad(provider,task,payload):return bad
        self.mock.side_effect=still_bad
        j=self.client.post('/api/profile/refresh',json={'provider_id':self.master}).json()
        self.assertEqual(self.wait_job(j['id'])['status'],'failed')
        self.assertEqual(self.app.state.service.db.setting('portrait'),previous)

    def test_profile_all_history(self):
        p=self.make_paper();self.submit(p);self.submit(p,1)
        for j in self.client.get('/api/jobs').json():
            if j['kind']=='profile':self.wait_job(j['id'])
        s=self.client.get('/api/profile').json();self.assertEqual(s['portrait']['history_count'],2)
        calls=[c for c in self.mock.call_args_list if c.args[1].startswith('基于全部')]
        rows=calls[-1].args[2]['all_history']
        self.assertEqual(len(rows),2)
        self.assertTrue(all('id' not in row and 'reviews' not in row and 'weaknesses' in row for row in rows))
        self.assertNotIn('原答案',json.dumps(rows,ensure_ascii=False))
    def test_large_history_is_batched_without_dropping_records(self):
        p=self.make_paper();a=self.submit(p)
        for j in self.client.get('/api/jobs').json():
            if j['kind']=='profile':self.wait_job(j['id'])
        for index in range(400):
            copied=json.loads(json.dumps(a));copied['id']='history-'+str(index)
            for g in copied['grades']:
                g['result']['weaknesses']=['不足分析'*40]
                g['result']['evidence']=['具体作答证据'*40]
            self.app.state.service.db.put('attempts',copied['id'],copied,copied['created_at'])
        job=self.client.post('/api/profile/refresh',json={'provider_id':self.master}).json()
        self.assertEqual(self.wait_job(job['id'])['status'],'completed')
        calls=[c for c in self.mock.call_args_list if c.args[1].startswith('历史分块')]
        self.assertTrue(calls)
        self.assertEqual(sum(len(c.args[2]['records']) for c in calls),401)
        s=self.client.get('/api/profile').json();self.assertEqual(s['portrait']['history_count'],401)
    def test_restart_persistence_and_recovery(self):
        p=self.make_paper();a=self.submit(p)
        other=create_app(self.tmp.name)
        self.assertEqual(other.state.service.db.get('attempts',a['id'])['answer'],a['answer'])
        j=other.state.service.job('grading','pending')
        pending=dict(a,id='pending',status='running');other.state.service.db.put('attempts','pending',pending,pending['created_at']);other.state.service.recover()
        self.assertEqual(other.state.service.db.get('attempts','pending')['status'],'interrupted')
        self.assertEqual(other.state.service.db.get('jobs',j['id'])['status'],'interrupted')
        self.assertTrue(other.state.service.db.provider(self.master,True)['api_key'])
    def test_bad_upload_and_cross_origin(self):
        self.assertEqual(self.client.post('/api/papers/import',files={'file':('x.pdf',b'fake')}).status_code,400)
        self.assertEqual(self.client.post('/api/profile/refresh',headers={'Origin':'https://attacker.example'},json={'provider_id':self.master}).status_code,403)
        self.assertEqual(self.client.get('/',headers={'Host':'attacker.example'}).status_code,403)
        self.assertEqual(self.client.get('/').status_code,200)
    def test_lan_mode_accepts_private_hosts_and_same_origin_writes(self):
        with patch.dict(os.environ,{'ALLOW_LAN':'1','APP_PASSWORD':'','ALLOWED_HOSTS':'localhost,127.0.0.1'}):
            for host in ['192.168.50.20:18086','10.0.0.2:18086','172.16.0.2:18086','[fd00::123]:18086']:
                with self.subTest(host=host):
                    self.assertEqual(self.client.get('/api/papers',headers={'Host':host}).status_code,200)
            headers={'Host':'192.168.50.20:18086','Origin':'http://192.168.50.20:18086'}
            body={'name':'LAN coach','protocol':'openai','base_url':'https://api.example.com/v1','model':'model'}
            self.assertEqual(self.client.post('/api/providers',headers=headers,json=body).status_code,200)
            headers['Origin']='https://attacker.example'
            self.assertEqual(self.client.post('/api/providers',headers=headers,json=body).status_code,403)
            for host in ['attacker.example:18086','8.8.8.8:18086','172.32.0.1:18086']:
                self.assertEqual(self.client.get('/api/papers',headers={'Host':host}).status_code,403)
    def test_lan_off_rejects_private_host_and_lan_on_preserves_password(self):
        with patch.dict(os.environ,{'ALLOW_LAN':'0','APP_PASSWORD':'','ALLOWED_HOSTS':'localhost,127.0.0.1'}):
            self.assertEqual(self.client.get('/api/papers',headers={'Host':'192.168.50.20:18086'}).status_code,403)
        with patch.dict(os.environ,{'ALLOW_LAN':'1','APP_PASSWORD':'example-password'}):
            self.assertEqual(self.client.get('/api/papers',headers={'Host':'192.168.50.20:18086'}).status_code,401)
            self.assertEqual(self.client.get('/api/papers',headers={'Host':'192.168.50.20:18086'},auth=('admin','example-password')).status_code,200)
    def test_password_and_health(self):
        with patch.dict(os.environ,{'APP_PASSWORD':'example-password'}):
            self.assertEqual(self.client.get('/api/providers').status_code,401)
            self.assertEqual(self.client.get('/api/providers',auth=('admin','example-password')).status_code,200)
            self.assertEqual(self.client.get('/health').status_code,200)

class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_protocols_real_wire_schema(self):
        original=httpx.AsyncClient
        for protocol in ('openai','anthropic','gemini'):
            def handle(request):
                data=json.loads(request.content)
                if protocol=='openai':
                    self.assertEqual(request.url.path,'/v1/chat/completions');self.assertIn('Authorization',request.headers)
                    return httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]})
                if protocol=='anthropic':
                    self.assertEqual(request.url.path,'/v1/messages');self.assertEqual(request.headers['x-api-key'],'test')
                    return httpx.Response(200,json={'content':[{'type':'text','text':'{"ok":true}'}]})
                self.assertEqual(request.url.path,'/v1/models/model:generateContent');self.assertNotIn('key=',str(request.url));self.assertEqual(request.headers['x-goog-api-key'],'test')
                return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'{"ok":true}'}]}}]})
            with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)):
                result=await call_model({'protocol':protocol,'base_url':'https://mock.example/v1','api_key':'test','model':'model','max_tokens':6000},'连接测试',{})
            self.assertTrue(result['ok'])
    async def test_http_error_never_leaks_body(self):
        original=httpx.AsyncClient
        with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(lambda r:httpx.Response(401,text='secret-credential')),**kw)):
            with self.assertRaises(ModelError) as cm:await call_model({'protocol':'openai','base_url':'https://mock.example/v1','api_key':'secret','model':'model','max_tokens':6000},'test',{})
        self.assertNotIn('secret',str(cm.exception))

if __name__=='__main__':unittest.main()
