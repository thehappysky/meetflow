"""Real product tool-loop / dynamic-DAG diagnostics in an isolated account database."""
import argparse
import hashlib
import json
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from current_graph_probe import TracedProvider, save
from meetflow.agents import AgentRunner
from role_schema_probe import DeterministicTransport
from test_access import access

CASES = [
    {'id': 'explicit', 'date': '2026-11-12', 'text': '销售王宁：我明天发送报价。'},
    {'id': 'revision', 'date': '2026-11-12', 'text': '销售王宁：我原定明天发送报价。\n客户李总：请改到下周一。\n销售王宁：好的，报价改为下周一发送。另外，只有贵司法务通过协议后我才安排试用，现在先不安排。'},
    {'id': 'unconfirmed', 'date': '', 'text': '客户李总：可以考虑下周演示，但时间和参会人都没确定。\n销售王宁：我先记下这个建议，今天不确定具体演示安排。'},
]


def check_report(case, report, artifacts, evidence):
    """Narrow, explicit checks on authored cases, not an LLM judge or general semantic scorer."""
    if report is None:
        return {'report_present': False}
    actions = report.get('actions', [])
    roles = {a['role'] for a in artifacts}
    checks = {'report_present': True, 'dynamic_roles': {'coordinator', 'meeting', 'planner', 'auditor'} <= roles,
              'summary_present': bool(report.get('summary')),
              'action_references_exist': all(a['evidence'] and set(a['evidence']) <= set(evidence) for a in actions)}
    quotes = [a for a in actions if '报价' in a['title']]
    if case['id'] in {'explicit', 'revision'}:
        checks['one_quote'] = len(quotes) == 1
        checks['quote_owner'] = len(quotes) == 1 and quotes[0]['owner'] in {'王宁', '销售王宁'}
        due = '2026-11-13' if case['id'] == 'explicit' else '2026-11-16'
        checks['quote_due'] = len(quotes) == 1 and quotes[0]['due'] == due
        checks['quote_status'] = len(quotes) == 1 and quotes[0]['status'] == 'todo'
    if case['id'] == 'revision':
        trials = [a for a in actions if '试用' in a['title']]
        checks['conditional_trial'] = len(trials) == 1 and trials[0]['status'] == 'blocked' and any(
            '法务' in c and '通过' in c for c in trials[0]['conditions'])
    if case['id'] == 'unconfirmed':
        checks['no_firm_demo'] = not actions
        checks['uncertainty_preserved'] = bool(report.get('questions')) or any(
            c['kind'] in {'suggestion', 'unresolved'} for c in report.get('claims', []))
    return checks


def main(args):
    folder = ROOT / 'test-results/backend-agent-audit/live-workflow' / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True, exist_ok=False)
    files = [Path(__file__), Path(__file__).with_name('PLAN.md'),
             Path(__file__).with_name('current_graph_probe.py'), Path(__file__).with_name('role_schema_probe.py'),
             *sorted((ROOT / 'backend/meetflow').glob('*.py'))]
    model_info = httpx.post('http://127.0.0.1:11434/api/show', json={'model': args.model}, timeout=30).json()
    save(folder / 'model.json', model_info)
    manifest = {'status': 'running', 'scope': args.scope, 'model': args.model, 'profile': args.profile,
        'server': httpx.get('http://127.0.0.1:11434/api/version').json(),
        'models': httpx.get('http://127.0.0.1:11434/api/tags').json(),
        'limits': {'steps': args.steps, 'output': args.output, 'calls': args.calls, 'seconds': args.seconds, 'tools': 100},
        'context': 8192, 'temperature': 0, 'seed': 2026, 'requested_think': False,
        'git_head': __import__('subprocess').check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
        'source_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        'limitations': 'agent-authored development cases; protocol completion is not semantic accuracy or independent quality validation'}
    save(folder / 'manifest.json', manifest)
    save(folder / 'source_bundle.json', {str(p.relative_to(ROOT)): p.read_text(encoding='utf-8') for p in files})
    save(folder / 'cases.json', CASES)
    results = []
    for case in CASES[:1] if args.scope == 'meeting' else CASES:
        out = folder / case['id']; out.mkdir()
        with tempfile.TemporaryDirectory(prefix='meetflow-live-workflow-') as directory:
            fixture = access.__wrapped__(Path(directory)); a = next(fixture)
            try:
                store = a.app.state.store; settings = store.settings
                # Create without inference first; freeze the same account input before enabling the local provider.
                response = a.client.post('/api/workspace/recordings', headers=a.actors['sales.a'], json={
                    'title': f'L1 synthetic {case["id"]}', 'meeting_date': case['date'], 'transcript': case['text']})
                response.raise_for_status(); row = response.json(); run_id = row['run_id']
                settings.model_name = args.model; settings.model_api_profile = args.profile
                settings.model_base_url = 'http://127.0.0.1:11434'; settings.model_context_window = 8192
                settings.max_agent_steps = args.steps; settings.max_output_tokens = args.output
                settings.max_model_calls = args.calls; settings.max_tool_calls = 100; settings.run_timeout = args.seconds
                settings.model_timeout = 90
                store.update_run(run_id, mode='live')
                provider = TracedProvider(settings, out)
                provider.inner.transport = DeterministicTransport(out)
                a.app.state.driver.provider = provider
                save(out / 'frozen_input.json', store.get_run(run_id)['manifest'])
                start = time.perf_counter()
                if args.scope == 'meeting':
                    store.update_run(run_id, status='running'); store.begin_processing(run_id)
                    try:
                        value = AgentRunner(store, provider).run(run_id, 'meeting', '提取本次明确承诺及其原文依据', {})
                        item = {'status': 'completed', 'result': value}
                    except Exception as exc:  # noqa: BLE001 -- preserve all diagnostic failures
                        item = {'status': 'failed', 'error': {'type': type(exc).__name__, 'message': str(exc)}}
                    finally:
                        store.end_processing(run_id)
                else:
                    a.app.state.driver.process(run_id)
                    run = store.get_run(run_id); report = store.latest_report(run_id)
                    item = {'status': run['status'], 'error': run['error'], 'report': report['body'] if report else None,
                            'artifacts': store.artifacts(run_id)}
                    item['checks'] = check_report(case, item['report'], item['artifacts'], store.source_map(run_id))
                run = store.get_run(run_id)
                item.update(case=case['id'], wall_seconds=time.perf_counter() - start,
                            roles=dict(Counter(c['role'] for c in provider.calls)),
                            no_tool_responses=sum(not c.get('response', {}).get('tool_calls') for c in provider.calls),
                            **{key: run[key] for key in ('model_calls', 'tool_calls', 'input_tokens', 'output_tokens')})
                save(out / 'result.json', item); results.append(item)
                save(folder / 'results.json', results)
                print(json.dumps({'folder': str(folder), **{key: value for key, value in item.items()
                    if key not in {'report', 'result', 'artifacts'}}}, ensure_ascii=True), flush=True)
            finally:
                fixture.close()
    manifest['status'] = 'completed'; save(folder / 'manifest.json', manifest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scope', choices=['meeting', 'graph'], default='meeting')
    parser.add_argument('--model', default='qwen2.5:7b')
    parser.add_argument('--profile', choices=['ollama', 'ollama_json'], default='ollama')
    parser.add_argument('--steps', type=int, default=6)
    parser.add_argument('--output', type=int, default=512)
    parser.add_argument('--calls', type=int, default=6)
    parser.add_argument('--seconds', type=int, default=180)
    main(parser.parse_args())
