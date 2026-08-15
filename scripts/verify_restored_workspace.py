"""Read-only original-row comparison, then authenticated checks on the restored copy only."""
import argparse
import hashlib
import io
import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
from meetflow.api import create_app
from meetflow.config import Settings
from meetflow.database import make_database


def rows(path):
    with closing(sqlite3.connect(path)) as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {table: sorted(connection.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr) for table in tables}


def main(args):
    original = rows(args.bundle / 'meetflow.db')
    config = Settings(data_dir=args.restored.resolve(), start_worker=False, asr_model='')
    engine, _ = make_database(config)
    engine.dispose()
    recovered = rows(args.restored / 'meetflow.db')
    assert all(recovered[table] == values for table, values in original.items()), '旧表内容发生变化'
    checks = [{'check': 'original tables and rows unchanged after migration', 'tables': len(original), 'passed': True}]
    credentials = json.loads(args.credentials.read_text(encoding='utf8'))['accounts']
    with TestClient(create_app(config)) as client:
        for role in ('sales', 'manager', 'boss'):
            account = next(a for a in credentials if a['role'] == role)
            response = client.post('/api/auth/login', json={key: account[key] for key in ('username', 'password')})
            assert response.status_code == 200
            headers = {'Authorization': 'Bearer ' + response.json()['token']}
            for path in ('/api/workspace/recordings', '/api/workspace/center',
                         '/api/workspace/business/dashboard', '/api/workspace/quality/cases?scope=center'):
                expected = 403 if role == 'sales' and 'quality' in path else 200
                result = client.get(path, headers=headers)
                assert result.status_code == expected, (role, path, result.status_code)
                checks.append({'role': role, 'path': path, 'status': result.status_code, 'passed': True})
            if role == 'sales':
                recordings = client.get('/api/workspace/recordings', headers=headers).json()
                for recording in recordings:
                    detail = client.get('/api/workspace/recordings/' + recording['id'], headers=headers).json()
                    if detail['has_audio']:
                        content = client.get('/api/workspace/recordings/' + recording['id'] + '/audio', headers=headers)
                        assert content.status_code == 200 and len(content.content) > 100
                        digest = hashlib.sha256(content.content).hexdigest()
                        assert any(item['sha256'] == digest for item in json.loads(
                            (args.bundle / 'manifest.json').read_text(encoding='utf8'))['files'])
                        checks.append({'check': 'restored audio authenticated download matches backup', 'passed': True})
                        break
                reports = client.get('/api/workspace/reports', headers=headers).json()
                confirmed = next((report for report in reports if report['confirmed_at']), None)
                if confirmed:
                    identifiers = []
                    for format in ('docx', 'pdf'):
                        result = client.get(f'/api/workspace/reports/{confirmed["id"]}/export/{format}', headers=headers)
                        assert result.status_code == 200
                        if format == 'docx':
                            with zipfile.ZipFile(io.BytesIO(result.content)) as package:
                                assert 'word/document.xml' in package.namelist()
                        else:
                            assert result.content.startswith(b'%PDF')
                        identifiers.append(result.headers['x-report-content-id'])
                    assert identifiers[0] == identifiers[1]
                    checks.append({'check': 'restored Word and PDF same confirmed version', 'passed': True})
    result = {'status': 'passed', 'checks': checks, 'source_tables': len(original),
              'migrated_tables': len(recovered), 'original_rows': sum(len(value) for value in original.values())}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({key: value for key, value in result.items() if key != 'checks'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--restored', type=Path, required=True)
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args())
