"""Publish only synthetic diagnostic counters; retain original traces and all failed runs locally."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def summarize(folder):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    results = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
    source = {key.replace('\\', '/'): value for key, value in
              json.loads((folder / 'source_bundle.json').read_text(encoding='utf-8')).items()}
    profile = manifest.get('profile', 'ollama')
    if manifest['scope'] == 'meeting':
        phase = 'meeting_protocol'
    elif profile == 'ollama':
        phase = 'original_native_graph'
    elif 'role_result_model' in source['backend/meetflow/agents.py']:
        phase = 'L2_role_schema_graph'
    else:
        phase = 'L1_structured_graph'
    model = json.loads((folder / 'model.json').read_text(encoding='utf-8'))
    return {'run': folder.name, 'phase': phase, 'model': manifest['model'], 'profile': profile,
            'server': manifest['server'], 'limits': manifest['limits'], 'status': manifest['status'],
            'model_digest': next((m['digest'] for m in manifest['models']['models'] if m['name'] == manifest['model']), None),
            'template_sha256': hashlib.sha256(model.get('template', '').encode()).hexdigest(),
            'source_hashes': {key.replace('\\', '/'): value for key, value in manifest['source_hashes'].items()
                              if key.replace('\\', '/').startswith('backend/meetflow/') or key.endswith('PLAN.md')},
            'attempts': len(results),
            'protocol_completed': sum(r['status'] in {'completed', 'waiting_review'} for r in results),
            'quality_checked_attempts': len(results) if manifest['scope'] == 'graph' else 0,
            'all_narrow_checks_passed': (sum(bool(r.get('checks')) and all(r['checks'].values()) for r in results)
                                         if manifest['scope'] == 'graph' else None),
            'results': [{key: r[key] for key in ('case', 'status', 'error', 'checks', 'wall_seconds', 'roles',
                        'model_calls', 'tool_calls', 'input_tokens', 'output_tokens', 'no_tool_responses') if key in r}
                        for r in results]}


def main():
    folders = sorted((ROOT / 'test-results/backend-agent-audit/live-workflow').glob('*'))
    runs = [summarize(f) for f in folders if (f / 'results.json').exists()]
    target = ROOT / 'labs/backend_agent_audit/results/live_workflow_summary.json'
    target.write_text(json.dumps({'scope': 'L1/L2 authored development integration diagnostics',
        'limitations': ['Three graph cases are authored development cases, not independent business gold.',
                        'Meeting protocol probes used smaller budgets and earlier source revisions.',
                        'Graph iterations are adaptive development, not independent trials or framework rankings.',
                        'Narrow checks include exact title keywords and do not replace semantic support review.',
                        'tool_calls count read gateway calls and exclude submit_result in the meeting runner.',
                        'Token counters may omit a malformed response if provider parsing failed; do not infer paid cost.'],
        'runs': runs}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(target.relative_to(ROOT)), 'runs': len(runs)}, ensure_ascii=True))


if __name__ == '__main__':
    main()
