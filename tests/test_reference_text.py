import json
import unittest
from app.reference_text import normalize_reference_text
from app.reference_policy import check_reference_style, source_references
from app.schemas import Grade, ReferenceDraft
from app.paper import paper_spec, check_paper
from test_app import grade
from test_reference_policy import COMMENT


class ReferenceTextTests(unittest.TestCase):
    def test_repeated_paragraph_wrappers_and_optional_title_are_removed(self):
        body='　　企业提出“创新为先”，完善服务体系。\n\n　　当地推广《惠企服务指南》，提高政策触达率。'
        for quotes in [lambda s:'"'+s+'"',lambda s:'\\"'+s+'\\"',lambda s:'“'+s+'”',lambda s:json.dumps(s,ensure_ascii=False)]:
            wrapped='\n\n'.join(quotes(s) for s in body.split('\n\n'))
            for title in ['', '让政策落地见效\n']:
                with self.subTest(title=title,wrapped=wrapped[:15]):
                    self.assertEqual(normalize_reference_text(title+wrapped),title+body)
                    self.assertEqual(normalize_reference_text(normalize_reference_text(title+wrapped)),title+body)

    def test_fully_encoded_document_decodes_only_proven_json_layers(self):
        body='标题\n　　主体说“创新为先”，项目路径为C:\\new\\paper。\n　　第二段保留《服务指南》。'
        for encoded in [json.dumps(body,ensure_ascii=False),json.dumps(json.dumps(body,ensure_ascii=False),ensure_ascii=False)]:
            self.assertEqual(normalize_reference_text(encoded),body)
        self.assertEqual(normalize_reference_text(None),None)

    def test_ordinary_quotations_book_titles_and_backslashes_are_untouched(self):
        values=['　　企业坚持“创新为先”，落实服务举措。\n　　书名《发展之路》与普通符号\\n不变。',
                '“创新是发展的第一动力。”\n当地据此推动技术升级，形成发展优势。',
                '“创新驱动发展。”\n“协同凝聚合力。”',
                '　　企业提出"创新为先"，路径C:\\new\\paper保留。\n　　下一段不加外包引号。',
                '"完整引用的一句话应保留。"',
                '标题\n"这是被单独引用的一段。"\n下一段应当保留原样。']
        for value in values:
            with self.subTest(value=value[:20]):self.assertEqual(normalize_reference_text(value),value)

    def test_only_generated_answer_field_is_cleaned_before_program_accounting(self):
        clean='　　甲'*1+'乙'*23+'。\n　　主体完善服务，推动政策落地见效。'
        wrapped='\n'.join('"'+line+'"' for line in clean.splitlines())
        raw=dict(grade(22),high_score_answer=wrapped,strengths=['"这条批评中的引号要保留。"'],evidence=['原答“材料1中”属于评分证据，不处理。'])
        for cls in (ReferenceDraft,Grade):
            parsed=cls.model_validate(raw)
            self.assertEqual(parsed.high_score_answer,clean)
            self.assertEqual(check_paper(parsed.high_score_answer,paper_spec({'word_max':450})),check_paper(clean,paper_spec({'word_max':450})))
        self.assertEqual(raw['high_score_answer'],wrapped)
        self.assertEqual(Grade.model_validate(raw).strengths,raw['strengths'])
        self.assertEqual(Grade.model_validate(raw).evidence,raw['evidence'])

    def test_exam_source_references_are_detected_for_every_training_bucket(self):
        samples=['材料中，某县完善公共服务。','材料3中A公司改进生产工艺。','资料二中的某企业推动绿色转型。',
                 '材料（一）显示当地形成发展合力。','从材料中可以看出，服务方式需要创新。',
                 '正如材料中所提到的，干部应改进工作。','根据给定资料，该地提升服务效能。','材料1、2的案例说明治理要协同。','材料几中介绍了典型案例。']
        for text in samples:
            with self.subTest(text=text):
                self.assertTrue(source_references(text))
                for kind,prompt in [('summary','概括措施。'),('practical','写一份建议书。'),('practical','写一则短评。'),('essay','写一篇议论性文章。')]:
                    result=check_reference_style(text,dict(kind=kind,prompt=prompt))
                    self.assertFalse(result['valid']);self.assertTrue(result['source_mentions'])

    def test_physical_material_terms_and_real_titles_do_not_trigger_source_repair(self):
        for text in ['原材料中的有害物质得到去除。','新材料中心推动技术创新。','建筑材料中的添加剂得到规范管理。',
                     '资料一体化管理提升服务效率。','企业提出“创新为先”，推动发展。','干部研读《材料中的治理智慧》，提高服务能力。']:
            with self.subTest(text=text):
                self.assertEqual(source_references(text),[])
                self.assertIsNone(check_reference_style(text,dict(kind='practical',prompt='写建议书。')))
        self.assertTrue(check_reference_style(COMMENT,dict(prompt='写一则短评。'))['valid'])
