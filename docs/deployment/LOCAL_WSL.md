# Local Development on WSL Ubuntu

## Status and safety boundary

This guide is for local development on WSL Ubuntu. The deterministic server is
bound to `127.0.0.1`, uses the production FastAPI application and official
OpenHands SDK `TestLLM`, and does not read a real LLM key.

The development identity is not production authentication. Public deployment
is blocked, production authentication is not implemented or complete, and this
guide must not be used to expose the service to a public interface.

## Prerequisites

- WSL2 Ubuntu on a Linux filesystem path
- Python 3.12 and `python3.12-venv`
- the OpenHands SDK version pinned by the repository build
- Node.js and npm only when working on the frontend
- SQLite CLI for manual backup inspection

Check the Python runtime:

```bash
python3.12 --version
```

Create the environment from the repository root:

```bash
python3.12 -m venv .venv
.venv/bin/python3.12 -m pip install --upgrade pip
.venv/bin/python3.12 -m pip install -e '.[dev]'
```

The current repository build references a local OpenHands SDK source checkout.
A new machine must provide the approved SDK source or an approved vendor
artifact before installation. Do not silently substitute another SDK version
or edit SDK source as part of deployment.

For frontend-only work:

```bash
cd frontend
npm ci
```

Task 5 verification itself is Python-only and does not run Node or npm.

## Local data and environment names

Create a private local directory:

```bash
mkdir -p .local/ai4b
chmod 700 .local/ai4b
```

Relevant names are:

- `DATABASE_URL`: SQLAlchemy URL. A SQLite path must resolve inside
  `FOCUSPROOF_DATA_DIR`.
- `FOCUSPROOF_DATA_DIR`: conversation files, locks, and local database root.
- `FOCUSPROOF_LOCK_TIMEOUT_SECONDS`: bounded session-lock wait.
- `FOCUSPROOF_API_BASE_URL`: server-side Next.js BFF target.

Do not place provider credentials in a tracked file. The deterministic server
does not require any provider-key variable.

## Apply migrations

Set the target URL explicitly in Alembic `Config`; the repository migration
environment does not read `DATABASE_URL` by itself:

```bash
DATABASE_URL="sqlite+pysqlite:///$PWD/.local/ai4b/focusproof.sqlite3" \
.venv/bin/python3.12 -c '
import os
from alembic import command
from alembic.config import Config
config = Config("alembic.ini")
config.set_main_option("script_location", "agent-server/migrations")
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
command.upgrade(config, "head")
'
```

Inspect current revision:

```bash
DATABASE_URL="sqlite+pysqlite:///$PWD/.local/ai4b/focusproof.sqlite3" \
.venv/bin/python3.12 -c '
import os
from alembic import command
from alembic.config import Config
config = Config("alembic.ini")
config.set_main_option("script_location", "agent-server/migrations")
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
command.current(config)
'
```

Test downgrade and re-upgrade only on disposable data or a verified backup:

```bash
# Use the same explicit Config pattern above, replacing the final call with:
command.downgrade(config, "-1")
command.upgrade(config, "head")
```

Run those two calls in separate Python invocations against the same explicit
`DATABASE_URL`; never rely on the fixed fallback URL in `alembic.ini`.

## Start the deterministic Agent Server

```bash
.venv/bin/python3.12 scripts/run_ai4b_test_server.py \
  --host 127.0.0.1 \
  --port 8000 \
  --database-url sqlite+pysqlite:///$PWD/.local/ai4b/focusproof.sqlite3 \
  --data-dir "$PWD/.local/ai4b" \
  --scenario general-flow
```

The script applies Alembic head and calls production `create_app`. It does not
implement a parallel HTTP review response or a second runtime.

In another WSL shell:

```bash
.venv/bin/python3.12 scripts/ai4b_smoke.py \
  --base-url http://127.0.0.1:8000
```

Add `--scripted-review` only when targeting this deterministic AI4B server.
Default smoke stops before review.

## Start the frontend

Set the server-side BFF target without exposing it to browser JavaScript:

```bash
cd frontend
FOCUSPROOF_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

The browser calls only same-origin `/api/focusproof/**` routes. The BFF is the
trust boundary and forwards only approved paths and headers.

## Health and shutdown

```bash
curl --fail --silent http://127.0.0.1:8000/health
```

`status=ok` with `readiness=null` means startup checks completed. A degraded
status or non-null readiness reason means traffic must not be admitted.

Stop the frontend with `Ctrl-C`, then stop the Agent Server with `Ctrl-C`.
Shutdown rejects new reviews, interrupts and closes active native
conversations, releases the provider registry, and disposes the database
engine. Do not kill the WSL VM while a migration or backup is running.

## Local verification

```bash
.venv/bin/python3.12 -m pytest \
  agent-server/tests/ai4b/test_release_artifacts.py -q
.venv/bin/python3.12 scripts/ai4b_check.py --backend-only
```

The full check command also contains frontend gates for later tasks. Task 5
does not execute them.
