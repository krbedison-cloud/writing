import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Body
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from .db import Database, dumps
from .schemas import ProviderInput, PaperInput, AttemptInput, KINDS
from .pdf import extract_pdf, parse_text
from .service import Service, uid, now, public_error
from .llm import ModelError

STATIC = Path(__file__).parent / 'static'

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
        allowed = os.getenv('ALLOWED_HOSTS','localhost,127.0.0.1,testserver').split(',')
        if request.url.hostname not in allowed:
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
        return [{k:v for k,v in p.items() if k not in ('raw_text','materials')} for p in db.all('papers')]
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
        return p
    @app.get('/api/papers/{id}')
    def paper(id: str):
        return get_paper(id)
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
        p, q = found[0]
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
        a = dict(id=id, question_id=q['id'], paper_id=p['id'], question_snapshot=dict(q, materials=p['materials'], source={k:p[k] for k in ('title','year','province','volume')}), answer=value.answer, providers=[safe(m) for m in models], master=safe(master), grades=[], mean_score=None, synthesis=None, status='queued', created_at=now())
        db.put('attempts', id, a, a['created_at'])
        j = service.job('grading', id)
        service.launch(service.grade(j, id))
        return {'attempt_id':id,'job_id':j['id']}
    @app.get('/api/attempts')
    def attempts():
        return [{k:v for k,v in a.items() if k not in ('question_snapshot','grades','answer','synthesis')} | {'question_number':a['question_snapshot']['number'],'kind':a['question_snapshot']['kind'],'source':a['question_snapshot']['source'],'answer_length':len(a['answer'])} for a in db.all('attempts')]
    @app.get('/api/attempts/{id}')
    def attempt(id: str):
        a = db.get('attempts', id)
        if not a:
            raise HTTPException(404, '记录不存在')
        return a
    @app.post('/api/attempts/{id}/retry')
    async def retry(id: str):
        a = attempt(id)
        if a['status'] in ('queued','running'):
            raise HTTPException(409, '任务正在执行')
        if a['status'] == 'completed':
            raise HTTPException(400, '本次批改已全部成功，无需重试')
        a['status'] = 'queued'
        db.put('attempts', id, a, a['created_at'])
        j = service.job('grading', id)
        service.launch(service.grade(j, id))
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
        return Response(dumps({'version':1,'exported_at':now(),'papers':db.all('papers'),'attempts':db.all('attempts'),'portrait':db.setting('portrait')}), media_type='application/json',headers={'Content-Disposition':'attachment; filename="shenlun-history.json"'})
    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app

app = create_app()
