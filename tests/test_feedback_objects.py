import copy
import unittest
from pydantic import ValidationError
from app.schemas import Grade,Synthesis,DIMENSIONS
from app.output_validation import text_list
from test_app import grade


class FeedbackObjectTests(unittest.TestCase):
    def test_glm_style_evidence_preserves_quote_analysis_and_deduction_without_rescoring(self):
        evidence={'quote':'加强建设','analysis':'缺少具体服务措施','deduction':2,'requirement':'提出明确措施','material_basis':['资料1的具体做法']}
        raw=dict(grade(21.5),evidence=[evidence]);original=copy.deepcopy(raw)
        parsed=Grade.model_validate(raw)
        self.assertEqual(parsed.score,21.5);self.assertEqual(parsed.dimensions,raw['dimensions'])
        self.assertEqual(parsed.high_score_answer,raw['high_score_answer'])
        for text in ['原答引文：加强建设','分析：缺少具体服务措施','扣分：2','题干要求：提出明确措施','材料依据：资料1的具体做法']:
            self.assertIn(text,parsed.evidence[0])
        self.assertEqual(raw,original)

    def test_chinese_aliases_mixed_lists_and_single_objects_are_readable(self):
        obj={'原文':'完善机制','扣分原因':'未展开具体责任','扣分':1}
        for shape in [[obj,'另一条原答证据。'],obj,{'1':'第一条原答证据。','2':'第二条原答证据。'}]:
            with self.subTest(shape=shape):
                value=Grade.model_validate(dict(grade(),evidence=shape))
                self.assertTrue(all(isinstance(item,str) for item in value.evidence))
                self.assertNotIn('{',value.evidence[0])
        self.assertIn('扣分原因：未展开具体责任',text_list(obj)[0])
        self.assertIn('扣分：1',text_list(obj)[0])
        for key in ['原文片段','原答摘录','用户答案','扣分点','扣分说明','originalText','original_answer','rationale','citation']:
            self.assertIn('原答有具体措施',Grade.model_validate(dict(grade(),evidence=[{key:'原答有具体措施'}])).evidence[0])

    def test_nested_feedback_keeps_all_leaves_and_existing_strings_exact(self):
        existing='　原答“加强建设”需要展开。\n保留完整句子。'
        value=dict(grade(),evidence=[existing,{'quote':'加强建设','analysis':{'problem':'对象不清','reason':['缺少主体','缺少措施']},'deduction':2}])
        parsed=Grade.model_validate(value)
        self.assertEqual(parsed.evidence[0],existing)
        for text in ['问题：对象不清','依据：缺少主体；缺少措施','扣分：2']:
            self.assertIn(text,parsed.evidence[1])
        self.assertEqual(text_list(['', '  ', '有效证据。']),['有效证据。'])

    def test_other_feedback_fields_can_use_the_same_known_narrative_shapes(self):
        value=dict(grade(),strengths=[{'point':'观点清晰','reason':'围绕题干组织'}],weaknesses=[{'issue':'要点遗漏','reason':'漏掉主体措施'}],suggestions=[{'action':'列要点','reason':'逐段核对材料'}])
        parsed=Grade.model_validate(value)
        self.assertIn('要点：观点清晰',parsed.strengths[0])
        self.assertIn('问题：要点遗漏',parsed.weaknesses[0])
        self.assertIn('行动：列要点',parsed.suggestions[0])
        result=Synthesis.model_validate(dict(analysis='针对原答补齐材料措施，提升表达的准确性。',consensus=[],disagreements=[],action_plan=[{'action':'先列措施','reason':'减少漏点'}]))
        self.assertIn('先列措施',result.action_plan[0])

    def test_unknown_objects_missing_evidence_bad_scores_and_invalid_dimensions_still_fail(self):
        for evidence in [[],[''],[{'invalid':'shape'}],[{'score':2}],[{'quote':None,'deduction':2}],[42],[None]]:
            with self.subTest(evidence=evidence),self.assertRaises(ValidationError):Grade.model_validate(dict(grade(),evidence=evidence))
        for update in [dict(score=51),dict(score=-1),dict(score=float('nan')),dict(dimensions={key:101 for key in DIMENSIONS})]:
            with self.assertRaises(ValidationError):Grade.model_validate(dict(grade(),**update))
        raw=grade();raw.pop('evidence')
        with self.assertRaises(ValidationError):Grade.model_validate(raw)
