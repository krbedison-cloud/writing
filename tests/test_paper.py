import json
from pathlib import Path
import random
import subprocess
import unittest
from app.paper import paper_spec,check_paper
from app.schemas import Grade

class PaperTests(unittest.TestCase):
    def test_requested_row_table_and_override(self):
        for limit,rows in [(300,12),(350,14),(500,20),(600,24),(1200,48)]:
            with self.subTest(limit=limit):
                spec=paper_spec({'kind':'summary','prompt':f'不超过{limit}字','word_max':limit})
                self.assertEqual((spec['columns'],spec['rows'],spec['cells']),(25,rows,rows*25))
        self.assertEqual(paper_spec({'word_max':300,'paper_rows':15})['rows'],15)
        self.assertEqual(paper_spec({'prompt':'字数1000-1200字','kind':'essay'})['word_min'],1000)
        self.assertEqual(paper_spec({'prompt':'不到三百字'})['word_max'],299)
        from app.pdf import word_limits
        self.assertEqual(word_limits('不足三百字'),(0,300))
        self.assertEqual(word_limits('三百五十字以内'),(0,350))
        self.assertEqual(word_limits('一千至一千二百字'),(1000,1200))
        spec=paper_spec({'prompt':'不到300字','word_max':300})
        self.assertEqual((spec['word_max'],spec['rows']),(299,12))
        self.assertFalse(check_paper('甲'*300,spec)['valid'])
    def test_wrapping_newlines_and_format_loss(self):
        spec=paper_spec({'word_max':300})
        self.assertEqual(check_paper('甲'*25,spec)['used_rows'],1)
        self.assertEqual(check_paper('甲'*25+'\n乙',spec)['occupied_cells'],26)
        check=check_paper('甲\n乙',spec)
        self.assertEqual((check['word_count'],check['occupied_cells'],check['line_break_loss']),(2,26,24))
        check=check_paper('　'*11+'倡议书\n　　正文\n某单位',spec)
        self.assertEqual(check['word_count'],8)
        self.assertGreater(check['formatting_cells'],30)
        self.assertEqual(check['used_rows'],3)
        self.assertEqual(check_paper('2026年11月2日',spec)['word_count'],10)
        self.assertEqual(check_paper('甲\u200b乙\ufeff',spec)['word_count'],2)
    def test_word_limit_alone_cannot_validate_paper_fit(self):
        spec=paper_spec({'word_max':300})
        check=check_paper('\n'.join(['甲'*10]*13),spec)
        self.assertEqual(check['word_count'],130)
        self.assertEqual(check['used_rows'],13)
        self.assertFalse(check['valid']);self.assertEqual(check['overflow_cells'],10)
    def test_python_browser_layout_parity(self):
        rng=random.Random(25)
        samples=['','甲'*25,'甲'*25+'\n','甲\n\n乙','　　标题\n落款2026年','A\u0301😀\t甲\r\n乙','\ufeff\u0085\u001c']
        alphabet=['甲','乙','A','2','，','。','\n','\t',' ','　','😀','\u0301','\u200d']
        samples += [''.join(rng.choice(alphabet) for _ in range(rng.randrange(1,150))) for _ in range(100)]
        spec=paper_spec({'word_max':350})
        script="const lib=require(process.argv[1]);let s='';process.stdin.on('data',b=>s+=b);process.stdin.on('end',()=>{const d=JSON.parse(s);process.stdout.write(JSON.stringify(d.samples.map(x=>lib.check(x,d.spec))))});"
        module=str(Path(__file__).resolve().parents[1]/'app/static/paper-layout.js')
        result=subprocess.run(['node','-e',script,module],input=json.dumps({'samples':samples,'spec':spec}),capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout),[check_paper(text,spec) for text in samples])
    def test_bad_model_self_count_does_not_discard_valid_grade(self):
        raw=dict(score=15,dimensions={k:70 for k in ['审题立意','要点提炼','材料运用','结构逻辑','语言表达','文体规范']},strengths=['优点'],weaknesses=['不足'],suggestions=['建议'],evidence=['依据'],high_score_answer='参考范文包含具体材料依据和规范表达。',high_score_self_check={'word_count':'不知道'})
        self.assertIsNone(Grade.model_validate(raw).high_score_self_check)

if __name__=='__main__':unittest.main()
