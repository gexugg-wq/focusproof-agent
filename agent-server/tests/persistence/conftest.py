from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine

from focusproof.persistence.unit_of_work import UnitOfWorkFactory


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "focusproof-test.sqlite3"


@pytest.fixture
def database_url(database_path: Path) -> str:
    return f"sqlite+pysqlite:///{database_path}"


@pytest.fixture
def alembic_config(project_root: Path, database_url: str) -> Config:
    config = Config(project_root / "alembic.ini")
    config.set_main_option("script_location", str(project_root / "agent-server/migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture
def migrated_engine(database_url: str, alembic_config: Config) -> Iterator[Engine]:
    from alembic import command

    from focusproof.persistence.database import create_database_engine

    command.upgrade(alembic_config, "head")
    engine = create_database_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def uow_factory(database_url: str) -> Iterator[UnitOfWorkFactory]:
    from focusproof.persistence.database import (
        create_database_engine,
        create_session_factory,
    )
    from focusproof.persistence.models import Base
    engine = create_database_engine(database_url)
    Base.metadata.create_all(engine)
    factory = UnitOfWorkFactory(create_session_factory(engine))
    try:
        yield factory
    finally:
        engine.dispose()
