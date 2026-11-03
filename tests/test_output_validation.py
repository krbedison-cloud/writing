import unittest
from pydantic import ValidationError
from app.schemas import Grade,Synthesis,Portrait,DIMENSIONS
from app.portrait_summary import brief_portrait
from app.service import public_error

def valid():
    return dict(score=16,dimensions={key:80 for key in DIMENSIONS},strengths=['材料依据充分'],weaknesses=[],suggestions=[],evidence=['原答提到“创新”，与材料中的产业举措相符'],high_score_answer='依据材料分类概括产业发展的关键举措，并提出明确可执行的建议。')

class OutputValidationTests(unittest.TestCase):
    def test_lossless_numeric_and_text_forms(self):
        value=valid();value.update(score='16分',strengths='材料依据充分\n表达清楚',evidence='原答案有明确材料依据')
        value['dimensions']={key:{'score':'80/100','reason':'具体说明'} for key in DIMENSIONS}
        parsed=Grade.model_validate(value)
        self.assertEqual(parsed.score,16);self.assertEqual(parsed.dimensions,{key:80 for key in DIMENSIONS})
        self.assertEqual(parsed.strengths,['材料依据充分','表达清楚'])
        self.assertEqual(parsed.evidence,['原答案有明确材料依据'])
    def test_dimension_records_without_rescaling(self):
        value=valid();value['dimensions']=[{'dimension':key,'score':'8分'} for key in DIMENSIONS]
        self.assertEqual(set(Grade.model_validate(value).dimensions.values()),{8})
        value['dimensions'].append(value['dimensions'][0])
        with self.assertRaises(ValidationError):Grade.model_validate(value)
    def test_optional_analysis_does_not_discard_valid_grade(self):
        for analysis in ['一段审题说明',{'task':'概括内容'},{'task':'概括内容','genre':'概括','requirements':None,'material_basis':[]}]:
            parsed=Grade.model_validate(dict(valid(),task_analysis=analysis))
            self.assertEqual(parsed.score,16);self.assertIsNone(parsed.task_analysis)
        parsed=Grade.model_validate(dict(valid(),task_analysis={'task':'概括内容','genre':'概括','requirements':'不超过350字','material_basis':'资料1的产业举措'}))
        self.assertEqual(parsed.task_analysis.requirements,['不超过350字'])
    def test_invalid_core_data_still_rejected_with_safe_diagnostics(self):
        for update,expected in [({'score':'not-a-score secret-value'},'总分'),({'score':float('nan')},'总分'),({'score':True},'总分'),({'dimensions':{'secret-key':80}},'六维分'),({'dimensions':{key:101 for key in DIMENSIONS}},'六维分'),({'evidence':[]},'评分证据'),({'high_score_answer':None},'高分答案')]:
            with self.subTest(update=expected):
                with self.assertRaises(ValidationError) as error:Grade.model_validate(dict(valid(),**update))
                message=public_error(error.exception)
                self.assertIn(expected,message);self.assertNotIn('secret-value',message);self.assertNotIn('secret-key',message)
        value=valid();value.pop('high_score_answer')
        with self.assertRaises(ValidationError) as error:Grade.model_validate(value)
        self.assertIn('高分答案：缺失必填字段',public_error(error.exception))
    def test_summary_empty_consensus_is_legitimate(self):
        parsed=Synthesis.model_validate(dict(analysis='教练之间暂未形成明确共识，需要逐项核对材料依据。',consensus=[],disagreements='观点不同',action_plan='重新核对题干要求'))
        self.assertEqual(parsed.consensus,[]);self.assertEqual(parsed.disagreements,['观点不同'])

    def test_summary_more_than_eight_recommendations_is_lossless(self):
        tips=[f'第{i}项具体修改建议' for i in range(1,21)]
        for value in [tips,'\n'.join(tips)]:
            parsed=Synthesis.model_validate(dict(analysis='依据原答和题干核对了材料运用上的问题。',consensus=[],disagreements=[],action_plan=value))
            self.assertEqual(parsed.action_plan,tips)
        with self.assertRaises(ValidationError):
            Synthesis.model_validate(dict(analysis='依据原答和题干核对了材料运用上的问题。',consensus=[],disagreements=[],action_plan=[{'invalid':'shape'}]))

    def test_long_portrait_is_bounded_and_deduplicated(self):
        raw=dict(analysis='趋势说明。'*200,strengths=['优势甲']*10+['优势乙'],weaknesses=['关键短板。'*50]*10,action_plan=[f'训练{i}：'+('一个具体动作。'*50) for i in range(20)])
        parsed=Portrait.model_validate(raw).model_dump()
        brief=brief_portrait(parsed)
        self.assertLessEqual(len(brief['analysis']),120)
        self.assertEqual(brief['strengths'],['优势甲','优势乙'])
        self.assertEqual(len(brief['weaknesses']),1)
        self.assertEqual(len(brief['action_plan']),3)
        self.assertTrue(all(len(t)<=60 for t in brief['action_plan']))
        self.assertEqual(parsed['analysis'],raw['analysis'])
