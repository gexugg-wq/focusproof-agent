# AI5.7 Task5 Fix Round 1 Report

Status: AI5_7_TASK5_FIX_ROUND1_READY
Date: 2026-08-25
Branch: agent/monad-evidence-plugin

## Scope

This round fixes only the three independently confirmed Important findings:

1. Unsafe raw payload copying in the FocusProof-to-OpenHands MessageEvent adapter.
2. Non-atomic mixed clean/non-clean media synchronization.
3. Cross-test OpenHands conversation/provider lifecycle pollution.

The pre-existing dirty worktree was preserved. No reset, revert, checkout, clean,
stage, commit, push, merge, or amend was performed. The pre-existing
`focusproof/persistence/repositories.py` changes were not touched.

## Root causes and fixes

### Event payload disclosure

Root cause: `focusproof_event_to_openhands_message` copied every payload field
except `sender`. That allowed opaque object keys, private temporary paths,
credentials/tokens, private metadata, and bytes to reach the official SDK
`MessageEvent`.

Fix: the adapter now selects fields from an explicit allowlist keyed by product
event type. Stable envelope identity fields come from the verified `Event`,
payload self-reported sender is never accepted, and byte values are rejected.
The output remains the official OpenHands SDK 1.31.0 `MessageEvent`.

### Mixed clean/non-clean synchronization

Root cause: the synchronizer constructed and sent each message in one loop.
The factory rejected the first non-clean media item after earlier clean items
had already mutated the official Conversation/EventLog, and later clean items
were never considered.

Fix: synchronization now prebuilds and safety-classifies the entire pending
batch before the first Conversation side effect. Unavailable, missing,
non-clean, timeout, error, pending, and legacy-unproven media facts fail closed
with zero contribution and do not block other clean or normal-text messages.
Only the prepared safe plan is sent, in original relative order. Existing
message keys remain the replay/idempotency authority, and skipped media is not
marked confirmed so a later actual clean receipt can contribute.

The media-type predicate remains in the approved
`runtime_evidence_message_factory.py` boundary; the generic synchronizer has
no image-specific identifier.

### OpenHands lifecycle/order pollution

Root cause: OpenHands SDK 1.31.0 `LocalConversation.__del__` calls `close()`.
Although SDK cleanup is internally idempotent, explicitly closed conversations
can remain in Python reference cycles and be collected during a later test that
monkeypatches `LocalConversation.close`. Those delayed destructor entries
inflated the later test's `close_calls`. Process-global FocusProof tool
providers/closers also survived test boundaries.

Fix: the OpenHands runtime test fixture now releases the process-global
FocusProof repository/fetcher/execution-pool providers and performs deterministic
cycle collection before and after every runtime test. The production shutdown
contract and `close_calls == 1` assertions were not weakened; no sleeps or
test-order dependencies were added.

## RED/GREEN evidence

- Event allowlist RED: failed because `private-object-key`,
  `/private/tmp/focusproof-secret`, credential/token values, private metadata,
  forged sender, and bytes appeared in serialized MessageEvent output.
- Mixed batch RED: 4 failed for clean/non-clean/clean, non-clean/clean,
  clean/non-clean, and all-non-clean; each failed at the current first
  non-clean factory rejection.
- Architecture RED after initial GREEN: the full backend caught an `image/`
  identifier in generic synchronizer code. The predicate was moved back to the
  approved media factory boundary.
- Focused final GREEN: 31 passed for event, runtime evidence, and manager
  shutdown contracts.

## Final test matrix

- Failed shutdown node isolated: 1 passed.
- OpenHands adapter/runtime excluding `test_real_llm.py`, consecutive final
  runs: 271 passed; 271 passed; 271 passed.
- `media_core/test_ingestion.py`, hard limit 360 seconds: 68 passed in
  274.23 seconds.
- Task1-4 explicit media regression matrix: 207 passed, 1 skipped.
- Architecture failure node plus Task5 runtime messages: 21 passed.
- Full backend default/non-real-LLM: 1860 passed, 6 skipped, 14 deselected,
  470 warnings in 846.50 seconds.
- Ruff over `agent-server/focusproof` and `agent-server/tests`: passed.
- Strict mypy over 117 source files: passed.
- `git diff --check`: passed.
- Cached diff: empty.
- `*.orig`: zero.

Explicit exclusions and external gates: `test_real_llm.py` was ignored.
Repository-default real external, postgres, staging, real clamd, and related
live-gated cases remained skipped/deselected; this report does not claim those
external gates.

## Files changed in this round

- `agent-server/focusproof/openhands_adapter/events.py`
- `agent-server/focusproof/openhands_runtime/runtime_evidence_message_factory.py`
- `agent-server/focusproof/openhands_runtime/synchronizer.py`
- `agent-server/tests/openhands_adapter/test_event_projection.py`
- `agent-server/tests/openhands_runtime/conftest.py`
- `agent-server/tests/openhands_runtime/test_runtime_evidence_messages.py`
- `docs/research/AI5_7_TASK5_FIX_ROUND1_REPORT.md`

## Residual risks

- Live LLM, external services, postgres, staging, and live clamd remain outside
  this fix round and were not certified.
- Non-clean/unavailable media remains intentionally retryable rather than marked
  synchronized; a later active clean receipt can safely contribute once.

## Fix Round 2: Verified sender preservation

Status: AI5_7_TASK5_FIX_ROUND2_READY
Date: 2026-08-25

### Scope and call-chain audit

This round fixes only the independently reported verified sender finding. It
does not modify repositories, mixed batching, lifecycle behavior, the SDK,
scoring/evidence models, Monad, or real LLM integration.

The complete repository call-site audit found no production caller of
`focusproof_event_to_openhands_message`; the adapter is currently exercised
only by `tests/openhands_adapter/test_event_projection.py`. Therefore there is
no production identity/session-owner call chain to update or to block, and no
identity was inferred from payload fields or `event.source`.

### Root cause and fix

The official OpenHands `MessageEvent` projection had historically emitted
`sender=None`, and its test had accepted that loss of provenance. The current
adapter boundary now requires an explicit keyword-only `verified_sender`,
keeps role provenance in `source` independently, rejects payload-forged
identity fields through the existing allowlist, and constructs the official
SDK event with `sender=verified_sender`.

The remaining boundary defect found by the Round 2 RED was that explicit
`None` reached `.strip()` and raised `AttributeError`. The minimal fix
rejects every non-string or blank value with a controlled `ValueError`
before any SDK event is constructed. Omitting the required keyword remains a
Python `TypeError`, so the interface is explicitly mandatory.

### RED/GREEN evidence

- RED: the explicit `None` sender test failed at
  `events.py:61` with `AttributeError: 'NoneType' object has no attribute
  'strip'`.
- GREEN: event projection/sender focused: 12 passed.
- Task5 focused event/runtime/SDK suite: 40 passed.
- Forged payload sender cannot override `verified_sender`; forbidden fields
  remain absent; user/environment role provenance, official SDK type, stable
  message key, session correlation, and sender identity are covered by the
  focused adapter tests.

### Final verification matrix

- OpenHands adapter/runtime contracts excluding `test_real_llm.py`, three
  consecutive runs: 276 passed, 1 warning each.
- Architecture boundary suite: 40 passed.
- Full backend default/non-real-LLM: 1865 passed, 6 skipped, 14 deselected,
  470 warnings in 826.25 seconds.
- Ruff over `agent-server/focusproof` and `agent-server/tests`: passed.
- Strict mypy over 117 source files: passed.
- Git diff check, cached diff, and `*.orig` checks are recorded after this
  report append in the final handoff.

Explicit exclusions: `test_real_llm.py` was ignored. Repository-default real
external, postgres, staging, live ClamAV, and related live-gated cases remained
skipped/deselected and are not certified here.

AI5_7_TASK5_FIX_ROUND2_READY
