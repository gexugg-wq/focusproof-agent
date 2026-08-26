from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-server"))

import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from focusproof.api.app import create_app  # noqa: E402

LOOPBACK_HOST = "127.0.0.1"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the official FocusProof FastAPI app in demo-deterministic mode on IPv4 loopback."
    )
    parser.add_argument("--host", required=True, choices=(LOOPBACK_HOST,))
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--data-dir", required=True, type=Path)
    return parser.parse_args(argv)


def _apply_migrations(database_url: str) -> None:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("script_location", str(ROOT / "agent-server" / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.environ.setdefault("FOCUSPROOF_PROFILE", "demo-deterministic")
    os.environ.setdefault("FOCUSPROOF_MEDIA_ENABLED", "false")
    os.environ.setdefault("FOCUSPROOF_PLUGIN_MONAD_ENABLED", "false")
    os.environ["FOCUSPROOF_DATA_DIR"] = str(args.data_dir)
    os.environ["FOCUSPROOF_DATABASE_URL"] = args.database_url
    os.environ["DATABASE_URL"] = args.database_url
    args.data_dir.mkdir(parents=True, exist_ok=True)
    _apply_migrations(args.database_url)
    app = create_app(database_url=args.database_url, data_dir=args.data_dir)
    uvicorn.run(app, host=args.host, port=args.port, log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
