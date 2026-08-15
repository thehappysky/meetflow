"""Metadata/API capacity only; does not simulate hundreds of completed audio inferences."""
import hashlib
import json
import platform
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from current_graph_probe import save
from meetflow.database import PersonalRecording, Run
from test_access import access


def percentile(values, proportion):
    import math
    return sorted(values)[max(0, math.ceil(len(values) * proportion) - 1)]


def main():
    out = ROOT / 'test-results/backend-agent-audit/capacity' / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True, exist_ok=False)
    files = [Path(__file__), ROOT / 'docs/32-completion-plan.md', *sorted((ROOT / 'backend/meetflow').rglob('*.py'))]
    save(out / 'manifest.json', {'scope': 'single-machine SQLite metadata workload only, inference disabled',
        'platform': platform.platform(), 'requests_per_group': 100, 'concurrency': [10, 25, 50],
        'limits': '3 synthetic sales accounts, 2 team scopes; not 100 real users, audio/model throughput or production SLA',
        'source_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})
    save(out / 'source_bundle.json', {str(p.relative_to(ROOT)): p.read_text(encoding='utf8') for p in files})
    rows = []
    with tempfile.TemporaryDirectory(prefix='meetflow-capacity-') as directory:
        fixture = access.__wrapped__(Path(directory)); a = next(fixture)
        try:
            for concurrency in (10, 25, 50):
                def create(index, concurrency=concurrency):
                    actor = ('sales.a', 'sales.a2', 'sales.b')[index % 3]
                    start = time.perf_counter()
                    result = a.client.post('/api/workspace/recordings', headers=a.actors[actor], json={
                        'title': f'合成容量验收{concurrency}-{index}', 'request_key': f'capacity-{concurrency}-{index}',
                        'transcript': f'销售：合成容量样本{concurrency}-{index}，我明天发送资料。'})
                    return {'status': result.status_code, 'seconds': time.perf_counter() - start}
                start = time.perf_counter()
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    results = list(pool.map(create, range(100)))
                elapsed = time.perf_counter() - start
                values = [r['seconds'] for r in results]
                rows.append({'phase': 'create', 'concurrency': concurrency, 'requests': len(results),
                    'success': sum(r['status'] == 201 for r in results), 'wall_seconds': elapsed,
                    'p50_seconds': percentile(values, .5), 'p95_seconds': percentile(values, .95),
                    'max_seconds': max(values), 'requests_per_second': len(results) / elapsed})
                save(out / 'results.json', {'groups': rows, 'last_raw_group': results})
                print(json.dumps(rows[-1]), flush=True)
            def read(index):
                actor = ('sales.a', 'manager.a', 'boss')[index % 3]
                path = '/api/workspace/recordings' if actor == 'sales.a' else '/api/workspace/center'
                start = time.perf_counter()
                result = a.client.get(path, headers=a.actors[actor])
                return {'status': result.status_code, 'seconds': time.perf_counter() - start}
            with ThreadPoolExecutor(max_workers=25) as pool:
                results = list(pool.map(read, range(150)))
            values = [r['seconds'] for r in results]
            rows.append({'phase': 'read', 'concurrency': 25, 'requests': len(results),
                'success': sum(r['status'] == 200 for r in results),
                'p50_seconds': percentile(values, .5), 'p95_seconds': percentile(values, .95)})
            with a.app.state.store.sessions() as session:
                counts = {'recordings': session.scalar(select(func.count()).select_from(PersonalRecording)),
                          'queued_runs': session.scalar(select(func.count()).select_from(Run).where(Run.status == 'queued'))}
            assert counts == {'recordings': 300, 'queued_runs': 300}, counts
            save(out / 'results.json', {'groups': rows, 'counts': counts, 'read_raw': results})
            print(json.dumps({'folder': str(out), 'groups': rows, 'counts': counts}), flush=True)
        finally:
            fixture.close()


if __name__ == '__main__':
    main()
