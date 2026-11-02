import copy
import json
import tempfile
import unittest
from fastapi.testclient import TestClient
from app.db import Database
from app.main import create_app
from app.materials import split_materials, referenced_ids, resolve_materials, prepare_paper, model_question
from app.schemas import QuestionInput

MATERIALS = '\n\n'.join(f'材料{i}\nPASSAGE_{i}_ONLY 这是资料{i}的独立内容。' for i in range(1,10))

def question(prompt='根据资料3，概括主要措施。', **kwargs):
    return dict(number=1,kind='summary',prompt=prompt,word_min=0,word_max=350,**kwargs)

class MaterialTests(unittest.TestCase):
    def test_numbered_passages_and_reference_forms(self):
        self.assertEqual([b['id'] for b in split_materials(MATERIALS)],list(range(1,10)))
        samples={
            '根据资料3，写简报。':[3],
            '根据资料8、9，写短评。':[8,9],
            '根据给定材料“1—3”以及资料五，概括措施。':[1,2,3,5],
            '根据资料（一）与（二）及（四），概括。':[1,2,4],
            '根据给定资料２至４和资料９，概括。':[2,3,4,9],
            '根据资料3和资料3，概括。':[3],
        }
        for prompt, ids in samples.items():
            with self.subTest(prompt=prompt):self.assertEqual(referenced_ids(prompt),ids)
        self.assertEqual([b['id'] for b in split_materials('资料一：背景\n内容甲\n资料二\n内容乙')],[1,2])
    def test_only_requested_passage_with_boundaries(self):
        text,scope=resolve_materials(MATERIALS,question())
        self.assertEqual(text,split_materials(MATERIALS)[2]['text'])
        self.assertEqual(scope['matched_ids'],[3])
        self.assertGreater(scope['saved_percent'],80)
        self.assertFalse(scope['needs_review'])
        text,scope=resolve_materials(MATERIALS,question('根据资料8、9，写短评。'))
        self.assertIn('PASSAGE_8_ONLY',text);self.assertIn('PASSAGE_9_ONLY',text)
        self.assertNotIn('PASSAGE_7_ONLY',text)
    def test_all_materials_request_overrides_incidental_reference(self):
        text,scope=resolve_materials(MATERIALS,question('结合全部给定资料，重点分析资料3，写议论文。'))
        self.assertEqual(text,MATERIALS);self.assertEqual(scope['mode'],'all')
        self.assertFalse(scope['needs_review'])
    def test_ambiguous_or_missing_references_never_silently_drop_evidence(self):
        for prompt in ['根据资料3、10，写简报。','根据资料5至3，写简报。']:
            with self.subTest(prompt=prompt):
                text,scope=resolve_materials(MATERIALS,question(prompt))
                self.assertEqual(text,MATERIALS);self.assertEqual(scope['mode'],'fallback')
                self.assertTrue(scope['needs_review'])
        duplicated=MATERIALS+'\n材料3\n重复标题，无法可靠定位。'
        self.assertEqual(split_materials(duplicated),[])
        self.assertEqual(resolve_materials(duplicated,question())[0],duplicated)
        plain='没有编号的完整材料内容。'
        self.assertEqual(resolve_materials(plain,question())[0],plain)
    def test_manual_selection_custom_and_validation(self):
        text,scope=resolve_materials(MATERIALS,question(material_mode='selected',material_ids=[2,5]))
        self.assertEqual(scope['matched_ids'],[2,5]);self.assertNotIn('PASSAGE_3_ONLY',text)
        text,scope=resolve_materials(MATERIALS,question(material_mode='custom',custom_materials='仅手工校对材料'))
        self.assertEqual(text,'仅手工校对材料');self.assertEqual(scope['mode'],'custom')
        for kwargs in [dict(material_mode='selected'),dict(material_mode='custom'),dict(material_ids=[0])]:
            with self.assertRaises(ValueError):QuestionInput.model_validate(question(**kwargs))
    def test_old_snapshot_is_narrowed_without_mutation_or_extra_fields(self):
        snapshot=question(materials=MATERIALS,source={'title':'old'},custom_materials='UNUSED_CUSTOM')
        original=copy.deepcopy(snapshot)
        context=model_question(snapshot)
        self.assertIn('PASSAGE_3_ONLY',context['materials'])
        self.assertNotIn('PASSAGE_1_ONLY',json.dumps(context))
        self.assertNotIn('UNUSED_CUSTOM',json.dumps(context))
        self.assertNotIn('source',context)
        self.assertEqual(snapshot,original)
    def test_startup_migrates_papers_but_keeps_history_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(tmp)
            old=dict(id='paper',title='旧试卷',year='2026',province='浙江',volume='A卷',confirmed=True,
                     materials=MATERIALS,raw_text='original',filename='old.pdf',created_at='2026-01-01',
                     questions=[dict(question(),id='question')])
            db.put('papers','paper',old,old['created_at'])
            attempt=dict(id='old-attempt',status='completed',answer='当时的原答案',created_at='2026-01-01',question_snapshot=dict(question(),materials=MATERIALS))
            db.put('attempts','old-attempt',attempt,attempt['created_at'])
            with TestClient(create_app(tmp)) as client:
                p=client.get('/api/papers/paper').json()
                self.assertTrue(p['confirmed']);self.assertEqual(p['questions'][0]['id'],'question')
                self.assertEqual(p['questions'][0]['material_scope']['matched_ids'],[3])
                focused=client.get('/api/papers/paper/questions/question').json()
                self.assertNotIn('materials',focused['paper'])
                self.assertNotIn('PASSAGE_1_ONLY',json.dumps(focused))
                listing=client.get('/api/papers').json()
                self.assertNotIn('PASSAGE_1_ONLY',json.dumps(listing))
            self.assertEqual(db.get('attempts','old-attempt'),attempt)
            self.assertEqual(db.get('papers','paper')['materials'],MATERIALS)
