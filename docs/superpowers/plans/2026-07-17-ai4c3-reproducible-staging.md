# AI4C.3 Reproducible Staging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the accepted service builds on a clean Linux host, runs as a single-worker OCI/Compose stack with PostgreSQL, and restores product data together with OpenHands native persistence.

**Architecture:** A capability preflight separates executable evidence from host blockers. Reproducible locks build non-root backend/frontend images. Compose runs one FastAPI worker, Next, PostgreSQL and persistent OpenHands data on private/loopback networks. PostgreSQL and native OpenHands state are captured and restored as one versioned recovery unit.

**Tech Stack:** Linux, Python 3.12, OpenHands SDK 1.31.0, OCI/Docker-compatible CLI, Compose v2, PostgreSQL, Alembic, Next production server, pytest.

## Constraints and Ownership

- Begin only after AI0 accepts AI4C.2; end with a local commit/report/stop for AI0.
- Never assume container/Compose/PostgreSQL capability. Preflight failure is a blocker, not a skipped pass represented as success.
- Test official `openhands-sdk==1.31.0` first. A custom wheel requires failed equivalence evidence and separate AI0 approval for fixed commit plus SHA-256; branch/tag/local paths are prohibited.
- Run one FastAPI worker. Do not create a scheduler, distributed runtime or second persistence implementation.
- Never read `.env`; use `.env.example` names, generated non-secret test inputs and secret mounts. Child processes remove provider keys and never print environment values.
- Default staging uses `TestLLM` and local test issuer. Do not modify scoring, product protocols, OpenHands source, identity semantics or provider admission. No push, merge, public deployment or AI4C.4.

**Create:** `scripts/check_ai4c_capabilities.py`,
`scripts/check_openhands_release_equivalence.py`, `scripts/ai4c_backup.py`,
`scripts/ai4c_restore.py`, `scripts/ai4c_staging_check.py`,
`deploy/agent-server.Dockerfile`, `deploy/frontend.Dockerfile`,
`deploy/compose.staging.yml`, root `.env.example`,
`requirements/production.lock`,
`agent-server/tests/ai4c/test_capability_preflight.py`,
`agent-server/tests/ai4c/test_openhands_release_equivalence.py`,
`agent-server/tests/ai4c/test_postgres_persistence.py`,
`agent-server/tests/ai4c/test_backup_restore.py`,
`agent-server/tests/ai4c/test_staging_stack.py`,
`agent-server/tests/ai4c/test_operational_telemetry.py`,
`docs/deployment/AI4C_STAGING.md`,
`docs/deployment/AI4C_RUNBOOK.md`, and
`docs/research/AI4C3_REPRODUCIBLE_STAGING_REPORT.md`.

**Modify only for named failures:** root `.env.example`, root `pyproject.toml`,
`agent-server/focusproof/api/app.py`,
`agent-server/focusproof/persistence/database.py`,
`agent-server/focusproof/persistence/providers.py`,
`agent-server/focusproof/persistence/repositories.py`,
`agent-server/focusproof/persistence/schema_check.py`,
`agent-server/focusproof/persistence/unit_of_work.py`, Alembic configuration,
`.gitignore`, and `frontend/package.json` plus `frontend/package-lock.json` only
if the production-start red test proves a script correction necessary. This
phase does not own UI behavior or public interfaces.

## Fixed Interfaces

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CapabilityState = Literal["available", "blocked"]

@dataclass(frozen=True, slots=True)
class CapabilityReport:
    container_cli: CapabilityState
    compose: CapabilityState
    postgres_client: CapabilityState
    linux_arch: str
    reasons: tuple[str, ...]

def detect_capabilities() -> CapabilityReport: ...
def require_capabilities(report: CapabilityReport, names: tuple[str, ...]) -> None: ...

@dataclass(frozen=True, slots=True)
class RecoveryManifest:
    schema_version: int
    application_revision: str
    database_sha256: str
    openhands_archive_sha256: str
    created_at_utc: str

def create_backup(*, database_url: str, openhands_data_dir: Path,
                  output_dir: Path) -> RecoveryManifest: ...
def restore_backup(*, manifest_path: Path, database_url: str,
                   openhands_data_dir: Path) -> None: ...
```

All subprocesses use argument arrays, `check=True`, explicit timeout and a minimal environment with `DASHSCOPE_API_KEY`, `OPENAI_API_KEY` and `FOCUSPROOF_LLM_API_KEY` removed. Output contains counts, digests and paths only.

### Task 1: Honest Capability Preflight

**Files:** `scripts/check_ai4c_capabilities.py`,
`agent-server/tests/ai4c/test_capability_preflight.py`, and root
`pyproject.toml` for marker registration.

- [ ] Write red tests mocking executable discovery/subprocess for missing container CLI, Compose, PostgreSQL client, available alternatives, non-Linux and sanitized diagnostics. `require_capabilities` must raise `CapabilityUnavailableError`, never convert absence into pass.
- [ ] Run red and observe missing module:

```bash
cd /home/holy/web3/focusproof-agent
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python -m pytest agent-server/tests/ai4c/test_capability_preflight.py -q
```

- [ ] Implement with `shutil.which`, `platform.system` and bounded `subprocess.run([...])`; accept Docker or Podman-compatible OCI CLI and Compose v2 only after version probes.
- [ ] Register `postgres` and `staging_external`. Explicitly selected fixtures stop with a clear blocker; default suites exclude them.
- [ ] Run green, Ruff/Mypy/diff check; commit `test: add honest staging capability preflight`.

### Task 2: OpenHands Release Equivalence and Locks

**Files:** `scripts/check_openhands_release_equivalence.py`,
`agent-server/tests/ai4c/test_openhands_release_equivalence.py`, root
`pyproject.toml`, and `requirements/production.lock`.

- [ ] Write red tests for an isolated temporary venv probe that installs exactly `openhands-sdk==1.31.0`, imports public `Agent`, `LocalConversation`, `EventLog`, `ToolDefinition`, `ToolExecutor`, `ActionEvent`, `ObservationEvent`, `LLM`, `TestLLM`, runs the accepted deterministic lifecycle and compares signatures/event serialization.
- [ ] Run the unit red test with installer/subprocess mocked; observe absent script.
- [ ] Implement the experiment with temporary Linux venv, bounded install/probe and output limited to version/signature/result/digest.
- [ ] Run the real controlled experiment:

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python scripts/check_openhands_release_equivalence.py \
  --version 1.31.0 --timeout-seconds 300
```

If install/network is unavailable, report blocked and stop. If comparison fails, stop and request AI0 approval before any wheel.

- [ ] On PASS only, replace the developer-local SDK path with the exact release,
  generate hash-locked `requirements/production.lock` using the repository
  dependency tool, and prove a clean venv installs with `--require-hashes`.
- [ ] Run the complete credential-free backend suite against the official package; commit `build: pin reproducible OpenHands dependencies`.

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python -m pytest agent-server/tests -q \
  -m "not real_llm and not postgres and not staging_external"
.venv/bin/ruff check agent-server scripts
.venv/bin/mypy agent-server scripts
git diff --check
```

The conditional fixed-commit wheel is deleted once an official release passes this identical probe.

### Task 3: PostgreSQL Persistence

**Files:** `agent-server/tests/ai4c/test_postgres_persistence.py`, migration
files, and the five named `focusproof/persistence` modules only for a named
dialect failure.

- [ ] Write `postgres`-marked red tests for empty-database upgrade/downgrade, UoW rollback, owner isolation, reviewed freeze, native/reference ID preservation after restart and concurrent idempotency. Fixture accepts a preflight-provided disposable URL and unique database/schema only.
- [ ] Run `.venv/bin/python scripts/check_ai4c_capabilities.py --require postgres_client`. If blocked, stop and report. Otherwise run the marked suite and capture named SQLite assumptions.
- [ ] Make minimum dialect-safe corrections while preserving UoW, SessionRunLock and API types; do not add a second persistence abstraction.
- [ ] Run PostgreSQL green plus SQLite regression, Ruff/Mypy/diff check; commit `fix: validate product persistence on PostgreSQL`.

```bash
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python -m pytest agent-server/tests/ai4c/test_postgres_persistence.py -q -m postgres
```

### Task 4: Non-Root OCI Images and Single-Worker Compose

**Files:** the three named `deploy/` files, root `.env.example`,
`agent-server/tests/ai4c/test_staging_stack.py`,
`docs/deployment/AI4C_STAGING.md`, and
`agent-server/focusproof/api/app.py` only if readiness is red.

- [ ] Write red static tests for pinned bases, non-root users, excluded `.env`/`var`/test artifacts/host venv, hash-locked backend install, `npm ci`, production Next start, exactly one Uvicorn worker, loopback publication, private database port, read-only secrets, health checks and volumes.
- [ ] Write `staging_external` test: preflight, clean Git-archive build context, deterministic FastAPI/TestLLM/local issuer, production BFF learning flow, service restart and preservation of Session/conversation/native event/projection/review IDs. Never mock a successful API.
- [ ] Run static red first; run external red only when capabilities pass. Missing capability remains a blocker.
- [ ] Implement minimal Dockerfiles/Compose. A one-shot Compose migration service
  applies Alembic before the backend starts; application startup only checks
  that the database is at exact head and never migrates implicitly. Backend
  starts exactly one worker. Frontend receives only internal API URL and public
  OIDC client metadata. Services are non-root, bounded and gracefully stopped.
- [ ] Add `/ready` only if red proves it missing. It checks migration/database/runtime registry without LLM calls or secret details and reuses app lifespan/manager/handle.
- [ ] Build and run twice from clean context; record image digests and recovery; run default regressions; commit `build: add reproducible single-host staging stack`.

### Task 5: Paired Backup, Restore and Operations

**Files:** the three named backup/restore/check scripts,
`agent-server/tests/ai4c/test_backup_restore.py`,
`agent-server/focusproof/api/app.py`,
`agent-server/tests/ai4c/test_operational_telemetry.py`, and
`docs/deployment/AI4C_RUNBOOK.md`.

- [ ] Write red unit tests for subprocess arrays, stripped keys, traversal/symlink rejection, manifest/digests, partial cleanup, revision mismatch and secret/evidence-free output.
- [ ] Write external recovery red test: create two owners plus completed review, stop writers, capture PostgreSQL dump and OpenHands archive, destroy disposable volumes, restore both, and assert all IDs plus idempotent retry are unchanged.
- [ ] Implement a maintenance lock, `pg_dump`/`pg_restore` arrays, deterministic archive metadata, SHA-256 verification and atomic output rename. No valid manifest exists until both artifacts complete.
- [ ] Add the minimum bounded operational signals through existing hooks in
  `agent-server/focusproof/api/app.py`: request/review status and latency,
  provider aggregate usage/cost, admission rejection, auth outcome, DB/runtime
  health, and recovery outcome. Reuse the Python logging/metrics primitives
  already selected by the application; do not create a new telemetry runtime.
  Never label with user content or credentials.
- [ ] Document deploy/migrate/readiness/backup/restore/rollback/incident shutdown/provider outage/identity outage/retention and single-worker limitation.
- [ ] Run unit green plus external drill after preflight and all regressions; commit `ops: prove paired staging backup and recovery`.

### Task 6: Phase Gate, Report and Stop

- [ ] Run the complete default gate without keys:

```bash
cd /home/holy/web3/focusproof-agent
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY \
  .venv/bin/python -m pytest agent-server/tests -q \
  -m "not real_llm and not postgres and not staging_external"
.venv/bin/ruff check agent-server scripts
.venv/bin/mypy agent-server scripts
cd frontend
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm run lint
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm run typecheck
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm test
env -u DASHSCOPE_API_KEY -u OPENAI_API_KEY -u FOCUSPROOF_LLM_API_KEY -u ANTHROPIC_API_KEY npm run build
cd ..
git diff --check
```

- [ ] Run explicit PostgreSQL/stack/recovery gates only if preflight passes. Missing capability or failed official SDK experiment makes AI4C.3 blocked.
- [ ] Write `AI4C3_REPRODUCIBLE_STAGING_REPORT.md` with capabilities, SDK equivalence, lock/image digests, migration counts, clean flow, paired restore IDs, monitoring/redaction, blockers and rollback.
- [ ] Commit `docs: report reproducible AI4C staging`; run diff check/status; report exact evidence and stop for AI0.

## Rollback and Gap Deletion

- Roll back images/manifests/code together to the accepted AI4C.2 revision and restore the paired data unit produced by that revision.
- Remove a conditionally approved custom wheel as soon as the official package passes equivalence.
- Remove FocusProof readiness wrappers when SDK public readiness covers runtime registry/Conversation without side effects; retain product database/migration checks.
- Backup coordination is product operations policy, never an EventLog replacement.
