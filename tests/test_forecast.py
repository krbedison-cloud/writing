import asyncio
import copy
import json
import tempfile
import threading
import unittest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.forecast import FORECAST_TASK, ForecastResult, forecast_evidence, forecast_signature
from app.main import create_app
from app.schemas import DIMENSIONS, KINDS
from app.llm import ModelError
import test_app as app_fixtures


def result_for(data):
    return dict(scores={kind:score if data['groups'][kind]['n'] else None for kind,score in [('summary',15),('practical',22),('essay',34)]},
                confidence='medium',analysis='参考近期要点完整性与论证表现，预估仍受材料运用不稳和重复练习影响，须继续检验迁移能力。')


class ForecastAppTests(unittest.TestCase):
    def setUp(self):
        self.fx=app_fixtures.AppTests();self.fx.setUp();self.addCleanup(self.fx.tearDown)
        self.client=self.fx.client;self.calls=[];self.failure=None;self.override=None;self.release=None;self.started=threading.Event()
        self.fx.mock.side_effect=self.model
        self.paper=self.fx.make_paper()

    async def model(self,provider,task,data):
        if task.startswith('预测浙江省考'):
            self.calls.append((provider['id'],copy.deepcopy(data)))
            self.started.set()
            if self.release is not None:
                while not self.release.is_set():await asyncio.sleep(.01)
            if self.failure:raise self.failure
            return self.override or result_for(data)
        return await app_fixtures.fake_model(provider,task,data)

    def fill(self):
        for index in range(3):self.fx.submit(self.paper,index)

    def update(self,provider=None):
        r=self.client.post('/api/profile/forecast',json={'provider_id':provider or self.fx.master})
        self.assertEqual(r.status_code,200,r.text)
        return self.fx.wait_job(r.json()['id'])

    def test_only_manual_update_calls_selected_model_and_uses_its_predictions(self):
        self.fill();other=self.fx.add_model('chosen')
        before=copy.deepcopy(self.fx.app.state.service.db.all('attempts'))
        for _ in range(3):
            self.assertIsNone(self.client.get('/api/profile').json()['estimated_total'])
            self.client.get('/api/attempts');self.client.get('/')
        self.assertEqual(self.calls,[])
        self.assertEqual(self.update(other['id'])['status'],'completed')
        s=self.client.get('/api/profile').json()
        self.assertEqual(s['estimated_total'],71)
        self.assertNotEqual(s['estimated_total'],sum(f['score'] for f in s['training_statistics'].values()))
        self.assertEqual(s['forecast_report']['provider_id'],other['id'])
        self.assertEqual(len(self.calls),1);self.assertEqual(self.calls[0][0],other['id'])
        self.assertEqual(self.fx.app.state.service.db.all('attempts'),before)
        exported=self.client.get('/api/export').json()
        self.assertEqual(exported['forecast_report'],s['forecast_report'])
        self.assertNotIn('secret-example-do-not-leak',json.dumps(exported))
        self.assertEqual(len(self.calls),1)

    def test_new_grading_and_reads_freeze_saved_score_until_manual_update(self):
        self.fill();self.update();previous=self.client.get('/api/profile').json()['forecast_report']
        self.fx.submit(self.paper)
        for _ in range(3):
            s=self.client.get('/api/profile').json()
            self.assertEqual(s['forecast_report'],previous);self.assertEqual(s['estimated_total'],71)
            self.assertTrue(s['forecast_stale'])
        self.assertEqual(len(self.calls),1)
        self.update();self.assertEqual(len(self.calls),2)
        self.assertFalse(self.client.get('/api/profile').json()['forecast_stale'])

    def test_failures_and_invalid_scores_preserve_previous_report_without_extra_calls(self):
        self.fill();self.update();previous=self.client.get('/api/profile').json()['forecast_report']
        for failure,override in [(ModelError('网络断线'),None),(None,dict(scores={'summary':15,'practical':22,'essay':51},confidence='high',analysis='有依据的预测说明。'))]:
            self.failure,self.override=failure,override
            self.assertEqual(self.update()['status'],'failed')
            s=self.client.get('/api/profile').json()
            self.assertEqual(s['forecast_report'],previous);self.assertEqual(s['estimated_total'],71)
            self.assertEqual(s['forecast_job']['status'],'failed')
        self.assertEqual(len(self.calls),3)

    def test_empty_history_and_missing_types_do_not_fabricate_total(self):
        r=self.client.post('/api/profile/forecast',json={'provider_id':self.fx.master})
        self.assertEqual(r.status_code,400);self.assertEqual(self.calls,[])
        self.fx.submit(self.paper)
        self.assertEqual(self.update()['status'],'completed')
        s=self.client.get('/api/profile').json()
        self.assertEqual(s['forecast']['summary']['score'],15)
        self.assertIsNone(s['forecast']['essay']['score']);self.assertIsNone(s['estimated_total'])
        saved=s['forecast_report']
        self.override=dict(scores={'summary':15,'practical':22,'essay':34},confidence='high',analysis='未练习题型也给出分数。')
        self.assertEqual(self.update()['status'],'failed')
        self.assertEqual(self.client.get('/api/profile').json()['forecast_report'],saved)

    def test_duplicate_clicks_do_not_start_two_requests(self):
        self.fill();self.release=threading.Event()
        try:
            r=self.client.post('/api/profile/forecast',json={'provider_id':self.fx.master})
            self.assertEqual(r.status_code,200);self.assertTrue(self.started.wait(2))
            duplicate=self.client.post('/api/profile/forecast',json={'provider_id':self.fx.master})
            self.assertEqual(duplicate.status_code,409);self.assertEqual(len(self.calls),1)
            self.assertIn(self.client.get('/api/profile').json()['forecast_job']['status'],('queued','running'))
        finally:self.release.set()
        self.assertEqual(self.fx.wait_job(r.json()['id'])['status'],'completed')
        self.assertEqual(len(self.calls),1)

    def test_data_added_while_prediction_is_running_marks_snapshot_stale(self):
        self.fill();self.release=threading.Event()
        try:
            r=self.client.post('/api/profile/forecast',json={'provider_id':self.fx.master})
            self.assertTrue(self.started.wait(2))
            self.fx.submit(self.paper)
        finally:self.release.set()
        self.assertEqual(self.fx.wait_job(r.json()['id'])['status'],'completed')
        s=self.client.get('/api/profile').json()
        self.assertEqual(s['forecast_report']['history_count'],3)
        self.assertEqual(s['graded_count'],4);self.assertTrue(s['forecast_stale'])
        self.assertEqual(len(self.calls),1)

    def test_restart_persists_prediction_and_never_calls_prediction_automatically(self):
        self.fill();self.update();saved=self.client.get('/api/profile').json()['forecast_report']
        for j in self.client.get('/api/jobs').json():
            if j['kind']=='profile':self.fx.wait_job(j['id'])
        other=create_app(self.fx.tmp.name)
        with TestClient(other) as client:
            self.assertEqual(client.get('/api/profile').json()['forecast_report'],saved)
            self.assertEqual(client.get('/api/profile').json()['estimated_total'],71)
        self.assertEqual(len(self.calls),1)

    def test_portrait_refresh_is_independent_and_keeps_existing_behavior(self):
        self.fill()
        for j in self.client.get('/api/jobs').json():
            if j['kind']=='profile':self.fx.wait_job(j['id'])
        r=self.client.post('/api/profile/refresh',json={'provider_id':self.fx.master})
        self.assertEqual(self.fx.wait_job(r.json()['id'])['status'],'completed')
        self.assertEqual(self.calls,[])
        self.assertIsNone(self.client.get('/api/profile').json()['estimated_total'])


class ForecastEvidenceTests(unittest.TestCase):
    def samples(self,count=1200):
        rows=[]
        for i in range(count):
            kind=list(KINDS)[i%3];full=KINDS[kind][1];score=round(full*(.2 if i<3 else .7+i%3*.02),2)
            rows.append(dict(id=str(i),question_id='q'+str(i%21),created_at=f'2026-01-{i%28+1:02d}',answer='PRIVATE_ANSWER_DO_NOT_SEND',mean_score=score,
                             question_snapshot=dict(id='q'+str(i%21),kind=kind,prompt='PRIVATE_PROMPT_DO_NOT_SEND',materials='PRIVATE_MATERIAL_DO_NOT_SEND',source={'province':'浙江'}),
                             grades=[dict(status='completed',model='model',result=dict(score=score,dimensions={k:70+i%10 for k in DIMENSIONS},
                                           strengths=['优点'+str(i%5)+'甲'*100],weaknesses=['短板'+str(i%7)+'乙'*100],suggestions=['行动'+str(i%3)+'丙'*100],high_score_answer='PRIVATE_REFERENCE_DO_NOT_SEND'))]))
        return rows

    def test_all_history_contributes_but_wire_input_is_bounded(self):
        rows=self.samples();original=copy.deepcopy(rows)
        data=forecast_evidence(rows)
        self.assertEqual(data['record_count'],1200)
        for kind,group in data['groups'].items():
            self.assertEqual(group['n'],400)
            self.assertEqual(sum(b['n'] for b in group['older_bins'])+len(group['recent']),400)
            self.assertLessEqual(len(group['older_bins']),4);self.assertLessEqual(len(group['recent']),6)
            self.assertEqual(group['score_stats']['minimum'],KINDS[kind][1]*.2)
        wire=json.dumps(data,ensure_ascii=False,separators=(',',':'))
        self.assertLess(len(wire),18000);self.assertLess(len(FORECAST_TASK),700)
        self.assertNotIn('PRIVATE_',wire)
        self.assertEqual(rows,original)

    def test_signature_tracks_evidence_changes_but_ignores_reference_rewrites(self):
        rows=self.samples(12);signature=forecast_signature(rows)
        self.assertEqual(forecast_signature(list(reversed(rows))),signature)
        rows[0]['grades'][0]['result']['high_score_answer']='新模型范文，不作为预测依据。'
        self.assertEqual(forecast_signature(rows),signature)
        rows[0]['grades'][0]['result']['weaknesses'].append('新短板')
        self.assertNotEqual(forecast_signature(rows),signature)

    def test_output_bounds_and_lossless_common_shapes(self):
        raw=dict(scores={'summary':'15分','practical':22,'essay':{'score':34}},confidence='中等',analysis=['近期表现稳定。','材料运用仍需加强。'])
        parsed=ForecastResult.model_validate(raw)
        self.assertEqual(sum(parsed.scores.values()),71)
        self.assertEqual(parsed.confidence,'medium');self.assertIn('材料运用仍需加强。',parsed.analysis)
        for scores in [{'summary':21,'practical':22,'essay':34},{'summary':15,'practical':31,'essay':34},{'summary':15,'practical':22,'essay':float('nan')},{'summary':True,'practical':22,'essay':34},{'summary':15,'practical':22}]:
            with self.assertRaises(ValidationError):ForecastResult.model_validate(dict(raw,scores=scores))
