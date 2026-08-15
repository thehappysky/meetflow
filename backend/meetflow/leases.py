"""Database-owned work leases with renewal and generation fencing (single host)."""
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from sqlalchemy import case, select, update
from sqlalchemy.dialects.sqlite import insert

from .database import WorkLease
from .domain import DomainError
from .workspace_lock import workspace_lock

_current = ContextVar('meetflow_work_lease', default=None)


def current_lease():
    return _current.get()


class LeaseLost(DomainError):
    def __init__(self):
        super().__init__('任务领取已失效，旧处理者停止写入，交由当前持有者恢复', 409)


class Lease:
    def __init__(self, manager, key, owner, generation, checkpoint_epoch):
        self.manager, self.key, self.owner, self.generation = manager, key, owner, generation
        self.checkpoint_epoch = checkpoint_epoch
        self.stopped, self.lost = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._renew, daemon=True, name='meetflow-lease-renew')

    def condition(self):
        return (WorkLease.id == self.key, WorkLease.owner == self.owner, WorkLease.generation == self.generation)

    def check(self):
        if self.lost.is_set():
            raise LeaseLost()
        with self.manager.sessions() as session:
            owned = session.scalar(select(WorkLease.id).where(*self.condition(), WorkLease.expires_at > time.time()))
        if owned is None:
            self.lost.set()
            raise LeaseLost()

    def _renew(self):
        while not self.stopped.wait(max(0.05, self.manager.ttl / 3)):
            try:
                with self.manager.sessions.begin() as session:
                    changed = session.execute(update(WorkLease).where(*self.condition(), WorkLease.expires_at > time.time())
                        .values(expires_at=time.time() + self.manager.ttl))
                    if changed.rowcount != 1:
                        self.lost.set(); return
            except Exception:  # noqa: BLE001 -- loss of database ownership must stop writes
                self.lost.set(); return

    def fence(self, session):
        changed = session.execute(update(WorkLease).where(*self.condition(), WorkLease.expires_at > time.time())
                                  .values(expires_at=WorkLease.expires_at))
        if changed.rowcount != 1:
            self.lost.set()
            raise LeaseLost()

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=2)
        with self.manager.sessions.begin() as session:
            session.execute(update(WorkLease).where(*self.condition()).values(owner=None, expires_at=0))


class LeaseManager:
    def __init__(self, sessions, data_dir, ttl=120):
        self.sessions, self.data_dir, self.ttl = sessions, data_dir, ttl

    @contextmanager
    def own(self, key):
        with workspace_lock(self.data_dir), self._own(key) as lease:
            yield lease

    @contextmanager
    def _own(self, key):
        owner = uuid4().hex
        lease = None
        with self.sessions.begin() as session:
            # A single UPDATE arbitrates takeover. The marker is checked under
            # the same writer transaction used by backup's barrier.
            session.execute(insert(WorkLease).values(id=key, owner=None, generation=0, checkpoint_epoch=0, expires_at=0, attempts=0)
                            .on_conflict_do_nothing(index_elements=['id']))
            if not (self.data_dir / 'maintenance.lock').exists():
                changed = session.execute(update(WorkLease).where(WorkLease.id == key, WorkLease.expires_at <= time.time())
                    .values(owner=owner, generation=WorkLease.generation + 1, attempts=WorkLease.attempts + 1,
                            checkpoint_epoch=case((WorkLease.owner.is_not(None), WorkLease.generation + 1),
                                                  else_=WorkLease.checkpoint_epoch),
                            expires_at=time.time() + self.ttl))
                if changed.rowcount == 1:
                    row = session.get(WorkLease, key, populate_existing=True)
                    lease = Lease(self, key, owner, row.generation, row.checkpoint_epoch)
        if lease is None:
            yield None
            return
        lease.thread.start()
        context_token = _current.set(lease)
        try:
            yield lease
        finally:
            _current.reset(context_token)
            lease.close()
