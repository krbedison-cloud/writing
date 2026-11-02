import asyncio
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
    def test_profile_all_history(self):
        p=self.make_paper();self.submit(p);self.submit(p,1)
        for j in self.client.get('/api/jobs').json():
            if j['kind']=='profile':self.wait_job(j['id'])
        s=self.client.get('/api/profile').json();self.assertEqual(s['portrait']['history_count'],2)
        calls=[c for c in self.mock.call_args_list if c.args[1].startswith('基于全部')]
        self.assertEqual(len(calls[-1].args[2]['all_history']),2)
    def test_large_history_is_batched_without_dropping_records(self):
        p=self.make_paper();a=self.submit(p)
        for j in self.client.get('/api/jobs').json():
            if j['kind']=='profile':self.wait_job(j['id'])
        for index in range(100):
            copied=json.loads(json.dumps(a));copied['id']='history-'+str(index)
            for g in copied['grades']:
                g['result']['weaknesses']=['不足分析'*40]
                g['result']['evidence']=['具体作答证据'*40]
            self.app.state.service.db.put('attempts',copied['id'],copied,copied['created_at'])
        job=self.client.post('/api/profile/refresh',json={'provider_id':self.master}).json()
        self.assertEqual(self.wait_job(job['id'])['status'],'completed')
        calls=[c for c in self.mock.call_args_list if c.args[1].startswith('历史分块')]
        self.assertTrue(calls)
        self.assertEqual(sum(len(c.args[2]['records']) for c in calls),101)
        s=self.client.get('/api/profile').json();self.assertEqual(s['portrait']['history_count'],101)
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
