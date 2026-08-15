"""Compare local tokenizer estimates to recorded server prompt counts, without inference."""
import json
from pathlib import Path

from meetflow.providers import context_bound

ROOT = Path(__file__).resolve().parents[2]
COHORTS = [('20261113T042310782975Z', 'qwen3-14b'),
           ('20261113T045119053186Z', 'qwen35-4b'),
           ('20261113T051201498541Z', 'qwen35-9b')]


def main():
    records = []
    for cohort, family in COHORTS:
        files = list((ROOT / 'data/models/tokenizers' / family).glob('*/tokenizer.json'))
        if len(files) != 1:
            raise ValueError(f'Expected one pinned tokenizer for {family}')
        folder = ROOT / 'test-results/backend-agent-audit/live-workflow' / cohort
        for case in ('explicit', 'revision', 'unconfirmed'):
            wire = [json.loads(line) for line in (folder / case / 'wire.jsonl').read_text(encoding='utf8').splitlines()]
            trace = [json.loads(line) for line in (folder / case / 'trace.jsonl').read_text(encoding='utf8').splitlines()]
            if len(wire) != len(trace) or any(row['status'] != 'completed' for row in trace):
                raise ValueError('Only sequential, fully matched recorded requests are used in calibration')
            for request, result in zip(wire, trace, strict=True):
                actual = result['usage']['prompt_tokens']
                estimate = context_bound(request['messages'], str(files[0]))
                byte_bound = context_bound(request['messages'])
                records.append({'run': cohort, 'case': case, 'role': result['role'], 'actual_prompt_tokens': actual,
                    'estimated_prompt_tokens': estimate, 'byte_upper_bound': byte_bound,
                    'window': request['options']['num_ctx'], 'output_cap': request['options']['num_predict']})
    result = {'protocol': 'matching official tokenizer content count +32 tokens per message including generation prefix',
        'scope': 'recorded sequential authored development requests; observed calibration, not a formal guarantee for every chat template',
        'requests': len(records), 'underestimates': sum(r['estimated_prompt_tokens'] < r['actual_prompt_tokens'] for r in records),
        'unnecessary_byte_refusals': sum(r['byte_upper_bound'] + r['output_cap'] > r['window'] and
                                       r['estimated_prompt_tokens'] + r['output_cap'] <= r['window'] for r in records),
        'records': records}
    # Reconstruct the rejected request only; this is not another model inference.
    import httpx
    from meetflow.config import Settings
    from meetflow.providers import LiveProvider
    blocked = ROOT / 'test-results/backend-agent-audit/live-workflow/20261113T052605442936Z/revision/trace.jsonl'
    last = json.loads(blocked.read_text(encoding='utf8').splitlines()[-1])
    captured = []
    def capture(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'content': '{"name":"submit_result","arguments":{}}'}})
    LiveProvider(Settings(model_api_profile='ollama_json', model_name='reconstruction-only', model_context_window=100000),
        transport=httpx.MockTransport(capture)).complete(last['role'], last['messages'], last['tools'], {})
    tokenizer = next((ROOT / 'data/models/tokenizers/qwen3-14b').glob('*/tokenizer.json'))
    result['blocked_request_reconstruction'] = {
        'byte_bound': context_bound(captured[0]['messages']),
        'tokenizer_estimate': context_bound(captured[0]['messages'], str(tokenizer)),
        'context': 8192, 'output_reserved': 1536,
        'actual_server_count': None, 'meaning': 'estimated suitability only; the rejected request was not sent'}
    (ROOT / 'labs/backend_agent_audit/results/context_calibration.json').write_text(
        json.dumps(result, indent=2) + '\n', encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k != 'records'}))


if __name__ == '__main__':
    main()
