import copy
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.import_validation import normalize_import, import_errors
from app.materials import prepare_paper
from app.main import create_app
from app.db import Database
from app.service import Service
from app.llm import ModelError

MATERIALS='材料1\n独立内容甲。\n材料2\n独立内容乙。\n材料3\n独立内容丙。'
PAPER=dict(id='p',filename='exam.pdf',raw_text='原试卷文字',created_at='2026-01-01',title='试卷',year='2026',province='浙江',volume='B卷',materials=MATERIALS,confirmed=False,
           questions=[dict(id='q',number=1,kind='summary',prompt='请概括主要措施，不超过300字。',word_min=0,word_max=300)])

class ImportTests(unittest.TestCase):
    def test_ambiguous_scopes_include_essay_and_generic_reference(self):
        for prompt in ['请概括主要措施。','结合给定资料，写一篇议论性文章。']:
            p=copy.deepcopy(PAPER);p['questions'][0].update(prompt=prompt,kind='essay')
            p=prepare_paper(p)
            self.assertTrue(p['requires_material_selection'])
            self.assertTrue(p['questions'][0]['material_scope']['requires_choice'])
        for changes in [dict(material_mode='all'),dict(material_mode='selected',material_ids=[2,3]),dict(prompt='结合全部给定资料写作。')]:
            p=copy.deepcopy(PAPER);p['questions'][0].update(changes);p=prepare_paper(p)
            self.assertFalse(p['requires_material_selection'])

    def test_common_shapes_and_metadata_are_normalized(self):
        raw={'data':{'year':2025,'materials':['材料1\n甲','材料2\n乙'],'questions':[{'number':'1','kind':'概括题','prompt':'请概括主要措施。','word_min':None,'word_max':'300字','classification_note':None}]}}
        p=normalize_import(raw,PAPER)
        self.assertEqual(p['year'],'2025');self.assertEqual(p['title'],'试卷')
        self.assertEqual(p['questions'][0]['word_max'],300)
        self.assertEqual(p['questions'][0]['kind'],'summary')
        self.assertIn('材料2',p['materials']);self.assertFalse(p['confirmed'])
        self.assertEqual(raw['data']['questions'][0]['word_min'],None)

    def test_model_cannot_invent_paper_capacity_or_override_explicit_prompt_limit(self):
        raw=dict(questions=[dict(number=3,kind='practical',prompt='请提出建议，要求不超过450字。',word_max=600,paper_rows=24)])
        q=normalize_import(raw,PAPER)['questions'][0]
        self.assertEqual((q['word_max'],q['paper_rows']),(450,0))
        self.assertEqual(raw['questions'][0]['paper_rows'],24)

    def test_core_missing_unknown_kind_and_invalid_limits_rejected(self):
        for q in [dict(number=1,kind='summary'),dict(number=1,kind='unknown',prompt='完整题干文字'),dict(number=1,kind='essay',prompt='完整题干文字',word_max=9000)]:
            with self.assertRaises(ValidationError) as caught:normalize_import(dict(questions=[q]),PAPER)
            self.assertNotIn('输出字段',import_errors(caught.exception))

    def test_confirm_and_grading_require_scope_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(tmp);db.put('papers','p',PAPER,PAPER['created_at'])
            with TestClient(create_app(tmp)) as client:
                p=client.get('/api/papers/p').json();p['confirmed']=True
                self.assertEqual(client.put('/api/papers/p',json=p).status_code,400)
                r=client.post('/api/attempts',json={'question_id':'q','answer':'原答案','provider_ids':['any'],'master_id':'any'})
                self.assertEqual(r.status_code,400);self.assertIn('材料范围',r.text)
                p['questions'][0].update(material_mode='selected',material_ids=[2,3])
                r=client.put('/api/papers/p',json=p);self.assertEqual(r.status_code,200,r.text)
                q=r.json()['questions'][0]
                self.assertNotIn('独立内容甲',q['materials'])
                self.assertEqual(q['material_scope']['matched_ids'],[2,3])

class ClassificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_repair_then_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(tmp);db.put('papers','p',PAPER,PAPER['created_at']);service=Service(db)
            good=dict(questions=[dict(number=1,kind='概括题',prompt=PAPER['questions'][0]['prompt'],word_max='300字')])
            with patch.object(service,'provider',return_value={}),patch.object(service,'ask',new=AsyncMock(side_effect=[{'questions':[{'number':1}]},good])) as ask:
                j=service.job('classification','p');await service.classify(j,'p','provider')
                self.assertEqual(db.get('jobs',j['id'])['status'],'completed')
                self.assertEqual(ask.await_count,2)
                self.assertIn('完整题干',ask.call_args.args[2]['format_errors'])
                self.assertTrue(db.get('papers','p')['requires_material_selection'])

    async def test_failed_repair_preserves_paper_and_reports_field(self):
        for error in [None,ModelError('接口超时（本次等待上限600秒）')]:
            with tempfile.TemporaryDirectory() as tmp:
                db=Database(tmp);db.put('papers','p',PAPER,PAPER['created_at']);service=Service(db)
                mock=AsyncMock(side_effect=error) if error else AsyncMock(return_value={'questions':[{'number':1}]})
                with patch.object(service,'provider',return_value={}),patch.object(service,'ask',new=mock):
                    j=service.job('classification','p');await service.classify(j,'p','provider')
                    self.assertEqual(db.get('jobs',j['id'])['status'],'failed')
                    self.assertEqual(db.get('papers','p'),PAPER)
                    self.assertEqual(mock.await_count,1 if error else 2)
                    if not error:self.assertIn('完整题干',db.get('jobs',j['id'])['error'])

    async def test_manual_choice_preserved_for_unchanged_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(tmp);p=copy.deepcopy(PAPER);p['questions'][0].update(material_mode='selected',material_ids=[2],paper_rows=16)
            db.put('papers','p',p,p['created_at']);service=Service(db)
            with patch.object(service,'provider',return_value={}),patch.object(service,'ask',new=AsyncMock(return_value=PAPER)):
                j=service.job('classification','p');await service.classify(j,'p','provider')
            self.assertEqual(db.get('papers','p')['questions'][0]['material_ids'],[2])
            self.assertEqual(db.get('papers','p')['questions'][0]['paper_rows'],16)
