"""Process-shared read locks for service/work; exclusive lock for offline backups."""
from contextlib import contextmanager

import portalocker

from .domain import DomainError


@contextmanager
def workspace_lock(directory, exclusive=False):
    directory.mkdir(parents=True, exist_ok=True)
    flags = (portalocker.LOCK_EX if exclusive else portalocker.LOCK_SH) | portalocker.LOCK_NB
    lock = portalocker.Lock(str(directory / 'workspace.lock'), mode='a+b', flags=flags, timeout=0)
    try:
        lock.acquire()
    except portalocker.exceptions.LockException as exc:
        message = '备份需要停止所有服务和处理任务，当前仍有进程使用资料目录' if exclusive else '资料目录正在备份维护，请稍后启动'
        raise DomainError(message, 409) from exc
    try:
        yield
    finally:
        lock.release()
