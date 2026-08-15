"""Paired coverage ablation; preserve raw flags and fix only zero-item role applicability."""
import argparse
import hashlib
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTES = {'记录建议', '记下建议', '记录讨论', '记录意见', '记下意见'}


def applicable_role_check(result, coverage):
    artifacts = result.get('artifacts', [])
    required = {'facts'} | ({'coverage'} if coverage else set())
    selected = 'coverage' if coverage else 'facts'
    mentions = [m for a in artifacts if a['role'] == selected for m in a['body'].get('mentions', [])
                if m['task'] not in NOTES]
    if mentions:
        required |= {'reconcile', 'audit'}
    return required <= {a['role'] for a in artifacts}


def cohort(folder):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf8'))
    results = json.loads((folder / 'results.json').read_text(encoding='utf8'))
    rows = []
    for r in results:
        original = r.get('checks', {})
        flags = {k:v for k,v in original.items() if k != 'dynamic_roles'}
        flags['applicable_roles'] = applicable_role_check(r, manifest['coverage_check'])
        rows.append({'case': r['case'], 'status': r['status'],
            'original_all_checks': bool(original) and all(original.values()),
            'all_applicable_checks': bool(flags) and all(flags.values()),
            'checks': flags, 'model_calls': r['model_calls'],
            'input_tokens': r.get('transport_input_tokens', r['input_tokens']),
            'output_tokens': r.get('transport_output_tokens', r['output_tokens']),
            'wall_seconds': r['wall_seconds'],
            'actions': len((r.get('report') or {}).get('actions', [])),
            'questions': len((r.get('report') or {}).get('questions', []))})
    return manifest, {'run': folder.name, 'coverage': manifest['coverage_check'], 'attempts': len(rows),
        'reports': sum(r['status'] == 'waiting_review' for r in rows),
        'original_all_checks': sum(r['original_all_checks'] for r in rows),
        'all_applicable_checks': sum(r['all_applicable_checks'] for r in rows),
        'model_calls': sum(r['model_calls'] for r in rows),
        'input_tokens': sum(r['input_tokens'] for r in rows),
        'output_tokens': sum(r['output_tokens'] for r in rows),
        'wall_seconds': sum(r['wall_seconds'] for r in rows),
        'median_seconds': statistics.median(r['wall_seconds'] for r in rows), 'cases': rows}


def main(args):
    manifests, values = zip(*(cohort(p) for p in (args.with_coverage, args.without_coverage)), strict=True)
    left, right = manifests
    for field in ('status', 'model', 'profile', 'workflow', 'case_set', 'case_count', 'limits', 'context',
                  'temperature', 'seed', 'tokenizer_sha256', 'server'):
        assert left[field] == right[field], f'Not a controlled pair: {field}'
    assert left['status'] == 'completed' and left['coverage_check'] and not right['coverage_check']
    for key, value in left['source_hashes'].items():
        if key.replace('\\', '/').startswith('backend/meetflow/'):
            assert right['source_hashes'][key] == value, 'Product code changed between cohorts'
    cases = [(p / 'cases.json').read_bytes() for p in (args.with_coverage, args.without_coverage)]
    assert cases[0] == cases[1], 'Inputs differ'
    result = {'scope': 'paired local development ablation, fifteen authored cases, single repetition',
        'case_sha256': hashlib.sha256(cases[0]).hexdigest(), 'model': left['model'],
        'limits': left['limits'], 'limitations': [
            'Every original result and failed flag is preserved in the raw cohorts.',
            'Original dynamic_roles falsely requires reconcile/audit even for zero-item meetings; applicable_roles checks actual conditional work.',
            'The applicability adjustment changes no expected owner, date, status, action count, condition or uncertainty flag.',
            'Both cohorts are development regression after seeing these cases, not independent accuracy or production SLA.',
            'Single sequential passes on a shared local machine; timing differences are descriptive, not statistically significant.',
            'Valid reference IDs do not demonstrate semantic support.'], 'cohorts': values}
    output = ROOT / 'labs/backend_agent_audit/results/coverage_ablation.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps([{k:v for k,v in item.items() if k != 'cases'} for item in values]))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--with-coverage', type=Path, required=True)
    parser.add_argument('--without-coverage', type=Path, required=True)
    main(parser.parse_args())
