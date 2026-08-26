# FocusProof AI4B Scripts

All commands run from the repository root with WSL Ubuntu Python 3.12. These
helpers do not create another Runtime, Agent loop, EventLog, tool protocol,
scheduler, or HTTP review implementation.

## Deterministic loopback server

```bash
.venv/bin/python3.12 scripts/run_ai4b_test_server.py \
  --host 127.0.0.1 \
  --port 8000 \
  --database-url sqlite+pysqlite:///$PWD/.local/ai4b/focusproof.sqlite3 \
  --data-dir "$PWD/.local/ai4b" \
  --scenario general-flow
```

The server:

- accepts only `127.0.0.1`;
- applies the real Alembic head;
- calls production `focusproof.api.app.create_app`;
- injects official OpenHands SDK `TestLLM`;
- uses native tool calls, actions, observations, EventLog, and review extraction;
- does not read a real provider key or return canned review responses.

## Safe smoke

```bash
.venv/bin/python3.12 scripts/ai4b_smoke.py \
  --base-url http://127.0.0.1:8000
```

Default smoke checks health, creates a random general-learning session, and
submits non-secret text evidence. It stops before review. Add
`--scripted-review` only for the deterministic AI4B server.

The script prints generated IDs and statuses, not raw evidence, answers,
environment values, URLs, provider output, or secrets.

## Gate orchestrator

Python-only backend gates:

```bash
.venv/bin/python3.12 scripts/ai4b_check.py --backend-only
```

Full later-phase gates:

```bash
.venv/bin/python3.12 scripts/ai4b_check.py
```

The orchestrator passes each command as a subprocess argument array, stops on
the first nonzero exit, and prints only the command, duration, and exit code.
Known provider-key variables are removed from every child environment.

Task 5 verification does not run Node or npm. The default full command contains
the later frontend gates for Task 8.

## Deployment boundary

These scripts support local development and private staging evidence only. The
development identity blocks public deployment. Production authentication is
not implemented or complete.

See:

- `docs/security/THREAT_MODEL.md`
- `docs/security/SECURITY_ACCEPTANCE.md`
- `docs/deployment/LOCAL_WSL.md`
- `docs/deployment/STAGING.md`
- `docs/deployment/OPERATIONS.md`
# General Core Gate

Run the real-provider acceptance harness only from Linux with Python 3.12. It starts the
official FocusProof FastAPI application, applies the production Alembic migrations to an
isolated `/tmp` SQLite database, and exercises the official session/evidence/review/answer/events
API path for text and public-HTTPS URL scenarios. No optional plugin is loaded.

```bash
PYTHONPATH=/mnt/d/web3/focusproof-general-core-gate/agent-server \
  /home/holy/web3/focusproof-agent/.venv/bin/python \
  scripts/run_general_core_gate.py --report /tmp/general-core-gate.json
```

Provide all `FOCUSPROOF_LLM_*` settings and credentials in the process environment. The harness
does not load `.env`, never falls back to a fake provider, and reports `PASS` (exit 0), `FAIL`
(exit 1), or `BLOCKED` (exit 2). Reports contain only non-sensitive provider/model identifiers;
credential values are redacted.

The CLI uses one monotonic total deadline and passes the remaining budget to every request.
Build Log comes only from the official events endpoint; native Action/Observation counts are
validated independently from the review response. The helper inherits a pre-bound loopback
socket and sets LiteLLM's official `LITELLM_MODE=PRODUCTION` switch to prevent implicit `.env`
loading.
