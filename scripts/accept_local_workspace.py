"""Actual HTTP acceptance against the explicitly named local synthetic account.

Only soft-removes/restores that account's own synthetic records. No real data reset.
"""
import hashlib
import io
import json
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    account = json.loads((ROOT / 'data/completion-acceptance-account.json').read_text(encoding='utf8'))
    assert account['username'] == 'qa.sales.accept.20261113' and '合成' in account['name']
    others = json.loads((ROOT / 'data/access-demo.json').read_text(encoding='utf8'))['accounts']
    out = ROOT / 'test-results/completion-live-http' / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True, exist_ok=False)
    checks, sessions = [], []
    with httpx.Client(base_url='http://127.0.0.1:8765', timeout=60) as client:
        def login(value):
            result = client.post('/api/auth/login', json={k:value[k] for k in ('username', 'password')}).raise_for_status().json()
            headers = {'Authorization': 'Bearer ' + result['token']}
            sessions.append(headers)
            return headers
        sales = login(account)
        manager = login(next(a for a in others if a['role'] == 'manager' and a['username'].endswith('.b')))
        other_manager = login(next(a for a in others if a['role'] == 'manager' and a['username'].endswith('.a')))
        boss = login(next(a for a in others if a['role'] == 'boss'))

        def expect(label, method, path, headers=sales, status=200, **kwargs):
            result = client.request(method, path, headers=headers, **kwargs)
            checks.append({'check': label, 'status': result.status_code, 'expected': status})
            (out / 'checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf8')
            assert result.status_code == status, (label, result.status_code, result.text[:250])
            return result
        try:
            health = client.get('/api/health').raise_for_status().json()
            assert health['audio_available']
            path = ROOT / 'data/benchmarks/clips/R8009_M8018_MS809_late.wav'
            metadata = {'title': '完整验收 · 公开会议录音（非销售）', 'request_key': 'completion-real-audio-20261113'}
            start = time.perf_counter()
            with path.open('rb') as stream:
                row = expect('real audio upload', 'POST', '/api/workspace/audio', status=201,
                    data={'metadata': json.dumps(metadata)}, files={'file': (path.name, stream, 'audio/wav')}).json()
            with path.open('rb') as stream:
                duplicate = expect('same upload idempotent', 'POST', '/api/workspace/audio', status=201,
                    data={'metadata': json.dumps(metadata)}, files={'file': (path.name, stream, 'audio/wav')}).json()
            assert duplicate['id'] == row['id']
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                detail = client.get('/api/workspace/recordings/' + row['id'], headers=sales).raise_for_status().json()
                if detail['status'] not in {'queued', 'running'}:
                    break
                time.sleep(.5)
            assert detail['status'] == 'waiting_review', detail.get('error')
            spans = [span for span in detail['evidence'].values() if span['source_kind'] == 'transcript']
            assert spans and all(0 <= span['start'] < span['end'] <= 56.8 for span in spans)
            assert detail['report']['body']['summary']
            assert detail['body']['meeting_date'] == ''
            checks.append({'check': 'real ASR full text and report', 'segments': len(spans),
                'wall_seconds': time.perf_counter() - start, 'analysis_mode': detail['report']['body']['mode'],
                'meeting_date_unknown': True})
            audio = expect('authenticated audio range', 'GET', '/api/workspace/recordings/' + row['id'] + '/audio',
                headers={**sales, 'Range': 'bytes=0-1023'}, status=206)
            assert audio.content == path.read_bytes()[:1024]
            for role, actor in [('manager', manager), ('boss', boss)]:
                expect(role + ' cannot read private audio', 'GET', '/api/workspace/recordings/' + row['id'], actor, 404)
            expect('sales cannot read management quality', 'GET', '/api/workspace/quality/cases?scope=center', status=403)

            center = expect('read own submitted synthetic record', 'GET', '/api/workspace/center').json()
            source = next(r for r in center if r['metadata']['title'].startswith('完整验收 · 采购审批需求会谈'))
            key = source['id']; personal = source['recording_id']
            original = expect('read current center', 'GET', '/api/workspace/center/' + key).json()
            old = expect('read prior center version', 'GET', f'/api/workspace/center/{key}/versions/1').json()
            personal_row = expect('read synthetic personal record', 'GET', '/api/workspace/recordings/' + personal).json()
            expect('soft remove synthetic personal record', 'DELETE', '/api/workspace/recordings/' + personal)
            expect('removed personal unavailable', 'GET', '/api/workspace/recordings/' + personal, status=404)
            assert expect('center retained after personal removal', 'GET', '/api/workspace/center/' + key).json() == original
            trash = expect('read personal recycle bin', 'GET', '/api/workspace/maintenance/trash?kind=personal').json()
            assert any(r['id'] == personal for r in trash)
            expect('restore own synthetic record', 'POST', f'/api/workspace/maintenance/personal/{personal}/restore',
                json={'version': personal_row['version'], 'reason': '合成完整验收：恢复个人资料'})
            for role, actor, expected in [('sales', sales, 403), ('boss', boss, 403), ('other team', other_manager, 404)]:
                expect(role + ' cannot withdraw center', 'POST', f'/api/workspace/center/{key}/delete', actor, expected,
                    json={'revision': source['revision'], 'reason': '合成权限验收'})
            expect('responsible manager withdraws synthetic center', 'POST', f'/api/workspace/center/{key}/delete', manager,
                json={'revision': source['revision'], 'reason': '合成完整验收：撤下后恢复'})
            expect('withdrawn center unavailable', 'GET', '/api/workspace/center/' + key, status=404)
            expect('boss cannot restore center', 'POST', f'/api/workspace/maintenance/center/{key}/restore', boss, 403,
                json={'version': source['revision'], 'reason': '合成权限验收'})
            expect('manager restores synthetic center', 'POST', f'/api/workspace/maintenance/center/{key}/restore', manager,
                json={'version': source['revision'], 'reason': '合成完整验收：恢复中心资料'})
            assert expect('center snapshot unchanged after restore', 'GET', '/api/workspace/center/' + key).json() == original
            assert expect('historical snapshot unchanged', 'GET', f'/api/workspace/center/{key}/versions/1').json() == old

            reports = expect('list own reports', 'GET', '/api/workspace/reports').json()
            report = next(r for r in reports if r['confirmed_at'])
            identifiers = []
            for format in ('docx', 'pdf'):
                result = expect('confirmed report export ' + format, 'GET', f'/api/workspace/reports/{report["id"]}/export/{format}')
                identifiers.append(result.headers['x-report-content-id'])
                (out / f'confirmed.{format}').write_bytes(result.content)
                if format == 'docx':
                    with zipfile.ZipFile(io.BytesIO(result.content)) as package:
                        assert '完整验收合成企业' in package.read('word/document.xml').decode('utf8')
                else:
                    assert result.content.startswith(b'%PDF')
            assert identifiers[0] == identifiers[1]
            summary = {'status': 'passed', 'checks': len(checks), 'audio_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                       'asr': 'real configured ASR', 'minutes_mode': detail['report']['body']['mode'],
                       'scope': 'actual localhost HTTP, named synthetic account only; browser file selection not certified'}
            (out / 'result.json').write_text(json.dumps(summary, indent=2), encoding='utf8')
            print(json.dumps({'folder': str(out), **summary}), flush=True)
        finally:
            for headers in sessions:
                client.post('/api/auth/logout', headers=headers)


if __name__ == '__main__':
    main()
