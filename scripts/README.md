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
