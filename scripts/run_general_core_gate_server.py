from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-server"))

import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from focusproof.api.app import create_app  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("script_location", str(ROOT / "agent-server" / "migrations"))
    config.set_main_option("sqlalchemy.url", args.database_url)
    command.upgrade(config, "head")
    app = create_app(database_url=args.database_url, data_dir=args.data_dir)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
