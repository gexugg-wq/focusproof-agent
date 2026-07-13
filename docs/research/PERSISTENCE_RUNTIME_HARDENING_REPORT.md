# Persistence and Runtime Hardening Report

Date: 2026-07-13
Branch: `ai2.1-persistence-runtime-hardening`
Review gate: AI0 (do not proceed to AI3)

## Result

AI2.1 now has durable product persistence, recoverable OpenHands conversations,
idempotent synchronization and review projection, per-session run locks, bounded
tool registration, and FastAPI lifespan ownership. The implementation follows the
approved design and plan in `docs/superpowers/`.

## Fact Boundaries

- FocusProof DB is authoritative for user ownership, Learning Session, raw
  Evidence, Learner Answer, and Review history.
- OpenHands EventLog is authoritative for native `MessageEvent`, `ActionEvent`,
  `ObservationEvent`, and conversation execution state.
- `audit_events` is a rebuildable query projection keyed by native event ID. It
  is not a second runtime fact source.
- FocusProof retains ownership of deterministic numeric scoring. Runtime tools
  return observations; they do not assign the product score.

## Database and Transactions

Alembic revision `0001_initial_focusproof_schema` creates `learning_sessions`,
`evidence`, `learner_answers`, `audit_events`, and `reviews`. Sessions carry
`owner_user_id`, optimistic `version`, conversation/runtime identity, and goal,
evidence, and answer synchronization markers. Reviews carry
`source_openhands_event_id` with a unique `(session_id,
source_openhands_event_id)` constraint. Audit projection rows also enforce native
source uniqueness.

Repositories contain table-specific operations. `UnitOfWork` owns one SQLAlchemy
session and the commit/rollback boundary. Runtime tools receive a repository
provider and load authoritative evidence by ID instead of capturing mutable
per-conversation state.

SQLite foreign keys are enabled for every connection. SQLite database files must
resolve under `FOCUSPROOF_DATA_DIR`; PostgreSQL URLs are unaffected by this local
path rule. Startup checks the Alembic revision without mutating the schema.

## Identity and Synchronization

`verified_user_id` is supplied only by the authentication dependency. Until a
production authentication layer exists, the explicit identity is
`dev-anonymous-user`. It becomes `learning_sessions.owner_user_id`, OpenHands
Conversation `user_id`, and every synchronized message `sender`; request bodies
cannot override it.

Goal, evidence, and answer messages use stable semantic keys. A retry first scans
native OpenHands messages for the key, then marks the DB synchronization timestamp
without resending when the native append succeeded before the DB commit. This
closes the message crash window.

Review drafts are derived from native `ObservationEvent` IDs. Repository insert
and reconcile use the same unique source key, so restore, retry, callback replay,
and reconciliation preserve history without duplicate Review rows.

## Restore and Runtime Ownership

The conversation UUID is deterministic per Session and persisted in the DB.
Restore opens that exact UUID from the configured data directory, reconciles
native history into `audit_events` and Review rows, synchronizes any pending
product facts, and resumes work. Corrupt or missing persistence is surfaced; the
manager never silently replaces it with a new conversation.

Only one review run may enter a Session at a time. A traversal-safe file lock
under the data root spans restore/synchronize/run/reconcile/commit. Contention is
reported as top-level HTTP 409; known database/readiness failures are sanitized
top-level 503 responses.

The Tool Registry contains exactly three fixed FocusProof tools and uses a
replaceable repository provider, so creating 100 conversations does not grow the
registry. FastAPI lifespan owns engine, UoW factory, audit projection, provider,
locks, and manager; shutdown closes conversations, releases the provider, and
disposes the engine.

Learner input and review completion use the OpenHands SDK 1.31.0 public
`conversation.pause()` method. No business code assigns
`conversation.state.execution_status`, and no `sdk_compat.py` fallback was
needed.

## Legacy Policy

Existing `var/conversations` data remains untouched as legacy/test acceptance
artifacts. It is not imported or deleted. New APIs do not guarantee lookup of old
Sessions that have no FocusProof DB row.

## Verification Evidence

- `alembic upgrade head`: passed.
- Persistence tests: 16 passed.
- OpenHands runtime excluding real LLM: 31 passed, 1 deselected.
- API tests: 18 passed.
- Full suite excluding real LLM: 99 passed, 1 deselected, 8 warnings.
- `ruff check agent-server`: passed.
- `mypy agent-server`: passed, 98 source files.
- `alembic downgrade base` followed by `alembic upgrade head`: passed.
- Explicit real LLM test: 1 passed, 99 deselected. The only runtime warning was
  unavailable cost metadata for the configured OpenAI-compatible model.

Real FastAPI restart acceptance used port 8766 and stopped only that test server.
Session `sess_1b3eeb5976eb43fe99f4d69bb52d1f38` reached `awaiting_user`, then
survived a process stop/restart with conversation ID
`be60c304-b0b1-59d4-834e-6b34f6329914`. After answer submission it completed
with score 35. Native events increased from 8 to 12; the query projection ended
with 7 rows/7 unique source IDs and Review history with 2 rows/2 unique source
IDs.

## AI3 Contract and Residual Risks

AI3 may consume the existing Session, Evidence, Answer, event-query, Review, and
health endpoints without adding a public runtime-mode selector. Ownership must
continue to come from the auth dependency, and API code must treat DB product
facts and OpenHands native runtime facts according to the boundaries above.

Residual risks are operational: SQLite permits one writer and is suitable for
the current local deployment, while a multi-instance deployment should use
PostgreSQL plus a distributed lock. The development anonymous identity must be
replaced before multi-user production use. Upstream warnings remain for the
Starlette `httpx` TestClient transition and missing LiteLLM price metadata.

## Repository Audit

Work was performed only in `/home/holy/web3/focusproof-agent`. No files were
edited in `frontend/`, `contracts/`, or the protected architecture/protocol/project
management documentation. `.env`, `var/`, caches, virtualenv data, and SQLite
artifacts are ignored; `.env` contents were not read, printed, or committed.

Git is local-only on branch `ai2.1-persistence-runtime-hardening`. No remote is
configured. Local `user.name` and `user.email` are absent, so no identity was
fabricated and no commit was created. The branch and working tree are deliberately
left in place for AI0 review.

## AI0 P1 Corrections

The corrections were applied on branch
`ai2.1-persistence-runtime-hardening` from baseline commit `72d598c`. The private
`origin` configured by AI0 was retained and no push was performed.

ReviewDraft Observation is now only an OpenHands-native draft submission. It no
longer projects directly to `review.completed`. Session creation writes
`session.created` before goal synchronization. After deterministic FocusProof
scoring succeeds, the completed Review row, protocol-complete
`score.calculated`, and `review.completed` are committed in one Unit of Work.
`review.completed.scoreEventId` references the preceding score event. Stable
event IDs derived from the native ReviewDraft Observation make retry, restart,
and reconcile idempotent. Scoring failure or Review persistence failure cannot
commit `review.completed`.

Evidence and Answer endpoints continue to commit product facts before attempting
OpenHands synchronization. Successful immediate synchronization returns
`syncPending=false`; `SessionRunLock` contention returns HTTP 200 with
`syncPending=true`, and the next review synchronizes the pending fact. Review
lock contention remains top-level HTTP 409. Other database failures retain the
sanitized 503 handling.

P1 verification on 2026-07-13:

- Directed audit, rollback, restart/retry, deferred Evidence/Answer sync, and
  review-lock tests: 11 passed.
- `pytest agent-server/tests -m "not real_llm" -q`: 103 passed, 1 deselected.
- `ruff check agent-server`: passed.
- `mypy agent-server`: passed for 98 source files.
- `git diff --check`: passed.
- No real LLM test was run, as explicitly excluded from this correction round.
- No protected protocol, architecture, project-management, frontend, or contract
  files were modified.
