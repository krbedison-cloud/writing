import json
from pathlib import Path
import random
import subprocess
import unittest
from app.paper import paper_spec,check_paper,word_requirement
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
    def test_prompt_limits_override_stale_cache_and_match_browser(self):
        prompts=['不超过450字','要求：450字','请写一份450字的建议书。','不超过４５０字','字数不超过4 5 0字','四百五十字以内','标题不超过20字，正文不超过450字',
                 '不少于1000字，不超过1200字','不能少于1000字','实际字数大于1000字，不超过1200字','不足三百字','写一篇短文。']
        questions=[dict(prompt=p,word_min=0,word_max=600,kind='practical') for p in prompts]
        questions += [dict(questions[0],paper_rows=24),dict(prompt='不少于1000字',word_min=0,word_max=0,kind='essay')]
        module=str(Path(__file__).resolve().parents[1]/'app/static/paper-layout.js')
        script="const l=require(process.argv[1]);let s='';process.stdin.on('data',b=>s+=b);process.stdin.on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(s).map(q=>[l.wordRequirement(q.prompt),l.specFor(q)]))));"
        result=subprocess.run(['node','-e',script,module],input=json.dumps(questions),capture_output=True,text=True,check=True)
        for q,(limits,browser) in zip(questions,json.loads(result.stdout)):
            with self.subTest(prompt=q['prompt']):
                expected=paper_spec(q);expected.pop('rules')
                self.assertEqual(limits,word_requirement(q['prompt']))
                self.assertEqual(browser,expected)
        for q in questions[:7]:
            self.assertEqual((paper_spec(q)['rows'],paper_spec(q)['cells']),(18,450))
            self.assertEqual(q['word_max'],600)  # Caller and historical snapshots are immutable.
        self.assertEqual(paper_spec(questions[7])['word_max'],1200)
        self.assertEqual(paper_spec(questions[-2])['rows'],24)  # Explicit physical-paper override survives.
        from app.schemas import QuestionInput
        self.assertEqual(QuestionInput(number=3,kind='practical',prompt='要求不超过450字。',word_max=600).word_max,450)

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
        alphabet=['甲','乙','A','2','，','。','\n','\t',' ','　','😀','\u0301','\u200d','（','）','“','”','—','…','.','%']
        samples += [''.join(rng.choice(alphabet) for _ in range(rng.randrange(1,150))) for _ in range(100)]
        spec=paper_spec({'word_max':350})
        script="const lib=require(process.argv[1]);let s='';process.stdin.on('data',b=>s+=b);process.stdin.on('end',()=>{const d=JSON.parse(s);process.stdout.write(JSON.stringify(d.samples.map(x=>lib.check(x,d.spec))))});"
        module=str(Path(__file__).resolve().parents[1]/'app/static/paper-layout.js')
        result=subprocess.run(['node','-e',script,module],input=json.dumps({'samples':samples,'spec':spec}),capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout),[check_paper(text,spec) for text in samples])
    def test_chinese_manuscript_rules_and_legacy(self):
        spec=paper_spec({'word_max':350})
        for text,cells in [('1.',1),('2026',2),('123',2),('12.5%',3),('ABC',2),('汉字',2),('甲'*25+'，',25),('甲'*25+'。”',25),('甲'*24+'（内容）',29),('甲'*24+'——乙',28),('甲'*24+'……乙',28)]:
            with self.subTest(text=text):self.assertEqual(check_paper(text,spec)['occupied_cells'],cells)
        self.assertEqual(check_paper('1.',dict(spec,layout_version=1))['occupied_cells'],2)
        self.assertEqual(check_paper('甲'*25+'，',spec)['word_count'],26)
        self.assertEqual(check_paper('甲'*25+'\n，',spec)['occupied_cells'],26)
    def test_bad_model_self_count_does_not_discard_valid_grade(self):
        raw=dict(score=15,dimensions={k:70 for k in ['审题立意','要点提炼','材料运用','结构逻辑','语言表达','文体规范']},strengths=['优点'],weaknesses=['不足'],suggestions=['建议'],evidence=['依据'],high_score_answer='参考范文包含具体材料依据和规范表达。',high_score_self_check={'word_count':'不知道'})
        self.assertIsNone(Grade.model_validate(raw).high_score_self_check)
    def test_overflow_selection_preserves_fitting_prefix_and_unicode(self):
        spec=paper_spec({'word_max':350})
        cases=[('甲'*350+'超出',350,'甲'*350),('甲'*349+'12尾',351,'甲'*349+'12'),('1'*800,700,'1'*700),('甲'*350+'，尾',351,'甲'*350+'，'),
               ('甲'*349+'\t尾',349,'甲'*349),
               ('甲'*350+'\n\n',351,'甲'*350+'\n'),
               ('\n'*15,14,'\n'*14),
               ('😀'*350+'尾',700,'😀'*350),
               ('甲'*349+'😀\u0301尾',352,'甲'*349+'😀\u0301'),
               ('甲'*350+'\u200b尾',351,'甲'*350+'\u200b'),
               ('甲'*350,None,'甲'*350)]
        module=str(Path(__file__).resolve().parents[1]/'app/static/paper-layout.js')
        script="const lib=require(process.argv[1]);let s='';process.stdin.on('data',b=>s+=b);process.stdin.on('end',()=>{const d=JSON.parse(s);process.stdout.write(JSON.stringify(d.cases.map(([text])=>{const a=lib.analyze(text,d.spec);return [a.overflow_start,a.overflow_start===null?text:text.slice(0,a.overflow_start)];})))});"
        result=subprocess.run(['node','-e',script,module],input=json.dumps({'cases':cases,'spec':spec}),capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout),[[start,prefix] for text,start,prefix in cases])
        for text,start,prefix in cases:
            self.assertLessEqual(check_paper(prefix,spec)['occupied_cells'],spec['cells'])
            self.assertEqual(check_paper(text,spec)['overflow_cells']>0,start is not None)

if __name__=='__main__':unittest.main()
