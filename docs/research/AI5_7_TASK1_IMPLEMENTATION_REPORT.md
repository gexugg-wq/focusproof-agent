# AI5.7 Task 1 Implementation Report

## Scope and baseline

- Authoritative repository: `/home/holy/web3/focusproof-agent`
- Branch: `agent/monad-evidence-plugin`
- Frozen baseline HEAD: `9a79998f6f62853d8dc000969ceb8a6f43040ba6`
- Implemented only Task 1, “Contracts, Models, and Audit Persistence.”
- The pre-existing dirty working tree was preserved. No reset, checkout, revert, stage,
  commit, push, merge, or amend was performed.

## Files changed for Task 1

- `agent-server/focusproof/media_core/models.py`
- `agent-server/focusproof/persistence/models.py`
- `agent-server/focusproof/persistence/audit_projection.py`
- `agent-server/tests/media_core/test_malware_scanner_contract.py`
- `agent-server/migrations/versions/0006_media_scan_audit_and_receipts.py`
- `agent-server/tests/persistence/test_media_scan_audit.py`
- `docs/research/AI5_7_TASK1_IMPLEMENTATION_REPORT.md`

## Implementation summary

- Added the exact frozen `ScanResultKind` values: `clean`, `malicious`, `oversize`,
  `timeout`, `unavailable`, and `error`. The new live persistence contract cannot emit
  `unknown`.
- Added frozen `ScanRejectionCode`, `MediaScanAttempt`, and `MediaCleanReceipt` domain
  types with validation. A non-clean attempt requires a rejection code; a clean attempt
  cannot carry rejection metadata; a receipt can only be derived from a clean attempt.
- Added `scan_attempts` and `clean_receipts` ORM tables and Alembic revision 0006.
- Persisted definitions freshness and all frozen resource-bound snapshots on both tables.
- Added named uniqueness constraints for attempt ID, idempotency key, receipt attempt ID,
  and receipt hash, plus result/rejection/resource check constraints and a restrictive FK.
- Extended the existing persistence projection module with a replay-safe repository. Exact
  duplicate attempts and receipts return the existing durable value; conflicting reuse is
  rejected. Receipt persistence verifies a persisted clean attempt and an identical frozen
  snapshot before insertion.
- No safe-fact projection was added. A non-clean attempt cannot produce a clean receipt.

## TDD evidence

### RED

1. `./.venv/bin/python -m pytest agent-server/tests/media_core/test_malware_scanner_contract.py -q`
   - Failed during collection because `MediaScanAttempt`, `MediaCleanReceipt`,
     `ScanResultKind`, and `ScanRejectionCode` did not exist.
2. `./.venv/bin/python -m pytest agent-server/tests/persistence/test_media_scan_audit.py -q`
   - Failed during collection for the same missing frozen contract; migration, tables, and
     repository were also absent.

These failures were caused by the missing Task 1 production contract, not a test typo or
environment failure.

### GREEN and regression

- Task 1 combined specialty suite: `27 passed in 2.34s`.
- Receipt-hash uniqueness mutation check: `1 passed in 0.37s`; the second receipt used a
  distinct receipt ID, so the hash constraint was the failing boundary.
- Existing migration suite: `90 passed in 25.06s`.
- Complete persistence suite: `130 passed, 5 skipped in 39.14s`.
- The skipped cases are explicitly marked PostgreSQL service tests that require a supplied
  disposable target.

## Migration matrix

| Target | Upgrade | Downgrade | Re-upgrade | Constraint/compatibility proof |
| --- | --- | --- | --- | --- |
| SQLite | Actual DB | Actual DB to base | Actual DB to head | Tables, columns, FK, named unique constraints, idempotent replay, and duplicate rejection tested |
| PostgreSQL | Alembic offline DDL compiled through 0006 | Revision has ordered receipt/attempt drops | ORM DDL compiled with PostgreSQL dialect | `TIMESTAMP WITH TIME ZONE`, FK, check constraints, and unique constraints compiled successfully |

No disposable PostgreSQL service was configured, so no claim of a live PostgreSQL execution
is made. The project’s explicitly marked PostgreSQL tests remain skipped under the default
test profile.

## OpenHands SDK reuse

Task 1 did not add or imitate Runtime, Conversation, EventLog, Action, Observation, or Tool.
The existing official OpenHands SDK-backed audit projection remains intact. The new repository
is strictly SQLAlchemy persistence for media scan attempts and clean receipts, alongside the
existing projection store; it is not a second runtime or event protocol.

## Residual risks and handoff boundary

- The legacy scanner port still contains its migration-era `unknown` status. Frozen Task 2
  explicitly owns migrating the existing clamd adapter and port. Task 1 instead establishes a
  new exact enum and database check that reject `unknown`; it does not reinterpret it as clean.
- Live PostgreSQL execution requires AI0 to provide an explicitly authorized disposable
  PostgreSQL target. Dialect and Alembic offline compilation are covered here.
- Scanner integration, live clamd behavior, ingestion gating, quarantine/decoder isolation,
  safe-fact projection, and deployment gates are Tasks 2–6 and were not implemented.

## Boundaries not touched

No Task 1 work modified frontend, contracts, Monad, scoring, Manager, Factory, Synchronizer,
ResultExtractor, the agent loop, OpenHands SDK source, scanner adapters, ingestion, quarantine,
decoder code, safe-fact projection, or deployment wiring. No fake-clean production fallback was
introduced. No secrets or environment files were read or modified.

## Fix Round 1: frozen outcome/rejection-code invariant

### Review finding reproduced

The first implementation only required a non-null rejection code for non-clean outcomes. It
therefore allowed cross-pairs such as `malicious + daemon_error`, and the database accepted
arbitrary non-null rejection-code strings.

RED commands and evidence:

- `./.venv/bin/python -m pytest agent-server/tests/media_core/test_malware_scanner_contract.py -q`
  produced 32 failures after parameterizing every illegal cross-pair and unknown enum strings.
- `./.venv/bin/python -m pytest agent-server/tests/persistence/test_media_scan_audit.py -q`
  produced 7 failures: SQLite accepted five cross-pairs plus an unknown rejection code, and
  the PostgreSQL offline authoritative CHECK assertion was absent.

### Fix

- Added `SCAN_RESULT_REJECTION_CODES` as the single authoritative domain mapping.
- Domain construction now rejects unknown result/code types and every pair outside the frozen
  mapping. `error` accepts only `daemon_error` or `legacy_unknown_unclassified`.
- Generated `SCAN_RESULT_REJECTION_CODE_CHECK_SQL` from that mapping.
- ORM metadata directly consumes the generated authoritative CHECK.
- Revision 0006 contains the frozen equivalent CHECK. The PostgreSQL offline Alembic test
  normalizes and compares its emitted DDL to the domain-generated authority, so drift fails.
- SQLite Core tests bypass the repository and prove the database rejects cross-pairs and
  unknown strings. All seven legal pairs construct and persist successfully.
- Revision 0006 was corrected in place; no 0007 migration was created.

### Review closure evidence

- Task 1 specialty suite after GREEN: `86 passed in 7.75s`.
- Existing migration suite after GREEN: `90 passed in 29.21s`.
- Targeted Ruff: passed.
- Targeted strict mypy: passed for all three Task 1 production modules.
- `git diff --check`: passed.

The final full persistence, project-wide Ruff/mypy, SQLite migration round-trip, PostgreSQL

Final fresh handoff verification:

- Task 1 specialty suite: `86 passed in 11.30s`.
- Migration suite: `90 passed in 40.96s`.
- Complete persistence suite: `145 passed, 5 skipped in 64.22s`.
- Full Ruff: passed.
- Strict mypy: `114 source files` passed.
- PostgreSQL Alembic offline upgrade through 0006: passed and emitted the authoritative CHECK.
- `git diff --check`: passed; staged state and intermediary-patch checks were clean.
