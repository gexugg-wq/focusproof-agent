from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openhands.sdk.testing import TestLLM

from focusproof.api.app import create_app


@dataclass(frozen=True)
class RunningAi4bApp:
    app: FastAPI
    client: TestClient
    database_url: str
    data_dir: Path


Ai4bAppFactory = Callable[
    [Callable[[str], TestLLM]],
    Iterator[RunningAi4bApp],
]


@pytest.fixture
def ai4b_app_factory(tmp_path: Path) -> Callable[..., object]:
    @contextmanager
    def factory(
        llm_factory: Callable[[str], TestLLM],
        *,
        review_timeout_seconds: float = 60.0,
    ) -> Iterator[RunningAi4bApp]:
        project_root = Path(__file__).resolve().parents[3]
        database_url = f"sqlite+pysqlite:///{tmp_path / 'ai4b.sqlite3'}"
        config = Config(project_root / "alembic.ini")
        config.set_main_option(
            "script_location",
            str(project_root / "agent-server/migrations"),
        )
        config.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(config, "head")
        app = create_app(
            database_url=database_url,
            data_dir=tmp_path,
            llm_factory=llm_factory,
            review_timeout_seconds=review_timeout_seconds,
        )
        with TestClient(app) as client:
            yield RunningAi4bApp(
                app=app,
                client=client,
                database_url=database_url,
                data_dir=tmp_path,
            )

    return factory
