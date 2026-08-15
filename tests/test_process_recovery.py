"""Actual OS process death; test adapter only, not model-quality evidence."""
import os
import subprocess
import sys
import time
from pathlib import Path

from meetflow.leases import LeaseManager
from meetflow.orchestration import RunDriver
from test_evidence_pipeline import TypedAdapter
from test_evidence_pipeline import typed_store as typed_store  # noqa: PLC0414

CHILD = '''
import sys,time
from pathlib import Path
from meetflow.config import Settings
from meetflow.database import make_database
from meetflow.store import Store
from meetflow.orchestration import RunDriver
from test_evidence_pipeline import TypedAdapter
data,run_id,ready=sys.argv[1:]
settings=Settings(data_dir=Path(data),start_worker=False,meeting_workflow='evidence_v1',model_name='test-adapter',model_api_key='test-only')
engine,sessions=make_database(settings)
store=Store(sessions,settings)
original=store.save_conversation
def save(*args):
 original(*args)
 if args[3][-1]['role']=='assistant':
  Path(ready).write_text('model response persisted')
  while True: time.sleep(.1)
store.save_conversation=save
driver=RunDriver(store,TypedAdapter());driver.leases.ttl=1.5
driver.process(run_id)
'''


def test_killed_process_keeps_response_and_new_generation_recovers_without_duplicate_inference(typed_store, tmp_path):
    store, run = typed_store
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(root / 'backend'), str(root / 'tests')])}
    ready = tmp_path / 'child-ready'
    process = subprocess.Popen([sys.executable, '-c', CHILD, str(store.settings.data_dir), run['id'], str(ready)],
                               env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert ready.exists(), process.communicate(timeout=1)
        with LeaseManager(store.sessions, store.settings.data_dir).own(f'meeting:{run["id"]}') as denied:
            assert denied is None
        process.kill()
        process.communicate(timeout=5)
        time.sleep(1.8)
        driver = RunDriver(store, TypedAdapter())
        try:
            driver.process(run['id'])
            assert store.get_run(run['id'])['status'] == 'waiting_review'
            assert store.get_run(run['id'])['model_calls'] == 4
            assert store.latest_report(run['id'])['body']['actions'][0]['owner'] == '王宁'
            assert len(store.artifacts(run['id'])) == 4
        finally:
            driver.close()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
