# AI2.1 Persistence and Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Persist FocusProof product facts, restore native OpenHands conversations after restart, serialize same-session runtime operations, bound tool registration, propagate verified identity, and manage all resources through FastAPI lifespan.

**Architecture:** SQLAlchemy repositories and Unit of Work own product facts while the native OpenHands EventLog remains the runtime fact source. A message synchronizer bridges the non-transactional database/file boundary with stable message keys, and native-source uniqueness makes audit and Review projection idempotent. FastAPI lifespan owns the engine, repositories, tool provider, locks, and conversation manager.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy 2.x typed ORM, Alembic, SQLite, PostgreSQL-compatible schema, OpenHands SDK 1.31.0, filelock, pytest, Ruff, Mypy.

## Global Constraints

- Work only in `/home/holy/web3/focusproof-agent` through WSL or the corresponding `\\wsl.localhost\\Ubuntu` path.
- Do not read, print, or log `.env` secrets.
- Do not modify `frontend/`, `contracts/`, `docs/architecture/`, `docs/protocol/`, or `docs/project-management/`.
- FocusProof DB owns product facts; OpenHands EventLog owns runtime facts; `audit_events` is rebuildable query projection only.
- Verified identity comes only from the authentication dependency; development uses `dev-anonymous-user`.
- FocusProof business code must not assign `conversation.state.execution_status`.
- Default tests use SDK `TestLLM` and never consume a real key.
- Existing `var/conversations` is retained as legacy/test acceptance data and is not imported or deleted.
- No Redis, Celery, Kafka, MongoDB, custom migration system, custom OpenHands EventLog, or new agent loop.

---

### Task 1: Git Safety Baseline and Dependencies

**Files:**
- Modify: `.gitignore`
- Modify: `.env.example`
- Modify: `pyproject.toml`
- Modify: `README.md`

**Interfaces:**
- Consumes: existing editable package and WSL virtual environment.
- Produces: ignored secret/database/runtime files and installed SQLAlchemy/Alembic dependencies.

- [x] **Step 1: Extend ignore and non-secret configuration templates**

Add these exact ignore patterns if absent:

```gitignore
*.db
*.sqlite
*.sqlite3
```

Set the non-secret template values:

```dotenv
DATABASE_URL=sqlite+pysqlite:///./var/focusproof.db
FOCUSPROOF_DATA_DIR=./var
FOCUSPROOF_LOCK_TIMEOUT_SECONDS=5
```

- [x] **Step 2: Scan filenames and initialize Git**

Run filename-only scans excluding `.env`, `.venv`, `var`, and caches; verify no database or credential-named artifact would be tracked. Then run:

```bash
git init
git config --get user.name
git config --get user.email
```

Expected: repository initializes with no remote. Because identity is absent, do not fabricate it and do not commit.

- [x] **Step 3: Add required dependencies**

Add to `[project].dependencies`:

```toml
"sqlalchemy>=2.0,<3",
"alembic>=1.13,<2",
"filelock>=3.16,<4",
```

Run:

```bash
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Expected: SQLAlchemy, Alembic, and filelock import successfully.

- [x] **Step 4: Document explicit migration startup**

Update README local development commands to run `alembic upgrade head` before Uvicorn and state that application startup checks but never applies migrations.

---

### Task 2: Typed ORM and Initial Alembic Migration

**Files:**
- Create: `agent-server/focusproof/persistence/__init__.py`
- Create: `agent-server/focusproof/persistence/database.py`
- Create: `agent-server/focusproof/persistence/models.py`
- Create: `agent-server/focusproof/persistence/schema_check.py`
- Create: `alembic.ini`
- Create: `agent-server/migrations/env.py`
- Create: `agent-server/migrations/script.py.mako`
- Create: `agent-server/migrations/versions/0001_initial_focusproof_schema.py`
- Create: `agent-server/tests/persistence/__init__.py`
- Create: `agent-server/tests/persistence/conftest.py`
- Create: `agent-server/tests/persistence/test_migrations.py`

**Interfaces:**
- Produces: `Base`, `create_database_engine(url)`, `create_session_factory(engine)`, `check_schema_revision(engine, config_path)`, and five ORM models.

- [x] **Step 1: Write failing migration tests**

Tests must create a temporary SQLite file and assert:

```python
EXPECTED_TABLES = {
    "learning_sessions",
    "evidence",
    "learner_answers",
    "audit_events",
    "reviews",
    "alembic_version",
}

def test_upgrade_downgrade_and_reupgrade(temp_database_url: str) -> None:
    upgrade(temp_database_url, "head")
    assert EXPECTED_TABLES <= inspect_tables(temp_database_url)
    downgrade(temp_database_url, "base")
    assert "learning_sessions" not in inspect_tables(temp_database_url)
    upgrade(temp_database_url, "head")
    assert EXPECTED_TABLES <= inspect_tables(temp_database_url)
```

Also assert SQLite foreign keys reject orphan evidence, native source uniqueness rejects duplicate audit/review rows, JSON round trips, and `CreateTable` compiles with `postgresql.dialect()`.

- [x] **Step 2: Verify RED**

Run:

```bash
pytest agent-server/tests/persistence/test_migrations.py -v
```

Expected: collection/import failure because persistence and migration modules do not exist.

- [x] **Step 3: Implement database and ORM models**

Use SQLAlchemy 2 typed declarations:

```python
class Base(DeclarativeBase):
    pass

class LearningSessionModel(Base):
    __tablename__ = "learning_sessions"
    session_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    goal_conversation_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
```

Define all approved fields and constraints, including:

```python
UniqueConstraint("session_id", "source_openhands_event_id")
Index("ix_evidence_session_content_hash", "session_id", "content_hash")
```

`create_database_engine` must enable SQLite foreign keys with an engine `connect` event and must not log the URL.

- [x] **Step 4: Implement Alembic and schema checker**

`env.py` reads the URL passed through Alembic config first and otherwise the non-secret environment variable; it never imports `.env`. The migration creates and drops all five tables in dependency-safe order.

`check_schema_revision` compares database heads with script heads and raises `SchemaOutOfDateError` without changing schema.

- [x] **Step 5: Verify GREEN**

Run:

```bash
pytest agent-server/tests/persistence/test_migrations.py -v
alembic upgrade head
alembic downgrade base
alembic upgrade head
```

Expected: all tests and all three commands pass.

---

### Task 3: Repository Protocols, SQL Repositories, and Unit of Work

**Files:**
- Create: `agent-server/focusproof/persistence/repositories.py`
- Create: `agent-server/focusproof/persistence/unit_of_work.py`
- Create: `agent-server/focusproof/persistence/event_log.py`
- Create: `agent-server/tests/persistence/test_session_repository.py`
- Create: `agent-server/tests/persistence/test_event_repository.py`
- Create: `agent-server/tests/persistence/test_unit_of_work.py`

**Interfaces:**
- Produces: `SessionRepository`, `EvidenceRepository`, `AnswerRepository`, `AuditEventRepository`, `ReviewRepository`, `UnitOfWork`, `SqlAlchemyUnitOfWork`, `UnitOfWorkFactory`, and `PersistentAuditEventLog`.

- [x] **Step 1: Write failing repository and UoW tests**

Cover these exact behaviors:

```python
def test_unit_of_work_rolls_back_uncommitted_session(uow_factory: UnitOfWorkFactory) -> None:
    with uow_factory() as uow:
        uow.sessions.create(session_record())
    with uow_factory() as uow:
        assert uow.sessions.get("sess_1") is None

def test_review_native_source_is_idempotent(uow_factory: UnitOfWorkFactory) -> None:
    first = add_review("native_1", uow_factory)
    second = add_review("native_1", uow_factory)
    assert second.review_id == first.review_id
    assert len(list_reviews(uow_factory)) == 1
```

Also test answer version increment/reset, goal/evidence/answer sync markers, optimistic status update, audit sequence, audit native-source idempotency, and historical review ordering.

- [x] **Step 2: Verify RED**

Run:

```bash
pytest agent-server/tests/persistence/test_session_repository.py agent-server/tests/persistence/test_event_repository.py agent-server/tests/persistence/test_unit_of_work.py -v
```

Expected: imports fail for missing repository/UoW classes.

- [x] **Step 3: Implement repository contracts and records**

Use immutable dataclasses for persistence records and protocols with the original goal methods. `ReviewRepository.add_from_native_event(record)` catches only the expected unique violation, rolls back the failed savepoint, and returns the existing `(session_id, source_openhands_event_id)` record.

`AuditEventRepository.append` uses a nested transaction/savepoint so competing sequence/native-source inserts can be retried or resolved without corrupting the outer Unit of Work.

- [x] **Step 4: Implement Unit of Work and audit adapter**

```python
class SqlAlchemyUnitOfWork(AbstractContextManager["SqlAlchemyUnitOfWork"]):
    def __enter__(self) -> Self:
        self.session = self._session_factory()
        self.sessions = SqlSessionRepository(self.session)
        self.evidence = SqlEvidenceRepository(self.session)
        self.answers = SqlAnswerRepository(self.session)
        self.audit_events = SqlAuditEventRepository(self.session)
        self.reviews = SqlReviewRepository(self.session)
        return self

    def commit(self) -> None:
        self.session.commit()

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None or not self._committed:
            self.session.rollback()
        self.session.close()
```

`PersistentAuditEventLog` exposes the append/list/latest/has-source surface needed by projector and result extraction while opening a fresh Unit of Work per atomic projection.

- [x] **Step 5: Verify GREEN**

Run the three persistence test files. Expected: all pass.

---

### Task 4: Verified Identity and Session Locks

**Files:**
- Create: `agent-server/focusproof/api/auth.py`
- Create: `agent-server/focusproof/openhands_runtime/locks.py`
- Create: `agent-server/tests/api/test_identity.py`
- Create: `agent-server/tests/openhands_runtime/test_concurrent_review_lock.py`

**Interfaces:**
- Produces: `VerifiedIdentity`, `get_verified_identity()`, `SessionRunLock`, `FileSessionRunLock`, `SessionBusyError`.

- [x] **Step 1: Write failing identity and lock tests**

```python
def test_development_identity_is_explicit() -> None:
    assert get_verified_identity().verified_user_id == "dev-anonymous-user"

def test_lock_rejects_path_traversal(tmp_path: Path) -> None:
    lock = FileSessionRunLock(tmp_path, timeout_seconds=0.01)
    with pytest.raises(ValueError):
        lock.acquire("../outside")
```

Use two threads and a barrier to prove one same-session review times out while a different-session operation acquires independently.

- [x] **Step 2: Verify RED**

Run both new test files. Expected: imports fail.

- [x] **Step 3: Implement identity and lock**

```python
class VerifiedIdentity(BaseModel):
    verified_user_id: str

def get_verified_identity() -> VerifiedIdentity:
    return VerifiedIdentity(verified_user_id="dev-anonymous-user")
```

`FileSessionRunLock.acquire(session_id)` validates `^[A-Za-z0-9_-]+$`, resolves `{data_dir}/locks/{session_id}.lock`, verifies containment, and maps `filelock.Timeout` to `SessionBusyError(session_id)`.

- [x] **Step 4: Verify GREEN**

Run both test files. Expected: all pass and no lock remains held after exceptions.

---

### Task 5: Bounded Tool Registry and Repository Provider

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/tool_registry.py`
- Modify: `agent-server/focusproof/openhands_runtime/tools/__init__.py`
- Modify: `agent-server/focusproof/openhands_runtime/tools/evidence_verification.py`
- Modify: `agent-server/focusproof/openhands_runtime/tools/learner_input.py`
- Modify: `agent-server/focusproof/openhands_runtime/tools/review_draft.py`
- Modify: `agent-server/focusproof/openhands_runtime/factory.py`
- Create: `agent-server/tests/openhands_runtime/test_tool_registry_lifecycle.py`

**Interfaces:**
- Produces: `configure_repository_provider(provider)`, `release_repository_provider()`, `ensure_focusproof_tools_registered()`, and fixed Tool specs parameterized by `session_id`.

- [x] **Step 1: Write failing 100-conversation registry test**

Record `list_registered_tools()` before and after creating 100 conversations. Assert the added registry set is exactly the three approved PascalCase names, registration is called at most three times, every agent `tools_map` has the three existing snake-case tool names, and forbidden default tools are absent.

- [x] **Step 2: Verify RED**

Expected: current factory adds 300 UUID-suffixed registry names and the test fails.

- [x] **Step 3: Implement fixed class registration**

Register each ToolDefinition subclass once. Agent specs are:

```python
Tool(name="FocusProofEvidenceVerificationTool", params={"session_id": session_id})
Tool(name="FocusProofLearnerInputTool", params={"session_id": session_id})
Tool(name="FocusProofReviewDraftTool", params={"session_id": session_id})
```

Each `create` accepts `session_id`; evidence execution resolves the current provider and loads `(session_id, evidence_id)`. No tool action accepts evidence text.

- [x] **Step 4: Verify GREEN**

Run registry and existing tool/factory tests. Expected: all pass with bounded registry growth.

---

### Task 6: Idempotent Conversation Synchronizer

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/synchronizer.py`
- Create: `agent-server/tests/openhands_runtime/test_message_synchronizer.py`

**Interfaces:**
- Produces: `ConversationSynchronizer.sync(handle, session, evidence, answers, verified_user_id) -> SyncResult` and `message_key_from_event(event)`.

- [x] **Step 1: Write failing synchronization tests**

Use a real persisted `LocalConversation` and TestLLM. Assert exact envelope keys, `MessageEvent.sender`, one goal message, one message per evidence, and one per answer version.

Simulate:

```python
# DB committed, crash before send: next sync sends once.
# Native send succeeded, crash before mark_synced: next sync marks without send.
```

- [x] **Step 2: Verify RED**

Expected: synchronizer module is missing.

- [x] **Step 3: Implement parser and synchronization**

Serialize with sorted compact JSON and send using:

```python
conversation.send_message(serialized_envelope, sender=verified_user_id)
```

After each native message is confirmed, open a Unit of Work and mark the matching goal/evidence/answer version synced. Parsing ignores malformed, non-user, wrong-session, or unsupported-version messages.

- [x] **Step 4: Verify GREEN**

Run synchronizer tests. Expected: all crash-window and sender assertions pass.

---

### Task 7: Persistent Projection and Review Idempotency

**Files:**
- Modify: `agent-server/focusproof/openhands_runtime/projector.py`
- Modify: `agent-server/focusproof/openhands_runtime/result_extractor.py`
- Modify: `agent-server/focusproof/runtime/event_log.py`
- Modify: `agent-server/tests/openhands_runtime/test_event_projection.py`
- Create: `agent-server/tests/persistence/test_restart_recovery.py`

**Interfaces:**
- Projector consumes `AuditEventRepository`/UoW factory.
- Result extractor consumes repositories and persists one Review per native result observation.

- [x] **Step 1: Write failing persistent projection tests**

Assert callback projection followed by simulated pre-commit crash can be reconciled, reconciling the same native event twice leaves one audit row, and two extractions of the same ReviewDraft leave one Review with one native source ID.

- [x] **Step 2: Verify RED**

Expected: current in-memory types and score/review append behavior fail persistent assertions.

- [x] **Step 3: Generalize projector and persist Reviews**

Replace `InMemoryEventLog` concrete annotations with an audit projection protocol. Projection insert uses native-source uniqueness. Remove product facts that lack native sources from production audit writes.

Result extraction constructs a `ReviewRecord` with `source_openhands_event_id` from the LearnerInput or ReviewDraft Observation and calls idempotent repository insertion. Completed scoring remains FocusProof-owned.

- [x] **Step 4: Verify GREEN**

Run persistent projection, restart recovery, existing projection, and scoring tests. Expected: all pass with one Review and one audit row per native source.

---

### Task 8: Restorable Manager and Public SDK Pause

**Files:**
- Modify: `agent-server/focusproof/openhands_runtime/handle.py`
- Modify: `agent-server/focusproof/openhands_runtime/factory.py`
- Modify: `agent-server/focusproof/openhands_runtime/manager.py`
- Create: `agent-server/tests/openhands_runtime/test_manager_shutdown.py`
- Modify: `agent-server/tests/openhands_runtime/test_conversation_lifecycle.py`
- Modify: `agent-server/tests/openhands_runtime/test_runtime_failure.py`
- Modify: `agent-server/tests/persistence/test_restart_recovery.py`

**Interfaces:**
- Produces the approved manager create/get/get_or_restore/run_review/close/close_all API backed by UoW, synchronizer, and locks.

- [x] **Step 1: Write failing restart, pause, and shutdown tests**

Create Session/Evidence, run awaiting review, record conversation/native/audit counts, close all and dispose engine, create entirely new services, restore, and assert IDs/counts/messages are stable. Submit answer and complete review; assert both Reviews remain.

Monkeypatch or source-scan to fail if FocusProof assigns `execution_status`. Assert public `pause()` is invoked for LearnerInput and ReviewDraft. Assert `close_all()` closes a paused conversation and rejects a new review after shutdown begins.

- [x] **Step 2: Verify RED**

Expected: current manager relies on dictionaries, has no restore/close_all, and directly assigns SDK state.

- [x] **Step 3: Refactor manager around durable services**

Public methods acquire the per-session lock once. `_get_or_restore_unlocked` loads product facts, creates/resumes the exact UUID/path/user ID, synchronizes messages, and reconciles native events. `run_review` uses verified identity and stores Reviews. Callback handling calls `handle.conversation.pause()` for both result observation types.

`close_all` atomically marks the manager as not accepting reviews, closes all handles, and clears only caches.

- [x] **Step 4: Verify GREEN**

Run manager, lifecycle, shutdown, failure, native-flow, and restart tests. Expected: all pass and source grep finds no FocusProof `execution_status =` assignment.

---

### Task 9: FastAPI Lifespan, Durable API, and Error Contracts

**Files:**
- Modify: `agent-server/focusproof/api/models.py`
- Modify: `agent-server/focusproof/api/app.py`
- Modify: `agent-server/tests/api/test_api_sessions.py`
- Modify: `agent-server/tests/api/test_review_conversation_runtime.py`
- Create: `agent-server/tests/api/test_restart_persistence.py`

**Interfaces:**
- Produces: `create_app(settings_override=None, llm_factory=None)`, lifespan-managed `app.state`, durable existing routes, and `GET /sessions/{id}/reviews`.

- [x] **Step 1: Write failing API tests**

Assert request JSON cannot set owner/sender, wrong dependency identity receives 404 or 403 without leaking ownership, restart with the same temporary database preserves GET Session/Events/Reviews, review lock timeout returns exact 409 JSON, and readiness failures return sanitized 503 codes.

Assert source/app state contains no module-level `_SESSIONS`, production `InMemoryEventLog`, or `_CONVERSATION_MANAGER`.

- [x] **Step 2: Verify RED**

Expected: current module-level in-memory app fails persistence and lifespan assertions.

- [x] **Step 3: Implement application factory and lifespan**

Lifespan creates settings, engine, session factory, schema readiness, UoW factory, persistent audit adapter, lock, tool provider, and manager; stores them on `app.state`; and performs approved shutdown ordering.

Routes load and mutate product facts only through Unit of Work. Session creation persists owner and stable UUID before creating runtime. Evidence/answer routes persist first and then synchronize. Review uses lazy restore and returns existing compatible fields plus persisted counts/status.

- [x] **Step 4: Implement sanitized errors and reviews route**

Return top-level 409 body exactly as approved. Return 503 bodies with only stable code/session/retryable fields. Add ordered historical reviews response without renaming existing response fields.

- [x] **Step 5: Verify GREEN**

Run all API tests. Expected: durable restart, identity, 409/503, and compatibility assertions pass.

---

### Task 10: Failure Recovery Matrix and Full Non-Real Regression

**Files:**
- Modify: persistence/runtime/API tests as needed for uncovered approved cases.

**Interfaces:**
- Consumes all implemented services.
- Produces authoritative evidence for crash, lock, corruption, and restart requirements.

- [x] **Step 1: Add any missing failing recovery cases**

Cover database locked, corrupt OpenHands persistence, callback-before-audit-commit crash, lock timeout release, paused shutdown, duplicate reconcile, and legacy artifacts not imported.

- [x] **Step 2: Verify each RED before implementation adjustment**

Run each new test by node ID and confirm it fails for the missing behavior rather than test setup.

- [x] **Step 3: Implement minimal error mapping or recovery behavior**

Map only known persistence/runtime exceptions to sanitized domain exceptions. Never replace a corrupt persisted conversation with a new UUID. Keep locks released through context managers.

- [x] **Step 4: Run required non-real verification**

```bash
alembic upgrade head
pytest agent-server/tests/persistence -v
pytest agent-server/tests/openhands_runtime -m "not real_llm" -v
pytest agent-server/tests/api -v
pytest agent-server/tests -m "not real_llm" -v
ruff check agent-server
mypy agent-server
alembic downgrade base
alembic upgrade head
```

Expected: all commands pass. Any failure starts a new red-green debugging cycle.

---

### Task 11: Real Acceptance, Report, and Completion Audit

**Files:**
- Create: `docs/research/PERSISTENCE_RUNTIME_HARDENING_REPORT.md`

**Interfaces:**
- Produces the AI0 handoff report and requirement-by-requirement completion evidence.

- [x] **Step 1: Run explicit real-LLM test only if configured**

```bash
pytest agent-server/tests -m real_llm -v -s
```

Do not print credentials. Record only pass/skip counts and sanitized model/runtime metadata.

- [x] **Step 2: Run real FastAPI restart acceptance**

Use 8765 only if the known process is ours and intentionally replaced; otherwise use 8766. Create Session/Evidence, reach awaiting user, stop only the process started for this acceptance, restart with the same DB/data directory, retrieve the same Session, submit Answer, complete Review, and verify unchanged conversation ID and duplicate-free events/reviews.

- [x] **Step 3: Write the report**

Include database ER explanation, fact boundaries, identity propagation, Repository/UoW boundary, restore flow, message idempotency, Review idempotency, lock scope, bounded registry, lifespan, SDK pause result, Alembic results, restart tests, ordinary tests, real acceptance, unresolved risks, legacy policy, and AI3 API contract.

- [x] **Step 4: Audit forbidden paths, secrets, Git, and completion gates**

Verify no files changed in forbidden directories, no `.env`/database/runtime artifacts are tracked, no remote exists, no business state assignment remains, and every original/revised requirement has direct test or command evidence.

- [x] **Step 5: Stop for AI0 review**

Do not enter AI3. Report the lack of Git commit due to missing local identity unless the environment changed naturally; never fabricate identity.
