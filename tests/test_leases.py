import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from meetflow.config import Settings
from meetflow.database import WorkLease, make_database
from meetflow.leases import LeaseLost, LeaseManager
from sqlalchemy import update


def test_only_one_process_style_manager_owns_same_work_and_generation_fences_old_owner(tmp_path):
    engine, sessions = make_database(Settings(data_dir=tmp_path))
    first = LeaseManager(sessions, tmp_path)
    second = LeaseManager(sessions, tmp_path)
    try:
        with first.own('meeting:test') as old:
            assert old is not None
            with second.own('meeting:test') as denied:
                assert denied is None
            with sessions.begin() as session:
                session.execute(update(WorkLease).where(WorkLease.id == 'meeting:test').values(expires_at=0))
            with second.own('meeting:test') as new:
                assert new and new.generation == old.generation + 1
                with pytest.raises(LeaseLost):
                    old.check()
                new.check()
        with first.own('meeting:test') as next_owner:
            assert next_owner
    finally:
        engine.dispose()


def test_renewal_keeps_long_work_and_maintenance_defers_new_claim(tmp_path):
    engine, sessions = make_database(Settings(data_dir=tmp_path))
    manager = LeaseManager(sessions, tmp_path, ttl=0.6)
    try:
        with manager.own('meeting:long') as lease:
            time.sleep(0.8)
            lease.check()
        (tmp_path / 'maintenance.lock').write_text('test', encoding='utf-8')
        with manager.own('meeting:new') as lease:
            assert lease is None
    finally:
        engine.dispose()


def test_simultaneous_claims_have_one_winner(tmp_path):
    import threading
    engine, sessions = make_database(Settings(data_dir=tmp_path))
    barrier = threading.Barrier(6)
    release = threading.Event()
    def claim(_):
        barrier.wait()
        with LeaseManager(sessions, tmp_path).own('meeting:same') as lease:
            if lease:
                release.wait(2)
                return 1
            return 0
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(claim, i) for i in range(6)]
            while sum(f.done() for f in futures) < 5:
                time.sleep(0.01)
            release.set()
            assert sum(f.result() for f in futures) == 1
    finally:
        engine.dispose()
