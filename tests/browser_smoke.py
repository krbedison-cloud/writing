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

counts={}
received_contexts=[]
received_reference_contexts=[]
class MockModel(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        data=json.loads(body['messages'][-1]['content']);task=data['task'];payload=data['data'];model=body['model']
        if task.startswith('独立批改'):
            received_contexts.append(payload['question'])
            counts[model]=counts.get(model,0)+1
            if model=='coach-b' and counts[model]==1:
                self.send_response(429);self.end_headers();self.wfile.write(b'mock rate limit');return
            result=dict(score=payload['full_score']-(4 if model=='coach-a' else 6),dimensions={k:70 for k in DIMENSIONS},strengths=['观点清晰'],weaknesses=['材料要点尚有遗漏'],suggestions=['分类概括关键措施'],evidence=['原答案“加强建设”需要展开具体措施'],high_score_answer='这是模拟接口生成的参考答案，仅用于验证调用与存档流程，不是真实模型批改。')
        elif task.startswith(('范文校正','单独重写高分范文')):
            received_reference_contexts.append(payload['question'])
            spec=payload['question']['paper_spec'];header='模拟接口生成的参考范文'
            target=payload.get('target_word_count') or max(spec['word_min'],round((spec['word_max'] or spec['cells'])*.8))
            target=max(len(header)+10,min(target,(spec['word_max'] or spec['cells'])-25))
            target=max(target,spec['word_min'])
            answer=header+'\n　　'+'甲'*(target-len(header))
            check=check_paper(answer,spec)
            result=dict(high_score_answer=answer,high_score_self_check=dict(word_count=check['word_count'],used_rows=check['used_rows'],complies=check['valid'],format_note='已检查标题、段首缩进和换行占格'))
        elif task.startswith('汇总'):
            result=dict(analysis='模拟汇总：各模型独立批改已完成，建议加强材料要点的分类与表达。',consensus=['需要更加具体的材料支撑'],disagreements=['评分存在一定分差'],action_plan=['先列出要点，再用短句组织'])
        elif task.startswith('基于全部'):
            result=dict(analysis='模拟历史画像：已分析全部历史摘要，当前应加强材料运用与结构表达。',strengths=['能够持续完成练习'],weaknesses=['要点提炼有待加强'],action_plan=['每天复盘一个遗漏要点'])
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
                    page.locator('#model-form button[type="submit"]').click()
                    expect(page.locator('#editor')).not_to_be_visible()
                    expect(page.locator('.model-card')).to_have_count(1 if model=='coach-a' else 2)
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
                page.locator('[data-action="edit-paper"]').first.click();page.locator('[name="confirmed"]').check();page.locator('#paper-form button[type="submit"]').click()
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
                        page.locator('[data-action="retry"]').click()
                    expect(page.locator('[data-action="retry"]')).to_have_count(0,timeout=20000)
                    expect(page.locator('#review-body')).to_contain_text('模拟接口生成',timeout=20000)
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
                        page.locator('.reference-preview > summary').click()
                        expect(page.locator('.paper-preview .paper-row')).to_have_count(expected['questions'][0]['paper_spec']['rows'])
                        page.locator('.reference-version').select_option('original')
                        expect(page.locator('.answer-reference')).to_have_text(original_reference)
                        page.locator('[data-action="review-tab"]').nth(1).click()
                        page.locator('[data-action="regenerate-reference"][data-direction="more"]').click()
                        expect(page.locator('.reference-version option')).to_have_count(2,timeout=20000)
                        expect(page.locator('[data-action="review-tab"].active')).to_contain_text('coach-b')
                for context in received_contexts:
                    assert context['materials']==expected['questions'][context['number']-1]['materials']
                    assert 'MANUAL_PASSAGE_ONLY' not in json.dumps(context)
                    assert 'material_blocks' not in context
                for context in received_reference_contexts:
                    assert context['materials']==expected['questions'][context['number']-1]['materials']
                assert counts['coach-a']==3,counts
                assert counts['coach-b']==4,counts
                page.goto(base+'/#profile');expect(page.locator('.forecast-total')).to_contain_text('85',timeout=20000)
                page.screenshot(path='/tmp/shenlun-profile-tested.png',full_page=True)
                page.goto(base+'/#history');expect(page.locator('tbody tr')).to_have_count(3)
                page.get_by_role('link',name='查看 →').first.click();expect(page.get_by_text(answers[-1]['answer'],exact=True)).to_be_visible()
                page.set_viewport_size({'width':390,'height':844});page.goto(base+'/#overview');page.wait_for_selector('.hero')
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'mobile horizontal overflow'
                page.screenshot(path='/tmp/shenlun-mobile.png',full_page=True)
                assert not errors,errors
                assert not page.evaluate('window.__csp'),page.evaluate('window.__csp')
                browser.close()
            print('PASS: reselected highlight cancellation/persistence/multiple marks, hover navigation, fully visible square-cell paper at 1280×800/1440×900/1920×1080 incl 48-row essay, cross-column editing, 25-column paper/formatting/undo/paste/IME/overflow, plain-to-grid overflow drag/Shift selection/one-action selection/Backspace/undo incl Unicode and blank lines, reference-only re-generation/history, reader typography/preferences/focus/layout/draft retention at 390/1280/1920px, browser import, per-question material display, manual scope editing, actual HTTP model context filtering, three answers, draft reload, parallel grading, partial retry, history, 85/100 profile, mobile; no browser exceptions. Mock data isolated.')
        finally:
            proc.terminate();proc.wait(timeout=10);mock.shutdown()
            if proc.returncode not in (0,-15):print(proc.stderr.read().decode(),file=sys.stderr)
if __name__=='__main__':main()
