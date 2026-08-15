"""Real management-quality graph in an isolated authenticated test company."""
import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from current_graph_probe import TracedProvider, save
from meetflow.database import QualityCase
from role_schema_probe import DeterministicTransport
from test_access import access

CASES = [
    {'id': 'supported_process', 'complete': True, 'text':
     '销售：您目前怎么处理采购申请？\n客户：用表格重复录入，影响审批效率。\n销售：所以您想减少重复录入、缩短审批等待，我理解对吗？\n客户：对。\n销售：下一步我明天发需求确认清单给您，请您周五回复。\n客户：好，按这个安排。'},
    {'id': 'incomplete_unknown', 'complete': False, 'text':
     '销售：目前还有一些情况需要核实。\n未知：采购安排尚未确认。'},
    {'id': 'unsupported_promise', 'complete': True, 'product_notes':
     '标准部署周期至少14天，且客户须先完成安全评审。不得承诺3天上线。', 'text':
     '销售：我保证3天内上线，不用安全评审。\n客户：我们尚未完成安全评审，这样也可以吗？\n销售：可以，保证3天上线。'},
]


def main(args):
    out = ROOT / 'test-results/backend-agent-audit/live-quality' / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True, exist_ok=False)
    files = [Path(__file__), Path(__file__).with_name('role_schema_probe.py'), Path(__file__).with_name('current_graph_probe.py'),
             ROOT / 'docs/32-completion-plan.md', *sorted((ROOT / 'backend/meetflow').rglob('*.py'))]
    manifest = {'status': 'running', 'model': args.model, 'base_url': args.base_url,
        'profile': 'ollama_json', 'server': httpx.get(args.base_url + '/api/version').json(),
        'limits': {'steps': 6, 'output': 2048, 'model_calls': 48, 'tools': 100, 'seconds': 900},
        'tokenizer_sha256': hashlib.sha256(args.tokenizer.read_bytes()).hexdigest() if args.tokenizer else None,
        'source_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        'limitations': 'authored synthetic management cases, exact safety/anchor checks; no independent personnel-rating accuracy claim'}
    save(out / 'manifest.json', manifest)
    save(out / 'source_bundle.json', {str(p.relative_to(ROOT)): p.read_text(encoding='utf8') for p in files})
    save(out / 'cases.json', CASES)
    results = []
    for case in CASES:
        folder = out / case['id']; folder.mkdir()
        with tempfile.TemporaryDirectory(prefix='meetflow-live-quality-') as directory:
            fixture = access.__wrapped__(Path(directory)); a = next(fixture)
            try:
                store = a.app.state.store
                row = a.client.post('/api/workspace/recordings', headers=a.actors['sales.a'],
                    json={'title': '合成质检验收 ' + case['id'], 'transcript': case['text'], 'meeting_date': '2026-11-12'}).json()
                a.app.state.driver.process(row['run_id'])
                if case.get('product_notes'):
                    cards = a.client.get('/api/workspace/quality/policy', headers=a.actors['admin']).json()['cards']
                    card = next(c for c in cards if c['stage'] == '通用复盘')
                    changed = a.client.post('/api/workspace/quality/policy', headers=a.actors['admin'], json={
                        'base_id': card['id'], 'name': '合成部署边界', 'rules': card['rules'], 'product_notes': case['product_notes']})
                    changed.raise_for_status()
                submission = a.client.post(f'/api/workspace/recordings/{row["id"]}/submit', headers=a.actors['sales.a'],
                    json={'version': 1, 'opportunity_id': a.opportunities['sales.a'], 'request_key': 'quality-' + case['id']})
                submission.raise_for_status()
                key = a.client.get('/api/workspace/quality/cases?scope=center', headers=a.actors['manager.a']).json()['cases'][0]['id']
                with store.sessions() as session:
                    analysis_id = session.get(QualityCase, key).active_analysis_id
                a.app.state.sales_driver.process(analysis_id)  # Explicit rule-only fixture preparation; excluded from live metrics.
                detail = a.client.get('/api/workspace/quality/cases/' + key + '?view=full', headers=a.actors['manager.a']).json()
                settings = store.settings
                settings.model_name = args.model; settings.model_base_url = args.base_url
                settings.model_api_profile = 'ollama_json'; settings.max_agent_steps = 6
                settings.max_output_tokens = 2048; settings.max_model_calls = 48
                settings.model_timeout = 180; settings.run_timeout = 900
                settings.model_tokenizer_path = str(args.tokenizer.resolve()) if args.tokenizer else ''
                context = a.client.post('/api/workspace/quality/cases/' + key + '/context', headers=a.actors['manager.a'],
                    json={'version': detail['version'], 'complete': case['complete'], 'reason': '合成验收：确认本例会话完整性'})
                context.raise_for_status()
                with store.sessions() as session:
                    analysis_id = session.get(QualityCase, key).active_analysis_id
                provider = TracedProvider(settings, folder)
                provider.inner.transport = DeterministicTransport(folder)
                a.app.state.sales_driver.provider = provider
                save(folder / 'frozen_snapshot.json', a.app.state.sales.get(analysis_id)['snapshot'])
                start = time.perf_counter()
                a.app.state.sales_driver.process(analysis_id)
                analysis = a.app.state.sales.get(analysis_id)
                report = analysis['report']
                checks = {'report_present': bool(report),
                    'sales_denied': a.client.get('/api/workspace/quality/cases/' + key, headers=a.actors['sales.a']).status_code == 403,
                    'boss_visible': a.client.get('/api/workspace/quality/cases/' + key, headers=a.actors['boss']).status_code == 200}
                if report:
                    by_id = {d['rule_id']: d for d in report['decisions']}
                    checks['all_five_rules'] = set(by_id) == {'needs', 'decision', 'promise', 'objection', 'next'}
                    if case['id'] == 'supported_process':
                        checks['needs_met'] = by_id['needs']['status'] == 'met'
                        checks['next_met'] = by_id['next']['status'] == 'met'
                    elif case['id'] == 'incomplete_unknown':
                        checks['no_unmet_from_incompleteness'] = all(d['status'] != 'unmet' for d in by_id.values())
                        checks['missing_product_unknown'] = by_id['promise']['status'] in {'unknown', 'not_applicable'}
                    else:
                        checks['explicit_unsupported_promise_unmet'] = by_id['promise']['status'] == 'unmet'
                result = {'case': case['id'], 'status': analysis['status'], 'error': analysis['error'],
                    'wall_seconds': time.perf_counter() - start, 'checks': checks, 'report': report,
                    **{k: analysis[k] for k in ('model_calls', 'tool_calls', 'input_tokens', 'output_tokens')}}
                results.append(result)
                save(out / 'results.json', results)
                print(json.dumps({'folder': str(out), **{k:v for k,v in result.items() if k != 'report'}}), flush=True)
            finally:
                fixture.close()
    manifest['status'] = 'completed'; save(out / 'manifest.json', manifest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='qwen3.5:4b-q4_K_M')
    parser.add_argument('--base-url', default='http://127.0.0.1:11435')
    parser.add_argument('--tokenizer', type=Path)
    main(parser.parse_args())
