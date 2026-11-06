import asyncio
import json
import ipaddress
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Body
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from .db import Database, dumps
from .schemas import ProviderInput, PaperInput, AttemptInput, RegenerateInput, RetryInput, HighlightInput, HighlightColor, KINDS
from .pdf import extract_pdf, parse_text
from .materials import prepare_paper, model_question
from .annotations import canonical_text
from .paper import check_paper
from .service import Service, uid, now, public_error
from .llm import ModelError

STATIC = Path(__file__).parent / 'static'
LAN_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16',
    'fc00::/7', 'fe80::/10',
))

def permitted_host(host):
    allowed = {h.strip().lower() for h in os.getenv('ALLOWED_HOSTS','localhost,127.0.0.1,testserver').split(',')}
    host = (host or '').lower()
    if host in allowed:
        return True
    if os.getenv('ALLOW_LAN', '0') != '1':
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address.version == network.version and address in network for network in LAN_NETWORKS)


def create_app(data_dir=None):
    db = Database(data_dir or os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent / 'data')))
    service = Service(db)
    @asynccontextmanager
    async def lifespan(app):
        service.recover()
        yield
        for task in service.tasks:
            task.cancel()
        await asyncio.gather(*service.tasks, return_exceptions=True)
    app = FastAPI(title='申论进阶', lifespan=lifespan)
    app.state.service = service
    @app.middleware('http')
    async def guard(request, call_next):
        if request.url.path == '/health':
            return await call_next(request)
        password = os.getenv('APP_PASSWORD', '')
        if password:
            import base64
            try:
                scheme, token = request.headers.get('authorization','').split(' ', 1)
                creds = base64.b64decode(token, validate=True).decode()
                username, supplied = creds.split(':', 1)
                valid = scheme.lower() == 'basic' and username == 'admin' and secrets.compare_digest(supplied.encode(), password.encode())
            except Exception:
                valid = False
            if not valid:
                return Response(status_code=401, headers={'WWW-Authenticate':'Basic realm="Shenlun", charset="UTF-8"'})
        # Guard against browser cross-site writes and DNS rebinding on local deployments.
        if not permitted_host(request.url.hostname):
            return JSONResponse({'detail':'访问主机未列入 ALLOWED_HOSTS'}, status_code=403)
        origin = request.headers.get('origin')
        if request.method not in ('GET','HEAD','OPTIONS') and origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'拒绝跨站写入请求'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers['Cache-Control'] = 'no-store'
        return response
    @app.exception_handler(ModelError)
    async def model_error(request, exc):
        return JSONResponse({'detail':str(exc)}, status_code=400)
    @app.exception_handler(ValueError)
    async def bad_input(request, exc):
        return JSONResponse({'detail':str(exc)}, status_code=400)
    @app.get('/health')
    def health():
        db.rows('SELECT 1')
        return {'status':'ok'}
    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html')
    @app.get('/api/providers')
    def providers():
        return db.providers()
    @app.post('/api/providers')
    def add_provider(p: ProviderInput):
        id = uid()
        db.save_provider(id, p.model_dump())
        return db.provider(id)
    @app.put('/api/providers/{id}')
    def edit_provider(id: str, p: ProviderInput):
        if not db.provider(id):
            raise HTTPException(404, '模型不存在')
        db.save_provider(id, p.model_dump())
        return db.provider(id)
    @app.delete('/api/providers/{id}')
    def delete_provider(id: str):
        db.execute('DELETE FROM providers WHERE id=?', (id,))
        return {'ok':True}
    @app.post('/api/providers/{id}/test')
    async def test_provider(id: str):
        try:
            result = await service.ask(service.provider(id), '连接测试，仅返回 {"ok":true}', {})
            if result.get('ok') is not True:
                raise ModelError('接口可访问，但模型未返回要求的测试结构')
            return {'ok':True}
        except Exception as exc:
            raise HTTPException(400, public_error(exc))
    @app.get('/api/papers')
    def papers():
        result = []
        for p in db.all('papers'):
            summary = {k:v for k,v in p.items() if k not in ('raw_text','materials','material_blocks')}
            summary['questions'] = [{k:v for k,v in q.items() if k not in ('materials','custom_materials')} for q in p['questions']]
            result.append(summary)
        return result
    @app.post('/api/papers/import')
    async def import_paper(file: UploadFile = File(...)):
        content = await file.read(25 * 1024 * 1024 + 1)
        if len(content) > 25 * 1024 * 1024:
            raise HTTPException(413, 'PDF 最大 25MB')
        if not content.startswith(b'%PDF-'):
            raise HTTPException(400, '请选择有效的 PDF 文件')
        text = await asyncio.to_thread(extract_pdf, content)
        filename = Path(file.filename or '试卷.pdf').name
        parsed = parse_text(text, filename)
        id = uid()
        for q in parsed['questions']:
            q['id'] = uid()
        p = dict(parsed, id=id, filename=filename, raw_text=text, created_at=now())
        upload_dir = db.path / 'uploads'
        upload_dir.mkdir(exist_ok=True, mode=0o700)
        (upload_dir / f'{id}.pdf').write_bytes(content)
        db.put('papers', id, p, p['created_at'])
        return p
    def get_paper(id):
        p = db.get('papers', id)
        if not p:
            raise HTTPException(404, '试卷不存在')
        return prepare_paper(p)
    @app.get('/api/papers/{id}')
    def paper(id: str):
        return get_paper(id)
    @app.get('/api/papers/{id}/questions/{question_id}')
    def question(id: str, question_id: str):
        p = get_paper(id)
        q = next((q for q in p['questions'] if q['id'] == question_id), None)
        if not q:
            raise HTTPException(404, '题目不存在')
        return {'paper':{k:p[k] for k in ('id','title','year','province','volume','confirmed')}, 'question':q}
    def question_data(paper_id, question_id):
        p = get_paper(paper_id)
        q = next((q for q in p['questions'] if q['id']==question_id),None)
        if not q:
            raise HTTPException(404,'题目不存在')
        return q
    @app.get('/api/papers/{id}/questions/{question_id}/highlights')
    def highlights(id: str, question_id: str):
        q = question_data(id,question_id)
        rows = db.rows('SELECT data,signature FROM highlights WHERE question_id=? ORDER BY rowid',(question_id,))
        return {'signature':q['material_signature'], 'highlights':[json.loads(r['data']) for r in rows if r['signature']==q['material_signature']], 'outdated_count':sum(r['signature']!=q['material_signature'] for r in rows)}
    @app.post('/api/papers/{id}/questions/{question_id}/highlights')
    def add_highlight(id: str, question_id: str, value: HighlightInput):
        q = question_data(id,question_id)
        if value.signature != q['material_signature']:
            raise HTTPException(409,'材料已发生变化，请刷新后重新选择；原有高亮已保留')
        canonical = canonical_text(q['materials'])
        if value.start>=value.end or value.end>len(canonical) or canonical[value.start:value.end]!=value.quote:
            raise HTTPException(400,'所选文字与材料不匹配，请重新选择')
        if db.rows('SELECT COUNT(*) AS n FROM highlights WHERE question_id=?',(question_id,))[0]['n']>=500:
            raise HTTPException(400,'本题标注已达到500处，请先删除部分标注')
        h = dict(value.model_dump(),id=uid(),question_id=question_id,paper_id=id,created_at=now(),updated_at=now())
        h['color'] = h['color'].lower()
        db.execute('INSERT INTO highlights VALUES(?,?,?,?)',(h['id'],question_id,value.signature,dumps(h)))
        return h
    @app.patch('/api/highlights/{id}')
    def color_highlight(id: str, value: HighlightColor):
        rows = db.rows('SELECT data FROM highlights WHERE id=?',(id,))
        if not rows:
            raise HTTPException(404,'标注不存在')
        h = json.loads(rows[0]['data'])
        h.update(color=value.color.lower(),updated_at=now())
        db.execute('UPDATE highlights SET data=? WHERE id=?',(dumps(h),id))
        return h
    @app.delete('/api/highlights/{id}')
    def delete_highlight(id: str):
        db.execute('DELETE FROM highlights WHERE id=?',(id,))
        return {'ok':True}
    @app.get('/api/papers/{id}/pdf')
    def paper_pdf(id: str):
        p = get_paper(id)
        path = db.path / 'uploads' / f'{p["id"]}.pdf'
        if not path.exists():
            raise HTTPException(404, '原 PDF 不存在')
        return FileResponse(path, filename=p['filename'], media_type='application/pdf')
    @app.put('/api/papers/{id}')
    def edit_paper(id: str, value: PaperInput):
        p = get_paper(id)
        active = [j for j in db.all('jobs') if j.get('target_id') == id and j['status'] in ('queued','running')]
        if active:
            raise HTTPException(409, 'AI 识别任务正在执行，请完成后再校对')
        old = {q['number']:q['id'] for q in p['questions']}
        v = value.model_dump()
        for q in v['questions']:
            q['id'] = old.get(q['number'], uid())
        p.update(v)
        p = prepare_paper(p)
        if p['confirmed'] and p['requires_material_selection']:
            raise HTTPException(400, '请逐题选择材料编号范围或全部材料，再确认试卷')
        db.put('papers', id, p, p['created_at'])
        return p
    @app.post('/api/papers/{id}/classify')
    async def classify(id: str, body: dict = Body(...)):
        get_paper(id)
        service.provider(body.get('provider_id'))
        if any(j.get('target_id') == id and j['status'] in ('queued','running') for j in db.all('jobs')):
            raise HTTPException(409, '识别任务已在执行')
        j = service.job('classification', id)
        service.launch(service.classify(j, id, body['provider_id']))
        return j
    @app.get('/api/jobs')
    def jobs():
        return db.all('jobs')[:30]
    @app.get('/api/jobs/{id}')
    def job(id: str):
        j = db.get('jobs', id)
        if not j:
            raise HTTPException(404, '任务不存在')
        return j
    @app.post('/api/attempts')
    async def submit(value: AttemptInput):
        if not value.answer.strip():
            raise HTTPException(400, '答案不能为空')
        found = [(p,q) for p in db.all('papers') for q in p['questions'] if q['id'] == value.question_id]
        if not found:
            raise HTTPException(404, '题目不存在')
        p, old_q = found[0]
        p = prepare_paper(p)
        q = next(q for q in p['questions'] if q['id'] == old_q['id'])
        if q['material_scope'].get('requires_choice'):
            raise HTTPException(400, '本题材料范围尚未确认，请在校对试卷中选择材料编号范围或全部材料')
        if not p['confirmed']:
            raise HTTPException(400, '请先校对并确认试卷')
        ids = list(dict.fromkeys(value.provider_ids))
        models = [service.provider(id) for id in ids]
        master = service.provider(value.master_id)
        # Duplicate submissions are rejected while the same answer is being graded.
        for a in db.all('attempts'):
            if a['question_id'] == q['id'] and a['answer'] == value.answer and a['status'] in ('queued','running'):
                raise HTTPException(409, '这份答案正在批改，请查看练习历史')
        safe = lambda model: {k:v for k,v in model.items() if k not in ('api_key','has_key')}
        id = uid()
        a = dict(id=id, question_id=q['id'], paper_id=p['id'], question_snapshot=dict(q, source={k:p[k] for k in ('title','year','province','volume')}), answer=value.answer, answer_layout=check_paper(value.answer,q['paper_spec']), providers=[safe(m) for m in models], master=safe(master), grades=[], mean_score=None, synthesis=None, status='queued', created_at=now())
        db.put('attempts', id, a, a['created_at'])
        j = service.job('grading', id)
        service.launch(service.grade(j, id))
        return {'attempt_id':id,'job_id':j['id']}
    @app.get('/api/attempts')
    def attempts():
        return [{k:v for k,v in a.items() if k not in ('question_snapshot','grades','answer','synthesis','retry_history')} | {'question_number':a['question_snapshot']['number'],'kind':a['question_snapshot']['kind'],'source':a['question_snapshot']['source'],'answer_length':len(a['answer'])} for a in db.all('attempts')]
    @app.get('/api/attempts/{id}')
    def attempt(id: str):
        a = db.get('attempts', id)
        if not a:
            raise HTTPException(404, '记录不存在')
        q = model_question(a['question_snapshot'])
        a['paper_spec'] = q['paper_spec']
        a['answer_layout'] = check_paper(a['answer'],q['paper_spec'])
        for g in a['grades']:
            if g['status']=='completed':
                g['references'] = service.references(a,g)
        return a
    def reference_running(attempt_id):
        return any(j['kind']=='reference' and j.get('target_id')==attempt_id and j['status'] in ('queued','running') for j in db.all('jobs'))
    @app.post('/api/attempts/{id}/references')
    async def regenerate_reference(id: str, value: RegenerateInput):
        a = attempt(id)
        if a['status'] in ('queued','running') or reference_running(id):
            raise HTTPException(409,'这份作答的任务正在执行，请完成后再生成范文')
        g = next((g for g in a['grades'] if g['provider_id']==value.provider_id and g['status']=='completed'),None)
        if not g:
            raise HTTPException(400,'请选择一个已成功批改的模型')
        service.provider(value.provider_id)
        if not any(r['id']==value.reference_id for r in g['references']):
            raise HTTPException(400,'所选范文版本不存在')
        j = service.job('reference',id)
        j.update(provider_id=value.provider_id,direction=value.direction)
        db.put('jobs',j['id'],j)
        service.launch(service.regenerate_reference(j,id,value.provider_id,value.direction,value.reference_id))
        return j
    @app.post('/api/attempts/{id}/retry')
    async def retry(id: str, value: RetryInput | None = None):
        a = attempt(id)
        if a['status'] in ('queued','running') or reference_running(id):
            raise HTTPException(409, '任务正在执行')
        if a['status'] == 'completed':
            raise HTTPException(400, '本次批改已全部成功，无需重试')
        value = value or RetryInput()
        targets = service.prepare_retry(a, value)
        provider_id = targets[0] if value.provider_id else None
        db.put('attempts', id, a, a['created_at'])
        j = service.job('grading', id)
        if provider_id is not None:
            j['provider_id'] = provider_id
            db.put('jobs', j['id'], j)
        service.launch(service.grade(j, id, targets))
        return {'attempt_id':id,'job_id':j['id']}
    @app.get('/api/profile')
    def profile():
        return service.stats()
    @app.post('/api/profile/refresh')
    async def refresh(body: dict = Body(...)):
        service.provider(body.get('provider_id'))
        if any(j['kind'] == 'profile' and j['status'] in ('queued','running') for j in db.all('jobs')):
            raise HTTPException(409, '画像正在更新，请稍后查看')
        j = service.job('profile')
        service.launch(service.portrait(j, body['provider_id']))
        return j
    @app.get('/api/export')
    def export():
        return Response(dumps({'version':1,'exported_at':now(),'papers':db.all('papers'),'attempts':db.all('attempts'),'portrait':db.setting('portrait'),'highlights':[json.loads(r['data']) for r in db.rows('SELECT data FROM highlights ORDER BY rowid')]}), media_type='application/json',headers={'Content-Disposition':'attachment; filename="shenlun-history.json"'})
    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app

app = create_app()
