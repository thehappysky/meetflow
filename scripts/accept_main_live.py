"""Run actual meeting and management agents on a named local synthetic account.

Creates a new clearly labelled synthetic record; never alters other records.
"""
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from meetflow.database import QualityCase, SalesAnalysis

ROOT = Path(__file__).resolve().parents[1]


def main():
    account = json.loads((ROOT / 'data/completion-acceptance-account.json').read_text(encoding='utf8'))
    assert account['username'] == 'qa.sales.accept.20261113' and '合成' in account['name']
    existing = json.loads((ROOT / 'data/access-demo.json').read_text(encoding='utf8'))['accounts']
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    out = ROOT / 'test-results/main-live' / stamp
    out.mkdir(parents=True)
    checks, tokens = [], []
    result = {'status': 'running', 'scope': 'actual localhost live model, synthetic account only; not business accuracy'}
    def save(name, value):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    save('manifest.json', result)
    with httpx.Client(base_url='http://127.0.0.1:8765', timeout=60) as client:
        def check(label, condition):
            checks.append({'check': label, 'passed': bool(condition)})
            save('checks.json', checks)
            assert condition, label
        def request(method, path, actor=None, status=200, **kwargs):
            headers = {'Authorization': 'Bearer ' + actor['token']} if actor else {}
            r = client.request(method, path, headers=headers, **kwargs)
            check(f'{method} {path} -> {status}', r.status_code == status)
            return r
        def login(value):
            row = request('POST', '/api/auth/login', json={k:value[k] for k in ('username', 'password')}).json()
            tokens.append(row)
            return row
        def wait(path, actor, label):
            deadline = time.monotonic() + 1000
            previous = None
            while time.monotonic() < deadline:
                value = client.get(path, headers={'Authorization':'Bearer ' + actor['token']}).raise_for_status().json()
                if value['status'] != previous:
                    print(json.dumps({'stage':label,'status':value['status']},ensure_ascii=True),flush=True)
                    previous = value['status']
                if value['status'] not in {'queued', 'running'}:
                    save(label + '.json', value)
                    check(label + ' produced live report', value['status'] == 'waiting_review' and bool(value['report']))
                    return value
                time.sleep(.5)
            raise TimeoutError(label + ' timed out')
        try:
            check('main server has actual model', request('GET','/api/health').json()['live_available'])
            sales = login(account)
            manager = login(next(a for a in existing if a['username'].endswith('.b') and a['role']=='manager'))
            other = login(next(a for a in existing if a['username'].endswith('.a') and a['role']=='manager'))
            boss = login(next(a for a in existing if a['role']=='boss'))
            opportunity = request('POST','/api/workspace/business/opportunities',sales,201,
                json={'customer_name':'真实AI链路合成客户','title':'仅作系统验收 ' + stamp}).json()
            row = request('POST','/api/workspace/recordings',sales,201,json={
                'title':'真实AI验收 · 需求确认（合成） ' + stamp, 'meeting_date':'2026-11-13',
                'request_key':'main-live-' + stamp, 'opportunity_id':opportunity['id'],
                'transcript':'销售王宁：您目前怎么整理采购申请？\n客户陈总：用表格重复录入，影响审批效率。\n销售王宁：所以您想减少重复录入、缩短审批等待，我理解对吗？\n客户陈总：对。\n销售王宁：我明天发送需求确认清单。\n客户陈总：好的。'}).json()
            started = time.monotonic()
            detail = wait('/api/workspace/recordings/' + row['id'],sales,'meeting')
            check('minutes actually live',detail['report']['body']['mode']=='live')
            check('latest frozen evidence protocol', detail['report']['body'].get('evidence_reference_protocol') == 'refs_v1'
                  and detail['report']['body'].get('evidence_prompt_revision') == 'state_v2')
            request('GET','/api/workspace/recordings/' + row['id'],manager,404)
            submission = request('POST',f'/api/workspace/recordings/{row["id"]}/submit',sales,201,
                json={'version':detail['version'],'opportunity_id':opportunity['id'],
                      'request_key':'main-submit-' + stamp}).json()
            center = submission['id']
            request('GET','/api/workspace/center/' + center,boss)
            request('GET','/api/workspace/center/' + center,other,404)
            rows = request('GET','/api/workspace/quality/cases?scope=center',manager).json()['cases']
            key = next(c['id'] for c in rows if c['source_id']==center)
            quality = wait('/api/workspace/quality/cases/' + key + '?view=full',manager,'quality')
            check('quality actually live',quality['mode']=='live')
            check('quality bound to uploader',quality['subject']['account_id']==sales['user']['id'])
            check('five explicit quality rules',len(quality['report']['decisions'])==5)
            with sqlite3.connect(ROOT / 'data/meetflow.db') as database:
                frozen, artifacts, calls = database.execute(
                    f'SELECT a.snapshot,a.artifacts,a.model_calls FROM "{SalesAnalysis.__tablename__}" a '
                    f'JOIN "{QualityCase.__tablename__}" q ON q.active_analysis_id=a.id WHERE q.id=?', (key,)).fetchone()
            check('latest management audit persisted', json.loads(frozen).get('audit_protocol') == 'comparison_v2')
            check('per-rule review and actual usage persisted', calls > 0 and any(
                k.endswith(':auditor') and len(v.get('checks', [])) == 5 for k,v in json.loads(artifacts).items()))
            request('GET','/api/workspace/quality/cases/' + key,sales,403)
            request('GET','/api/workspace/quality/cases/' + key,other,404)
            request('GET','/api/workspace/quality/cases/' + key,boss)
            summary = request('GET','/api/workspace/quality/cases/' + key + '?view=summary',manager).json()
            check('summary hides full evidence and reasons', 'spans' not in summary and 'report' not in summary and
                all('reason' not in d and 'evidence' not in d for d in summary['decisions']))
            deeper = request('GET','/api/workspace/quality/cases/' + key + '?view=details',manager).json()
            check('second level includes reasons but not transcript', 'spans' not in deeper and bool(deeper['decisions']))
            check('third level includes source evidence',bool(quality['spans']))
            result.update(status='passed',checks=len(checks),recording_id=row['id'],center_id=center,
                          quality_id=key,wall_seconds=time.monotonic()-started,
                          meeting_mode=detail['report']['body']['mode'],quality_mode=quality['mode'])
            print(json.dumps({'folder':str(out),**result},ensure_ascii=True),flush=True)
        except Exception as exc:
            result.update(status='failed',error={'type':type(exc).__name__,'message':str(exc)})
            raise
        finally:
            save('manifest.json',result)
            for actor in tokens:
                client.post('/api/auth/logout',headers={'Authorization':'Bearer ' + actor['token']})


if __name__=='__main__':
    main()
