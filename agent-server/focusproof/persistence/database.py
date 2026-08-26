from __future__ import annotations

import logging
import sqlite3

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def enforce_safe_database_logging() -> None:
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def create_database_engine(database_url: str) -> Engine:
    enforce_safe_database_logging()
    engine = create_engine(database_url, pool_pre_ping=True, hide_parameters=True)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def _enable_sqlite_foreign_keys(
    dbapi_connection: sqlite3.Connection,
    connection_record: object,
) -> None:
    del connection_record
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
