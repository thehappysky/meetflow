import json

import pytest
from fastapi.testclient import TestClient
from meetflow.api import create_app
from meetflow.config import Settings
from meetflow.domain import DomainError
from meetflow.operations import backup, restore
from meetflow.workspace_lock import workspace_lock


def test_offline_backup_restore_preserves_report_audio_and_checkpoint(tmp_path):
    data = tmp_path / 'data'
    app = create_app(Settings(data_dir=data, start_worker=False, auth_enabled=False))
    with TestClient(app) as client:
        row = client.post('/api/demo').json()
        run_id = row['run']['id']
        app.state.driver.process(run_id)
        report = app.state.store.latest_report(run_id)
        (data / 'audio').mkdir()
        (data / 'audio/test.wav').write_bytes(b'test-audio-preservation')
        with pytest.raises(DomainError, match='停止'):
            backup(data, tmp_path / 'blocked')
    bundle = tmp_path / 'bundle'
    assert backup(data, bundle)['status'] == 'complete'
    recovered = tmp_path / 'recovered'
    assert restore(bundle, recovered)['status'] == 'restored'
    assert (recovered / 'audio/test.wav').read_bytes() == b'test-audio-preservation'
    restored_app = create_app(Settings(data_dir=recovered, start_worker=False, auth_enabled=False))
    with TestClient(restored_app):
        assert restored_app.state.store.latest_report(run_id)['content_hash'] == report['content_hash']
        before = restored_app.state.store.get_run(run_id)['model_calls']
        restored_app.state.driver.process(run_id)
        assert restored_app.state.store.get_run(run_id)['model_calls'] == before
        assert restored_app.state.store.get_run(run_id)['status'] == 'waiting_review'
    with pytest.raises(DomainError, match='空目录'):
        restore(bundle, recovered)


def test_tampered_backup_and_traversal_are_rejected_before_restore_writes(tmp_path):
    data = tmp_path / 'data'
    app = create_app(Settings(data_dir=data, start_worker=False))
    with TestClient(app):
        pass
    bundle = tmp_path / 'bundle'
    backup(data, bundle)
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    manifest['files'][0]['path'] = '../escape.db'
    (bundle / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(DomainError, match='越界'):
        restore(bundle, tmp_path / 'destination')
    assert not (tmp_path / 'destination').exists()


def test_maintenance_lock_is_shared_for_workers_and_exclusive_for_backup(tmp_path):
    with workspace_lock(tmp_path), workspace_lock(tmp_path), pytest.raises(DomainError), workspace_lock(tmp_path, exclusive=True):
        pass
    with workspace_lock(tmp_path, exclusive=True), pytest.raises(DomainError), workspace_lock(tmp_path):
        pass
