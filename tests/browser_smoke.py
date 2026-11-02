"""Isolated browser + real HTTP adapter smoke test. No real model credentials used.
python tests/browser_smoke.py [--pdf /path/to/exam.pdf]
Requires Playwright + a browser; CHROMIUM_EXECUTABLE may select system Chromium.
"""
import argparse
import json
import os
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

counts={}
received_contexts=[]
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
                    expect(page.locator('#question-materials')).to_have_text(expected['questions'][index]['materials'])
                    answer='加强基础建设，推动绿色创新；完善产业配套，提升竞争优势。'+str(index)
                    page.locator('#answer').fill(answer)
                    page.reload();expect(page.locator('#answer')).to_have_value(answer)
                    page.locator('input[name="provider"]').nth(0).check();page.locator('input[name="provider"]').nth(1).check()
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
                for context in received_contexts:
                    assert context['materials']==expected['questions'][context['number']-1]['materials']
                    assert 'MANUAL_PASSAGE_ONLY' not in json.dumps(context)
                    assert 'material_blocks' not in context
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
                browser.close()
            print('PASS: browser import, per-question material display, manual scope editing, actual HTTP model context filtering, three answers, draft reload, parallel grading, partial retry, history, 85/100 profile, mobile; no browser exceptions. Mock data isolated.')
        finally:
            proc.terminate();proc.wait(timeout=10);mock.shutdown()
            if proc.returncode not in (0,-15):print(proc.stderr.read().decode(),file=sys.stderr)
if __name__=='__main__':main()
