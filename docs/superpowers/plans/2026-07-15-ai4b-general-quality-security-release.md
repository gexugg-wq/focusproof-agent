# AI4B General Quality, Security & Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove and harden the existing general learning-verification product so its real OpenHands-backed flow is safe, recoverable, visually accepted, and ready for a vendor-neutral staging deployment.

**Architecture:** Keep FastAPI, SQLite, the Next.js BFF, and OpenHands SDK `Agent`/`LocalConversation`/native events as the only execution path. Use deterministic SDK `TestLLM` scripts for repeatable real-runtime acceptance, add bounded validation and persistence-backed idempotency only when a failing test proves a gap, and map every release claim to automated or captured evidence.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy, Alembic, OpenHands SDK 1.31.0, pytest, Ruff, Mypy, Next.js 15, TypeScript, Vitest, Playwright.

## Global Constraints

- Baseline is `8c04372`; work only on `ai4b-general-quality-security-release`.
- Do not add a Runtime, Agent loop, Conversation, EventLog, tool protocol, or runtime truth store.
- Continue using OpenHands `Agent`, `LocalConversation`, `ConversationState`, `EventLog/View`, native Message/Action/Observation events, `ToolDefinition`, `ToolExecutor`, `interrupt`, `close`, recovery, and persistence.
- OpenHands default programming tools remain disabled.
- Do not modify core scoring rules. Capture any scoring defect for AI0 instead.
- Do not select an identity provider. The current development identity is a public-release blocker.
- Do not modify public architecture/protocol documents, `contracts/`, `.env`, `var/`, or SDK source.
- Do not implement Web3, wallet writes, on-chain proof, or multimodal processing.
- Default tests use deterministic SDK `TestLLM`; do not execute real-LLM smoke without AI0 authorization.
- Every production change starts with a focused failing test and uses the smallest repair.
- Use small local commits. Do not push, merge, or deploy publicly.

## File Responsibility Map

- `agent-server/tests/ai4b/conftest.py`: real migrated FastAPI/OpenHands fixtures and scripted SDK messages.
- `agent-server/tests/ai4b/test_general_quality.py`: four-domain and six quality-scenario acceptance.
- `agent-server/tests/ai4b/test_api_security.py`: ownership, forgery, input bounds, sanitization, and replay acceptance.
- `agent-server/tests/ai4b/test_reliability.py`: restart, failure injection, concurrency, cancellation, and shutdown acceptance.
- `agent-server/tests/ai4b/test_release_artifacts.py`: secret scans and release-artifact invariants.
- `agent-server/focusproof/api/models.py`: request-field bounds and evidence-shape validation.
- `agent-server/focusproof/api/app.py`: request-size guard and persistence-backed duplicate handling.
- `agent-server/focusproof/persistence/repositories.py`: content-hash lookup used for atomic evidence deduplication.
- `agent-server/focusproof/persistence/models.py` and a new Alembic revision only if a failing concurrent test proves the existing index is insufficient.
- `scripts/run_ai4b_test_server.py`: loopback-only migrated FastAPI process with deterministic SDK LLM scripts.
- `scripts/ai4b_check.py`: gate orchestrator that prints commands/results and never reads secret values.
- `scripts/ai4b_smoke.py`: vendor-neutral health and local/staging HTTP smoke checks.
- `frontend/tests/security-and-recovery.test.tsx`: XSS, error visibility, input retention, and state recovery.
- `frontend/e2e/ai4b-real-flow.spec.ts`: non-mocked BFF/FastAPI browser flow, geometry checks, restart recovery, and screenshots.
- `frontend/playwright.config.ts`: starts the real AI4B test server and Next.js BFF for the real-flow project.
- `docs/security/*`, `docs/deployment/*`, and `docs/research/*`: security, operating, visual, and final acceptance evidence.

---

### Task 1: Real OpenHands Acceptance Harness and Four-Domain Matrix

**Files:**
- Create: `agent-server/tests/ai4b/__init__.py`
- Create: `agent-server/tests/ai4b/conftest.py`
- Create: `agent-server/tests/ai4b/test_general_quality.py`
- Reuse: `agent-server/tests/openhands_runtime/conftest.py`
- Reuse: `agent-server/focusproof/api/app.py`

**Interfaces:**
- Consumes: `focusproof.api.app.create_app(database_url, data_dir, llm_factory)` and SDK `TestLLM.from_messages(...)`.
- Produces: `migrated_client(tmp_path, llm_factory)` and `scripted_review_llm(scenario)` fixtures for Tasks 2–4.

- [ ] **Step 1: Create a real migrated application fixture**

Write `agent-server/tests/ai4b/conftest.py` with a fixture that applies the real
Alembic head and enters `TestClient(create_app(...))`:

```python
from collections.abc import Callable, Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from openhands.sdk.testing import TestLLM

from focusproof.api.app import create_app


def migrated_client(
    tmp_path: Path,
    llm_factory: Callable[[str], TestLLM],
) -> Iterator[TestClient]:
    root = Path(__file__).resolve().parents[3]
    database_url = f"sqlite+pysqlite:///{tmp_path / 'ai4b.sqlite3'}"
    config = Config(root / "alembic.ini")
    config.set_main_option("script_location", str(root / "agent-server/migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    with TestClient(
        create_app(database_url=database_url, data_dir=tmp_path, llm_factory=llm_factory)
    ) as client:
        yield client
```

The actual fixture must expose the database URL and data directory as well as
the client so Task 4 can restart the app against the same files.

- [ ] **Step 2: Write failing native-flow and domain matrix tests**

Parameterize programming, mathematics, language, and reading sessions. Each
script must emit an SDK `MessageToolCall` for the matching evidence verifier,
then a learner-input call, then after the answer a review-draft call. Assert:

```python
@pytest.mark.parametrize("case", GENERAL_DOMAIN_CASES, ids=lambda case: case.domain)
def test_general_domain_uses_native_action_observation_and_review(case, ai4b_app):
    session_id = create_session(ai4b_app.client, case)
    evidence_id = submit_text(ai4b_app.client, session_id, case.text)
    first = ai4b_app.client.post(f"/sessions/{session_id}/review")
    assert first.json()["reviewStatus"] == "awaiting_user"
    question = first.json()["agentQuestions"][0]
    ai4b_app.client.post(
        f"/sessions/{session_id}/answer",
        json={"questionId": question["questionId"], "answer": case.answer},
    ).raise_for_status()
    completed = ai4b_app.client.post(f"/sessions/{session_id}/review")
    assert completed.json()["reviewStatus"] == "completed"
    assert completed.json()["reviewResult"] is not None

    handle = ai4b_app.manager.get_or_restore(session_id, "dev-anonymous-user")
    native_events = list(handle.conversation.state.events)
    action_index = next(i for i, event in enumerate(native_events) if event.__class__.__name__ == "ActionEvent")
    observation_index = next(i for i, event in enumerate(native_events) if event.__class__.__name__ == "ObservationEvent")
    assert action_index < observation_index
    assert evidence_id in json.dumps([event.model_dump(mode="json") for event in native_events])
```

Also submit a URL evidence record and script the real URL verification tool
through a deterministic test fetcher already supported by the URL-tool tests.
Do not bypass `ToolExecutor` or append events manually.

- [ ] **Step 3: Run the new tests and record the expected first failures**

Run:

```bash
pytest agent-server/tests/ai4b/test_general_quality.py -q
```

Expected: failures identify missing fixture wiring or quality behavior; no test
may pass by substituting a fake Conversation or direct ReviewResult.

- [ ] **Step 4: Complete the scripted SDK message sequences and fixture access**

Use only `openhands.sdk.testing.TestLLM`, `Message`, `MessageToolCall`, and
`TextContent`. Expose the production `ConversationManager` from
`client.app.state.conversation_manager`. Use unique tool-call IDs per scripted
message and repository-backed evidence IDs returned by the API.

- [ ] **Step 5: Add the six conservative quality scenarios**

Add tests named:

```text
test_vague_notes_never_receive_high_confidence
test_goal_copy_is_not_independent_evidence
test_goal_evidence_mismatch_is_reported
test_correct_reflection_can_support_an_error_record
test_strong_follow_up_can_improve_support
test_elapsed_time_alone_never_proves_learning
```

Assertions must use approved `ReviewResult` fields and current score meanings.
If any assertion can pass only by editing `focusproof/domain/scoring.py`, leave
the failing fixture unchanged, record it under an `AI0 scoring decision`
heading in the final report, and stop that repair.

- [ ] **Step 6: Verify and commit Task 1**

Run:

```bash
pytest agent-server/tests/ai4b/test_general_quality.py -q
pytest agent-server/tests/openhands_runtime/test_native_event_flow.py -q
git diff --check
```

Expected: all selected tests pass, or an explicit scoring stop condition is
reported before production scoring changes.

Commit:

```bash
git add agent-server/tests/ai4b
git commit -m "test: add AI4B general runtime acceptance matrix"
```

### Task 2: Persistence-Backed Idempotency and Input Bounds

**Files:**
- Create: `agent-server/tests/ai4b/test_api_security.py`
- Modify: `agent-server/focusproof/api/models.py`
- Modify: `agent-server/focusproof/api/app.py`
- Modify: `agent-server/focusproof/persistence/repositories.py`
- Test: `agent-server/tests/api/test_api_sessions.py`
- Test: `agent-server/tests/persistence/test_session_repository.py`

**Interfaces:**
- Consumes: `hash_evidence_content(text_content, source_url)` and existing repository/UoW transactions.
- Produces: `EvidenceRepository.get_by_content_hash(session_id, content_hash)` and bounded Pydantic request models; no public request/response field is added.

- [ ] **Step 1: Write failing sequential and concurrent replay tests**

Submit the same evidence twice and assert the same `evidenceId`, one stored row,
and one `evidence.submitted`/native synchronization sequence. Submit the same
answer twice and request the same completed review twice; assert one logical
answer version, one review row, and no new native events after completion.

For concurrency, use two clients/threads against one file-backed SQLite database
and a barrier before the duplicate writes:

```python
def test_duplicate_evidence_is_one_logical_record(ai4b_app):
    session_id = create_session(ai4b_app.client, PROGRAMMING_CASE)
    payload = {"evidenceType": "text", "textContent": PROGRAMMING_CASE.text}
    first = ai4b_app.client.post(f"/sessions/{session_id}/evidence", json=payload)
    second = ai4b_app.client.post(f"/sessions/{session_id}/evidence", json=payload)
    assert second.status_code == 200
    assert second.json()["evidenceId"] == first.json()["evidenceId"]
    detail = ai4b_app.client.get(f"/sessions/{session_id}").json()
    assert len(detail["state"]["evidence"]) == 1
```

- [ ] **Step 2: Run replay tests to verify the existing random-ID failure**

Run:

```bash
pytest agent-server/tests/ai4b/test_api_security.py -q -k "duplicate or replay"
```

Expected: duplicate evidence assertion fails because the current endpoint
creates `ev_<uuid>` for each request.

- [ ] **Step 3: Add repository lookup and deterministic internal identity**

Extend `EvidenceRepository` and `SqlEvidenceRepository`:

```python
def get_by_content_hash(self, session_id: str, content_hash: str) -> StoredEvidence | None:
    model = self._session.scalar(
        select(EvidenceModel).where(
            EvidenceModel.session_id == session_id,
            EvidenceModel.content_hash == content_hash,
        ).order_by(EvidenceModel.created_at, EvidenceModel.evidence_id)
    )
    return _stored_evidence(model) if model is not None else None
```

In `submit_evidence`, calculate the approved content hash first, return the
existing owned-session record when found, and derive a bounded internal ID such
as `ev_` plus the first 48 hex characters of SHA-256 over
`session_id + evidence_type + content_hash + canonical_metadata`. Catch only the
expected uniqueness race, reopen a clean UoW, and return the winning record.
Never catch a broad database error as duplicate success.

If the concurrent test proves a database uniqueness gap, add one Alembic
revision with a unique constraint on the exact logical idempotency key and add
upgrade/downgrade/re-upgrade coverage to
`agent-server/tests/persistence/test_migrations.py`.

- [ ] **Step 4: Write failing Pydantic bounds and shape tests**

Assert `422` before manager execution for empty/oversized domain, title, goal,
evidence type, text, URL, answer, question ID, invalid planned minutes, and
metadata beyond the approved JSON byte/depth/item budget. Assert text evidence
requires text and URL evidence requires both URL and explanation.

Use constants in `api/models.py` and strict field definitions:

```python
MAX_GOAL_CHARS = 20_000
MAX_EVIDENCE_TEXT_CHARS = 100_000
MAX_URL_CHARS = 2_048
MAX_ANSWER_CHARS = 20_000

class SubmitAnswerRequest(BaseModel):
    questionId: str = Field(min_length=1, max_length=128)
    answer: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)
```

Add a model validator for cross-field evidence shape and a deterministic
metadata serialization size check. Do not truncate accepted learner content.

- [ ] **Step 5: Add and test an ASGI request-body ceiling**

Write a test posting a body larger than the documented limit with a sentinel
that must not appear in the response or database. Implement a small middleware
in `api/app.py` that rejects a declared oversized `Content-Length` and caps
streamed bytes before Pydantic/LLM work. Return `413` with only:

```json
{"code":"request_too_large","retryable":false}
```

- [ ] **Step 6: Run regression tests and commit Task 2**

Run:

```bash
pytest agent-server/tests/ai4b/test_api_security.py -q -k "duplicate or replay or oversized or validation"
pytest agent-server/tests/api/test_api_sessions.py agent-server/tests/persistence -q
ruff check agent-server/focusproof/api agent-server/focusproof/persistence agent-server/tests/ai4b
mypy agent-server
```

Commit only the migration if the concurrency test required it:

```bash
git add agent-server/focusproof agent-server/tests/ai4b agent-server/tests/api agent-server/tests/persistence agent-server/migrations
git commit -m "fix: bound and deduplicate session submissions"
```

### Task 3: General Security Boundary Tests and Minimal Repairs

**Files:**
- Modify: `agent-server/tests/ai4b/test_api_security.py`
- Modify: `agent-server/tests/openhands_runtime/test_url_safety.py`
- Modify: `agent-server/tests/openhands_runtime/test_url_evidence_tool.py`
- Modify only on a demonstrated failure: `agent-server/focusproof/openhands_runtime/tools/url_safety.py`
- Modify only on a demonstrated failure: `agent-server/focusproof/openhands_runtime/tools/url_fetcher.py`
- Modify only on a demonstrated failure: `agent-server/focusproof/openhands_runtime/url_redaction.py`
- Modify: `frontend/tests/security-and-recovery.test.tsx`
- Modify only on a demonstrated failure: `frontend/app/api/focusproof/[...path]/route.ts`
- Modify only on a demonstrated failure: `frontend/features/session/SessionWorkspace.tsx`

**Interfaces:**
- Consumes: current verified-identity dependency override, repository-backed tool lookup, URL policy/fetcher, React default escaping, and BFF allowlist.
- Produces: automated evidence for SEC-01 through SEC-11 without choosing production auth.

- [ ] **Step 1: Prove owner isolation on every session-derived endpoint**

Override `get_verified_identity` with owner A to create a session, switch to
owner B, and parameterize GET session/events/reviews and POST evidence/answer/
review. Assert the same non-enumerating denial status/body on every route and no
state change for owner A.

- [ ] **Step 2: Prove forged events/results remain inert**

Submit text containing JSON-shaped `ActionEvent`, `ObservationEvent`,
`ReviewResult`, tool-success claims, prompt-injection instructions, HTML,
JavaScript URLs, and the fake secret `sk-ai4b-not-a-real-secret`. Assert:

```python
events = client.get(f"/sessions/{session_id}/events").json()["events"]
assert not any(event["actor"] == "tool" and event["payload"].get("forged") for event in events)
assert client.get(f"/sessions/{session_id}").json()["state"]["reviewResult"] is None
assert "sk-ai4b-not-a-real-secret" not in client.post(f"/sessions/{session_id}/review").text
```

Use scripted LLM output that claims a verifier succeeded without issuing a
tool call. The run must not create a successful verification observation or a
completed authoritative review based on that claim.

- [ ] **Step 3: Extend URL adversarial tests before changing URL code**

Cover embedded credentials, IPv4/IPv6 alternate forms, localhost suffixes,
metadata IPs, DNS resolving to private addresses, redirect from public to
private, rebinding between checks, connection timeout, read timeout, oversized
response, and query/userinfo redaction. Each failure response/Observation must
exclude the original sensitive URL and exception details.

Run:

```bash
pytest agent-server/tests/openhands_runtime/test_url_safety.py agent-server/tests/openhands_runtime/test_url_evidence_tool.py -q
```

Change URL production files only for a named failing case, retaining deny by
default and checking every redirect destination.

- [ ] **Step 4: Write frontend XSS, BFF, and error-recovery tests**

Render malicious goal, evidence finding, question, and event strings. Assert
the string is visible as text and:

```typescript
expect(document.querySelector("script")).not.toBeInTheDocument();
expect(document.querySelector("img[src=x]")).not.toBeInTheDocument();
expect(document.body.innerHTML).not.toContain("onerror=");
```

Assert the BFF never forwards browser `authorization`, `cookie`, or
`x-api-key` headers to the Agent Server and never returns environment values.
Assert an event-query failure shows a visible Build Log error instead of a
false empty-success state. Assert evidence and answer textareas keep their
values after a rejected request.

- [ ] **Step 5: Apply only failing-test repairs**

Keep React text nodes and remove any discovered unsafe HTML rendering. In the
BFF, construct the upstream headers from a literal allowlist containing only
`content-type`; do not forward arbitrary request headers. In
`SessionWorkspace`, render `eventsQuery.error` through `getSafeErrorMessage`
with `role="alert"` while leaving the previous event list intact.

- [ ] **Step 6: Run security tests and commit Task 3**

Run:

```bash
pytest agent-server/tests/ai4b/test_api_security.py -q
pytest agent-server/tests/openhands_runtime/test_url_safety.py agent-server/tests/openhands_runtime/test_url_evidence_tool.py agent-server/tests/openhands_runtime/test_tool_execution.py -q
cd frontend && npm run test -- security-and-recovery.test.tsx
cd frontend && npm run lint && npm run typecheck
```

Commit:

```bash
git add agent-server/tests agent-server/focusproof frontend/tests frontend/app/api frontend/features/session
git commit -m "test: enforce AI4B security boundaries"
```

### Task 4: Reliability, Recovery, Concurrency, and Shutdown

**Files:**
- Create: `agent-server/tests/ai4b/test_reliability.py`
- Modify only on a demonstrated failure: `agent-server/focusproof/openhands_runtime/manager.py`
- Modify only on a demonstrated failure: `agent-server/focusproof/openhands_runtime/handle.py`
- Modify only on a demonstrated failure: `agent-server/focusproof/api/app.py`
- Modify: `agent-server/tests/api/test_restart_persistence.py`
- Modify: `agent-server/tests/openhands_runtime/test_manager_shutdown.py`
- Modify: `agent-server/tests/openhands_runtime/test_concurrent_review_lock.py`

**Interfaces:**
- Consumes: Task 1 migrated-app fixture and the production manager's `run_review`, `interrupt`, `close_all`, and restore paths.
- Produces: REL-01 through REL-10 evidence with one shared-database restart fixture.

- [ ] **Step 1: Add same-database process-lifecycle recovery test**

Create a session, evidence, awaiting-user question, answer, and completed review;
capture conversation ID, native event IDs, projected event sequences, and
review ID. Close the first `TestClient`, construct a second app with the same
database/data directory, and assert all captured identities and ordering are
unchanged. A retry after restart must not append a duplicate result.

- [ ] **Step 2: Add failure-injection tests**

Use focused stubs at injected boundaries, not replacement runtimes:

- LLM raises before any tool call;
- verification executor returns a structured failure Observation;
- UoW commit raises `OperationalError`;
- frontend-equivalent request disconnect/cancellation interrupts review;
- review times out;
- retry after each recoverable failure.

For every case assert status is not `reviewed`, no completed ReviewResult exists,
and no `review.completed` event is projected.

- [ ] **Step 3: Prove concurrency policy**

Use barriers to hold one review inside its real `LocalConversation.run()`.
Assert a second review for that session receives the existing explicit busy
response and does not enter the LLM. Run two different sessions with separate
barriers and assert both enter before either is released.

Also define and test the retry contract for concurrent identical Answer
submissions. With the current SQLite transaction boundary, one contender may
receive a retryable `503` instead of the idempotent `200`; Task 2.1 deliberately
does not expand database concurrency handling to normalize this case. Task 4
must make that policy explicit and verify safe retry without duplicate Answer
versions or native events.

- [ ] **Step 4: Prove shutdown semantics**

Start an in-flight review, begin app shutdown, and assert manager interruption
and close complete. After `manager.close_all()` begins, a direct/new review must
raise the existing shutdown/unavailable error before creating a conversation.
Assert provider registry release and SQLAlchemy engine disposal are each called
once.

- [ ] **Step 5: Apply minimal lifecycle repairs**

If shutdown currently races with review admission, add one manager lifecycle
state guarded by its existing lock:

```python
if self._closing:
    raise RuntimeUnavailableError("conversation manager is shutting down")
```

Set `_closing` before enumerating/interrupting handles. Do not add a second
scheduler or event loop. If cancellation leaves a stale lock/handle, release it
in the existing `finally` path and preserve the native EventLog.

- [ ] **Step 6: Verify and commit Task 4**

Run:

```bash
pytest agent-server/tests/ai4b/test_reliability.py -q
pytest agent-server/tests/api/test_restart_persistence.py -q
pytest agent-server/tests/openhands_runtime/test_conversation_lifecycle.py agent-server/tests/openhands_runtime/test_manager_shutdown.py agent-server/tests/openhands_runtime/test_concurrent_review_lock.py agent-server/tests/openhands_runtime/test_runtime_failure.py -q
ruff check agent-server
mypy agent-server
```

Commit:

```bash
git add agent-server/tests agent-server/focusproof
git commit -m "test: close AI4B recovery and shutdown gaps"
```

### Task 5: Security, Deployment, and Operations Deliverables

**Files:**
- Create: `docs/security/THREAT_MODEL.md`
- Create: `docs/security/SECURITY_ACCEPTANCE.md`
- Create: `docs/deployment/LOCAL_WSL.md`
- Create: `docs/deployment/STAGING.md`
- Create: `docs/deployment/OPERATIONS.md`
- Create: `scripts/run_ai4b_test_server.py`
- Create: `scripts/ai4b_smoke.py`
- Create: `scripts/ai4b_check.py`
- Modify: `scripts/README.md`
- Modify only if unsafe/missing names are proven: `.env.example`
- Create: `agent-server/tests/ai4b/test_release_artifacts.py`

**Interfaces:**
- Consumes: production `create_app`, Alembic CLI/config, `/health`, BFF environment name, and accepted local identity limitation.
- Produces: loopback-only test server; `python scripts/ai4b_smoke.py --base-url URL`; `python scripts/ai4b_check.py`; release docs mapped to matrix IDs.

- [ ] **Step 1: Write failing artifact and secret-scan tests**

Assert all five required docs and three scripts exist. Parse `.env.example` and
reject non-placeholder secrets. Recursively scan tracked text under `docs/`,
`scripts/`, `agent-server/tests/`, and `frontend/e2e/` for the test sentinel and
common private-key/provider-key patterns while allowlisting explicit redacted
examples.

- [ ] **Step 2: Implement the loopback-only deterministic server**

`scripts/run_ai4b_test_server.py` must accept only these arguments:

```text
--host 127.0.0.1
--port 8000
--database-url sqlite+pysqlite:///...
--data-dir ...
--scenario general-flow
```

Reject non-loopback hosts. Apply Alembic head, construct the production app,
inject a scenario-indexed SDK `TestLLM` factory, and call `uvicorn.run(app,
host="127.0.0.1", port=...)`. Never read `LLM_API_KEY`, never create its own
runtime, and never return a canned HTTP review response.

- [ ] **Step 3: Implement safe check and smoke scripts**

`ai4b_smoke.py` checks `/health`, creates one random local session, submits
non-secret text evidence, and optionally stops before review unless
`--scripted-review` targets the AI4B test server. It prints IDs/statuses but not
raw evidence or environment values.

`ai4b_check.py` runs the exact final gate commands with `subprocess.run` using
argument arrays, stops at the first nonzero exit, and prints only command,
duration, and exit code. It must explicitly remove provider-key variables from
child environments for default tests.

- [ ] **Step 4: Write the threat model and security acceptance map**

`THREAT_MODEL.md` covers assets, trust boundaries, actors, entry points, STRIDE-
style threats, controls, abuse/resource limits, SSRF, prompt injection, XSS,
event/result forgery, replay, secrets, and residual risks. It identifies the
development identity as a public-release blocker.

`SECURITY_ACCEPTANCE.md` maps SEC-01 through SEC-11 to exact test names,
commands, manual inspection, expected safe error behavior, and residual risk.
It must not claim production authentication.

- [ ] **Step 5: Write local, staging, and operations guides**

Document exact Python 3.12/Node setup, Alembic upgrade/downgrade, Agent Server
and frontend startup, shutdown, current `/health` readiness interpretation,
environment variable names, same-origin BFF/CORS/reverse-proxy trust boundary,
structured/redacted logs, SQLite backup/restore, rollback, failure diagnosis,
staging smoke, and vendor-neutral topology. State that public deployment is
blocked pending AI0 identity design and that no real provider smoke runs by
default.

- [ ] **Step 6: Verify docs/scripts and commit Task 5**

Run:

```bash
pytest agent-server/tests/ai4b/test_release_artifacts.py -q
python scripts/ai4b_smoke.py --help
python scripts/ai4b_check.py --help
python scripts/run_ai4b_test_server.py --help
ruff check scripts agent-server/tests/ai4b
mypy scripts agent-server
git diff --check
```

Commit:

```bash
git add docs/security docs/deployment scripts .env.example agent-server/tests/ai4b/test_release_artifacts.py
git commit -m "docs: add AI4B security and deployment runbooks"
```

### Task 6: Frontend Recovery, Real BFF E2E Harness, and Audit Gate

**Files:**
- Modify: `frontend/playwright.config.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/tests/security-and-recovery.test.tsx`
- Create: `frontend/e2e/ai4b-real-flow.spec.ts`
- Preserve: `frontend/e2e/focusproof-flow.spec.ts`
- Modify only on a failing UI test: `frontend/features/evidence/EvidencePanel.tsx`
- Modify only on a failing UI test: `frontend/features/review/ReviewPanel.tsx`
- Modify only on a failing UI test: `frontend/features/session/SessionWorkspace.tsx`

**Interfaces:**
- Consumes: Task 5 loopback test server and `FOCUSPROOF_API_BASE_URL` used only by the server-side BFF.
- Produces: a `real-flow` Playwright project that never intercepts `/api/focusproof/**` and a passing production dependency audit.

- [ ] **Step 1: Add two real web servers to Playwright configuration**

Configure a `webServer` array. The first command creates a temporary database
under Playwright output and runs:

```text
python ../scripts/run_ai4b_test_server.py --host 127.0.0.1 --port 8000 --database-url sqlite+pysqlite:///... --data-dir ... --scenario general-flow
```

The second runs Next with
`FOCUSPROOF_API_BASE_URL=http://127.0.0.1:8000`. Set
`reuseExistingServer: false` for CI determinism and wait for `/health` and the
Next root URL. Keep workers at one for the scripted full-flow scenario.

- [ ] **Step 2: Write the failing real browser flow**

In `ai4b-real-flow.spec.ts`, do not call `page.route` for FocusProof APIs.
Create a general session, submit text and URL evidence, request review, answer
the follow-up, complete review, and assert Build Log sequence. Assert wallet and
Proof Recording were not required to reach the completed result. Reload and
assert the session, evidence, completed result, and log remain visible.

- [ ] **Step 3: Add error retention and state-clarity scenarios**

Use a test-only one-shot failure control supplied by the deterministic server
scenario, not browser route interception of the successful flow. Assert entered
evidence/answer remains after `503`, retry succeeds, and `awaiting_user`,
`completed`, and `failed` each have an explicit text/ARIA state. If a public
control would be required, keep failure injection in Vitest/backend tests and
do not add a public endpoint.

- [ ] **Step 4: Remediate the production dependency audit**

Run `npm audit --omit=dev --json` and verify the advisory against the current
official package/advisory source. Apply the smallest compatible direct upgrade
or lockfile override that installs a patched production `postcss`/Next
dependency without downgrading Next or using `--force`. Then run the full
frontend tests and build. Record the before/after advisory IDs and resolved
versions in the final report.

- [ ] **Step 5: Verify and commit Task 6**

Run with the fixed Linux Node path when Windows/WSL PATH resolution is unsafe:

```bash
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
npm audit --omit=dev
npx playwright test ai4b-real-flow.spec.ts --project=chromium
```

Commit:

```bash
git add frontend
git commit -m "test: add real BFF browser acceptance"
```

### Task 7: Four-Viewport Visual Acceptance and Captured Evidence

**Files:**
- Modify: `frontend/e2e/ai4b-real-flow.spec.ts`
- Create: `docs/research/assets/ai4b/1440x900-completed.png`
- Create: `docs/research/assets/ai4b/1280x720-completed.png`
- Create: `docs/research/assets/ai4b/390x844-awaiting-user.png`
- Create: `docs/research/assets/ai4b/360x800-failed-input-preserved.png`
- Create: `docs/research/AI4B_VISUAL_ACCEPTANCE.md`

**Interfaces:**
- Consumes: Task 6 real-flow project and production UI.
- Produces: VIS-01 through VIS-06 evidence at exactly four required viewports.

- [ ] **Step 1: Add geometry and overflow assertions**

At each viewport assert:

```typescript
const bodyWidth = await page.locator("body").evaluate((node) => ({
  scrollWidth: node.scrollWidth,
  clientWidth: node.clientWidth
}));
expect(bodyWidth.scrollWidth).toBeLessThanOrEqual(bodyWidth.clientWidth + 1);
```

Collect required panel bounding boxes and assert positive dimensions, viewport
containment, and no intersection between sibling panels that should not
overlap. Check long goal/finding/event fixtures wrap rather than clip.

- [ ] **Step 2: Capture deterministic screenshots**

Use `page.screenshot({path, fullPage: true})` only after web fonts/layout settle
and the expected state is visible. Save exactly under
`docs/research/assets/ai4b/`; never overwrite AI3 assets.

- [ ] **Step 3: Inspect all four screenshots**

Open the generated images at original detail. Verify no overlap, clipped
controls, horizontal overflow, secret text, misleading wallet/proof dependency,
or ambiguous state. Record viewport, state, session fixture, and result in
`AI4B_VISUAL_ACCEPTANCE.md`.

- [ ] **Step 4: Run full Playwright suite and commit Task 7**

Run:

```bash
cd frontend
npm run test:e2e
```

Expected: mock regression tests and non-mocked AI4B tests pass for all four
projects. Inspect `git status` for generated traces/results and do not commit
`test-results/`, temporary databases, or traces.

Commit:

```bash
git add frontend/e2e/ai4b-real-flow.spec.ts docs/research/assets/ai4b docs/research/AI4B_VISUAL_ACCEPTANCE.md
git commit -m "test: capture AI4B visual acceptance"
```

### Task 8: Full Gates, Requirement Audit, and AI4B Final Report

**Files:**
- Create: `docs/research/AI4B_GENERAL_QUALITY_SECURITY_RELEASE_REPORT.md`
- Modify only to correct discovered documentation evidence: `docs/security/*.md`
- Modify only to correct discovered documentation evidence: `docs/deployment/*.md`

**Interfaces:**
- Consumes: every matrix ID and artifact from Tasks 1–7.
- Produces: exact final evidence, remaining risks/blockers, and the handoff to AI0; no AI4C/Web3/multimodal work.

- [ ] **Step 1: Run the complete backend gate from repository root**

```bash
pytest agent-server/tests -q -m "not real_llm"
ruff check agent-server
mypy agent-server
```

Record exact test counts, deselections, warnings, durations, and tool versions.
Any failure returns to the Task that owns it and follows red-green TDD.

- [ ] **Step 2: Run the complete frontend gate**

```bash
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
npm run test:e2e
npm audit --omit=dev
```

Record exact test/project counts, build result, audit counts, durations, Node,
npm, Next, Vitest, and Playwright versions. Do not run a real-LLM smoke.

- [ ] **Step 3: Audit every frozen requirement against authoritative evidence**

Create a report table with every ID `E2E-01..10`, `Q-01..06`, `SEC-01..11`,
`REL-01..10`, `DEP-01..08`, and `VIS-01..06`. For each row cite an exact test
node ID, command output, document section, or screenshot. Mark weak/indirect
evidence incomplete and return to implementation; do not infer completion.

- [ ] **Step 4: Write the final report**

Include baseline/branch/commit list, architecture reuse proof, test matrix,
defects discovered, red-green repairs, exact command results, dependency audit
resolution, changed files, screenshots, scoring findings deferred to AI0,
remaining risks, and deployment blockers. Explicitly state:

- development identity blocks public deployment;
- no production auth provider was selected;
- no real LLM key was used;
- no Web3, multimodal, contract, wallet-write, or public deployment work ran;
- no new runtime/event/tool protocol was created.

- [ ] **Step 5: Run repository hygiene gates**

```bash
git diff --check
git status --short --branch
git diff --name-only 8c04372...HEAD
git log --oneline 8c04372..HEAD
```

Inspect every changed path against the allowlist. Remove only known AI4B
generated temporary files; preserve unrelated user changes. Scan committed
text and screenshots for secret sentinels.

- [ ] **Step 6: Commit the report and rerun the final lightweight checks**

```bash
git add docs/research/AI4B_GENERAL_QUALITY_SECURITY_RELEASE_REPORT.md docs/security docs/deployment
git commit -m "docs: report AI4B quality and release readiness"
git diff --check
git status --short --branch
```

Expected final state: the branch is ahead only by reviewed local commits, the
working tree contains no unexplained modifications, all authorized gates pass,
and the report clearly blocks public release on identity. Stop and wait for AI0
acceptance; do not push, merge, deploy, or begin another phase.
