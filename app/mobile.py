"""Scoped phone pairing and durable OCR inbox; photos and OCR keys stay on the phone."""
import hashlib
import json
import secrets
import time
from collections import deque
from typing import Literal
from uuid import UUID
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from .db import dumps
from .service import now, uid


class PairInput(BaseModel):
    code: str = Field(pattern=r'^\d{6}$')
    device_name: str = Field(min_length=1,max_length=80)


class CaptureInput(BaseModel):
    request_id: UUID
    question_signature: str = Field(pattern=r'^[a-f0-9]{64}$')
    text: str = Field(min_length=1,max_length=20000)
    model: str = Field(default='',max_length=160)


class AckInput(BaseModel):
    status: Literal['accepted','dismissed']


def signature(paper_id, question):
    values=[paper_id]+[question.get(k) for k in ('id','prompt','kind','word_min','word_max','material_signature')]
    return hashlib.sha256(dumps(values).encode()).hexdigest()


class MobileBridge:
    def __init__(self,db):
        self.db=db
        self.pair_attempts=deque(maxlen=64)

    def authenticate(self,header):
        scheme,_,token=(header or '').partition(' ')
        if scheme.lower()!='bearer' or not 20<=len(token)<=200:
            raise HTTPException(401,'请先在电脑端生成配对码，重新连接手机')
        hashed=hashlib.sha256(token.encode()).hexdigest()
        rows=self.db.rows('SELECT id,name,last_seen_at FROM mobile_devices WHERE token_hash=? AND revoked=0',(hashed,))
        if not rows:raise HTTPException(401,'手机连接已失效，请重新配对')
        device=rows[0]
        self.db.execute('UPDATE mobile_devices SET last_seen_at=? WHERE id=?',(now(),device['id']))
        return device

    def pair_code(self):
        code=f'{secrets.randbelow(1000000):06d}'
        expires=time.time()+600
        with self.db.lock:
            self.db.setting('mobile_pairing',dict(code_hash=hashlib.sha256(code.encode()).hexdigest(),expires_at=expires,failed_count=0))
        return dict(code=code,expires_at=expires,expires_in=600)

    def pair(self,value):
        with self.db.lock:
            current=time.monotonic()
            while self.pair_attempts and self.pair_attempts[0]<current-60:self.pair_attempts.popleft()
            if len(self.pair_attempts)>=30:raise HTTPException(429,'配对尝试过于频繁，请一分钟后重试')
            self.pair_attempts.append(current)
            pairing=self.db.setting('mobile_pairing')
            if not pairing or pairing['expires_at']<time.time() or pairing['failed_count']>=10:
                raise HTTPException(400,'配对码已失效，请在电脑端重新生成')
            if not secrets.compare_digest(pairing['code_hash'],hashlib.sha256(value.code.encode()).hexdigest()):
                pairing['failed_count']+=1;self.db.setting('mobile_pairing',pairing)
                raise HTTPException(400,'配对码不正确，请核对电脑端显示的六位数字')
            token=secrets.token_urlsafe(32);identifier=uid();created=now()
            self.db.execute('INSERT INTO mobile_devices(id,name,token_hash,created_at,last_seen_at,revoked) VALUES(?,?,?,?,?,0)',
                            (identifier,value.device_name.strip() or '安卓手机',hashlib.sha256(token.encode()).hexdigest(),created,created))
            self.db.setting('mobile_pairing',dict(pairing,expires_at=0))
            server_id=self.db.setting('mobile_server_id') or self.db.setting('mobile_server_id',uid())
        return dict(token=token,device_id=identifier,server_id=server_id,server_name='申论进阶',api_version=1)

    def devices(self):
        return self.db.rows('SELECT id,name,created_at,last_seen_at,revoked FROM mobile_devices ORDER BY created_at DESC')

    def revoke(self,identifier):
        self.db.execute('UPDATE mobile_devices SET revoked=1 WHERE id=?',(identifier,))

    def receive(self,paper,question,value,device):
        if not value.text.strip():raise HTTPException(400,'识别文字不能为空')
        payload=dict(text=value.text,model=value.model,question_signature=value.question_signature)
        request_id=str(value.request_id)
        with self.db.lock:
            existing=self.db.rows('SELECT data FROM mobile_captures WHERE device_id=? AND request_id=?',(device['id'],request_id))
            if existing:
                old=json.loads(existing[0]['data'])
                if any(old[k]!=payload[k] for k in payload) or old['question_id']!=question['id']:
                    raise HTTPException(409,'这次同步编号已被使用，原同步记录保留；请重新编辑后再发送')
                return old
            if signature(paper['id'],question)!=value.question_signature:
                raise HTTPException(409,'电脑端题目已更新，请刷新题目后核对再同步；手机文字仍保留')
            record=dict(id=uid(),paper_id=paper['id'],question_id=question['id'],question_number=question['number'],
                        paper_title=paper['title'],question_prompt=question['prompt'],device_id=device['id'],device_name=device['name'],request_id=request_id,
                        created_at=now(),status='pending',**payload)
            self.db.execute('INSERT INTO mobile_captures(id,paper_id,question_id,device_id,request_id,status,data,created_at) VALUES(?,?,?,?,?,?,?,?)',
                            (record['id'],paper['id'],question['id'],device['id'],request_id,'pending',dumps(record),record['created_at']))
        return record

    def captures(self,question_id=None,status='pending'):
        clauses=[];args=[]
        if question_id:clauses.append('question_id=?');args.append(question_id)
        if status!='all':clauses.append('status=?');args.append(status)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        order='ASC' if status=='pending' else 'DESC'
        rows=self.db.rows(f'SELECT data FROM mobile_captures{where} ORDER BY created_at {order},rowid {order} LIMIT 50',args)
        return dict(items=[json.loads(r['data']) for r in rows],pending_count=self.db.rows('SELECT COUNT(*) AS count FROM mobile_captures WHERE status="pending"')[0]['count'])

    def acknowledge(self,identifier,status):
        with self.db.lock:
            rows=self.db.rows('SELECT data FROM mobile_captures WHERE id=?',(identifier,))
            if not rows:raise HTTPException(404,'手机录入记录不存在')
            record=json.loads(rows[0]['data'])
            if record['status'] not in ('pending',status):raise HTTPException(409,'该录入记录已在其他窗口处理')
            record.update(status=status,processed_at=record.get('processed_at') or now())
            self.db.execute('UPDATE mobile_captures SET status=?,data=? WHERE id=?',(status,dumps(record),identifier))
        return record


def mount_mobile_routes(app,bridge,get_paper,question_data):
    @app.post('/api/phone/pair-code')
    def pair_code():return bridge.pair_code()

    @app.get('/api/phone/devices')
    def devices():return bridge.devices()

    @app.delete('/api/phone/devices/{identifier}')
    def revoke(identifier:str):
        bridge.revoke(identifier);return {'ok':True}

    @app.get('/api/phone/captures')
    def captures(question_id:str|None=None,status:Literal['pending','all']='pending'):
        return bridge.captures(question_id,status)

    @app.post('/api/phone/captures/{identifier}/ack')
    def acknowledge(identifier:str,value:AckInput):return bridge.acknowledge(identifier,value.status)

    @app.post('/api/mobile/pair')
    def pair(value:PairInput):return bridge.pair(value)

    @app.get('/api/mobile/papers')
    def papers():
        return [dict(id=p['id'],title=p['title'],year=p['year'],province=p['province'],volume=p['volume'],question_count=len(p['questions'])) for p in bridge.db.all('papers')]

    @app.get('/api/mobile/papers/{paper_id}/questions')
    def questions(paper_id:str):
        p=get_paper(paper_id)
        return dict(paper={k:p[k] for k in ('id','title','year','province','volume')},questions=[
            {k:q[k] for k in ('id','number','kind','prompt','word_min','word_max')}|{'question_signature':signature(paper_id,q)} for q in p['questions']])

    @app.post('/api/mobile/papers/{paper_id}/questions/{question_id}/captures')
    def receive(paper_id:str,question_id:str,value:CaptureInput,request:Request):
        return bridge.receive(get_paper(paper_id),question_data(paper_id,question_id),value,request.state.mobile_device)
