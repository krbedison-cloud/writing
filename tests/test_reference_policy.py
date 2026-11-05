import unittest
from app.reference_policy import is_short_comment, reference_policy, check_reference_style

COMMENT = '\n'.join([
    '发展产业需要把创新与绿色转型结合起来，为持续发展积蓄动力。',
    '技术创新能够增强产业韧性。当地企业改进生产工艺，提高产品质量，拓展市场空间。',
    '协同服务能够降低转型门槛。有关部门搭建服务平台，帮助企业解决经营难题。',
    '绿色发展能够培育长期优势。园区推动资源循环利用，改善生产环境，实现集约发展。',
    '把创新、服务与绿色转型贯通起来，才能推动产业发展行稳致远。'])

class ReferencePolicyTests(unittest.TestCase):
    def test_genre_follows_task_not_training_bucket_or_materials(self):
        for prompt in ['根据资料8、9写一则短评，不超过600字。','请撰写一篇简短评论。','请写一则\n短评，不能写成简报。','请写一篇文章，文体为短评。','短评：围绕产业发展谈看法。']:
            with self.subTest(prompt=prompt):
                self.assertTrue(is_short_comment({'prompt':prompt,'kind':'summary'}))
        for prompt in ['根据材料写一份简报。','概括材料中短评的主要观点。','阅读《短评写作》，写一份工作汇报。','写一篇议论性文章。','请写一份简报，参考短评中的案例。']:
            with self.subTest(prompt=prompt):
                self.assertFalse(is_short_comment({'prompt':prompt,'kind':'practical','materials':'请写一则短评。'}))
                self.assertEqual(reference_policy({'prompt':prompt}), '')

    def test_five_paragraphs_and_optional_title(self):
        q={'prompt':'写一则短评，不超过600字。'}
        for answer in [COMMENT,'让产业发展行稳致远\n'+COMMENT]:
            checked=check_reference_style(answer,q)
            self.assertTrue(checked['valid']);self.assertEqual(checked['body_paragraphs'],5)
        self.assertFalse(check_reference_style(COMMENT.replace('\n',''),q)['valid'])
        self.assertFalse(check_reference_style(COMMENT+'\n以上举措值得坚持。',q)['valid'])
        self.assertIsNone(check_reference_style(COMMENT,{'prompt':'请写简报。'}))

    def test_quantity_forms_are_flagged_without_rejecting_indefinite_subjects(self):
        q={'prompt':'请写一则短评。'}
        for phrase in ['1000家','1,000家','１０００家','一万吨','一万余吨','三千名','100万元','1.5亿元','30%','百分之三十','提升3倍']:
            with self.subTest(phrase=phrase):
                result=check_reference_style(COMMENT.replace('当地企业','当地'+phrase+'企业'),q)
                self.assertFalse(result['valid']);self.assertTrue(result['quantities'])
        self.assertTrue(check_reference_style(COMMENT.replace('当地企业','当地一家企业'),q)['valid'])
        self.assertTrue(check_reference_style(COMMENT.replace('发展产业','从三个方面发展产业'),q)['valid'])
        self.assertTrue(check_reference_style(COMMENT.replace('动力','守护万家灯火、惠及千家万户的动力'),q)['valid'])
