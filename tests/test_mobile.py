import base64
import json
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import create_app
from app.pdf import parse_text
from app.service import now
from test_app import TEXT


class MobileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'APP_PASSWORD':''});self.env.start()
        self.app=create_app(self.tmp.name);self.client=TestClient(self.app);self.client.__enter__()
        self.db=self.app.state.service.db
        paper=dict(parse_text(TEXT,'exam.pdf'),id='paper',created_at=now(),filename='exam.pdf',raw_text=TEXT,confirmed=True)
        for i,q in enumerate(paper['questions']):q['id']=f'q{i+1}'
        self.db.put('papers','paper',paper,paper['created_at'])
        self.model=patch('app.service.call_model',side_effect=AssertionError('手机录入不得触发批改'));self.model.start()
    def tearDown(self):
        self.client.__exit__(None,None,None);self.model.stop();self.env.stop();self.tmp.cleanup()
    def pair(self,name='测试安卓手机'):
        code=self.client.post('/api/phone/pair-code').json()['code']
        response=self.client.post('/api/mobile/pair',json={'code':code,'device_name':name})
        self.assertEqual(response.status_code,200,response.text)
        value=response.json();return value,{'Authorization':'Bearer '+value['token']}
    def body(self,headers,text='　　这是格子纸上的手写答案。\n　　保留原用词和标点！'):
        questions=self.client.get('/api/mobile/papers/paper/questions',headers=headers).json()['questions']
        return dict(request_id=str(uuid.uuid4()),question_signature=questions[0]['question_signature'],text=text,model='vision-model')
    def test_pair_code_is_single_use_expires_and_stores_only_hashes(self):
        code=self.client.post('/api/phone/pair-code').json()['code']
        request={'code':code,'device_name':'LineageOS Android 17'}
        paired=self.client.post('/api/mobile/pair',json=request).json()
        self.assertGreater(len(paired['token']),20)
        self.assertEqual(self.client.post('/api/mobile/pair',json=request).status_code,400)
        stored=self.db.rows('SELECT * FROM mobile_devices')[0]
        self.assertNotIn(paired['token'],json.dumps(stored));self.assertEqual(len(stored['token_hash']),64)
        self.assertNotIn(code,json.dumps(self.db.setting('mobile_pairing')))
        expired=self.db.setting('mobile_pairing');expired['expires_at']=0;self.db.setting('mobile_pairing',expired)
        self.assertEqual(self.client.post('/api/mobile/pair',json=request).status_code,400)
        self.assertEqual(self.client.get('/api/mobile/papers').status_code,401)
    def test_ten_wrong_codes_invalidate_pairing(self):
        code=self.client.post('/api/phone/pair-code').json()['code'];wrong='000000' if code!='000000' else '111111'
        for _ in range(10):self.assertEqual(self.client.post('/api/mobile/pair',json={'code':wrong,'device_name':'test'}).status_code,400)
        self.assertEqual(self.client.post('/api/mobile/pair',json={'code':code,'device_name':'test'}).status_code,400)
    def test_phone_scope_works_with_desktop_password_and_can_be_revoked(self):
        value,headers=self.pair()
        self.assertEqual(self.client.get('/api/providers',headers=headers).status_code,403)
        self.assertEqual(self.client.post('/api/phone/pair-code',headers=headers).status_code,403)
        with patch.dict(os.environ,{'APP_PASSWORD':'desktop-password'}):
            self.assertEqual(self.client.get('/api/providers').status_code,401)
            papers=self.client.get('/api/mobile/papers',headers=headers).json()
            self.assertEqual(papers[0]['id'],'paper')
            text=self.client.get('/api/mobile/papers/paper/questions',headers=headers).text
            self.assertNotIn('materials',text);self.assertNotIn('api_key',text)
            admin={'Authorization':'Basic '+base64.b64encode(b'admin:desktop-password').decode()}
            self.assertEqual(self.client.delete('/api/phone/devices/'+value['device_id'],headers=admin).status_code,200)
            self.assertEqual(self.client.get('/api/mobile/papers',headers=headers).status_code,401)
    def test_sync_is_idempotent_and_never_submits_an_attempt(self):
        _,headers=self.pair();body=self.body(headers);path='/api/mobile/papers/paper/questions/q1/captures'
        first=self.client.post(path,json=body,headers=headers)
        self.assertEqual(first.status_code,200,first.text)
        repeated=self.client.post(path,json=body,headers=headers)
        self.assertEqual(repeated.json()['id'],first.json()['id'])
        self.assertEqual(len(self.db.rows('SELECT id FROM mobile_captures')),1)
        self.assertEqual(first.json()['text'],body['text'])
        changed=dict(body,text='不同文字')
        self.assertEqual(self.client.post(path,json=changed,headers=headers).status_code,409)
        self.assertEqual(self.db.all('attempts'),[]);self.assertEqual(self.db.all('jobs'),[])
        inbox=self.client.get('/api/phone/captures?question_id=q1').json()
        self.assertEqual(inbox['items'][0]['id'],first.json()['id'])
        self.assertEqual(self.client.get('/api/phone/captures?question_id=q2').json()['items'],[])
    def test_changed_question_and_invalid_content_preserve_phone_record(self):
        _,headers=self.pair();body=self.body(headers);path='/api/mobile/papers/paper/questions/q1/captures'
        paper=self.db.get('papers','paper');paper['questions'][0]['prompt']+='新要求。';self.db.put('papers','paper',paper,paper['created_at'])
        self.assertEqual(self.client.post(path,json=body,headers=headers).status_code,409)
        self.assertEqual(self.client.post(path,json=dict(self.body(headers),text='   '),headers=headers).status_code,400)
        self.assertEqual(self.client.post(path,json=dict(self.body(headers),text='字'*20001),headers=headers).status_code,422)
        self.assertEqual(self.db.rows('SELECT id FROM mobile_captures'),[])
    def test_inbox_processing_export_and_restart_keep_original_text(self):
        value,headers=self.pair();body=self.body(headers)
        second,_=self.pair('另一台手机')
        self.assertEqual(value['server_id'],second['server_id'])
        record=self.client.post('/api/mobile/papers/paper/questions/q1/captures',json=body,headers=headers).json()
        path='/api/phone/captures/'+record['id']+'/ack'
        self.assertEqual(self.client.post(path,json={'status':'accepted'}).status_code,200)
        self.assertEqual(self.client.post(path,json={'status':'accepted'}).status_code,200)
        self.assertEqual(self.client.post(path,json={'status':'dismissed'}).status_code,409)
        self.assertEqual(self.client.get('/api/phone/captures').json()['items'],[])
        export=self.client.get('/api/export').json()
        self.assertEqual(export['phone_captures'][0]['text'],body['text'])
        self.assertNotIn(value['token'],json.dumps(export));self.assertNotIn('token_hash',json.dumps(export))
        with TestClient(create_app(self.tmp.name)) as other:
            self.assertEqual(other.get('/api/mobile/papers',headers=headers).status_code,200)
            self.assertEqual(other.get('/api/phone/captures?status=all').json()['items'][0]['text'],body['text'])
