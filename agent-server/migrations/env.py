from __future__ import annotations

import logging
from logging.config import fileConfig
from typing import cast

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

from focusproof.persistence.models import Base  # noqa: E402

target_metadata = Base.metadata


def _apply_database_url_override() -> None:
    raw = cast(list[str], context.get_x_argument())
    if not raw:
        return
    if len(raw) != 1:
        raise ValueError("invalid Alembic database_url override count")
    key, separator, value = raw[0].partition("=")
    if key != "database_url" or not separator:
        raise ValueError("unknown Alembic override key")
    if not value:
        raise ValueError("empty Alembic database_url override")
    try:
        make_url(value)
    except Exception:
        raise ValueError("unparseable Alembic database_url override") from None
    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))


_apply_database_url_override()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        hide_parameters=True,
    )
    with connectable.connect() as connection:
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
