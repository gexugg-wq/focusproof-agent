from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_SERVER = ROOT / "agent-server"
if str(AGENT_SERVER) not in sys.path:
    sys.path.insert(0, str(AGENT_SERVER))

import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from starlette.responses import Response  # noqa: E402
import focusproof.api.app  # noqa: E402
from focusproof.openhands_runtime.demo_deterministic_provider import (  # noqa: E402
    build_demo_deterministic_test_llm,
)

LOOPBACK_HOST = "127.0.0.1"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the production FocusProof FastAPI application with a deterministic "
            "OpenHands SDK TestLLM on IPv4 loopback only."
        )
    )
    parser.add_argument("--host", required=True, choices=(LOOPBACK_HOST,))
    parser.add_argument("--port", required=True, type=_port)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--scenario", required=True, choices=("general-flow",))
    return parser.parse_args(argv)


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _apply_migrations(database_url: str) -> None:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option(
        "script_location",
        str(ROOT / "agent-server" / "migrations"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _scenario_factory(scenario: str) -> Callable[[str], object]:
    if scenario == "general-flow":
        return build_demo_deterministic_test_llm
    raise ValueError(f"Unsupported deterministic scenario: {scenario}")


def _install_image_unknown_retry_probe(application: FastAPI) -> None:
    first_keys: dict[str, str] = {}

    @application.middleware("http")
    async def image_unknown_retry_probe(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        nonlocal first_keys
        if request.method != "POST" or not request.url.path.endswith("/evidence/image"):
            return await call_next(request)
        payload = await request.body()
        if b'filename="diagram.png"' not in payload:
            return await call_next(request)
        match = re.search(rb'name="idempotency_key"\r\n\r\n([A-Za-z0-9._:-]{1,255})\r\n', payload)
        if match is None:
            return JSONResponse(
                status_code=422,
                content={"code": "invalid_idempotency_key", "retryable": False},
            )
        key = match.group(1).decode("ascii")
        session_path = request.url.path
        if session_path not in first_keys:
            first_keys[session_path] = key
            return JSONResponse(
                status_code=503,
                content={"code": "media_unavailable", "retryable": True},
            )
        if key != first_keys[session_path]:
            return JSONResponse(
                status_code=409,
                content={"code": "idempotency_conflict", "retryable": False},
            )
        return await call_next(request)


def build_app(args: argparse.Namespace) -> FastAPI:
    if args.host != LOOPBACK_HOST:
        raise ValueError("AI4B test server is restricted to 127.0.0.1")
    data_dir = Path(args.data_dir).resolve()
    database_url = str(args.database_url)
    focusproof.api.app._validate_database_path(database_url, data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    _apply_migrations(database_url)
    application = focusproof.api.app.create_app(
        database_url=database_url,
        data_dir=data_dir,
        llm_factory=_scenario_factory(str(args.scenario)),
    )
    if args.scenario == "general-flow":
        _install_image_unknown_retry_probe(application)
    return application


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    app = build_app(args)
    uvicorn.run(
        app,
        host=LOOPBACK_HOST,
        port=args.port,
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
