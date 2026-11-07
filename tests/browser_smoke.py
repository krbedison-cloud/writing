"""Isolated browser + real HTTP adapter smoke test. No real model credentials used.
python tests/browser_smoke.py [--pdf /path/to/exam.pdf]
Requires Playwright + a browser; CHROMIUM_EXECUTABLE may select system Chromium.
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.schemas import DIMENSIONS
from app.paper import check_paper
from app.reference_policy import is_short_comment, SHORT_COMMENT_POLICY
from test_reference_policy import COMMENT
from test_essay_policy import essay_fixture
from app.grading_prompts import ESSAY_CALIBRATION

counts={}
received_contexts=[]
received_reference_contexts=[]
class MockModel(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        data=json.loads(body['messages'][-1]['content']);task=data['task'];payload=data['data'];model=body['model']
        if model=='coach-exhausted':
            if task.startswith('独立批改'):counts[model]=counts.get(model,0)+1
            self.send_response(402);self.end_headers();self.wfile.write(b'quota exhausted');return
        if task.startswith('独立批改'):
            if model=='coach-c':time.sleep(13)
            assert (SHORT_COMMENT_POLICY in task)==is_short_comment(payload['question'])
            if payload['question']['kind']=='essay':
                assert payload['calibration']==ESSAY_CALIBRATION
                assert payload['reference_paper_spec']['word_min']==1001
            received_contexts.append(payload['question'])
            counts[model]=counts.get(model,0)+1
            if model=='coach-b' and counts[model]==1:
                self.send_response(429);self.end_headers();self.wfile.write(b'mock rate limit');return
            if model=='coach-truncated' and (body.get('max_completion_tokens') or body.get('max_tokens'))<32768:
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(json.dumps({'choices':[{'finish_reason':'length','message':{'content':'partial'}}],'usage':{'completion_tokens':6000,'completion_tokens_details':{'reasoning_tokens':5900}}}).encode());return
            result=dict(score=payload['full_score']-(4 if model=='coach-a' else 6),dimensions={k:70 for k in DIMENSIONS},strengths=['观点清晰'],weaknesses=['材料要点尚有遗漏'],suggestions=['分类概括关键措施'],evidence=['原答案“加强建设”需要展开具体措施'],high_score_answer='这是模拟接口生成的参考答案，仅用于验证调用与存档流程，不是真实模型批改。',task_analysis=dict(task=payload['question']['prompt'],genre='按本题题干审题',requirements=[payload['question']['prompt']],material_basis=[payload['question']['materials'][:80]]))
        elif task.startswith(('范文校正','单独重写高分范文')):
            assert (SHORT_COMMENT_POLICY in task)==is_short_comment(payload['question'])
            received_reference_contexts.append(payload['question'])
            spec=payload.get('reference_paper_spec') or payload['question']['paper_spec'];header='模拟接口生成的参考范文'
            target=payload.get('target_word_count') or max(spec['word_min'],round((spec['word_max'] or spec['cells'])*.8))
            target=max(len(header)+10,min(target,(spec['word_max'] or spec['cells'])-25))
            target=max(target,spec['word_min'])
            answer=header+'\n　　'+'甲'*(target-len(header))
            if is_short_comment(payload['question']):answer=COMMENT
            elif payload['question']['kind']=='essay':answer=essay_fixture(6)
            check=check_paper(answer,spec)
            result=dict(high_score_answer=answer,high_score_self_check=dict(word_count=check['word_count'],used_rows=check['used_rows'],complies=check['valid'],format_note='已检查标题、段首缩进和换行占格'))
        elif task.startswith('识别试卷'):
            from app.pdf import parse_text
            result=parse_text(payload['text'],payload['filename']);result['year']=int(result['year'])
            for q in result['questions']:
                q['kind']={'summary':'概括题','practical':'应用文题','essay':'大作文题'}[q['kind']]
                q['word_max']=str(q['word_max'])+'字'
            result={'data':result}
        elif task.startswith('汇总'):
            result=dict(analysis='模拟汇总：各模型独立批改已完成，建议加强材料要点的分类与表达。',consensus=['需要更加具体的材料支撑'],disagreements=['评分存在一定分差'],action_plan=[f'训练{i}：先列出要点，再用短句组织' for i in range(12)])
        elif task.startswith('基于全部'):
            result=dict(analysis='模拟历史画像：已分析全部历史摘要，当前应加强材料运用与结构表达。'*50,strengths=['能够持续完成练习']*12,weaknesses=['要点提炼有待加强']*12,action_plan=[f'训练{i}：每天复盘一个遗漏要点' for i in range(12)])
        elif task.startswith('重写能力观察'):
            result=dict(analysis='材料运用仍需加强，先补齐要点再精简表达。',strengths=['能够坚持完成练习。'],weaknesses=['材料中的具体措施仍有遗漏。'],action_plan=['作答前列出各段措施，写完后逐项核对。','每次复盘一个遗漏要点，并重新写进答案。'])
        else:result={'ok':True}
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
        self.wfile.write(json.dumps({'choices':[{'message':{'content':json.dumps(result,ensure_ascii=False)},'finish_reason':'stop'}]}).encode())

def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pdf');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    mock=ThreadingHTTPServer(('127.0.0.1',0),MockModel);threading.Thread(target=mock.serve_forever,daemon=True).start()
    port=free_port();base=f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory() as temp:
        env=dict(os.environ,DATA_DIR=temp,APP_PASSWORD='',ALLOWED_HOSTS='localhost,127.0.0.1')
        proc=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                try:
                    if httpx.get(base+'/health').status_code==200:break
                except httpx.ConnectError:pass
                time.sleep(.1)
            else:raise RuntimeError('server did not start')
            from playwright.sync_api import sync_playwright, expect
            with sync_playwright() as pw:
                executable=os.getenv('CHROMIUM_EXECUTABLE') or shutil.which('chromium')
                browser=pw.chromium.launch(headless=True,executable_path=executable,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1080})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.add_init_script("window.__csp=[];document.addEventListener('securitypolicyviolation',e=>window.__csp.push(e.violatedDirective));")
                page.goto(base+'/#models')
                for model in ['coach-a','coach-b']:
                    page.locator('[data-action="add-model"]').first.click()
                    page.locator('[name="name"]').fill(model)
                    page.locator('[name="base_url"]').fill(f'http://127.0.0.1:{mock.server_port}/v1')
                    page.locator('[name="model"]').fill(model)
                    page.locator('[name="api_key"]').fill('mock-only')
                    expect(page.locator('[name="timeout_seconds"]')).to_have_value('600')
                    page.locator('#model-form button[type="submit"]').click()
                    expect(page.locator('#editor')).not_to_be_visible()
                    expect(page.locator('.model-card')).to_have_count(1 if model=='coach-a' else 2)
                # Per-model timeout survives an edit/reload and does not require re-entering the key.
                coach_b=page.locator('.model-card').filter(has=page.get_by_role('heading',name='coach-b',exact=True))
                coach_b.locator('[data-action="edit-model"]').click()
                page.locator('[name="timeout_seconds"]').fill('900')
                page.locator('#model-form button[type="submit"]').click()
                expect(page.locator('#editor')).not_to_be_visible()
                page.reload();expect(page.locator('.model-card')).to_have_count(2)
                coach_b=page.locator('.model-card').filter(has=page.get_by_role('heading',name='coach-b',exact=True))
                expect(coach_b).to_contain_text('等待时限 900 秒')
                coach_b.locator('[data-action="edit-model"]').click()
                expect(page.locator('[name="timeout_seconds"]')).to_have_value('900')
                page.locator('[data-action="close-dialog"]').click()
                import fitz
                doc=fitz.open();p=doc.new_page()
                p.insert_text((30,40),'2025年浙江省考（B卷）\n给定材料\n材料1\n独立材料甲。\n材料2\n独立材料乙。\n材料3\n独立材料丙。\n作答要求\n第一题\n请概括主要措施，不超过300字。\n第二题\n请写一则短评，不超过600字。',fontname='china-s',fontsize=12)
                scope_pdf=Path(temp)/'scope.pdf';doc.save(scope_pdf);doc.close()
                page.locator('#pdf-input').set_input_files(str(scope_pdf))
                page.wait_for_url('**/#paper/*');expect(page.locator('#editor')).to_be_visible()
                scopeid=page.url.split('/')[-1]
                first=page.locator('.edit-q').first
                expect(first.locator('[name="material_mode"]')).to_have_value('')
                first.locator('[data-range-start]').select_option('2');first.locator('[data-range-end]').select_option('3')
                first.locator('[data-action="apply-material-range"]').click()
                expect(first.locator('[name="material_mode"]')).to_have_value('selected')
                expect(first.locator('[name="material_id"]:checked')).to_have_count(2)
                page.locator('.edit-q').nth(1).locator('[name="material_mode"]').select_option('all')
                page.locator('[name="confirmed"]').check();page.locator('#paper-form button[type="submit"]').click()
                expect(page.locator('#editor')).not_to_be_visible()
                scoped=httpx.get(base+'/api/papers/'+scopeid).json()
                assert scoped['questions'][0]['material_scope']['matched_ids']==[2,3]
                assert scoped['questions'][1]['material_scope']['mode']=='all'
                assert not scoped['requires_material_selection']
                page.once('dialog',lambda dialog:dialog.accept())
                page.locator('[data-action="classify-paper"]').click()
                expect(page.get_by_text('第 1 题',exact=True)).to_be_visible()
                for _ in range(100):
                    jobs=httpx.get(base+'/api/jobs').json()
                    job=next((j for j in jobs if j['kind']=='classification' and j['target_id']==scopeid),None)
                    if job and job['status'] not in ('queued','running'):break
                    time.sleep(.1)
                assert job['status']=='completed',job
                scoped=httpx.get(base+'/api/papers/'+scopeid).json()
                assert scoped['questions'][0]['material_ids']==[2,3]
                assert not scoped['confirmed']
                page.goto(base+'/#papers');expect(page.locator('#paper-list')).to_be_visible()
                if args.pdf:
                    pdf=Path(args.pdf)
                else:
                    # Chinese text rendering is irrelevant to extraction; insert_text uses a CJK font.
                    import fitz
                    doc=fitz.open();p=doc.new_page()
                    p.insert_text((30,40),'2026年浙江省考（A卷）\n给定材料\n材料1\n企业加强创新，推动绿色产业发展。\n作答要求\n第一题\n概括材料主要措施，不超过350字。\n第二题\n写一则短评，字数不超过600字。\n第三题\n写一篇议论性文章，1000-1200字。',fontname='china-s',fontsize=12)
                    pdf=Path(temp)/'2026年浙江省考（A卷）.pdf';doc.save(pdf);doc.close()
                page.locator('#pdf-input').set_input_files(str(pdf))
                page.wait_for_url('**/#paper/*');expect(page.get_by_text('第 1 题',exact=True)).to_be_visible()
                if not page.locator('#editor').is_visible():page.locator('[data-action="edit-paper"]').first.click()
                for select in page.locator('[name="material_mode"]').all():
                    if select.input_value()=='':select.select_option('all')
                page.locator('[name="confirmed"]').check();page.locator('#paper-form button[type="submit"]').click()
                expect(page.locator('#editor')).not_to_be_visible()
                paperid=page.url.split('/')[-1]
                expected=httpx.get(base+'/api/papers/'+paperid).json()
                # Edit a manual scope and restore automatic matching through the actual UI.
                page.locator('[data-action="edit-paper"]').first.click()
                first=page.locator('.edit-q').first
                first.locator('[name="material_mode"]').select_option('custom')
                first.locator('[name="custom_materials"]').fill('MANUAL_PASSAGE_ONLY 这是仅用于第一题的校对材料。')
                page.locator('#paper-form button[type="submit"]').click()
                expect(page.locator('#editor')).not_to_be_visible()
                page.locator('a[href^="#practice/"]').first.click()
                expect(page.locator('#question-materials')).to_have_text('MANUAL_PASSAGE_ONLY 这是仅用于第一题的校对材料。')
                page.goto(base+'/#paper/'+paperid)
                page.locator('[data-action="edit-paper"]').first.click()
                page.locator('.edit-q').first.locator('[name="material_mode"]').select_option('auto')
                page.locator('#paper-form button[type="submit"]').click()
                expect(page.locator('#editor')).not_to_be_visible()
                expected=httpx.get(base+'/api/papers/'+paperid).json()
                if args.pdf:
                    assert [q['material_scope']['matched_ids'] for q in expected['questions']]==[[3],[8,9],list(range(1,10))]
                answers=[]
                for index in range(3):
                    if index:page.goto(base+'/#paper/'+paperid)
                    page.locator('a[href^="#practice/"]').nth(index).click()
                    assert re.sub(r'\s+','',page.locator('#question-materials').text_content())==re.sub(r'\s+','',expected['questions'][index]['materials'])
                    if index==0:
                        expect(page.locator('#practice-stopwatch output')).to_be_visible()
                        page.locator('[aria-label="自动暂停时长"]').select_option('3')
                        page.locator('#answer').focus()
                        page.evaluate('practiceTimer.clock.lastActivity=performance.now()-180001')
                        expect(page.locator('.practice-pause')).to_be_visible()
                        frozen=page.evaluate('practiceTimer.clock.elapsed')
                        page.wait_for_timeout(350)
                        assert page.evaluate('practiceTimer.clock.elapsed')==frozen
                        page.keyboard.press('x')
                        expect(page.locator('.practice-pause')).not_to_be_visible()
                        assert 'x' in page.locator('#answer').input_value()
                        page.locator('#answer').fill('　　')
                        page.evaluate('practiceTimer.clock.lastActivity=performance.now()-180001')
                        expect(page.locator('.practice-pause')).to_be_visible()
                        page.mouse.move(10,10)
                        expect(page.locator('.practice-pause')).not_to_be_visible()
                        page.wait_for_timeout(1100)
                        page.reload();expect(page.locator('#practice-stopwatch')).to_be_visible()
                        assert page.evaluate('practiceTimer.clock.elapsed')>=1000
                        expect(page.locator('[aria-label="自动暂停时长"]')).to_have_value('3')
                        assert page.locator('.practice-pause').count()==1
                        page.locator('[data-timer-reset]').click()
                        expect(page.locator('#practice-stopwatch output')).to_have_text('00:00:00')
                        expect(page.locator('#highlight-count')).to_contain_text('已保存')
                        assert page.locator('.paper-row').count()==expected['questions'][0]['paper_spec']['rows']
                        assert page.locator('.paper-row').first.locator('.paper-cell').count()==25
                        assert page.locator('#answer').input_value()=='　　'
                        # Select source text and apply the default bright-yellow annotation.
                        page.evaluate("""() => {const p=document.querySelector('#question-materials p');const w=document.createTreeWalker(p,NodeFilter.SHOW_TEXT);const n=w.nextNode();const r=document.createRange();r.setStart(n,0);r.setEnd(n,Math.min(12,n.length));const s=getSelection();s.removeAllRanges();s.addRange(r);} """)
                        expect(page.locator('#material-toolbar')).to_be_visible()
                        expect(page.locator('#highlight-color')).to_have_value('#ffff00')
                        page.locator('[data-highlight-save]').click()
                        expect(page.locator('mark.material-mark')).to_have_count(1)
                        assert page.locator('mark.material-mark').evaluate('el=>getComputedStyle(el).backgroundColor')=='rgb(255, 255, 0)'
                        page.reload();page.wait_for_selector('mark.material-mark')
                        page.locator('mark.material-mark').click()
                        page.locator('#highlight-color').fill('#ff8800')
                        page.locator('[data-highlight-save]').click()
                        expect(page.locator('mark.material-mark')).to_have_attribute('data-color','#ff8800')
                        assert page.locator('mark.material-mark').evaluate('el=>getComputedStyle(el).backgroundColor')=='rgb(255, 136, 0)'
                        # Selecting existing highlighted text offers removal instead of a duplicate highlight.
                        page.evaluate("""() => {document.activeElement.blur();const n=document.querySelector('mark.material-mark').firstChild;const r=document.createRange();r.setStart(n,1);r.setEnd(n,Math.min(6,n.length));const s=getSelection();s.removeAllRanges();s.addRange(r);} """)
                        expect(page.locator('[data-highlight-delete]')).to_be_visible()
                        expect(page.locator('[data-highlight-save]')).not_to_be_visible()
                        page.locator('[data-highlight-delete]').click()
                        expect(page.locator('mark.material-mark')).to_have_count(0)
                        page.reload();expect(page.locator('#highlight-count')).to_contain_text('已保存 0 处')
                        # A mixed selection spanning two annotations removes both selected annotations.
                        for start,end in [(0,4),(8,12)]:
                            page.evaluate("""([start,end])=>{document.activeElement.blur();const n=document.querySelector('#question-materials p').firstChild;const w=document.createTreeWalker(document.querySelector('#question-materials p'),NodeFilter.SHOW_TEXT);let at=0,a,b;while(w.nextNode()){const t=w.currentNode;if(at<=start&&at+t.length>start)a=[t,start-at];if(at<end&&at+t.length>=end)b=[t,end-at];at+=t.length;}const r=document.createRange();r.setStart(...a);r.setEnd(...b);const s=getSelection();s.removeAllRanges();s.addRange(r);} """,[start,end])
                            expect(page.locator('[data-highlight-save]')).to_be_visible();page.locator('[data-highlight-save]').click()
                            expect(page.locator('mark.material-mark')).to_have_count(1 if start==0 else 2)
                            expect(page.locator('#material-toolbar')).not_to_be_visible()
                        expect(page.locator('mark.material-mark')).to_have_count(2)
                        page.evaluate("""()=>{document.activeElement.blur();const marks=document.querySelectorAll('mark.material-mark');const r=document.createRange();r.setStart(marks[0].firstChild,0);r.setEnd(marks[1].firstChild,marks[1].firstChild.length);const s=getSelection();s.removeAllRanges();s.addRange(r);} """)
                        expect(page.locator('[data-highlight-delete]')).to_be_visible();expect(page.locator('[data-highlight-save]')).not_to_be_visible()
                        page.locator('[data-highlight-delete]').click();expect(page.locator('mark.material-mark')).to_have_count(0)
                        # The edge drawer opens on hover and releases its width when the mouse leaves.
                        page.set_viewport_size({'width':1440,'height':900})
                        page.mouse.move(800,70)
                        expect(page.locator('#navigation-drawer')).to_have_attribute('data-open','false')
                        page.locator('#nav-reveal').hover();expect(page.locator('#nav-reveal')).to_have_attribute('aria-expanded','true')
                        page.wait_for_timeout(300)
                        assert abs(page.locator('.sidebar').bounding_box()['x'])<1
                        page.mouse.move(800,70);expect(page.locator('#nav-reveal')).to_have_attribute('aria-expanded','false')
                        page.wait_for_timeout(300)
                        assert page.locator('.sidebar').bounding_box()['x']+page.locator('.sidebar').bounding_box()['width']<=1
                        # Native typing, formatting, undo, paste and IME confirmation keep the plain-text source.
                        page.locator('#answer').fill('标题\n　　正文ABC123。')
                        page.locator('#answer').evaluate('el=>el.setSelectionRange(1,1)')
                        page.locator('[data-paper-format="center"]').click()
                        assert page.locator('#answer').input_value().startswith('　'*11+'标题')
                        page.locator('#answer').press('Control+z')
                        expect(page.locator('#answer')).to_have_value('标题\n　　正文ABC123。')
                        page.locator('#answer').fill('正文');page.locator('#answer').press('End');page.locator('#answer').press('Enter');page.keyboard.insert_text('换段内容')
                        expect(page.locator('#answer')).to_have_value('正文\n　　换段内容')
                        page.context.grant_permissions(['clipboard-read','clipboard-write'])
                        page.evaluate("navigator.clipboard.writeText('粘贴中文\\n　　第二段')")
                        page.locator('#answer').fill('');page.locator('#answer').press('Control+v')
                        expect(page.locator('#answer')).to_have_value('粘贴中文\n　　第二段')
                        page.locator('#answer').evaluate("""el=>{el.value='拼音';el.setSelectionRange(2,2);el.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true}));el.dispatchEvent(new InputEvent('input',{data:'拼音',inputType:'insertCompositionText',isComposing:true,bubbles:true}));el.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}));}""")
                        expect(page.locator('#answer')).to_have_value('拼音')
                        page.locator('#answer').fill('甲\n'*15)
                        expect(page.locator('#paper-warning')).to_contain_text('纸面超限')
                        assert page.locator('.paper-overflow').count()>0
                        # A long plain-text draft must remain selectable as a single range in grid mode.
                        capacity=expected['questions'][0]['paper_spec']['cells']
                        kept='甲'*capacity;overlong=kept+'乙'*75
                        page.locator('[data-paper-mode]').click()
                        expect(page.locator('#paper-editor')).to_have_attribute('data-mode','plain')
                        page.locator('#answer').fill(overlong)
                        page.locator('[data-paper-mode]').click()
                        expect(page.locator('#paper-editor')).to_have_attribute('data-mode','grid')
                        first=page.locator(f'.paper-cell[data-cell="{capacity}"]').bounding_box()
                        last=page.locator(f'.paper-cell[data-cell="{len(overlong)-1}"]').bounding_box()
                        # Start again on the caret's native-input overlay, not only on an uncovered cell.
                        page.mouse.click(first['x']+1,first['y']+first['height']/2)
                        page.mouse.move(first['x']+1,first['y']+first['height']/2);page.mouse.down()
                        page.mouse.move(last['x']+last['width']+5,last['y']+last['height']/2,steps=20);page.mouse.up()
                        selected=page.locator('#answer').evaluate('el=>[el.selectionStart,el.selectionEnd]')
                        assert selected==[capacity,len(overlong)],selected
                        page.keyboard.press('Backspace');expect(page.locator('#answer')).to_have_value(kept)
                        page.locator('#answer').press('Control+z');expect(page.locator('#answer')).to_have_value(overlong)
                        # One action selects the entire overflow, including Unicode and empty-line format loss.
                        for text,prefix in [(overlong,kept),('😀'*capacity+'尾😀','😀'*capacity),(kept+'\n\n',kept+'\n'),('甲\n'*(capacity//25+1),'甲\n'*(capacity//25))]:
                            page.locator('[data-paper-mode]').click();page.locator('#answer').fill(text);page.locator('[data-paper-mode]').click()
                            page.locator('[data-paper-select-overflow]').click()
                            assert page.locator('#answer').evaluate('el=>el.value.slice(el.selectionStart,el.selectionEnd)')==text[len(prefix):]
                            assert page.locator('.paper-selected').count()>0
                            for theme in ['night','paper']:
                                page.evaluate('theme=>document.documentElement.dataset.readingTheme=theme',theme)
                                assert page.locator('.paper-overflow.paper-selected').first.evaluate('el=>getComputedStyle(el).backgroundColor')=='rgb(165, 200, 237)'
                            page.keyboard.press('Backspace');expect(page.locator('#answer')).to_have_value(prefix)
                            expect(page.locator('[data-paper-select-overflow]')).not_to_be_visible()
                            expect(page.locator('#paper-warning')).not_to_be_visible()
                            page.locator('#answer').press('Control+z');expect(page.locator('#answer')).to_have_value(text)
                        # Shift-click can select across the entire overflow; selecting all still uses the native editor.
                        page.locator('#answer').fill(overlong)
                        page.locator(f'.paper-cell[data-cell="{capacity}"]').click(position={'x':1,'y':2})
                        assert page.locator('#answer').evaluate('el=>[el.selectionStart,el.selectionEnd]')==[capacity,capacity]
                        cell=page.locator(f'.paper-cell[data-cell="{len(overlong)-1}"]');bounds=cell.bounding_box()
                        cell.click(position={'x':bounds['width']-1,'y':2},modifiers=['Shift'])
                        selected=page.locator('#answer').evaluate('el=>[el.selectionStart,el.selectionEnd]')
                        assert selected==[capacity,len(overlong)],selected
                        page.keyboard.press('Backspace');expect(page.locator('#answer')).to_have_value(kept)
                        page.locator('#answer').press('Control+a');page.keyboard.press('Backspace');expect(page.locator('#answer')).to_have_value('')
                    if index==0:
                        for text,cell_index,visible in [('1.措施2026',0,'1.'),('甲'*25+'，乙',24,'甲，'),('甲'*24+'（内容）',25,'（'),('甲'*24+'——乙',25,'—')]:
                            page.locator('#answer').fill(text)
                            expect(page.locator(f'.paper-cell[data-cell="{cell_index}"]')).to_have_text(visible)
                        # Hanging punctuation must not shrink the last Chinese character or its punctuation.
                        for suffix in ['，','。','；','！”','。”）']:
                            page.locator('#answer').fill('甲'*25+suffix+'乙')
                            last=page.locator('.paper-cell[data-cell="24"]')
                            expect(last.locator('.paper-hanging-base')).to_have_text('甲')
                            expect(last.locator('.paper-hanging-punctuation')).to_have_text(suffix)
                            expect(page.locator('.paper-cell[data-cell="25"]')).to_have_text('乙')
                            metrics=last.evaluate("""el=>{const base=el.querySelector('.paper-hanging-base'),punct=el.querySelector('.paper-hanging-punctuation'),normal=el.parentElement.querySelector('.paper-cell'),b=base.getBoundingClientRect(),p=punct.getBoundingClientRect(),v=document.querySelector('#paper-viewport').getBoundingClientRect(),m=new DOMMatrix(getComputedStyle(punct).transform);return {same:getComputedStyle(base).fontSize===getComputedStyle(normal).fontSize&&getComputedStyle(punct).fontSize===getComputedStyle(normal).fontSize,unscaled:m.a===1&&m.d===1,separate:p.left>=b.right-.5,visible:p.right<=v.right+.5};}""")
                            assert all(metrics.values()),metrics
                        page.locator('#paper-surface').screenshot(path='/tmp/shenlun-full-size-punctuation.png')
                        page.locator('#answer').fill('1.措施')
                        cell=page.locator('.paper-cell[data-cell="0"]');bounds=cell.bounding_box()
                        cell.click(position={'x':bounds['width']/2,'y':bounds['height']/2})
                        page.keyboard.insert_text('2');expect(page.locator('#answer')).to_have_value('12.措施')
                        page.locator('#answer').fill('1.推进数字化，2026年继续实施。\n　　'+'甲'*23+'，下一行正文。')
                        page.screenshot(path='/tmp/shenlun-chinese-paper.png',full_page=True)
                    # Every row and all 25 columns fit beside the materials, including the 48-row essay.
                    for width,height in [(1280,800),(1440,900),(1920,1080)]:
                        page.set_viewport_size({'width':width,'height':height})
                        page.wait_for_timeout(150)
                        geometry=page.evaluate("""()=>{const v=document.querySelector('#paper-viewport'),b=v.getBoundingClientRect();return {fits:[...document.querySelectorAll('#paper-grid .paper-cell')].every(c=>{const r=c.getBoundingClientRect();return r.left>=b.left-1&&r.right<=b.right+1&&r.top>=b.top-1&&r.bottom<=b.bottom+1&&r.bottom<=innerHeight;}),scrollX:v.scrollWidth-v.clientWidth,scrollY:v.scrollHeight-v.clientHeight,body:document.documentElement.scrollWidth<=innerWidth,square:(()=>{const r=document.querySelector('.paper-cell').getBoundingClientRect();return Math.abs(r.width-r.height)<.1;})()};}""")
                        assert geometry['fits'] and geometry['scrollX']<=1 and geometry['scrollY']<=1 and geometry['body'] and geometry['square'],(index,width,height,geometry)
                        assert abs(page.locator('.reading-pane').bounding_box()['y']-page.locator('.writing-pane').bounding_box()['y'])<1
                    page.set_viewport_size({'width':1440,'height':900})
                    if index==2:
                        page.locator('#answer').fill('文'*expected['questions'][index]['paper_spec']['cells'])
                        assert int(page.locator('#paper-editor').get_attribute('data-paper-columns'))>=2
                        cell=page.locator('.paper-page-start').nth(1).locator('.paper-cell').first
                        offset=int(cell.get_attribute('data-cell'));cell.click(position={'x':2,'y':2});page.keyboard.insert_text('标')
                        assert page.locator('#answer').input_value()[offset]=='标'
                        page.screenshot(path='/tmp/shenlun-composition-overflow-tested.png',full_page=True)
                    answer='加强基础建设，推动绿色创新；完善产业配套，提升竞争优势。'+str(index)
                    page.locator('#answer').fill(answer)
                    if index==2:
                        page.screenshot(path='/tmp/shenlun-composition-essay-tested.png',full_page=True)
                    if index==0:
                        assert page.locator('#question-materials').evaluate('el=>getComputedStyle(el).fontSize')=='20px'
                        page.locator('#reading-menu > summary').click()
                        page.locator('#reading-size').select_option('24')
                        page.locator('#reading-leading').select_option('2.2')
                        page.locator('#reading-font').select_option('serif')
                        page.locator('#reading-theme').select_option('night')
                        page.locator('#reading-layout').select_option('stacked')
                        expect(page.locator('#answer')).to_have_value(answer)
                        assert page.locator('#answer').evaluate('el=>getComputedStyle(el).fontSize')=='24px'
                        assert abs(float(page.locator('#question-materials').evaluate('el=>parseFloat(getComputedStyle(el).lineHeight)'))-52.8)<.1
                        page.locator('#reading-menu > summary').click()
                        page.reload();expect(page.locator('#answer')).to_have_value(answer)
                        expect(page.locator('html')).to_have_attribute('data-reading-size','24')
                        expect(page.locator('html')).to_have_attribute('data-reading-theme','night')
                        assert page.locator('.writing-pane').bounding_box()['y']>page.locator('.reading-pane').bounding_box()['y']
                        page.locator('.page-head [data-action="toggle-focus"]').click()
                        expect(page.locator('.sidebar')).not_to_be_visible()
                        page.locator('#reading-menu > summary').click()
                        page.locator('[data-action="reader-reset"]').click()
                        page.locator('#reading-menu > summary').click()
                        expect(page.locator('#answer')).to_have_value(answer)
                        expect(page.locator('html')).to_have_attribute('data-reading-size','20')
                        page.screenshot(path='/tmp/shenlun-reader-browser-tested.png',full_page=True)
                        page.keyboard.press('Escape')
                        expect(page.locator('.sidebar')).to_be_visible()
                        # Reading controls and long Chinese passages must fit narrow and wide screens.
                        for width in [390,1280,1920]:
                            page.set_viewport_size({'width':width,'height':1000})
                            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
                        page.set_viewport_size({'width':1440,'height':1080})
                    page.reload();expect(page.locator('#answer')).to_have_value(answer)
                    page.locator('.grading-settings > summary').click()
                    page.locator('input[name="provider"]').nth(0).check();page.locator('input[name="provider"]').nth(1).check()
                    expect(page.locator('#grading-summary')).to_contain_text('2')
                    page.locator('#answer-form button[type="submit"]').click()
                    page.wait_for_url('**/#attempt/*')
                    if index==0:
                        expect(page.get_by_text('仅成功模型参与均分。',exact=False)).to_be_visible(timeout=20000)
                        retry=page.locator('[data-action="retry-coach"]')
                        expect(retry).to_have_count(1)
                        expect(page.locator('.score-card').filter(has=page.get_by_text('coach-a',exact=True)).locator('[data-action="retry-coach"]')).to_have_count(0)
                        failed_id=retry.get_attribute('data-provider');good_calls=counts['coach-a'];bad_calls=counts['coach-b']
                        page.evaluate("window.__retryPaper=document.querySelector('.paper-preview')")
                        with page.expect_request(lambda request:request.method=='POST' and request.url.endswith('/retry')) as sent:
                            retry.click()
                        assert sent.value.post_data_json=={'provider_id':failed_id}
                    expect(page.locator('[data-action="retry"]')).to_have_count(0,timeout=20000)
                    expect(page.locator('[data-action="retry-coach"]')).to_have_count(0,timeout=20000)
                    if index==0:
                        assert counts['coach-a']==good_calls and counts['coach-b']==bad_calls+1,counts
                        assert page.evaluate("window.__retryPaper===document.querySelector('.paper-preview')")
                    expect(page.locator('#review-body')).to_contain_text('本题审题',timeout=20000)
                    # Ensure both results have arrived before moving to the next question.
                    aid=page.url.split('/')[-1]
                    for _ in range(100):
                        a=httpx.get(base+'/api/attempts/'+aid).json()
                        if a['status']=='completed':break
                        time.sleep(.1)
                    assert a['status']=='completed',a
                    assert a['answer']==answer
                    assert a['mean_score']==[15,25,45][index]
                    answers.append(a)
                    if index==0:
                        # Re-generation changes only the selected model's reference, not its score or original answer.
                        original_reference=a['grades'][0]['result']['high_score_answer']
                        page.locator('[data-action="regenerate-reference"][data-direction="more"]').click()
                        expect(page.locator('.reference-version option')).to_have_count(2,timeout=20000)
                        page.locator('[data-action="regenerate-reference"][data-direction="less"]').click()
                        expect(page.locator('.reference-version option')).to_have_count(3,timeout=20000)
                        updated=httpx.get(base+'/api/attempts/'+aid).json()
                        assert updated['mean_score']==a['mean_score'] and updated['answer']==a['answer']
                        assert updated['grades'][0]['result']['high_score_answer']==original_reference
                        assert updated['grades'][0]['references'][-1]['layout_check']['valid']
                        page.evaluate("""()=>{const host=document.createElement('div');host.id='punctuation-preview-check';host.className='reference-paper-target';host.append(GridPaper.preview('甲'.repeat(25)+'。”乙',state.attempt.paper_spec));document.querySelector('#main').append(host);}""")
                        preview=page.locator('#punctuation-preview-check .paper-hanging')
                        expect(preview.locator('.paper-hanging-base')).to_have_text('甲')
                        assert preview.evaluate("el=>getComputedStyle(el.querySelector('.paper-hanging-base')).fontSize===getComputedStyle(el.parentElement.querySelector('.paper-cell')).fontSize")
                        assert page.locator('#punctuation-preview-check').evaluate('el=>el.scrollWidth<=el.clientWidth+1')
                        page.locator('#punctuation-preview-check').evaluate('el=>el.remove()')
                        expect(page.locator('.reference-paper-target')).to_be_visible()
                        expect(page.locator('.paper-preview .paper-row')).to_have_count(expected['questions'][0]['paper_spec']['rows'])
                        page.locator('.reference-version').select_option('original')
                        expect(page.locator('.answer-reference')).to_have_text(original_reference)
                        page.locator('[data-action="review-tab"]').nth(1).click()
                        page.locator('[data-action="regenerate-reference"][data-direction="more"]').click()
                        expect(page.locator('.reference-version option')).to_have_count(2,timeout=20000)
                        expect(page.locator('[data-action="review-tab"].active')).to_contain_text('coach-b')
                    if index==1 and is_short_comment(expected['questions'][index]):
                        expect(page.get_by_text('短评形式检查：通过',exact=True)).to_be_visible()
                        before=httpx.get(base+'/api/attempts/'+a['id']).json()
                        selected_provider=page.locator('[data-action="regenerate-reference"][data-direction="rewrite"]').get_attribute('data-provider')
                        page.locator('[data-action="regenerate-reference"][data-direction="rewrite"]').click()
                        expect(page.locator('.reference-version option')).to_have_count(3,timeout=20000)
                        refreshed=httpx.get(base+'/api/attempts/'+a['id']).json()
                        new_grade=next(g for g in refreshed['grades'] if g['provider_id']==selected_provider)
                        old_grade=next(g for g in before['grades'] if g['provider_id']==selected_provider)
                        assert new_grade['result']==old_grade['result']
                        assert new_grade['references'][-1]['style_check']['valid']
                        assert new_grade['references'][-1]['answer']==COMMENT
                    if index==2:
                        expect(page.get_by_text('大作文形式检查：通过',exact=True)).to_be_visible()
                        expect(page.get_by_text('大作文训练校准：',exact=False)).to_be_visible()
                        latest=a['grades'][0]['references'][-1]
                        assert latest['layout_check']['valid']
                        assert 1000<latest['layout_check']['word_count']<=1200
                        assert latest['layout_check']['used_rows']<=48
                        assert latest['style_check']['body_paragraphs']==6
                        assert latest['paper_spec']['word_min']==1001
                        expect(page.locator('.reference-paper-target .paper-row')).to_have_count(48)
                for context in received_contexts:
                    assert context['materials']==expected['questions'][context['number']-1]['materials']
                    assert 'MANUAL_PASSAGE_ONLY' not in json.dumps(context)
                    assert 'material_blocks' not in context
                for context in received_reference_contexts:
                    assert context['materials']==expected['questions'][context['number']-1]['materials']
                assert counts['coach-a']==3,counts
                assert counts['coach-b']==4,counts
                page.goto(base+'/#profile');expect(page.locator('.forecast-total')).to_contain_text('85',timeout=20000)
                expect(page.get_by_role('heading',name='AI 的长期观察 · 要点',exact=True)).to_be_visible()
                portrait=httpx.get(base+'/api/profile').json()['portrait']['result']
                assert len(portrait['analysis'])<=60 and len(portrait['action_plan'])<=3
                assert len(portrait['strengths'])<=2 and len(portrait['weaknesses'])<=2
                expect(page.get_by_role('heading',name='下一次怎么练',exact=True)).to_be_visible()
                assert '…' not in json.dumps(portrait,ensure_ascii=False)
                page.screenshot(path='/tmp/shenlun-profile-tested.png',full_page=True)
                old_stats=httpx.get(base+'/api/profile').json()
                old_stats['portrait_needs_refresh']=True
                old_stats['portrait']['result']['analysis']='旧报告f8编号83.7和截断句子…'
                page.route('**/api/profile',lambda route:route.fulfill(json=old_stats))
                page.reload();expect(page.get_by_text('这份观察是旧版长报告',exact=False)).to_be_visible()
                expect(page.get_by_text('旧报告f8编号83.7和截断句子…',exact=True)).to_have_count(0)
                page.unroute('**/api/profile');page.reload()
                expect(page.get_by_role('heading',name='当前结论',exact=True)).to_be_visible()
                page.set_viewport_size({'width':390,'height':844});page.reload()
                expect(page.get_by_role('heading',name='下一次怎么练',exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.set_viewport_size({'width':1440,'height':900})
                page.goto(base+'/#history');expect(page.locator('tbody tr')).to_have_count(3)
                expect(page.locator('.practice-pause')).to_have_count(0)
                page.get_by_role('link',name='查看 →').first.click();expect(page.get_by_text(answers[-1]['answer'],exact=True)).to_be_visible()
                page.set_viewport_size({'width':390,'height':844});page.goto(base+'/#overview');page.wait_for_selector('.hero')
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'mobile horizontal overflow'
                page.screenshot(path='/tmp/shenlun-mobile.png',full_page=True)
                # Two results arrive while a third coach is still grading: polling must preserve the selected DOM.
                providers=httpx.get(base+'/api/providers').json()
                third=httpx.post(base+'/api/providers',json=dict(name='coach-c',protocol='openai',base_url=f'http://127.0.0.1:{mock.server_port}/v1',model='coach-c',api_key='mock-only')).json()
                new=httpx.post(base+'/api/attempts',json=dict(question_id=expected['questions'][0]['id'],answer='加强创新，推动发展。',provider_ids=[p['id'] for p in providers]+[third['id']],master_id=providers[0]['id'])).json()
                page.set_viewport_size({'width':1440,'height':1000});page.goto(base+'/#attempt/'+new['attempt_id'])
                expect(page.locator('[data-action="review-tab"]')).to_have_count(2,timeout=10000)
                slow=page.locator('.score-card').filter(has=page.get_by_text('coach-c',exact=True))
                expect(slow).to_contain_text('批改请求中')
                expect(slow.locator('[data-clock-start]')).to_contain_text('已等待')

                # Hold an already-fetched poll response while the user selects the second coach.
                page.evaluate("""()=>{const original=window.fetch;window.__holdPoll=true;window.fetch=async(...args)=>{const response=await original(...args);if(window.__holdPoll&&String(args[0]).startsWith('/api/attempts/')){window.__holdPoll=false;await new Promise(resolve=>window.__releasePoll=resolve);}return response;};}""")
                page.wait_for_function('() => typeof window.__releasePoll === "function"',timeout=5000)
                page.locator('[data-action="review-tab"]').nth(1).click()
                expect(page.locator('[data-action="review-tab"].active')).to_contain_text('coach-b')
                expect(page.locator('.reference-paper-target .paper-preview')).to_be_visible()
                page.locator('details.plain-details > summary').click()
                page.evaluate("window.__stableReview=document.querySelector('#review-body');window.__stablePaper=document.querySelector('.paper-preview');window.__stableDetails=document.querySelector('details.plain-details');")
                page.evaluate('window.__releasePoll()')
                page.wait_for_timeout(5500)
                expect(page.locator('[data-action="review-tab"].active')).to_contain_text('coach-b')
                assert page.evaluate("window.__stableReview===document.querySelector('#review-body')&&window.__stablePaper===document.querySelector('.paper-preview')&&window.__stableDetails.open")
                expect(page.locator('[data-action="review-tab"]')).to_have_count(3,timeout=15000)
                expect(page.locator('[data-action="review-tab"].active')).to_contain_text('coach-b')
                assert page.evaluate("window.__stablePaper===document.querySelector('.paper-preview')")
                expect(page.locator('#review-body')).to_contain_text('本题审题')
                expect(page.locator('.reference-preview')).to_be_visible()
                page.screenshot(path='/tmp/shenlun-live-review-tested.png',full_page=True)
                for width in [1280,390]:
                    page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(100)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
                    assert page.locator('.reference-paper-target').evaluate('el=>el.scrollWidth<=el.clientWidth+1'),width

                # A truncated legacy low-limit model can be raised and retried without regrading peers.
                clipped=httpx.post(base+'/api/providers',json=dict(name='coach-truncated',protocol='openai',base_url=f'http://127.0.0.1:{mock.server_port}/v1',model='coach-truncated',api_key='mock-only',max_tokens=6000)).json()
                job=httpx.post(base+'/api/attempts',json=dict(question_id=expected['questions'][0]['id'],answer='测试截断后重试。',provider_ids=[clipped['id']],master_id=providers[0]['id'])).json()
                page.set_viewport_size({'width':1280,'height':900});page.goto(base+'/#attempt/'+job['attempt_id'])
                boost=page.locator('[data-action="boost-retry"]');expect(boost).to_be_enabled(timeout=15000)
                expect(page.locator('.score-card').filter(has=boost)).to_contain_text('6000')
                before=counts['coach-a'];boost.click()
                expect(page.locator('[data-action="boost-retry"]')).to_have_count(0,timeout=15000)
                expect(page.locator('#review-body')).to_contain_text('模拟接口生成',timeout=15000)
                expect(page.locator('[data-action="retry-coach"]')).to_have_count(0)
                saved=next(p for p in httpx.get(base+'/api/providers').json() if p['id']==clipped['id'])
                assert saved['max_tokens']==32768 and saved['has_key']
                assert counts['coach-a']==before and counts['coach-truncated']==2
                legacy=page.evaluate("()=>{const a=structuredClone(state.attempt);a.grades.forEach(g=>delete g.stage);return coachScoreCard(a,a.providers[0],false);}")
                assert '已完成' in legacy and '未完成' not in legacy
                page.screenshot(path='/tmp/shenlun-token-progress-tested.png',full_page=True)
                # Editing a failed interface in place must refresh its snapshot on targeted retry.
                def api_provider(name,model):
                    return httpx.post(base+'/api/providers',json=dict(name=name,protocol='openai',base_url=f'http://127.0.0.1:{mock.server_port}/v1',model=model,api_key='mock-only')).json()
                def wait_attempt(attempt_id):
                    for _ in range(100):
                        current=httpx.get(base+'/api/attempts/'+attempt_id).json()
                        if current['status'] not in ('queued','running'):return current
                        time.sleep(.1)
                    raise AssertionError('replacement attempt did not finish')
                exhausted=api_provider('额度已用完','coach-exhausted')
                first=httpx.post(base+'/api/attempts',json=dict(question_id=expected['questions'][0]['id'],answer='测试接口更换后保留原答案。',provider_ids=[providers[0]['id'],exhausted['id']],master_id=providers[0]['id'])).json()
                old=wait_attempt(first['attempt_id']);assert old['status']=='partial'
                kept=next(g for g in old['grades'] if g['provider_id']==providers[0]['id'])
                page.goto(base+'/#models')
                page.locator('[data-action="edit-model"][data-id="'+exhausted['id']+'"]').click()
                page.locator('[name="name"]').fill('更新后的接口');page.locator('[name="model"]').fill('coach-updated')
                page.locator('#model-form button[type="submit"]').click();expect(page.locator('#editor')).not_to_be_visible()
                page.goto(base+'/#attempt/'+first['attempt_id'])
                expect(page.get_by_text('当前接口已更新为',exact=False)).to_be_visible()
                before=counts['coach-a']
                page.locator('[data-action="retry-coach"][data-provider="'+exhausted['id']+'"]').click()
                expect(page.locator('[data-action="review-tab"]')).to_have_count(2,timeout=15000)
                changed=wait_attempt(first['attempt_id']);assert changed['status']=='completed'
                assert counts['coach-a']==before and counts['coach-updated']==1
                assert next(g for g in changed['grades'] if g['provider_id']==providers[0]['id'])==kept
                assert next(g for g in changed['grades'] if g['provider_id']==exhausted['id'])['model']=='coach-updated'
                # A deleted failed interface may be replaced by a different configuration, including its master role.
                deleted=api_provider('已删除的额度接口','coach-exhausted');replacement=api_provider('新的补批接口','coach-replacement')
                second=httpx.post(base+'/api/attempts',json=dict(question_id=expected['questions'][0]['id'],answer='测试删除旧接口后单独补批。',provider_ids=[providers[0]['id'],deleted['id']],master_id=deleted['id'])).json()
                old=wait_attempt(second['attempt_id']);assert old['status']=='summary_failed'
                kept=next(g for g in old['grades'] if g['provider_id']==providers[0]['id'])
                httpx.delete(base+'/api/providers/'+deleted['id'])
                page.goto(base+'/#attempt/'+second['attempt_id'])
                page.locator('[data-action="replace-coach"][data-provider="'+deleted['id']+'"]').click()
                expect(page.locator('#retry-form')).to_be_visible()
                assert page.locator('#retry-form option[value="'+providers[0]['id']+'"]').count()==0
                page.locator('#retry-form [name="replacement"]').select_option(replacement['id'])
                before=counts['coach-a']
                with page.expect_request(lambda r:r.method=='POST' and r.url.endswith('/retry')) as sent:
                    page.locator('#retry-form button[type="submit"]').click()
                assert sent.value.post_data_json=={'provider_id':deleted['id'],'replacement_provider_id':replacement['id']}
                expect(page.locator('#editor')).not_to_be_visible()
                expect(page.locator('[data-action="review-tab"]')).to_have_count(2,timeout=15000)
                changed=wait_attempt(second['attempt_id']);assert changed['status']=='completed'
                assert changed['master']['id']==replacement['id'] and counts['coach-a']==before
                assert next(g for g in changed['grades'] if g['provider_id']==providers[0]['id'])==kept
                assert any(e['previous']['id']==deleted['id'] for e in changed['retry_history'])
                # A master-only quota failure can be reassigned without any coaching requests.
                summary_only=api_provider('汇总额度不足','coach-exhausted')
                third=httpx.post(base+'/api/attempts',json=dict(question_id=expected['questions'][0]['id'],answer='测试只更换汇总接口。',provider_ids=[providers[0]['id']],master_id=summary_only['id'])).json()
                old=wait_attempt(third['attempt_id']);assert old['status']=='summary_failed'
                page.goto(base+'/#attempt/'+third['attempt_id'])
                page.locator('[data-action="replace-master"]').click()
                page.locator('#retry-form [name="replacement"]').select_option(replacement['id'])
                before=counts['coach-a']
                page.locator('#retry-form button[type="submit"]').click()
                expect(page.locator('#editor')).not_to_be_visible()
                changed=wait_attempt(third['attempt_id']);assert changed['status']=='completed'
                assert changed['grades']==old['grades'] and counts['coach-a']==before
                page.reload();expect(page.locator('[data-action="replace-master"]')).to_have_count(0)
                expect(page.get_by_text('重试与接口更换记录',exact=True)).to_be_visible()
                assert not errors,errors
                assert not page.evaluate('window.__csp'),page.evaluate('window.__csp')
                browser.close()
            print('PASS: edited failed interface retry, deleted-interface replacement and master reassignment, master-only recovery without regrading, staged API progress and clocks, truncation budget boost and targeted retry, legacy completed stages, persisted per-model timeout editing, single-coach retry/other-coach call preservation, question-first audit, three staggered coaches/in-flight poll tab race/unchanged paper DOM, always-visible reference paper at 1440/1280/390px, reselected highlight cancellation/persistence/multiple marks, hover navigation, fully visible square-cell paper at 1280×800/1440×900/1920×1080 incl 48-row essay, cross-column editing, 25-column paper/formatting/undo/paste/IME/overflow, plain-to-grid overflow drag/Shift selection/one-action selection/Backspace/undo incl Unicode and blank lines, reference-only re-generation/history, reader typography/preferences/focus/layout/draft retention at 390/1280/1920px, browser import, per-question material display, manual scope editing, actual HTTP model context filtering, three answers, draft reload, parallel grading, partial retry, history, 85/100 profile, mobile; no browser exceptions. Mock data isolated.')
        finally:
            proc.terminate();proc.wait(timeout=10);mock.shutdown()
            if proc.returncode not in (0,-15):print(proc.stderr.read().decode(),file=sys.stderr)
if __name__=='__main__':main()
