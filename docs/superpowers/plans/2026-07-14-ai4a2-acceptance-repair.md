# AI4A.2 Acceptance Repair Implementation Plan

> Execute against `ai4a-general-verification-framework` at baseline
> `00e32722d3550a05e0c45852ef96dbc0e47af281`. The six AI0 normative
> working-tree files are read-only inputs and must never be staged.

## Constraints

- Reuse OpenHands SDK 1.31.0 `Agent`, `LocalConversation`, `EventLog`,
  `ToolDefinition`, `ToolExecutor`, `ActionEvent`, and `ObservationEvent`.
- Use `LocalConversation.arun()`, `LocalConversation.interrupt()`,
  `LocalConversation.cancel_token`, `ToolExecutor.interrupt()`, and
  `ToolExecutor.close()` for native lifecycle and cancellation behavior.
- Do not add a runtime, agent loop, event log, tool protocol, or default OpenHands
  programming tools. Do not modify frontend, contracts, `.env`, `var`, or SDK
  source. Do not push.
- Follow RED-GREEN-REFACTOR. Create and run all three independent RED groups
  before changing production code.

## Task 1: RED — interruptible URL deadline

**Files:**

- Create: `agent-server/tests/openhands_runtime/test_interruptible_url_deadline.py`

Add tests proving: a resolver delayed 200 ms returns `network_timeout` within a
50 ms wall-clock budget and tolerance; slow-drip response exceeds the same total
budget; `interrupt()` is idempotent; timeout in Session A does not affect Session
B sharing the fetcher/client; observations and projected audit data contain no
raw credential/query/fragment/path URL; cancelling `LocalConversation.arun()`
uses native `InterruptEvent` and orphan-action completion semantics.

Run the file and record expected failures caused by the missing hard-deadline
adapter, ignored conversation token, synchronous manager run, and missing
executor lifecycle hooks.

## Task 2: RED — restore projection ordering

**Files:**

- Create: `agent-server/tests/openhands_runtime/test_restore_projection_order.py`

Persist old native events, leave Evidence/Answer rows pending, restore, and
assert old audit events precede newly synchronized projections with continuous,
unique source indices beginning at zero. Assert a second restore creates no
duplicate Event/Review/audit rows. Simulate the crash window where the native
message exists but the DB sync marker does not and assert marker-only recovery,
with no resend.

Run the file and record the expected ordering/index failure.

## Task 3: RED — bounded text evidence messages

**Files:**

- Create: `agent-server/tests/openhands_runtime/test_runtime_evidence_messages.py`

For persistent and legacy ingestion, assert `MessageEvent.to_llm_message()`
contains the evidence's conceptual sentence; long text is capped with
`textTruncated` and `originalCharacterCount`; prompt-like text remains untrusted
content; existing OpenHands secret redaction removes API-key patterns from the
persisted native message; URL messages retain origin/hash only; audit events
never contain bodies; tool action arguments contain only `evidence_id`; restore
does not duplicate a text evidence message.

Run the file and record expected failures caused by the current use of
`safe_evidence_payload()` as the Agent-facing message.

## Task 4: GREEN — URL hard total timeout and native cancellation

**Files:**

- Modify: `agent-server/focusproof/openhands_runtime/tools/url_fetcher.py`
- Modify: `agent-server/focusproof/openhands_runtime/tools/url_evidence.py`
- Modify: `agent-server/focusproof/openhands_runtime/manager.py`
- Test: `agent-server/tests/openhands_runtime/test_interruptible_url_deadline.py`

Implement a minimal per-call wall-clock deadline adapter at the URL executor
boundary because SDK 1.31.0 has no single-tool hard-deadline primitive. Isolate
blocking fetch work in a daemon worker, return an inconclusive
`network_timeout` immediately at expiry, and signal cooperative cancellation to
the fetcher without closing the shared client. Track active calls per executor
with a lock and operation-local events; make `interrupt()` and `close()`
thread-safe and idempotent; read `conversation.cancel_token`. Preserve DNS
pinning, redirect revalidation, size limits, and SSRF policy. Run review through
native `LocalConversation.arun()` so `LocalConversation.interrupt()` owns
conversation cancellation.

Run focused and adjacent URL/lifecycle tests, Ruff, and Mypy. Commit exactly the
Task 1 implementation/test scope as:
`fix(runtime): enforce interruptible URL deadline`.

## Task 5: GREEN — reconcile restored events before synchronization

**Files:**

- Modify: `agent-server/focusproof/openhands_runtime/manager.py`
- Test: `agent-server/tests/openhands_runtime/test_restore_projection_order.py`

Immediately after create/restore, snapshot `conversation.state.events`,
reconcile that snapshot so the projector advances its callback index, then run
the synchronizer. Do not replay callbacks or create another event log. Retain
source-event idempotence and native `message_key` crash compensation.

Run focused and adjacent persistence/projection tests. Commit exactly this scope
as: `fix(runtime): reconcile restored events before synchronization`.

## Task 6: GREEN — Agent-visible bounded text evidence

**Files:**

- Create: `agent-server/focusproof/openhands_runtime/evidence_messages.py`
- Modify: `agent-server/focusproof/openhands_runtime/synchronizer.py`
- Modify: `agent-server/focusproof/openhands_runtime/manager.py`
- Test: `agent-server/tests/openhands_runtime/test_runtime_evidence_messages.py`

Leave `safe_evidence_payload()` unchanged as the privacy-safe audit projection.
Add one explicit runtime message builder. For text evidence, include a fixed
character-capped, OpenHands-`redact_text_secrets`-sanitized text field plus
`textTruncated` and `originalCharacterCount`. Label it untrusted evidence
content. For URL evidence, emit only the existing origin/hash metadata. Use the
same builder for persistent and legacy paths. Keep tool actions restricted to
`evidence_id`, and do not use `extended_content`.

Run focused and adjacent synchronization/projection/tool tests, Ruff, and Mypy.
Commit exactly this scope as:
`fix(runtime): expose bounded text evidence to native messages`.

## Task 7: Full verification

Run the three new groups independently, then the full pytest suite with real-LLM
tests deselected, Ruff, Mypy, and `git diff --check`. If no migration/schema
changed, record Alembic as explicitly unaffected; otherwise run upgrade,
downgrade, and re-upgrade. Recheck the six protected file hashes and ensure no
`.env`, `var`, frontend, contracts, SDK source, or secret files are staged.

## Task 8: Report and stop

**Files:**

- Modify: `docs/research/AI4A_GENERAL_VERIFICATION_FRAMEWORK_REPORT.md`

Add `Root Causes`, `OpenHands APIs Reused`, `FocusProof-Owned SDK Gaps`,
`Security/Privacy Verification`, `Commands And Exact Results`, and `Remaining
Limitations`. Include exact verification outputs and the formal SDK hard-deadline
gap. Commit report/tests/plan-only residue as:
`test/docs: close AI4A acceptance repairs`.

Do not push and do not begin AI4B. Stop for AI0 acceptance.
