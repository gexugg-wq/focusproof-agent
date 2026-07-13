from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine


class SchemaOutOfDateError(RuntimeError):
    """Raised when the database revision does not match Alembic head."""


def check_schema_revision(engine: Engine, alembic_ini: Path) -> None:
    config = Config(alembic_ini)
    script = ScriptDirectory.from_config(config)
    expected = set(script.get_heads())
    with engine.connect() as connection:
        actual = set(MigrationContext.configure(connection).get_current_heads())
    if actual != expected:
        raise SchemaOutOfDateError("Database schema revision is out of date")
