import copy
import unittest
from app.materials import prepare_paper,model_question,split_materials
from test_materials import MATERIALS


def paper():
    return dict(materials=MATERIALS,questions=[
        dict(number=1,kind='summary',prompt='根据资料1、2，概括措施。'),
        dict(number=2,kind='practical',prompt='根据资料3，写一则短评。'),
        dict(number=3,kind='essay',prompt='围绕创新发展写一篇议论性文章。',material_mode='all'),
        dict(number=4,kind='summary',prompt='根据资料9，概括变化。'),
    ])


class MaterialPriorityTests(unittest.TestCase):
    def test_only_earlier_question_scopes_set_priorities_without_removing_materials(self):
        original=paper();before=copy.deepcopy(original)
        original['questions'].reverse()
        prepared=prepare_paper(original)
        essay=next(q for q in prepared['questions'] if q['number']==3)
        priority=essay['reference_material_priority']
        self.assertEqual(priority['preferred_ids'],list(range(4,10)))
        self.assertEqual(priority['secondary_ids'],[1,2,3])
        self.assertEqual(priority['unresolved_prior_questions'],[])
        self.assertEqual(essay['materials'],MATERIALS)
        self.assertNotIn('reference_material_priority',model_question(essay))
        self.assertEqual(original,dict(before,questions=list(reversed(before['questions']))))
        self.assertTrue(all('reference_material_priority' not in q for q in prepared['questions'] if q['kind']!='essay'))

    def test_manual_scopes_and_exact_custom_blocks_respect_current_essay_scope(self):
        p=paper();blocks=split_materials(MATERIALS)
        p['questions'][0].update(material_mode='selected',material_ids=[2,4])
        p['questions'][1].update(material_mode='custom',custom_materials=blocks[6]['text'])
        p['questions'][2].update(material_mode='selected',material_ids=[1,4,5,7])
        q=prepare_paper(p)['questions'][2]
        self.assertEqual(q['reference_material_priority']['preferred_ids'],[1,5])
        self.assertEqual(q['reference_material_priority']['secondary_ids'],[4,7])
        self.assertNotIn('PASSAGE_2_ONLY',q['materials'])
        self.assertNotIn('PASSAGE_9_ONLY',q['materials'])
        p['questions'][0].update(material_ids=[5])
        changed=prepare_paper(p)['questions'][2]['reference_material_priority']
        self.assertEqual(changed['preferred_ids'],[1,4])
        self.assertEqual(q['reference_material_priority']['preferred_ids'],[1,5])

    def test_uncertain_ranges_or_custom_text_do_not_imply_all_materials_were_used(self):
        p=paper()
        p['questions'][0]['prompt']='概括主要措施。'
        p['questions'][1].update(material_mode='custom',custom_materials='材料3\n经过修改或截断的资料。')
        priority=prepare_paper(p)['questions'][2]['reference_material_priority']
        self.assertTrue(priority['available'])
        self.assertEqual(priority['preferred_ids'],list(range(1,10)))
        self.assertEqual(priority['secondary_ids'],[])
        self.assertEqual(priority['unresolved_prior_questions'],[1,2])
        p['questions'][2].update(material_mode='custom',custom_materials='未编号的人工材料。')
        priority=prepare_paper(p)['questions'][2]['reference_material_priority']
        self.assertFalse(priority['available']);self.assertEqual(priority['secondary_ids'],[])

    def test_all_materials_used_or_unreliable_headings_leave_no_false_exclusions(self):
        p=paper();p['questions'][0].update(material_mode='all')
        q=prepare_paper(p)['questions'][2]
        self.assertEqual(q['reference_material_priority']['preferred_ids'],[])
        self.assertEqual(q['reference_material_priority']['secondary_ids'],list(range(1,10)))
        self.assertEqual(q['materials'],MATERIALS)
        p['materials']+='\n材料1\n重复标题。'
        priority=prepare_paper(p)['questions'][2]['reference_material_priority']
        self.assertFalse(priority['available']);self.assertEqual(priority['secondary_ids'],[])
