from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from meetflow.config import Settings
from meetflow.database import Base, make_database
from meetflow.domain import DomainError
from meetflow.schema import migrate
from sqlalchemy import create_engine, inspect


def legacy(engine):
    cfg = Config()
    import meetflow.migrations
    cfg.set_main_option('script_location', str(Path(meetflow.migrations.__file__).parent))
    with engine.begin() as c:
        cfg.attributes['connection'] = c
        command.upgrade(cfg, '0001')
        c.exec_driver_sql('DROP TABLE alembic_version')
        c.exec_driver_sql("INSERT INTO projects (id,name,created_at) VALUES ('old','保留旧项目','2026-01-01')")


def test_fresh_schema_matches_models_and_is_idempotent(tmp_path):
    settings = Settings(data_dir=tmp_path)
    engine, _ = make_database(settings)
    try:
        migrate(engine)
        with engine.connect() as c:
            assert c.exec_driver_sql('SELECT version_num FROM alembic_version').scalar() == '0003'
            assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
            assert c.exec_driver_sql('PRAGMA foreign_key_check').fetchall() == []
    finally:
        engine.dispose()


def test_legacy_baseline_is_adopted_without_losing_data(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "old.db"}')
    try:
        legacy(engine)
        migrate(engine)
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT name FROM projects WHERE id='old'").scalar() == '保留旧项目'
            assert c.exec_driver_sql('SELECT version_num FROM alembic_version').scalar() == '0003'
            assert {'work_leases', 'agent_conversations'} <= set(inspect(c).get_table_names())
    finally:
        engine.dispose()


def test_unknown_legacy_shape_stops_without_writing_version_or_deleting_data(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "unknown.db"}')
    try:
        with engine.begin() as c:
            c.exec_driver_sql('CREATE TABLE valuable (value TEXT)')
            c.exec_driver_sql("INSERT INTO valuable VALUES ('保留')")
        with pytest.raises(DomainError, match='基线'):
            migrate(engine)
        with engine.connect() as c:
            assert c.exec_driver_sql('SELECT value FROM valuable').scalar() == '保留'
            assert 'alembic_version' not in inspect(c).get_table_names()
    finally:
        engine.dispose()


def test_simultaneous_startup_migrates_once(tmp_path):
    def start(_):
        engine, _ = make_database(Settings(data_dir=tmp_path))
        try:
            with engine.connect() as c:
                return c.exec_driver_sql('SELECT version_num FROM alembic_version').scalar()
        finally:
            engine.dispose()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(start, range(4))) == ['0003'] * 4
