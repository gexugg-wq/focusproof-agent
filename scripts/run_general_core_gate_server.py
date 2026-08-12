from __future__ import annotations

import argparse
import socket
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
    parser.add_argument("--fd", type=int, required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    alembic_config = Config(ROOT / "alembic.ini")
    alembic_config.set_main_option("script_location", str(ROOT / "agent-server" / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", args.database_url)
    command.upgrade(alembic_config, "head")
    app = create_app(database_url=args.database_url, data_dir=args.data_dir)
    inherited_socket = socket.socket(fileno=args.fd)
    server_config = uvicorn.Config(app, log_config=None, access_log=False)
    uvicorn.Server(server_config).run(sockets=[inherited_socket])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
