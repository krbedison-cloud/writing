import copy
import unittest
from app.paper import paper_spec, check_paper, RULES
from app.reference_policy import reference_paper_spec, reference_payload, reference_policy, check_reference_style, ESSAY_REFERENCE_POLICY
from app.grading_prompts import calibration, ESSAY_CALIBRATION, GRADING_TASK

QUESTION=dict(kind='essay',prompt='围绕产业发展写一篇议论性文章，自拟题目，1000-1200字。',word_min=1000,word_max=1200)

def essay_fixture(body=5, size=None):
    size=size or (210 if body==5 else 173)
    starts=['产业发展需要创新与协同共同发力。','创新驱动能够增强产业韧性。','公共服务能够减轻企业转型负担。','绿色发展能够培育长期竞争优势。']
    if body==6:starts.append('协同治理能够凝聚持续发展合力。')
    starts.append('把创新、服务与绿色转型贯通起来，才能推动产业发展行稳致远。')
    return '创新协同推动产业发展\n'+'\n'.join('　　'+s+'甲'*(size-len(s)-1)+'。' for s in starts)

class EssayPolicyTests(unittest.TestCase):
    def test_calibration_is_compact_and_separate_from_generation(self):
        self.assertEqual(calibration(QUESTION),ESSAY_CALIBRATION)
        for text in ['30分','33分','35分','36–38分','38分以上','四个分论段','非保底或封顶']:self.assertIn(text,ESSAY_CALIBRATION)
        self.assertNotIn(ESSAY_CALIBRATION,GRADING_TASK)
        self.assertNotIn(ESSAY_CALIBRATION,reference_policy(QUESTION))
        self.assertLess(len(ESSAY_CALIBRATION+ESSAY_REFERENCE_POLICY),700)
        self.assertNotEqual(calibration({'kind':'practical','prompt':'请写一则短评。'}),ESSAY_CALIBRATION)
        for prompt in ['请写一份简报，字数1200字。','拟写一份通知，1200字。','请写一封信。']:
            q={'kind':'essay','prompt':prompt}
            self.assertEqual(reference_policy(q),'')
            self.assertNotEqual(calibration(q),ESSAY_CALIBRATION)

    def test_five_and_six_body_paragraphs_both_pass(self):
        for paragraphs in (5,6):
            answer=essay_fixture(paragraphs)
            style=check_reference_style(answer,QUESTION)
            self.assertTrue(style['valid'],style)
            self.assertEqual(style['body_paragraphs'],paragraphs)
            self.assertTrue(check_paper(answer,reference_paper_spec(QUESTION))['valid'])
        bad=essay_fixture().replace('甲甲','1000家',1)
        self.assertFalse(check_reference_style(bad,QUESTION)['valid'])

    def test_reference_uses_strict_minimum_and_independent_1200_cell_spec(self):
        q=dict(QUESTION,paper_spec=paper_spec(QUESTION));original=copy.deepcopy(q)
        spec=reference_paper_spec(q)
        self.assertEqual((spec['word_min'],spec['word_max'],spec['columns'],spec['rows'],spec['cells']),(1001,1200,25,48,1200))
        self.assertTrue(check_paper('甲'*1000,q['paper_spec'])['valid'])
        self.assertFalse(check_paper('甲'*1000,spec)['valid'])
        self.assertTrue(check_paper('甲'*1001,spec)['valid'])
        self.assertFalse(check_paper('甲'*1201,spec)['valid'])
        wasted=check_paper(essay_fixture(size=236),spec)
        self.assertLessEqual(wasted['word_count'],1200)
        self.assertGreater(wasted['used_rows'],48)
        self.assertFalse(wasted['valid'])
        self.assertEqual(q,original)
        self.assertNotIn('rules',reference_payload(q)['reference_paper_spec'])
        legacy=dict(q,paper_spec=dict(q['paper_spec'],layout_version=1,rules='旧占格规则'))
        self.assertEqual(reference_payload(legacy)['reference_paper_spec']['rules'],RULES)
