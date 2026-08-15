"""Isolated real TCP/ASR/model acceptance, including durable queued-work restart.

No fixtures/providers replace inference. The production workspace is never opened
for writes. Raw evidence, credentials and databases remain under test-results.
"""
import argparse
import hashlib
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from meetflow.auth import password_digest
from meetflow.config import Settings
from meetflow.database import Account, Company, make_database

ROOT = Path(__file__).resolve().parents[1]
SERVER = '''
import sys,threading,time
from pathlib import Path
import uvicorn
server=uvicorn.Server(uvicorn.Config('meetflow.api:app',host='127.0.0.1',port=int(sys.argv[1]),access_log=False))
def watch():
 while not Path(sys.argv[2]).exists(): time.sleep(.1)
 server.should_exit=True
threading.Thread(target=watch,daemon=True).start()
server.run()
'''


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')


def main(args):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))  # Never terminate an existing listener.
    out = ROOT / 'test-results/backend-runtime' / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True)
    local = Settings()
    settings = Settings(_env_file=None, data_dir=out / 'workspace',
        database_url=f"sqlite:///{out / 'workspace/meetflow.db'}", auth_enabled=True, start_worker=False,
        asr_model=str(Path(local.asr_model).resolve()), asr_device='cpu',
        model_name=args.model, model_api_profile='ollama_json', model_base_url=args.base_url,
        model_tokenizer_path=str(args.tokenizer.resolve()), model_context_window=8192,
        meeting_workflow='evidence_v1', max_agent_steps=6, max_output_tokens=2048,
        max_model_calls=48, max_tool_calls=100, model_timeout=180, run_timeout=900)
    assert Path(settings.asr_model).is_dir(), 'Prepare the local Whisper model first'
    engine, sessions = make_database(settings)
    password = secrets.token_urlsafe(24)
    with sessions.begin() as session:
        session.add(Company(id='accept-company', name='真实链路验收公司（合成）'))
        session.flush()
        session.add(Account(id='accept-admin', company_id='accept-company', username='accept.admin',
            name='验收管理员', role='admin', password_hash=password_digest(password)))
    engine.dispose()
    save(out / 'credentials.json', {'username': 'accept.admin', 'password': password})
    sources = [Path(__file__), *sorted((ROOT / 'backend/meetflow').rglob('*.py'))]
    save(out / 'source_bundle.json', {str(p.relative_to(ROOT)): p.read_text(encoding='utf8') for p in sources})
    manifest = {'status': 'running', 'model': args.model, 'base_url': args.base_url,
        'source_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        'audio_sha256': hashlib.sha256(args.audio.read_bytes()).hexdigest(),
        'tokenizer_sha256': hashlib.sha256(args.tokenizer.read_bytes()).hexdigest(),
        'scope': 'real localhost TCP, CPU Whisper, real local model, isolated synthetic company; not quality accuracy'}
    save(out / 'manifest.json', manifest)
    environment = {k: v for k, v in os.environ.items() if not k.startswith('MEETFLOW_')}
    environment.update({'MEETFLOW_' + k.upper(): str(v).lower() if isinstance(v, bool) else str(v)
                        for k, v in settings.model_dump().items()})
    environment['PYTHONIOENCODING'] = 'utf-8'
    server, log, stop = None, None, None
    starts, checks = 0, []
    client = httpx.Client(base_url=f'http://127.0.0.1:{args.port}', timeout=60)

    def check(label, condition, **details):
        checks.append({'check': label, 'passed': bool(condition), **details})
        save(out / 'checks.json', checks)
        assert condition, label

    def request(method, path, actor=None, status=200, **kwargs):
        headers = {'Authorization': 'Bearer ' + actor['token']} if actor else {}
        response = client.request(method, path, headers=headers, **kwargs)
        check(f'{method} {path}', response.status_code == status, status=response.status_code, expected=status)
        return response

    def stop_server():
        nonlocal server, log
        if server:
            stop.write_text('stop this acceptance child', encoding='utf8')
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                # This PID is the child created here, never a user service.
                subprocess.run(['taskkill', '/PID', str(server.pid), '/T', '/F'], capture_output=True, check=False)
                server.wait(timeout=10)
                raise RuntimeError('Acceptance child did not stop gracefully; log retained') from None
            finally:
                log.close()
                server = None

    def start_server(workers):
        nonlocal server, log, stop, starts
        starts += 1
        environment['MEETFLOW_START_WORKER'] = str(workers).lower()
        stop = out / f'stop-{starts}'
        log = (out / f'server-{starts}.log').open('wb')
        server = subprocess.Popen([sys.executable, '-c', SERVER, str(args.port), str(stop)],
            cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and server.poll() is None:
            try:
                if client.get('/api/health', timeout=2).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(.2)
        raise RuntimeError('Acceptance server failed to start; inspect server log')

    def ready(row, actor):
        start = time.monotonic()
        previous = None
        while time.monotonic() - start < 1000:
            value = client.get('/api/workspace/recordings/' + row['id'],
                headers={'Authorization': 'Bearer ' + actor['token']}).raise_for_status().json()
            state = value['status'], value['phase']
            if state != previous:
                print(json.dumps({'recording': row['id'], 'state': state}, ensure_ascii=True), flush=True)
                previous = state
            if state[0] not in {'queued', 'running'}:
                save(out / (row['id'] + '.json'), value)
                check('real background analysis produced report', value['status'] == 'waiting_review',
                      wall_seconds=time.monotonic() - start, error=value['error'])
                return value
            time.sleep(.5)
        raise TimeoutError('Real recording did not finish within acceptance deadline')

    try:
        start_server(False)
        health = request('GET', '/api/health').json()
        check('configured actual model and ASR', health['live_available'] and health['audio_available'])
        html = request('GET', '/').text
        check('same-origin production frontend served', 'type="module"' in html)
        admin = request('POST', '/api/auth/login', json={'username': 'accept.admin', 'password': password}).json()
        team = request('POST', '/api/admin/teams', admin, 201, json={'name': '验收销售组'}).json()
        second = request('POST', '/api/admin/teams', admin, 201, json={'name': '另一组'}).json()
        actors = {'admin': admin}
        for role, name, team_id in [('boss', 'boss', None), ('manager', 'manager', team['id']),
                                    ('manager', 'manager.other', second['id'])]:
            request('POST', '/api/admin/staff', admin, 201, json={'username': name, 'name': name,
                'role': role, 'team_id': team_id, 'password': password})
            actors[name] = request('POST', '/api/auth/login', json={'username': name, 'password': password}).json()
        invite = request('POST', '/api/admin/invitations', admin, 201, json={'team_id': team['id']}).json()
        sales = request('POST', '/api/auth/register', status=201, json={'invitation': invite['code'],
            'username': 'accept.sales', 'name': '验收销售（合成）', 'password': password}).json()
        actors['sales'] = sales
        check('invitation bound sales to team', sales['user']['team_id'] == team['id'] and sales['user']['role'] == 'sales')
        request('POST', '/api/auth/register', status=410, json={'invitation': invite['code'],
            'username': 'duplicate.sales', 'name': '重复使用', 'password': password})
        meta = {'request_key': 'real-audio-restart-test'}
        def upload():
            with args.audio.open('rb') as stream:
                return request('POST', '/api/workspace/audio', sales, 201,
                    data={'metadata': json.dumps(meta)}, files={'file': (args.audio.name, stream, 'audio/wav')}).json()
        row = upload()
        check('repeat upload deduplicated', upload()['id'] == row['id'])
        check('work persisted before execution', row['status'] == 'queued')
        stop_server()
        with sqlite3.connect(settings.data_dir / 'meetflow.db') as database:
            check('queue survives server exit', database.execute('SELECT status FROM runs WHERE id=?',
                (row['run_id'],)).fetchone()[0] == 'queued')
        start_server(True)
        detail = ready(row, sales)
        check('actual live report, never demo fallback', detail['report']['body']['mode'] == 'live')
        check('complete transcript retained', bool(detail['transcript_text']) and bool(detail['evidence']))
        check('meeting date not fabricated from upload', detail['body']['meeting_date'] == '')
        audio = request('GET', '/api/workspace/recordings/' + row['id'] + '/audio', sales)
        check('stored audio matches uploaded bytes', hashlib.sha256(audio.content).hexdigest() == manifest['audio_sha256'])
        for actor in (actors['manager'], actors['boss']):
            request('GET', '/api/workspace/recordings/' + row['id'], actor, 404)
        request('GET', '/api/workspace/quality/cases?scope=center', sales, 403)
        opportunity = request('POST', '/api/workspace/business/opportunities', sales, 201,
            json={'customer_name': '合成验收客户', 'title': '公开音频技术验收（非销售业务样本）'}).json()
        submission = request('POST', '/api/workspace/recordings/' + row['id'] + '/submit', sales, 201,
            json={'version': detail['version'], 'opportunity_id': opportunity['id'], 'request_key': 'submit-real-audio'}).json()
        key = submission['id']
        request('GET', '/api/workspace/center/' + key, actors['manager'])
        request('GET', '/api/workspace/center/' + key, actors['boss'])
        request('GET', '/api/workspace/center/' + key, actors['manager.other'], 404)
        quality = request('GET', '/api/workspace/quality/cases?scope=center', actors['manager']).json()['cases']
        check('unidentified voices wait for confirmation', quality[0]['status'] == 'needs_speakers')
        draft = request('POST', '/api/workspace/reports', sales, 201, json={'template': 'brief',
            'start': '2026-11-01', 'end': '2026-11-30', 'request_key': 'real-report-test',
            'sources': [{'kind': 'personal', 'id': row['id'], 'revision': detail['version']}]}).json()
        entries = [{k: e[k] for k in ('key', 'meeting_date', 'summary', 'next_steps', 'risks', 'note')}
                   for e in draft['body']['entries']]
        confirm = request('POST', f'/api/workspace/reports/{draft["id"]}/confirm', sales,
            json={'version': draft['version'], 'title': '真实链路验收简报', 'entries': entries,
                  'plan': '公开数据仅作技术验收', 'accept_unknown_dates': True}).json()
        for kind in ('docx', 'pdf'):
            result = request('GET', f'/api/workspace/reports/{draft["id"]}/export/{kind}', sales)
            check('confirmed export same content ' + kind, result.headers['x-report-content-id'] == confirm['content_id'])
            (out / f'confirmed.{kind}').write_bytes(result.content)
        stop_server()
        with sqlite3.connect(settings.data_dir / 'meetflow.db') as database:
            before = database.execute('SELECT model_calls FROM runs WHERE id=?', (row['run_id'],)).fetchone()[0]
            check('database integrity', database.execute('PRAGMA quick_check').fetchone()[0] == 'ok')
            check('database foreign keys', not database.execute('PRAGMA foreign_key_check').fetchall())
            tables = len(database.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        start_server(True)
        restored = request('GET', '/api/workspace/recordings/' + row['id'], sales).json()
        time.sleep(2)
        with sqlite3.connect(settings.data_dir / 'meetflow.db') as database:
            after = database.execute('SELECT model_calls FROM runs WHERE id=?', (row['run_id'],)).fetchone()[0]
        check('finished work not inferred again on restart', after == before)
        check('restart retains identical report', restored['report'] == detail['report'])
        request('GET', f'/api/workspace/reports/{draft["id"]}/export/pdf', sales)
        manifest.update(status='passed', checks=len(checks), model_calls=after, database_tables=tables,
                        server_starts=starts, report_mode=detail['report']['body']['mode'])
        print(json.dumps({'folder': str(out), **{k: manifest[k] for k in
            ('status', 'checks', 'model', 'model_calls', 'database_tables', 'server_starts', 'report_mode')}},
            ensure_ascii=True), flush=True)
    except Exception as exc:
        manifest.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)}, checks=len(checks))
        raise
    finally:
        save(out / 'manifest.json', manifest)
        stop_server()
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--base-url', default='http://127.0.0.1:11435')
    parser.add_argument('--model', default='qwen3.5:4b-q4_K_M')
    parser.add_argument('--tokenizer', type=Path, required=True)
    parser.add_argument('--audio', type=Path, default=ROOT / 'data/benchmarks/clips/R8009_M8018_MS809_late.wav')
    main(parser.parse_args())
