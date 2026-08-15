"""Bounded real-model diagnostic of the actual production graph, not a cross-policy quality ranking."""
import argparse
import hashlib
import json
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests'))
from meetflow.agents import AgentRunner
from meetflow.providers import LiveProvider
from test_access import access


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


class TracedProvider:
    def __init__(self,settings,folder):
        self.inner=LiveProvider(settings);self.folder=folder;self.calls=[]

    def complete(self,role,messages,tools,info):
        start=time.perf_counter()
        row={'role':role,'messages':messages,'tools':tools}
        try:
            message,usage=self.inner.complete(role,messages,tools,info)
            row.update(response=message,usage=usage,status='completed')
            return message,usage
        except Exception as exc:
            row.update(status='failed',error={'type':type(exc).__name__,'message':str(exc)})
            raise
        finally:
            row['wall_seconds']=time.perf_counter()-start
            self.calls.append(row)
            with (self.folder/'trace.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(row,ensure_ascii=False)+'\n')


def main(args):
    folder=ROOT/'test-results/backend-agent-audit/current-graph'/datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    folder.mkdir(parents=True,exist_ok=False)
    original_run=AgentRunner.run
    def static_coordinator(runner,run_id,role,goal,upstream):
        if role!='coordinator':
            return original_run(runner,run_id,role,goal,upstream)
        return {'delegations':[
            {'id':'meeting','role':'meeting','goal':'依据本次会谈提取事实','depends_on':[]},
            {'id':'planner','role':'planner','goal':'核对明确承诺与责任日期','depends_on':['meeting']},
            {'id':'risk','role':'risk','goal':'核对条件与风险','depends_on':['meeting']},
            {'id':'auditor','role':'auditor','goal':'独立核对证据与错误','depends_on':['meeting','planner','risk']}]}
    with tempfile.TemporaryDirectory(prefix='meetflow-live-graph-') as directory, patch.object(
            AgentRunner,'run',static_coordinator if args.static_coordinator else original_run):
        fixture=access.__wrapped__(Path(directory));a=next(fixture)
        try:
            settings=a.app.state.store.settings
            settings.model_name=args.model;settings.model_api_key='ollama-local'
            settings.model_api_profile=args.profile
            settings.model_base_url='http://127.0.0.1:11434/v1';settings.max_output_tokens=512
            settings.max_model_calls=18;settings.max_tool_calls=40;settings.run_timeout=180;settings.max_agent_steps=6
            provider=TracedProvider(settings,folder);a.app.state.driver.provider=provider
            save(folder/'manifest.json',{'mode':'real local model, actual product graph, one development case',
                'model':settings.model_name,'generation_cap_per_call':512,'max_model_calls':18,'run_timeout':180,
                'provider_profile':args.profile,'context_window':settings.model_context_window,
                'controlled_cross_architecture_comparison':False,'schema':'production AgentResult',
                'static_coordinator':args.static_coordinator,'static_plan_calls_are_not_model_inference':True,
                'source_hashes':{name:hashlib.sha256((ROOT/'backend/meetflow'/name).read_bytes()).hexdigest()
                    for name in ('orchestration.py','agents.py','providers.py','tools.py')},
                'thinking_mode':'false' if args.profile=='ollama' else 'provider default; not a matched-thinking comparison'})
            row=a.client.post('/api/workspace/recordings',headers=a.actors['sales.a'],json={
                'title':'real graph synthetic diagnostic','meeting_date':'2026-11-12',
                'transcript':'销售王宁：我明天发送报价。'}).json()
            start=time.perf_counter();a.app.state.driver.process(row['run_id']);elapsed=time.perf_counter()-start
            run=a.app.state.store.get_run(row['run_id']);report=a.app.state.store.latest_report(row['run_id'])
            result={'status':run['status'],'error':run['error'],'wall_seconds':elapsed,
                'model_calls':run['model_calls'],'tool_calls':run['tool_calls'],
                'input_tokens':run['input_tokens'],'output_tokens':run['output_tokens'],
                'roles':dict(Counter(c['role'] for c in provider.calls)),'report':report['body'] if report else None}
            save(folder/'result.json',result)
            if run['status']=='failed':save(folder/'failure.json',{'status':run['status'],'error':run['error']})
            print(json.dumps({'folder':str(folder),**result}),flush=True)
        finally:fixture.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--static-coordinator',action='store_true')
    parser.add_argument('--model',default='qwen2.5:7b')
    parser.add_argument('--profile',choices=['openai','ollama'],default='openai')
    main(parser.parse_args())
