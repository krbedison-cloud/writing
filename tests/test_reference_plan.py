import json
import unittest
from unittest.mock import patch
import httpx
from app.schemas import Grade,ReferenceDraft,ReferencePlan,ProviderInput,DIMENSIONS
from app.llm import call_model,usage_metrics
from app.reference_policy import ESSAY_REFERENCE_POLICY,SHORT_COMMENT_POLICY

PLAN=dict(framework='以创新协同推动发展为总论点，分述创新、服务与绿色转型。',progression='由企业能力到公共服务，再到长期发展条件，逐层展开。',material_use=['材料1：支撑创新分论点，与材料2的服务保障衔接。','材料2：支撑公共服务，串联企业行动与外部条件。','材料3：不采用，与中心论点关联较弱，避免牵强入文。','材料外补例：材料缺少典型案例时，可用事实可靠的知名事例辅助说明。'])

class PlanTests(unittest.TestCase):
    def test_plan_field_is_separate_from_answer_and_optional_for_legacy(self):
        parsed=ReferenceDraft.model_validate({'high_score_answer':'这是一段足够长度的完整范文。','high_score_plan':PLAN})
        self.assertNotIn('framework',parsed.high_score_answer)
        self.assertEqual(parsed.high_score_plan.material_use,PLAN['material_use'])
        self.assertIsNone(ReferenceDraft.model_validate({'high_score_answer':parsed.high_score_answer}).high_score_plan)
        value=dict(score=35,dimensions={k:70 for k in DIMENSIONS},strengths=[],weaknesses=[],suggestions=[],evidence=['原答证据。'],high_score_answer=parsed.high_score_answer,high_score_plan={'framework':None})
        self.assertIsNone(Grade.model_validate(value).high_score_plan)
        self.assertEqual(ReferencePlan.model_validate(dict(PLAN,material_use='材料1：用于第一分论点。')).material_use,['材料1：用于第一分论点。'])

    def test_material_selection_and_fallback_priorities_are_explicit(self):
        for text in ['论点准确与层次递进优先','无法支撑论点的材料可舍弃','不求全覆盖','仅当材料缺乏可用典型案例','少量补充知名、代表性且事实可靠','材料外补例及补充原因']:
            self.assertIn(text,ESSAY_REFERENCE_POLICY)
        self.assertIn('不用材料外案例',SHORT_COMMENT_POLICY)

    def test_usage_counts_report_cached_input_and_reasoning_as_subsets(self):
        self.assertEqual(usage_metrics({'usage':{'prompt_tokens':500,'prompt_tokens_details':{'cached_tokens':400},'completion_tokens':300,'completion_tokens_details':{'reasoning_tokens':200}}},'openai'),dict(input_tokens=500,cached_tokens=400,output_tokens=300,reasoning_tokens=200))
        ds=usage_metrics({'usage':{'prompt_tokens':500,'prompt_cache_hit_tokens':400,'completion_tokens':30}},'openai')
        self.assertEqual(ds['cached_tokens'],400)
        usage=usage_metrics({'usage':{'input_tokens':100,'cache_read_input_tokens':400,'cache_creation_input_tokens':50,'output_tokens':20}},'anthropic')
        self.assertEqual(usage['input_tokens'],550);self.assertEqual(usage['cached_tokens'],400)

class RequestPerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_stable_question_prefix_and_scoped_effort_setting(self):
        original=httpx.AsyncClient
        configs=[('https://open.bigmodel.cn/api/paas/v4','glm-5.3','default',False),('https://open.bigmodel.cn/api/paas/v4','glm-5.3','high',True),('https://other.example/v1','glm-5.3','high',False)]
        for base,model,effort,sent in configs:
            def handle(request):
                body=json.loads(request.content)
                self.assertEqual('reasoning_effort' in body,sent)
                if sent:self.assertEqual(body['reasoning_effort'],'high')
                message=body['messages'][-1]['content']
                self.assertTrue(message.startswith('{"data":{"question":'))
                self.assertEqual(json.loads(message)['data']['question']['materials'],' 保留材料原文\n以及换行。')
                return httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'}}]})
            config=dict(base_url=base,model=model,protocol='openai',api_key='secret',max_tokens=32768,reasoning_effort=effort)
            with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)):
                await call_model(config,'任务',{'answer':'原答','question':{'materials':' 保留材料原文\n以及换行。'}})
        self.assertEqual(ProviderInput(name='GLM',base_url=config['base_url'],model='glm-5.3').reasoning_effort,'default')
