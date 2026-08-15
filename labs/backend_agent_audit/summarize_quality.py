"""Publish synthetic management-quality counters without raw audio or account data."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    runs = []
    for folder in sorted((ROOT / 'test-results/backend-agent-audit/live-quality').glob('*')):
        if not (folder / 'results.json').is_file():
            continue
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf8'))
        rows = json.loads((folder / 'results.json').read_text(encoding='utf8'))
        runs.append({'run': folder.name, 'status': manifest['status'], 'model': manifest['model'],
            'server': manifest['server'], 'limits': manifest['limits'],
            'source_hashes': {k.replace('\\', '/'):v for k,v in manifest['source_hashes'].items()},
            'attempts': len(rows), 'reports': sum(bool(r['report']) for r in rows),
            'all_narrow_checks': sum(bool(r['checks']) and all(r['checks'].values()) for r in rows),
            'cases': [{k:v for k,v in r.items() if k != 'report'} for r in rows]})
    output = ROOT / 'labs/backend_agent_audit/results/quality_summary.json'
    output.write_text(json.dumps({'scope':'adaptive development, three synthetic management scenarios',
        'limitations':['Every failed cohort remains in the record.',
            'Narrow checks are not independent human personnel-scoring accuracy.',
            'Meeting tool counters exclude submit whereas quality counters include submit; compare within the same runner.',
            'Earlier manifests record model tags and server versions but do not freeze model digests.'],
        'runs':runs},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'runs':len(runs),'output':str(output.relative_to(ROOT))}))


if __name__=='__main__':
    main()
