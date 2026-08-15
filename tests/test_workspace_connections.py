"""A one-connection pool exposes accidental nested borrowing without a slow stress test."""
from meetflow import database
from test_access import access


def test_workspace_list_detail_and_duplicate_upload_work_with_one_connection(tmp_path, monkeypatch):
    original = database.create_engine

    def one_connection(*args, **kwargs):
        return original(*args, **kwargs, pool_size=1, max_overflow=0, pool_timeout=.3)

    monkeypatch.setattr(database, 'create_engine', one_connection)
    fixture = access.__wrapped__(tmp_path)
    a = next(fixture)
    try:
        payload = {'title': '单连接回归（合成）', 'transcript': '销售：我明天发送资料。', 'request_key': 'one-pool-recording'}
        first = a.client.post('/api/workspace/recordings', headers=a.actors['sales.a'], json=payload)
        assert first.status_code == 201
        duplicate = a.client.post('/api/workspace/recordings', headers=a.actors['sales.a'], json=payload)
        assert duplicate.status_code == 201 and duplicate.json()['id'] == first.json()['id']
        listing = a.client.get('/api/workspace/recordings', headers=a.actors['sales.a'])
        assert listing.status_code == 200 and len(listing.json()) == 1
        detail = a.client.get('/api/workspace/recordings/' + first.json()['id'], headers=a.actors['sales.a'])
        assert detail.status_code == 200 and detail.json()['transcript_text'] == payload['transcript']
        assert detail.json()['evidence'] and detail.json()['report'] is None
        a.app.state.driver.process(first.json()['run_id'])
        assert a.app.state.store.get_run(first.json()['run_id'])['status'] == 'waiting_review'
        dashboard = a.client.get('/api/workspace/business/dashboard', headers=a.actors['sales.a'])
        assert dashboard.status_code == 200
        sources = a.client.get('/api/workspace/reports/sources?start=2026-11-01&end=2026-11-30',
                               headers=a.actors['sales.a'])
        assert sources.status_code == 200 and len(sources.json()) == 1
    finally:
        fixture.close()
