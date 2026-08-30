# FocusProof Verification Scripts

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

# Real speech acceptance gate

Run this gate only on Linux/Python 3.12 after deliberately provisioning all
production dependencies: a disposable PostgreSQL database named with the
`focusproof_test_task3_` prefix, a healthy real Clamd endpoint, and executable
`/usr/bin/bwrap`, `/usr/bin/mediainfo`, and `/usr/bin/prlimit`. Production
inspection executes one sandboxed MediaInfo command and rejects audio when
MediaInfo cannot prove a positive duration, including truncated seekless WebM.
The browser recorder therefore emits one complete WebM/Opus chunk without a
timeslice or `requestData()`. Configure the existing speech and media settings
through the process environment or the
project's allowlisted environment loader. Never place credentials on the
command line.

The default invocation refuses to execute. A real run requires explicit
authorization, three absolute recordings under
`/tmp/focusproof-real-speech/`, and explicit authorization:

```bash
.venv/bin/python scripts/run_real_speech_gate.py \
  --authorized \
  --report /tmp/focusproof-real-speech-report.json \
  --chinese /tmp/focusproof-real-speech/chinese.webm \
  --english /tmp/focusproof-real-speech/english.webm \
  --mixed /tmp/focusproof-real-speech/mixed.webm
```

The gate reuses the production DashScope ASR adapter, TranscriptionService,
Clamd scanner, UoW, repositories, and shared resource slots. It performs one
provider call per clip with no retry; the model remains the configured
`qwen3-asr-flash` Beijing ASR model. It also runs the real Clamd
clean/EICAR/timeout/unavailable/error/oversize matrix and four real PostgreSQL
concurrency/idempotency/slot/recovery checks. Task 7 requires exactly-once nonblank candidates; languageFeatureCount is diagnostic only.

Exit 0 means every real stage passed. Exit 1 is a redacted failure. Exit 2 is a
redacted refusal or missing prerequisite. The JSON report contains only status,
model metadata, bounded durations, counts, and booleans—never credentials,
endpoints, database URLs, local paths, audio, or candidate text.
The gate never writes candidates to Evidence, the database, OpenHands, scoring,
logs, or the report. The report always sets `productManualSubmitProved` to false.
It does not prove that a user edited a candidate and clicked the product's
Submit Evidence control. That browser-to-product Evidence journey remains an
explicit Task 8 acceptance requirement and must not be inferred from this
script's report.

Ordinary pytest runs exclude the external test. After separately authorizing
the same environment and three path variables, collect or run it explicitly:

```bash
PYTHONPATH=.:agent-server .venv/bin/pytest -m real_asr \
  agent-server/tests/speech_acceptance/test_real_speech_gate_external.py
```

The gate does not start or remove Docker resources. The operator must use
fresh, uniquely named PostgreSQL and Clamd containers, health-check them, and
remove only those containers and volumes in a finally-style cleanup after the
run.

The PostgreSQL database must be newly created, use the `public` current schema,
and contain no user objects in any non-system schema. Database URL options that
override schema or search path are rejected. Freshness is checked again
immediately before the destructive PostgreSQL acceptance nodes. Discard the
explicitly named database container and volume after the run; never reuse that
database for another gate.
