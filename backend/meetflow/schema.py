"""Transactional baseline adoption and versioned application schema upgrades."""
from pathlib import Path
from threading import RLock

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from .domain import DomainError
from .migrations.baseline import SCHEMA
from .migrations.baseline_types import TYPES

_migration_lock = RLock()


def migrate(engine):
    with _migration_lock:
        _migrate(engine)


def _migrate(engine):
    cfg = Config()
    cfg.set_main_option('script_location', str(Path(__file__).with_name('migrations')))
    with engine.connect() as connection:
        if engine.dialect.name == 'sqlite':
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            connection.begin()
        try:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            if tables and 'alembic_version' not in tables:
                if tables != set(SCHEMA):
                    raise DomainError('未版本化数据库不匹配34表基线；请先备份并使用对应旧版升级路径', 409)
                for name, shape in SCHEMA.items():
                    columns = sorted(c['name'] for c in inspector.get_columns(name))
                    types = [(c['name'], str(c['type']), c['nullable']) for c in inspector.get_columns(name)]
                    pk = sorted(inspector.get_pk_constraint(name)['constrained_columns'])
                    fks = sorted((col, f['referred_table'], ref) for f in inspector.get_foreign_keys(name)
                                 for col, ref in zip(f['constrained_columns'], f['referred_columns'], strict=True))
                    if columns != shape['columns'] or pk != shape['pk'] or fks != shape['foreign_keys'] or types != TYPES[name]:
                        raise DomainError(f'旧库表{name}与冻结基线不一致；已停止，未删除或改写数据', 409)
                cfg.attributes['connection'] = connection
                command.stamp(cfg, '0001')
            cfg.attributes['connection'] = connection
            command.upgrade(cfg, 'head')
            connection.commit()
        except Exception:
            connection.rollback()
            raise
