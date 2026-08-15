"""Actual TCP metadata workload: 100 synthetic sales, 5 teams; inference disabled.

This measures application/database concurrency, never completed-audio throughput.
"""
import hashlib
import json
import math
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx
from accept_backend_runtime import SERVER, save
from meetflow.auth import password_digest
from meetflow.config import Settings
from meetflow.database import Account, Company, PersonalRecording, Project, Run, Team, make_database

ROOT = Path(__file__).resolve().parents[1]


def main():
    port = 8767
    with socket.socket() as test:
        test.bind(('127.0.0.1', port))
    out = ROOT / 'test-results/company-capacity' / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True)
    config = Settings(_env_file=None, data_dir=out/'workspace', database_url='', start_worker=False,
                      auth_enabled=True, model_name='', model_api_key='', asr_model='')
    engine, sessions = make_database(config)
    password = secrets.token_urlsafe(24)
    encoded = password_digest(password)  # Shared only across these synthetic load-test accounts.
    with sessions.begin() as session:
        session.add(Company(id='capacity-company',name='100人合成负载公司'))
        session.flush()
        session.add_all(Team(id=f't{i}',company_id='capacity-company',name=f'合成团队{i}') for i in range(5))
        session.add_all(Project(id=f'p{i}',name=f'合成个人区{i}') for i in range(100))
        session.flush()
        session.add_all(Account(id=f'u{i}',company_id='capacity-company',team_id=f't{i%5}',
            username=f'capacity.sales.{i}',name=f'合成销售{i}',role='sales',project_id=f'p{i}',password_hash=encoded)
            for i in range(100))
    engine.dispose()
    files = [Path(__file__), Path(__file__).with_name('accept_backend_runtime.py'),
             *sorted((ROOT/'backend/meetflow').rglob('*.py'))]
    manifest = {'status':'running','sales_accounts':100,'teams':5,'port':port,'inference_enabled':False,
        'scope':'localhost TCP metadata writes/reads and account isolation, not audio/model throughput or production SLA',
        'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    save(out/'manifest.json',manifest)
    save(out/'source_bundle.json',{str(p.relative_to(ROOT)):p.read_text(encoding='utf8') for p in files})
    environment = {k:v for k,v in os.environ.items() if not k.startswith('MEETFLOW_')}
    environment.update({'MEETFLOW_'+k.upper():str(v).lower() if isinstance(v,bool) else str(v)
                        for k,v in config.model_dump().items()})
    stop = out/'stop'
    groups, checks = [], []
    log = (out/'server.log').open('wb')
    server = subprocess.Popen([sys.executable,'-c',SERVER,str(port),str(stop)],cwd=ROOT,env=environment,
                              stdout=log,stderr=subprocess.STDOUT)
    client = httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=60,
                         limits=httpx.Limits(max_connections=120,max_keepalive_connections=120))
    def check(label, value):
        checks.append({'check':label,'passed':bool(value)})
        save(out/'checks.json',checks)
        assert value,label
    try:
        deadline = time.monotonic()+40
        while time.monotonic()<deadline and server.poll() is None:
            try:
                if client.get('/api/health',timeout=2).status_code==200:break
            except httpx.TransportError:
                pass
            time.sleep(.2)
        def login(index):
            result=client.post('/api/auth/login',json={'username':f'capacity.sales.{index}','password':password})
            result.raise_for_status()
            return {'Authorization':'Bearer '+result.json()['token']}
        with ThreadPoolExecutor(max_workers=8) as pool:
            actors=list(pool.map(login,range(100)))
        check('100 distinct authenticated sales sessions',len({a['Authorization'] for a in actors})==100)
        for concurrency in (20,50,100):
            def create(index, level=concurrency):
                start=time.perf_counter()
                response=client.post('/api/workspace/recordings',headers=actors[index],json={
                    'title':f'合成负载{level}-{index}','request_key':f'capacity-{level}-{index}',
                    'transcript':f'销售：第{index}位合成销售的系统负载输入，不是实际业务会谈。'})
                return {'account':index,'status':response.status_code,'seconds':time.perf_counter()-start,
                        'recording':response.json().get('id')}
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                rows=list(pool.map(create,range(100)))
            values=sorted(r['seconds'] for r in rows)
            group={'phase':'create','concurrency':concurrency,'requests':len(rows),
                   'successful':sum(r['status']==201 for r in rows),
                   'p50_seconds':values[math.ceil(len(values)*.5)-1],
                   'p95_seconds':values[math.ceil(len(values)*.95)-1]}
            groups.append(group)
            save(out/f'create-{concurrency}.json',rows)
            save(out/'results.json',groups)
            print(json.dumps(group),flush=True)
            check(f'{concurrency} concurrency creates all succeeded',group['successful']==100)
        def read(index):
            start=time.perf_counter()
            response=client.get('/api/workspace/recordings',headers=actors[index%100])
            rows=response.json()
            return {'status':response.status_code,'seconds':time.perf_counter()-start,
                    'private_scope_ok':response.status_code==200 and len(rows)==3 and
                        all(r['body']['title'].endswith('-'+str(index%100)) for r in rows)}
        with ThreadPoolExecutor(max_workers=100) as pool:
            rows=list(pool.map(read,range(300)))
        values=sorted(r['seconds'] for r in rows)
        groups.append({'phase':'read','concurrency':100,'requests':300,
            'successful':sum(r['status']==200 for r in rows),'scope_correct':sum(r['private_scope_ok'] for r in rows),
            'p50_seconds':values[149],'p95_seconds':values[284]})
        save(out/'reads.json',rows);save(out/'results.json',groups)
        check('300 reads returned only the current sales data',all(r['private_scope_ok'] for r in rows))
        foreign=client.get('/api/workspace/recordings/'+json.loads((out/'create-20.json').read_text(encoding='utf8'))[0]['recording'],headers=actors[1])
        check('another sales account cannot read a private recording',foreign.status_code==404)
        with sqlite3.connect(config.data_dir/'meetflow.db') as database:
            check('all jobs durably queued',database.execute(f'SELECT count(*) FROM "{Run.__tablename__}" WHERE status=?',('queued',)).fetchone()[0]==300)
            check('no inference accidentally executed',database.execute(f'SELECT sum(model_calls) FROM "{Run.__tablename__}"').fetchone()[0]==0)
            check('300 personal rows retained',database.execute(f'SELECT count(*) FROM "{PersonalRecording.__tablename__}"').fetchone()[0]==300)
            check('foreign keys valid',not database.execute('pragma foreign_key_check').fetchall())
            check('database integrity',database.execute('pragma quick_check').fetchone()[0]=='ok')
        manifest.update(status='passed',checks=len(checks),groups=groups)
        print(json.dumps({'folder':str(out),'status':manifest['status'],'checks':len(checks),'groups':groups}),flush=True)
    except Exception as exc:
        manifest.update(status='failed',error={'type':type(exc).__name__,'message':str(exc)})
        raise
    finally:
        save(out/'manifest.json',manifest)
        stop.write_text('stop this capacity child',encoding='utf8')
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill','/PID',str(server.pid),'/T','/F'],capture_output=True,check=False)
            raise RuntimeError('Capacity server did not stop gracefully') from None
        finally:
            log.close();client.close()


if __name__=='__main__':
    main()
