# AI2.1 Persistence and Runtime Hardening Design

## Status

Approved by the user on 2026-07-12. This specification supplements the original
AI2.1 goal. Where the original goal conflicts with this document, the fact
boundaries, identity propagation, review idempotency, OpenHands public lifecycle
API, and legacy-data policy defined here take precedence.

## Goal

Make the official FastAPI -> OpenHands `LocalConversation` review path durable
across process restarts, safe under same-host concurrency, bounded in its SDK
tool registration, and explicit about ownership and lifecycle. Preserve the
native OpenHands Agent/Action/Tool/Observation flow and FocusProof-owned scoring.

## Scope

The work covers the Python Agent Server, SQLAlchemy persistence, Alembic
migrations, OpenHands conversation restoration, per-session locks, FastAPI
lifespan, tests, and the AI2.1 research report.

The work does not modify agent judgment strategy, frontend code, smart
contracts, architecture control documents, protocol control documents, or
project-management control documents. It does not enter AI3.

## Fact Boundaries

Three fact boundaries are authoritative and non-overlapping:

1. **FocusProof database: product facts.** The database owns authenticated user
   association, learning sessions, original evidence, learner answers, review
   history, and the latest product review projection.
2. **OpenHands EventLog: runtime facts.** The native EventLog owns
   `MessageEvent`, `ActionEvent`, `ObservationEvent`, pause events, conversation
   state, and execution status. FocusProof must not build a replacement runtime
   ledger.
3. **`audit_events`: query projection.** Audit rows are FocusProof-shaped query
   projections of native OpenHands events. They are rebuildable by reconciling
   the native EventLog with product facts. They are not a second runtime fact
   source and must not drive conversation restoration.

Production runtime projections use a non-null `source_openhands_event_id`.
The database column remains nullable for migration compatibility and controlled
historical rows, but new runtime projection code does not create source-less
rows. Product creation, evidence, answer, and review facts remain in their own
tables rather than being inferred from `audit_events`.

## Identity Boundary

An authentication dependency returns a `VerifiedIdentity` whose
`verified_user_id` cannot be supplied by request JSON. Until a real authentication
system exists, that dependency returns the explicit development identity
`dev-anonymous-user`.

Identity propagation is mandatory:

- `learning_sessions.owner_user_id` is populated from `VerifiedIdentity`.
- Every session-scoped API verifies that the current identity owns the session.
- `Conversation(..., user_id=verified_user_id)` receives the verified owner.
- Every `send_message` call supplies `sender=verified_user_id`.
- Request models do not expose `owner_user_id`, `verified_user_id`, or `sender`.
- Lazy restore receives the current verified identity, verifies ownership, and
  reuses that identity as the OpenHands `user_id` and message sender.

Replacing development authentication later changes only the authentication
dependency. Persistence and runtime interfaces continue to consume
`VerifiedIdentity` or the verified string extracted from it.

## Legacy Runtime Artifacts

Existing `var/conversations` content is classified as legacy/test acceptance
artifacts. It is retained, not imported, and not deleted. The new Alembic-managed
database starts empty. The new API guarantees access only to sessions represented
in the database and does not guarantee lookup of old acceptance session IDs.

## Configuration and Path Safety

The non-secret development defaults are:

```dotenv
DATABASE_URL=sqlite+pysqlite:///./var/focusproof.db
FOCUSPROOF_DATA_DIR=./var
FOCUSPROOF_LOCK_TIMEOUT_SECONDS=5
```

Configuration is loaded during FastAPI lifespan, never by running migrations at
module import. Database URLs are not logged. SQLite connections enable
`PRAGMA foreign_keys=ON`.

All conversation, workspace, persistence, and lock paths are derived below the
resolved `FOCUSPROOF_DATA_DIR`. A requested path is accepted only when its
`Path.resolve()` result remains within the intended resolved parent. Session IDs
also pass a strict ASCII identifier allowlist before being used in a filename.

## Relational Model

### `learning_sessions`

- `session_id`: primary key
- `owner_user_id`: non-null authenticated owner
- `status`, `adapter_mode`, `domain`, `title`, `goal`
- `expected_output`, `planned_minutes`: nullable
- `conversation_id`: unique UUID string
- `runtime_mode`
- `review_result_json`: nullable latest review projection
- `goal_conversation_synced_at`: nullable synchronization marker
- `version`: optimistic concurrency integer
- `created_at`, `updated_at`

### `evidence`

- `evidence_id`: primary key
- `session_id`: foreign key to `learning_sessions`
- `evidence_type`, `content_hash`
- `text_content`, `source_url`: nullable original product evidence
- `metadata_json`
- `conversation_synced_at`: nullable
- `created_at`
- unique `(session_id, evidence_id)`
- index `(session_id, content_hash)` without content deduplication

### `learner_answers`

- composite primary key `(session_id, question_id)`
- `answer`
- `version`: increments on changed answer
- `conversation_synced_at`: reset on changed answer, set after native message exists
- `created_at`, `updated_at`

### `audit_events`

- `event_id`: primary key
- `session_id`: foreign key
- `sequence`: monotonically increasing within a session
- `type`, `actor`, `payload_json`
- `source_openhands_event_id`: nullable schema field
- `created_at`
- unique `(session_id, sequence)`
- unique `(session_id, source_openhands_event_id)`, allowing multiple nulls

### `reviews`

- `review_id`: primary key
- `session_id`: foreign key
- `conversation_id`
- `review_status`
- `score`, `result_json`: nullable
- `native_event_count`
- `source_openhands_event_id`: nullable for failures without a native source
- `created_at`
- unique `(session_id, source_openhands_event_id)`, allowing multiple nulls

Awaiting-user reviews bind to the corresponding native
`LearnerInputObservation` ID. Completed reviews bind to the accepted native
`ReviewDraftObservation` ID. Review history is append-only; the session's latest
review JSON is only a convenience projection.

All ORM types and migration operations compile for SQLite and PostgreSQL. JSON
uses SQLAlchemy's dialect-neutral `JSON` type. Application services depend on
repository protocols and Unit of Work, not directly on SQLAlchemy `Session`.

## Repository and Unit of Work Boundary

Protocols expose the operations required by the original AI2.1 goal:

- `SessionRepository`: create, get, update status, set conversation, list recoverable
- `EvidenceRepository`: add, get, list for session, mark synced
- `AnswerRepository`: upsert, list for session, mark synced
- `AuditEventRepository`: append, list, latest, has source event
- `ReviewRepository`: add from native event, list for session
- `UnitOfWork`: owns repository instances and commit/rollback lifecycle

Every API mutation runs inside one Unit of Work transaction. OpenHands file
persistence is never presented as part of that database transaction.

The audit repository allocates sequence numbers in the transaction. Native-event
uniqueness is the authoritative idempotency guard. SQLite lock and operational
errors are mapped to sanitized service errors; SQL, paths, connection strings,
and secrets are never returned to clients.

## Message Synchronization

Every user message has a stable key:

```text
goal:{session_id}
evidence:{evidence_id}
answer:{session_id}:{question_id}:{answer_version}
```

The serialized message envelope is:

```json
{
  "schema_version": 1,
  "message_key": "...",
  "kind": "goal|evidence|answer",
  "session_id": "...",
  "payload": {}
}
```

Identity is not trusted from the envelope. `MessageEvent.sender` is supplied
separately from verified authentication state.

Synchronization performs these steps under the session lock:

1. Read product goal, evidence, and answers from the database.
2. Scan native user `MessageEvent` objects and parse valid envelopes.
3. Build the set of native `message_key` values.
4. Send only missing messages with `sender=verified_user_id`.
5. After the native EventLog contains the message, commit its database sync marker.
6. If the native message already exists but the marker is absent, set the marker
   without resending.

This handles a crash before send, after send but before marker commit, and a
repeated restore without relying only on database markers.

## Conversation Creation and Restoration

`ConversationManager` exposes:

```text
create(session_id, verified_user_id) -> ConversationHandle
get(session_id) -> ConversationHandle
get_or_restore(session_id, verified_user_id) -> ConversationHandle
send_evidence(session_id, verified_user_id) -> None
send_answer(session_id, verified_user_id) -> None
run_review(session_id, verified_user_id) -> RuntimeReviewResult
close(session_id) -> None
close_all() -> None
```

The exact send interfaces may accept product identifiers rather than model
objects, but they always reload authoritative product data from repositories.

Restore performs:

1. Validate the verified identity against `owner_user_id`.
2. Acquire the per-session lock.
3. Load the session and persisted `conversation_id` from the database.
4. Resolve and validate the same workspace and persistence paths.
5. Construct `LocalConversation` with the same UUID, paths, and verified `user_id`.
6. Let OpenHands SDK 1.31.0 perform its native create-or-resume behavior.
7. Synchronize missing goal, evidence, and answers by `message_key`.
8. Reconcile native events not yet represented by audit projections or reviews.
9. Cache the resulting handle.

The handle, goal, evidence, and answer dictionaries are optional caches only.
They are not facts and may be empty after restart without data loss.

Corrupted or mismatched OpenHands persistence produces a sanitized runtime
unavailable response. The manager does not silently create a new conversation
with a different ID.

## Review Idempotency and Scoring

The result extractor reads native events after the most recent answer message.
It does not infer runtime progress from audit rows.

- A latest `LearnerInputObservation` produces an awaiting-user Review tied to
  that observation's native event ID.
- A latest accepted `ReviewDraftObservation` invokes FocusProof's deterministic
  scorer and produces a completed Review tied to that observation's native ID.
- A failure without a native source may store a Review with a null source ID.

`ReviewRepository.add_from_native_event` inserts the Review and updates the
session's latest product projection in one Unit of Work. If the unique native
source already exists, it returns the existing Review and does not append a
duplicate. Repeated retry, restore, callback reconciliation, and explicit
reconciliation therefore produce one Review per native result observation.

The LLM and tool observations never assign the numeric FocusProof score.

## Audit Projection

The projector accepts repository protocols rather than `InMemoryEventLog`.
For each supported native event it creates a query projection containing:

- source conversation ID
- source native event ID and type
- source native index
- tool call ID where applicable
- related evidence IDs
- FocusProof session ID

The unique native source constraint makes callback projection and later
reconciliation idempotent. `InMemoryEventLog` remains available only to isolated
unit tests and legacy adapter tests. Production FastAPI never constructs it.

## Session Concurrency

`SessionRunLock` is a protocol implemented by `FileSessionRunLock` using
`filelock.FileLock` at:

```text
{FOCUSPROOF_DATA_DIR}/locks/{session_id}.lock
```

Create, send, restore, review, and close serialize operations for one session.
Different sessions use different lock files and may run concurrently. Each
public manager operation acquires the lock once and delegates to unlocked private
helpers, preventing nested lock acquisition from obscuring ownership.

Review lock timeout raises `SessionBusyError`. FastAPI returns HTTP 409:

```json
{"code":"session_busy","sessionId":"...","retryable":true}
```

Context managers release locks after success, exception, cancellation, or
timeout. The guarantee covers one host with multiple Uvicorn workers; multi-host
distributed locking remains outside this phase.

## Bounded Tool Registry

The process registers exactly three fixed SDK registry names once:

- `FocusProofEvidenceVerificationTool`
- `FocusProofLearnerInputTool`
- `FocusProofReviewDraftTool`

Agent tool specs carry `session_id` in params. Definition names presented to the
LLM remain the stable existing snake-case names. Executors obtain the configured
repository provider and load authoritative evidence using `session_id` and
`evidence_id`. Evidence text is never accepted as a tool argument.

Creating 100 conversations does not create per-conversation registry names and
does not register Terminal, FileEditor, Browser, ApplyPatch, or other default
programming tools.

## SDK Lifecycle Rules

FocusProof business code must not assign
`conversation.state.execution_status`.

OpenHands SDK 1.31.0 publicly exposes `pause()`, `interrupt()`, and `close()` but
does not expose `finish()`. Both `LearnerInputObservation` and
`ReviewDraftObservation` first use `conversation.pause()` to stop the current run.
FocusProof then stores the awaiting or completed product Review. SDK-owned code
may transition its own state to `FINISHED`; FocusProof does not imitate that
private behavior.

No `sdk_compat.py` is planned. Such a module is permitted only if a failing
integration test proves the public APIs cannot satisfy required semantics. In
that case it must contain the minimal compatibility operation, reject any SDK
version other than exactly 1.31.0, explain the upstream limitation, and have
version and state-transition tests. The final report must mark it for upstream
confirmation.

## FastAPI Lifespan and Errors

FastAPI uses an application factory and lifespan. Startup:

1. Load non-secret runtime settings.
2. Create SQLAlchemy engine and session factory.
3. Check the current Alembic revision without changing schema.
4. Create Unit of Work factory, repositories, locks, tool provider, and manager.
5. Store services and readiness state in `app.state`.
6. Restore conversations lazily per request.

Shutdown:

1. Reject new reviews.
2. Close all manager handles.
3. Close every `LocalConversation` and tool executor.
4. Release the repository provider.
5. Dispose the SQLAlchemy engine.

The production app does not use module-level `_SESSIONS`, `InMemoryEventLog`, or
`_CONVERSATION_MANAGER` facts. Dependencies resolve services from `app.state`.

Public routes remain compatible:

- `POST /sessions`
- `POST /sessions/{id}/evidence`
- `POST /sessions/{id}/answer`
- `POST /sessions/{id}/review`
- `GET /sessions/{id}`
- `GET /sessions/{id}/events`
- optional `GET /sessions/{id}/reviews`

Sanitized 503 codes are `database_unavailable`, `schema_out_of_date`, and
`openhands_runtime_unavailable`. Responses never expose SQL, filesystem paths,
database URLs, stack traces, or secrets.

## Migration and Git Baseline

Alembic owns schema changes. Application imports never execute migrations.
Development setup explicitly runs `alembic upgrade head`; application startup
only checks the revision. Upgrade, downgrade to base, and re-upgrade must pass.

Before Git initialization, `.gitignore` excludes `.env`, `.venv/`, `var/`,
database files, caches, and IDE state. A filename-only scan excludes secrets and
database artifacts from the baseline. No remote is configured and nothing is
pushed. Because no local Git identity is configured, the repository is
initialized but no identity is fabricated and no commit is required.

## Testing Strategy

Implementation follows red-green-refactor. Tests use independent temporary
SQLite files and real OpenHands `LocalConversation` objects with SDK `TestLLM`.
The default suite never consumes a real API key.

Required coverage includes:

- migration upgrade, downgrade, re-upgrade, tables, foreign keys, uniqueness,
  JSON round trips, and SQLite/PostgreSQL schema compilation
- repository contracts, Unit of Work commit and rollback, optimistic session
  versioning, historical reviews, and native-source idempotency
- explicit development identity, owner enforcement, untrusted-body rejection,
  Conversation `user_id`, and MessageEvent `sender`
- goal/evidence/answer synchronization after crashes before send and before
  marker commit
- callback projection crash before audit commit and repeated reconciliation
- stable conversation ID and native history after completely new engine,
  repositories, and manager instances
- no duplicate messages, audit projections, or Reviews after restore
- completed second review after an answer and persisted Review history
- corrupt conversation persistence, locked SQLite database, session lock timeout,
  and paused-conversation shutdown
- two concurrent reviews for one session entering `conversation.run()` once,
  while different sessions remain independent
- 100 conversations retaining exactly three safe FocusProof tools and bounded
  registry growth
- source scan proving FocusProof business code does not assign
  `execution_status`
- public `pause()` behavior for learner input and review draft
- FastAPI restart preserving GET session behavior and compatibility fields
- legacy acceptance artifacts retained but not imported or queried

The explicit `real_llm` marker remains opt-in. After ordinary verification, a
real acceptance run exercises awaiting-user review, FastAPI restart, same-session
lookup, answer submission, completed review, stable conversation ID, and
duplicate-free event results without printing credentials.

## Acceptance

Completion requires every command and invariant from the original AI2.1 goal,
including Alembic regression, persistence/runtime/API suites, full non-real
suite, Ruff, Mypy, optional configured real-LLM test, restart acceptance, final
report, forbidden-directory audit, and secret-safe Git baseline.

After completion, work stops for AI0 review and does not enter AI3.
