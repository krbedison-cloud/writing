"""Prompt budgets and task boundaries. These do not estimate real providers' hidden reasoning."""
import copy
import json
import unittest
from unittest.mock import patch
import httpx
from app import grading_prompts as prompts
from app.llm import SYSTEM, PAPER_GUIDE, compact_payload, request_message, call_model
from app.paper import paper_spec, check_paper, RULES
from app.reference_policy import reference_policy, reference_paper_spec, reference_payload, needs_reference_repair
from app.schemas import Grade, ReferenceDraft, ReferenceSelfCheck, DIMENSIONS
from test_app import grade
from test_essay_policy import essay_fixture


class PromptEfficiencyTests(unittest.TestCase):
    def test_counting_is_optional_without_weakening_program_checks(self):
        q=dict(kind='essay',prompt='写一篇议论性文章，1000–1200字。')
        spec=reference_paper_spec(q)
        draft=dict(grade(35),high_score_answer=essay_fixture(),high_score_self_check=dict(word_count=None,used_rows=None,complies=True,format_note='已检查总分总结构、段首观点、材料案例及篇幅。'))
        parsed=Grade.model_validate(draft)
        self.assertIsNone(parsed.high_score_self_check.word_count)
        self.assertEqual(parsed.score,35)
        # Missing a self-reported number alone does not trigger a second billed request.
        ref=dict(layout_check=check_paper(parsed.high_score_answer,spec))
        self.assertFalse(needs_reference_repair(ref))
        # A confident AI self-report cannot overrule too few words or too many rows.
        for answer in ['甲'*1000,'甲'*1201,'\n'.join(['甲'*20]*50)]:
            self.assertTrue(needs_reference_repair(dict(layout_check=check_paper(answer,spec))))
        self.assertEqual(ReferenceSelfCheck.model_validate(dict(complies=True,format_note='结构符合。')).used_rows,None)
        legacy=dict(word_count=1080,used_rows=47,complies=True,format_note='旧计数保留。')
        self.assertEqual(ReferenceSelfCheck.model_validate(legacy).model_dump(),legacy)
        self.assertIsNone(ReferenceDraft.model_validate(dict(high_score_answer=essay_fixture(),high_score_self_check={'word_count':'不知道','complies':True})).high_score_self_check)

    def test_valid_json_examples_and_once_only_efficiency_rules(self):
        self.assertEqual(set(json.loads(prompts.GRADE_JSON)),set(grade())|{'task_analysis','high_score_plan','high_score_self_check'})
        self.assertEqual(set(json.loads(prompts.REFERENCE_JSON)),{'high_score_answer','high_score_plan','high_score_self_check'})
        self.assertEqual(json.loads(prompts.REFERENCE_JSON)['high_score_self_check']['word_count'],None)
        for q in [dict(kind='summary',prompt='概括措施。'),dict(kind='practical',prompt='写一则短评。'),dict(kind='essay',prompt='写一篇议论文。')]:
            task=prompts.GRADING_TASK+prompts.REFERENCE_RULES+reference_policy(q)+prompts.PLAN_RULES
            all_rules=SYSTEM+task
            self.assertEqual(all_rules.count(prompts.EFFICIENCY_RULES),1)
            self.assertEqual(task.count(prompts.REFERENCE_RULES),1)
            self.assertNotIn(prompts.calibration(q),task)
        self.assertIn('否则填null',prompts.REFERENCE_RULES)
        self.assertIn('精确计字占格由程序校验',prompts.REFERENCE_RULES)
        self.assertIn('不能省略关键漏点',prompts.GRADING_TASK)

    def test_fixed_prompt_budget(self):
        # v0.1.29 system + grading task + common reference + plan + type-specific
        # policy + calibration, measured without question, source material or answer.
        baselines={'summary':1852,'practical':2464,'essay':2774}
        for kind,prompt in [('summary','概括措施。'),('practical','写一则短评。'),('essay','写一篇议论文。')]:
            q=dict(kind=kind,prompt=prompt)
            if kind=='essay':q['reference_material_priority']={'available':True}
            fixed=SYSTEM+prompts.GRADING_TASK+prompts.REFERENCE_RULES+prompts.PLAN_RULES+reference_policy(q)+prompts.calibration(q)
            with self.subTest(kind=kind):self.assertLessEqual(len(fixed),baselines[kind]*.80)

    def test_compact_transport_preserves_complete_inputs_constraints_and_legacy(self):
        q=dict(kind='practical',prompt='请根据材料3写短评，不超过450字。',materials='　材料3\n原材料中的空白、标点和数字1000都须保留。',word_min=0,word_max=450)
        q['paper_spec']=paper_spec(q)
        payload=dict(question=q,answer='　　这是我的原答案。\n第二段。',answer_layout=check_paper('原答',q['paper_spec']),full_score=30,dimensions=DIMENSIONS,calibration=prompts.calibration(q))
        original=copy.deepcopy(payload);compact=compact_payload(prompts.GRADING_TASK,payload)
        self.assertEqual(payload,original)
        self.assertEqual(compact['question']['materials'],q['materials'])
        self.assertEqual(compact['question']['prompt'],q['prompt'])
        self.assertEqual(compact['answer'],payload['answer'])
        self.assertEqual(compact['calibration'],payload['calibration'])
        self.assertEqual({k:v for k,v in compact['question']['paper_spec'].items() if k!='rules'},{k:v for k,v in q['paper_spec'].items() if k!='rules'})
        self.assertEqual(compact['question']['paper_spec']['rules'],PAPER_GUIDE)
        self.assertLess(len(PAPER_GUIDE),len(RULES))
        for version,rules in [(1,'旧规则：每个数字占一格。'),(2,'人工指定的特殊纸面规则。')]:
            legacy=copy.deepcopy(payload);legacy['question']['paper_spec'].update(layout_version=version,rules=rules)
            for task in [prompts.GRADING_TASK,prompts.SYNTHESIS_TASK]:self.assertEqual(compact_payload(task,legacy)['question']['paper_spec']['rules'],rules)
        message=json.loads(request_message(prompts.GRADING_TASK,payload))
        self.assertEqual(list(message['data'])[0],'question');self.assertEqual(list(message['data'])[-1],'answer')
        self.assertEqual(payload,original)

    def test_explanation_and_summary_do_not_receive_unneeded_paper_simulation_rules(self):
        q=dict(kind='essay',prompt='写一篇议论文。',materials='材料全文。',word_min=1000,word_max=1200)
        q['paper_spec']=paper_spec(q)
        payload=dict(question=q,**reference_payload(q))
        self.assertNotIn('rules',compact_payload(prompts.SYNTHESIS_TASK,payload)['question']['paper_spec'])
        explained=compact_payload(prompts.PLAN_TASK,payload)['question']
        self.assertNotIn('paper_spec',explained);self.assertEqual(explained['materials'],q['materials']);self.assertEqual(explained['prompt'],q['prompt'])
        self.assertEqual(q['paper_spec']['rules'],RULES)


class EfficientTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_providers_keep_their_settings_and_complete_grade_in_one_call(self):
        original=httpx.AsyncClient
        providers=[('https://open.bigmodel.cn/api/paas/v4','glm-5.3'),('https://api.deepseek.com/v1','deepseek-reasoner'),('https://api.moonshot.cn/v1','kimi-k2.5')]
        q=dict(kind='practical',prompt='根据材料3提出建议，不超过450字。',materials='材料3\n独立材料。',word_max=450);q['paper_spec']=paper_spec(q)
        for base,model in providers:
            calls=[]
            def handle(request):
                body=json.loads(request.content);calls.append(body)
                self.assertEqual(set(body),{'model','messages','max_tokens' if model=='glm-5.3' else 'max_completion_tokens'})
                self.assertEqual(body.get('max_tokens') or body.get('max_completion_tokens'),65536)
                data=json.loads(body['messages'][-1]['content'])['data']
                self.assertEqual(data['question']['materials'],q['materials']);self.assertEqual(data['answer'],'完整原答案。')
                result=dict(grade(21),high_score_self_check=dict(word_count=None,used_rows=None,complies=True,format_note='已检查结构、材料与篇幅。'))
                return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result,ensure_ascii=False)}}],'usage':{'completion_tokens':1800,'completion_tokens_details':{'reasoning_tokens':500}}})
            with patch('app.llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)):
                result=await call_model(dict(protocol='openai',base_url=base,model=model,api_key='test-only',max_tokens=65536,reasoning_effort='default'),prompts.GRADING_TASK,dict(question=q,answer='完整原答案。'))
            with self.subTest(model=model):
                self.assertEqual(len(calls),1);self.assertEqual(Grade.model_validate(result).score,21)
                self.assertEqual(result.usage['reasoning_tokens'],500)
