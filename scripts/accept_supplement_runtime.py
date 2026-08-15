"""Real local API: seller supplement -> fixed report, without another model run."""
import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import httpx
from accept_backend_runtime import save
from docx import Document

ROOT=Path(__file__).resolve().parents[1]


def main():
    account=json.loads((ROOT/'data/completion-acceptance-account.json').read_text(encoding='utf8'))
    assert account['username']=='qa.sales.accept.20261113' and '合成' in account['name']
    out=ROOT/'test-results/supplement-runtime'/datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True)
    checks=[]
    result={'status':'running','scope':'Actual local TCP/live model, named synthetic seller only; no accuracy claim'}
    save(out/'source_bundle.json',{str(p.relative_to(ROOT)):p.read_text(encoding='utf8') for p in
        [Path(__file__),*sorted((ROOT/'backend/meetflow').rglob('*.py'))]})
    def check(label,condition):
        checks.append({'check':label,'passed':bool(condition)})
        save(out/'checks.json',checks)
        assert condition,label
    with httpx.Client(base_url='http://127.0.0.1:8765',timeout=60) as client:
        token=None
        try:
            health=client.get('/api/health').raise_for_status().json()
            check('live model configured',health['live_available'] and health['auth_enabled'])
            login=client.post('/api/auth/login',json={k:account[k] for k in ('username','password')}).raise_for_status().json()
            token=login['token']; headers={'Authorization':'Bearer '+token}
            def request(method,path,status=200,**kwargs):
                response=client.request(method,path,headers=headers,**kwargs)
                check(method+' '+path+' -> '+str(status),response.status_code==status)
                return response
            row=request('POST','/api/workspace/recordings',201,json={'title':'本人补充与报告映射验收（合成）',
                'transcript':'销售李青：我明天发送资料。','meeting_date':''}).json()
            path='/api/workspace/recordings/'+row['id']
            deadline=time.monotonic()+900
            while time.monotonic()<deadline:
                detail=client.get(path,headers=headers).raise_for_status().json()
                if detail['status'] not in {'queued','running'}:break
                time.sleep(.5)
            check('actual analysis produced live report',detail['status']=='waiting_review' and detail['report']['body']['mode']=='live')
            check('unknown actual date requires confirmation',bool(detail['report']['body']['questions']) and not detail['body']['meeting_date'])
            save(out/'before.json',detail)
            def counters():
                with sqlite3.connect(ROOT/'data/meetflow.db') as database:
                    calls,meeting=database.execute('SELECT model_calls,meeting_id FROM runs WHERE id=?',(row['run_id'],)).fetchone()
                    count=database.execute('SELECT count(*) FROM runs WHERE meeting_id=?',(meeting,)).fetchone()[0]
                return {'model_calls':calls,'meeting_runs':count}
            before=counters()
            def report(version,key):
                return request('POST','/api/workspace/reports',201,json={'template':'brief','start':'2026-11-01','end':'2026-11-30',
                    'sources':[{'kind':'personal','id':row['id'],'revision':version}],'request_key':key+'-'+out.name}).json()
            def confirm(draft):
                ack=request('POST','/api/workspace/reports/'+draft['id']+'/confirm',json={'version':draft['version'],
                    'title':'本人补充映射验收（合成）','entries':[{k:e[k] for k in ('key','meeting_date','summary','next_steps','risks','note')}
                    for e in draft['body']['entries']], 'plan':'合成验收，不是实际客户资料','accept_unknown_dates':True}).json()
                confirmed=request('GET','/api/workspace/reports/'+draft['id']).json()
                check('confirmed body matches acknowledgement',confirmed['body']['content_id']==ack['content_id'])
                return confirmed
            old=confirm(report(1,'supplement-before'))
            question=detail['report']['body']['questions'][0]['key']
            answer='本人记得会谈在11月12日，仍需核实；此补充未经客户确认。'
            changed=request('POST',path+'/clarifications',201,json={'version':1,'answers':{question:answer}}).json()
            check('personal version changes without replacing analysis',changed['version']==2 and changed['run_id']==row['run_id'])
            after=request('GET',path).json()
            check('raw transcript and analysis unchanged',after['transcript_text']==detail['transcript_text'] and after['report']==detail['report'])
            check('seller note does not set actual meeting date',not after['body']['meeting_date'])
            check('saved supplement visible',after['clarifications'][-1]['body']['answers'][question]==answer)
            request('POST',path+'/clarifications',409,json={'version':1,'answers':{question:'stale edit'}})
            time.sleep(2)
            check('no extra model call or meeting run',counters()==before)
            old_read=request('GET','/api/workspace/reports/'+old['id']).json()
            check('old confirmed body immutable and source stale',old_read['body']==old['body'] and bool(old_read['stale_sources']))
            draft=report(2,'supplement-after')
            check('new report maps all latest supplement text',draft['body']['entries'][0]['supplements']==[
                {'question':detail['report']['body']['questions'][0]['text'],'answer':answer,'context':'当前分析的本人补充'}])
            fresh=confirm(draft)
            for kind in ('docx','pdf'):
                export=request('GET','/api/workspace/reports/'+fresh['id']+'/export/'+kind)
                check('same confirmed content '+kind,export.headers['x-report-content-id']==fresh['body']['content_id'])
                (out/('confirmed.'+kind)).write_bytes(export.content)
                if kind=='docx':
                    text='\n'.join(p.text for p in Document(BytesIO(export.content)).paragraphs)
                    check('Word includes clearly labelled seller supplement',answer in text and '会谈中保存的本人补充（未经客户确认）' in text)
            save(out/'after.json',after)
            result.update(status='passed',checks=len(checks),recording=row['id'],before=before,after=counters(),
                content_id=fresh['body']['content_id'],docx_sha256=hashlib.sha256((out/'confirmed.docx').read_bytes()).hexdigest())
            print(json.dumps({'folder':str(out),'status':'passed','checks':len(checks),'before':before,'after':counters()}),flush=True)
        except Exception as exc:
            result.update(status='failed',checks=len(checks),error={'type':type(exc).__name__,'message':str(exc)})
            raise
        finally:
            save(out/'manifest.json',result)
            if token:client.post('/api/auth/logout',headers={'Authorization':'Bearer '+token})


if __name__=='__main__':
    main()
