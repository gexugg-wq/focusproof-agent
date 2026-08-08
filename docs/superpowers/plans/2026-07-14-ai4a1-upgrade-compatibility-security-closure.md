# AI4A.1 Upgrade Compatibility And Security Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close AI0's AI4A acceptance findings for cross-version OpenHands restoration, URL execution deadlines and secret redaction, and the Agent/Observation trust boundary without entering AI4B.

**Architecture:** Continue using the OpenHands SDK 1.31.0 `Agent`, `Conversation`/`LocalConversation`, `ConversationState`, native `EventLog`, `ActionEvent`/`ObservationEvent`, and `ToolDefinition`/`ToolExecutor`. Fresh conversations receive only the AI4A tools; conversations with an existing SDK `base_state.json` receive an additive compatibility tool superset so `Agent.verify()` never sees a removed legacy tool. URL verification gets one monotonic deadline propagated from capability metadata, and all URL-derived data crosses a single redaction boundary before reaching Observations, messages, or audit projection.

**Tech Stack:** Python 3.12, OpenHands SDK 1.31.0, Pydantic, HTTPX, pytest, Ruff, Mypy.

## Global Constraints

- Work only on branch `ai4a-general-verification-framework`, starting at `7a93546ca3963a5e934f9bbb6a2ff03cea9028ee`.
- Do not create another Conversation, Agent loop, EventLog, Action/Observation protocol, or tool runtime.
- Keep `include_default_tools=[]`; do not enable OpenHands programming tools.
- Do not modify `frontend/`, `contracts/`, `.env`, `var/`, OpenHands SDK source, `docs/architecture/`, `docs/protocol/`, or `docs/project-management/`.
- Do not read, print, or commit API keys, database credentials, or real user URLs.
- Use strict TDD: add one failing behavior test, record the expected RED, implement the minimum fix, and independently verify GREEN.
- Do not run the real-LLM test without separate AI0 authorization.
- Do not push, merge, or start AI4B; finish with a clean local worktree for AI0 acceptance.

---

### Task 1: Reproduce BASE To AI4A Restoration With Native OpenHands State

**Files:**
- Create: `agent-server/tests/openhands_runtime/test_upgrade_compatibility.py`

**Interfaces:**
- Consumes: OpenHands `Agent`, `Conversation`, `LocalConversation`, `ConversationState.append_event()`, native `ActionEvent`, `ObservationEvent`, and the three pre-AI4A tool classes.
- Produces: a real persisted legacy conversation fixture and an acceptance test that initially fails through `RuntimeCreationError` caused by `tools were removed mid-conversation`.

- [x] **Step 1: Build a real legacy conversation fixture**

Create a helper that registers FocusProof tools, constructs an SDK `Agent` with exactly these `Tool` specs, and persists it through `Conversation`:

```python
LEGACY_TOOL_CLASSES = (
    "FocusProofEvidenceVerificationTool",
    "FocusProofLearnerInputTool",
    "FocusProofReviewDraftTool",
)

legacy_agent = Agent(
    llm=_legacy_llm(),
    tools=[Tool(name=name, params={"session_id": session_id}) for name in LEGACY_TOOL_CLASSES],
    include_default_tools=[],
    system_prompt=FOCUSPROOF_SYSTEM_PROMPT,
)
conversation = Conversation(
    agent=legacy_agent,
    workspace=workspace_path,
    persistence_dir=persistence_path,
    conversation_id=conversation_id,
    visualizer=None,
    delete_on_close=False,
)
```

Use `send_message()` for a native `MessageEvent`, then `conversation.state.append_event()` for a legacy `ActionEvent` carrying `EvidenceVerificationAction` and its matching `ObservationEvent` carrying `EvidenceVerificationObservation`. Save the serialized event JSON and IDs before closing.

- [x] **Step 2: Write the cross-version restore acceptance test**

The test must call the real current `ConversationFactory.create()` with the same session and conversation ID and assert:

```python
assert restored.conversation_id == conversation_id
assert [event.id for event in restored.conversation.state.events] == legacy_event_ids
assert [event.model_dump_json() for event in restored.conversation.state.events[:3]] == legacy_json
assert "focusproof_evidence_verification" in restored.conversation.agent.tools_map
assert "focusproof_text_evidence_verification" in restored.conversation.agent.tools_map
assert "focusproof_url_evidence_verification" in restored.conversation.agent.tools_map
```

Append or run a new AI4A tool call after restore and confirm the new native event follows, rather than replaces, the legacy events. Reopen a second time and reconcile twice through `OpenHandsEventProjector`, asserting the audit count is unchanged on the second reconcile.

- [x] **Step 3: Run the new test and record the expected RED**

Run:

```bash
.venv/bin/python -m pytest \
  agent-server/tests/openhands_runtime/test_upgrade_compatibility.py -q
```

Expected: FAIL with `RuntimeCreationError`; `exc.__cause__` contains OpenHands `Cannot resume conversation: tools were removed mid-conversation` and names `focusproof_evidence_verification`.

Do not mock `Agent.verify()`, `ConversationState.create()`, or the SDK tool compatibility check.

---

### Task 2: Assemble An Additive Compatibility Toolset On Restore

**Files:**
- Modify: `agent-server/focusproof/openhands_runtime/tool_assembler.py`
- Modify: `agent-server/focusproof/openhands_runtime/factory.py`
- Modify: `agent-server/focusproof/openhands_runtime/handle.py`
- Modify: `agent-server/focusproof/openhands_runtime/projector.py`
- Modify: `agent-server/focusproof/openhands_runtime/result_extractor.py`
- Modify: `agent-server/tests/openhands_runtime/test_factory.py`
- Modify: `agent-server/tests/openhands_runtime/test_event_projection.py`
- Modify: `agent-server/tests/openhands_runtime/test_native_event_flow.py`
- Test: `agent-server/tests/openhands_runtime/test_upgrade_compatibility.py`

**Interfaces:**
- Produces: `SessionToolAssembler.assemble(..., compatibility_restore: bool = False)`, matching `version()`, and `ConversationHandle.compatibility_restore: bool`.
- Consumes: the SDK's public `LocalConversation.get_persistence_dir()` path derivation and its persisted `base_state.json`; no private registry or execution status mutation.

- [x] **Step 1: Add RED tests for all restoration classifications**

Cover four cases under an explicit temporary `data_dir`:

```python
assert fresh_empty_directory.compatibility_restore is False
assert set(fresh_empty_directory.conversation.agent.tools_map) == NEW_TOOL_NAMES
assert new_conversation.compatibility_restore is False
assert "focusproof_evidence_verification" not in new_conversation.conversation.agent.tools_map
assert legacy_restore.compatibility_restore is True
assert set(legacy_restore.conversation.agent.tools_map) == COMPATIBILITY_TOOL_NAMES
assert ai4a_restore.compatibility_restore is True
assert set(ai4a_restore.conversation.agent.tools_map) == COMPATIBILITY_TOOL_NAMES
```

Also assert a path outside `FOCUSPROOF_DATA_DIR` cannot be classified or used as restoration state.

- [x] **Step 2: Implement exact persisted-state detection and compatibility assembly**

In `ConversationFactory`, derive the conversation-specific path using the SDK public method and classify restoration only when the exact base snapshot is a file:

```python
conversation_store = Path(
    LocalConversation.get_persistence_dir(persistence_path, conversation_id)
).resolve()
if not conversation_store.is_relative_to(self._data_dir):
    raise ValueError("conversation persistence path is outside FOCUSPROOF_DATA_DIR")
compatibility_restore = (conversation_store / "base_state.json").is_file()
```

Pass `compatibility_restore` to assembler and version calculation. When true, prepend or append `Tool(name="FocusProofEvidenceVerificationTool", params={"session_id": session_id})` without duplicating any current tool. Fresh assembly remains learner-input, review-draft, text verifier, and URL verifier only.

- [x] **Step 3: Add RED tests for legacy action/observation compatibility**

Create native events with `EvidenceVerificationAction` and `EvidenceVerificationObservation`, then assert projector and extractor behavior:

```python
assert projected_action.type == "verification.requested"
assert projected_observation.type == "verification.completed"
assert projected_observation.payload["weak_signals"] == legacy.weak_signals
assert raw_legacy_event.model_dump_json() == before_json
assert projector.reconcile(events) == 0  # second pass

converted = _focusproof_observations([legacy_event])
assert converted[0].status == "inconclusive"
assert converted[0].sourceRefs == safe_source_refs
assert converted[0].facts["weak_signals"] == legacy.weak_signals
```

The legacy `verified` boolean must not become final learning status or a score.

- [x] **Step 4: Implement read-only legacy conversion**

Extend `OpenHandsEventProjector` type checks to accept both legacy and AI4A types. Build a new audit payload from the legacy model without assigning back to the native event. Extend `_focusproof_observations()` similarly, mapping legacy results to read-only `Observation(status="inconclusive", ...)` with preserved `sourceRefs` and `weakSignals`.

- [x] **Step 5: Verify GREEN independently**

Run:

```bash
.venv/bin/python -m pytest \
  agent-server/tests/openhands_runtime/test_upgrade_compatibility.py \
  agent-server/tests/openhands_runtime/test_factory.py \
  agent-server/tests/openhands_runtime/test_event_projection.py \
  agent-server/tests/openhands_runtime/test_native_event_flow.py -q
```

Expected: all selected tests PASS, no `tools were removed mid-conversation`, legacy native JSON unchanged, second reconciliation adds zero audit rows.

- [x] **Step 6: Commit Tasks 1 And 2**

```bash
git add \
  agent-server/focusproof/openhands_runtime/tool_assembler.py \
  agent-server/focusproof/openhands_runtime/factory.py \
  agent-server/focusproof/openhands_runtime/handle.py \
  agent-server/focusproof/openhands_runtime/projector.py \
  agent-server/focusproof/openhands_runtime/result_extractor.py \
  agent-server/tests/openhands_runtime/test_upgrade_compatibility.py \
  agent-server/tests/openhands_runtime/test_factory.py \
  agent-server/tests/openhands_runtime/test_event_projection.py \
  agent-server/tests/openhands_runtime/test_native_event_flow.py
git commit -m "fix(runtime): restore legacy OpenHands conversations"
```

---

### Task 3: Enforce One Capability-Driven URL Deadline

**Files:**
- Modify: `agent-server/focusproof/openhands_runtime/capabilities.py`
- Modify: `agent-server/focusproof/openhands_runtime/tools/url_fetcher.py`
- Modify: `agent-server/focusproof/openhands_runtime/factory.py`
- Modify: `agent-server/tests/openhands_runtime/test_capability_registry.py`
- Modify: `agent-server/tests/openhands_runtime/test_url_safety.py`

**Interfaces:**
- Produces: `BoundedUrlFetcher(..., total_timeout_seconds: float, clock: Callable[[], float] = monotonic)` and `VerificationCapabilityRegistry.get(name: str) -> VerificationCapability | None`.
- Consumes: URL capability `timeout_seconds`; the factory passes that exact value to the production fetcher.

- [x] **Step 1: Add fake-clock RED tests for every phase**

Use a manual clock, not `sleep()`:

```python
class FakeClock:
    now = 0.0
    def __call__(self) -> float:
        return self.now
    def advance(self, seconds: float) -> None:
        self.now += seconds
```

Add tests where resolver validation advances past the deadline, redirect handling advances past it, and a `SyncByteStream` yields small chunks while advancing the clock. Assert each raises `UrlFetchError` with `code == "network_timeout"`, and the stream/response close marker is set immediately after the timeout.

- [x] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  agent-server/tests/openhands_runtime/test_url_safety.py \
  agent-server/tests/openhands_runtime/test_capability_registry.py -q
```

Expected: FAIL because `total_timeout_seconds`, fake clock injection, and registry timeout lookup are absent; a slow small-chunk stream currently completes or exceeds only the byte limit.

- [x] **Step 3: Implement the absolute monotonic deadline**

At the beginning of `fetch()` calculate one deadline:

```python
deadline = self._clock() + self._total_timeout_seconds
```

Use `_remaining(deadline)` before and after policy validation/DNS, before and after every redirect validation, before each HTTP request, before and after each streamed chunk, and before and after title/text extraction. `_remaining()` raises the stable timeout error when no time remains:

```python
def _remaining(self, deadline: float) -> float:
    remaining = deadline - self._clock()
    if remaining <= 0:
        raise UrlFetchError("network_timeout", "The URL request timed out.")
    return remaining
```

Apply the remaining budget to all HTTPX timeout phases on the built request. Keep `closing(response)` around the entire redirect/body path so deadline exceptions close the response synchronously and stop iteration.

- [x] **Step 4: Wire capability metadata into the production fetcher**

Add a thread-safe registry lookup, obtain the built-in URL capability in `ConversationFactory.__init__`, and construct:

```python
BoundedUrlFetcher(
    policy=UrlSafetyPolicy(allow_http=False),
    client=client,
    total_timeout_seconds=url_capability.timeout_seconds,
)
```

Add a test with a custom URL capability timeout and a recording fetcher constructor or exposed read-only timeout property, proving the metadata value is consumed rather than duplicated.

- [x] **Step 5: Verify GREEN and commit**

Run:

```bash
.venv/bin/python -m pytest \
  agent-server/tests/openhands_runtime/test_url_safety.py \
  agent-server/tests/openhands_runtime/test_capability_registry.py \
  agent-server/tests/openhands_runtime/test_factory.py -q
```

Expected: PASS; slow small chunks stop with `network_timeout` and close the stream.

```bash
git add \
  agent-server/focusproof/openhands_runtime/capabilities.py \
  agent-server/focusproof/openhands_runtime/tools/url_fetcher.py \
  agent-server/focusproof/openhands_runtime/factory.py \
  agent-server/tests/openhands_runtime/test_capability_registry.py \
  agent-server/tests/openhands_runtime/test_url_safety.py
git commit -m "fix(runtime): enforce URL verification deadline"
```

---

### Task 4: Redact URL Secrets Before Native And Product Projection

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/url_redaction.py`
- Modify: `agent-server/focusproof/openhands_runtime/tools/url_evidence.py`
- Modify: `agent-server/focusproof/openhands_runtime/synchronizer.py`
- Modify: `agent-server/focusproof/openhands_runtime/manager.py`
- Modify: `agent-server/focusproof/openhands_runtime/projector.py`
- Modify: `agent-server/focusproof/openhands_runtime/result_extractor.py`
- Modify: `agent-server/tests/openhands_runtime/test_url_evidence_tool.py`
- Modify: `agent-server/tests/openhands_runtime/test_message_synchronizer.py`
- Modify: `agent-server/tests/openhands_runtime/test_event_projection.py`
- Modify: `agent-server/tests/openhands_runtime/test_conversation_lifecycle.py`

**Interfaces:**
- Produces: `redact_url(url: str) -> dict[str, object]`, `redact_url_text(text: str | None, urls: Collection[str]) -> str | None`, and `sanitize_source_refs(refs: Collection[str]) -> list[str]`.
- Preserves: database `source_url`; only LLM/native Observation/audit boundaries are redacted.

- [x] **Step 1: Add parameterized RED tests for URL secret shapes**

Cover path secrets, signed path segments, query, fragment, credentials, non-default port, and redirect chains. Use only synthetic `example.com` values:

```python
SECRET_URLS = (
    "https://example.com/hooks/secret-token",
    "https://example.com/download/signed/abc123",
    "https://example.com/path?token=secret#private",
    "https://user:password@example.com:8443/private/key",
)
```

Assert `secret-token`, `abc123`, `token=secret`, `private`, `user`, and `password` are absent from native Observation content, facts, source refs, redirect chain, `model_dump_json()`, synchronized `MessageEvent` JSON, and projected audit payload JSON. Assert the database repository still receives the original URL.

- [x] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  agent-server/tests/openhands_runtime/test_url_evidence_tool.py \
  agent-server/tests/openhands_runtime/test_message_synchronizer.py \
  agent-server/tests/openhands_runtime/test_event_projection.py \
  agent-server/tests/openhands_runtime/test_conversation_lifecycle.py -q
```

Expected: FAIL because path segments remain in `normalized_url`, `redirect_chain`, and `source_refs`, while `ConversationSynchronizer` and legacy manager messages still include full `sourceUrl` and arbitrary metadata.

- [x] **Step 3: Implement one URL redaction representation**

The representation must expose no path/query/fragment/userinfo:

```python
{
    "scheme": "https",
    "hostname": "example.com",
    "port": 8443,              # only when non-default
    "origin": "https://example.com:8443",
    "path_redacted": True,
    "url_sha256": "<sha256 of full canonical URL>",
}
```

Never return the canonical URL itself. Replace URL path/query/userinfo tokens that appear in title or excerpt with `[redacted]`. Convert URL-like source refs to `url-sha256:<digest>` and retain non-URL evidence IDs/content hashes.

- [x] **Step 4: Apply the redaction boundary everywhere**

`UrlEvidenceVerificationExecutor` must keep passing the original database URL only to `fetcher.fetch()`, but build all Observation fields from redacted metadata. `ConversationSynchronizer` and the legacy manager message path must emit only `evidenceId`, `evidenceType`, `contentHash`, and redacted URL metadata; omit `textContent`, raw `sourceUrl`, and arbitrary metadata. `OpenHandsEventProjector` must sanitize old persisted evidence messages and old observation source refs during read-only projection.

- [x] **Step 5: Verify GREEN and commit**

Run the RED command again. Expected: PASS with all secret substrings absent while repository/database assertions still show the original synthetic URL.

```bash
git add \
  agent-server/focusproof/openhands_runtime/url_redaction.py \
  agent-server/focusproof/openhands_runtime/tools/url_evidence.py \
  agent-server/focusproof/openhands_runtime/synchronizer.py \
  agent-server/focusproof/openhands_runtime/manager.py \
  agent-server/focusproof/openhands_runtime/projector.py \
  agent-server/focusproof/openhands_runtime/result_extractor.py \
  agent-server/tests/openhands_runtime/test_url_evidence_tool.py \
  agent-server/tests/openhands_runtime/test_message_synchronizer.py \
  agent-server/tests/openhands_runtime/test_event_projection.py \
  agent-server/tests/openhands_runtime/test_conversation_lifecycle.py
git commit -m "fix(runtime): redact URL evidence secrets"
```

---

### Task 5: Harden The Agent And Observation Trust Boundary

**Files:**
- Modify: `agent-server/focusproof/openhands_runtime/prompts.py`
- Modify: `agent-server/focusproof/openhands_runtime/tools/verification.py`
- Modify: `agent-server/tests/openhands_runtime/test_native_event_flow.py`
- Modify: `agent-server/tests/openhands_runtime/test_verification_contract.py`
- Test: `agent-server/tests/openhands_runtime/test_text_evidence_tool.py`
- Test: `agent-server/tests/openhands_runtime/test_url_evidence_tool.py`

**Interfaces:**
- Produces: recursive pre-validation of `facts` and raw `weak_signals` input against a fixed reserved verdict-key set.
- Preserves: existing text/URL executor output and deterministic scoring outside tool Observations.

- [x] **Step 1: Add prompt and recursive-validation RED tests**

Assert the prompt states all four trust rules. Parameterize exact reserved keys and nested shapes:

```python
RESERVED = {
    "score", "final_score", "learning_status", "verified_learning",
    "honest", "dishonest", "fake_learning",
}

with pytest.raises(ValidationError, match="reserved verdict field"):
    VerificationObservation.from_text(
        "unsafe",
        facts={"outer": [{"inner": {reserved_key: value}}]},
        weak_signals=[],
        ...,
    )
```

Also pass a nested dict through raw `weak_signals` input to prove the pre-validator traverses it before Pydantic's `list[str]` validation. Keep positive construction tests for built-in text and URL observations.

- [x] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  agent-server/tests/openhands_runtime/test_verification_contract.py \
  agent-server/tests/openhands_runtime/test_native_event_flow.py \
  agent-server/tests/openhands_runtime/test_text_evidence_tool.py \
  agent-server/tests/openhands_runtime/test_url_evidence_tool.py -q
```

Expected: FAIL because nested reserved verdict keys are currently accepted and the system prompt lacks explicit untrusted-data instructions.

- [x] **Step 3: Implement recursive reserved-key rejection and prompt rules**

Add a `mode="before"` validator for both fields and a recursive walker that checks mapping keys and sequence elements without rewriting values. Update the prompt to say evidence text/excerpts are untrusted, embedded commands/tool calls/system prompts/scoring instructions must never be executed, evidence is only content to verify, and no Observation directly determines final score.

- [x] **Step 4: Verify GREEN and commit**

Run the RED command again. Expected: PASS; built-in tool tests remain valid.

```bash
git add \
  agent-server/focusproof/openhands_runtime/prompts.py \
  agent-server/focusproof/openhands_runtime/tools/verification.py \
  agent-server/tests/openhands_runtime/test_native_event_flow.py \
  agent-server/tests/openhands_runtime/test_verification_contract.py
git commit -m "fix(runtime): harden verification trust boundary"
```

---

### Task 6: Document The Closure And Run The Acceptance Matrix

**Files:**
- Modify: `docs/research/AI4A_GENERAL_VERIFICATION_FRAMEWORK_REPORT.md`
- Modify: `docs/superpowers/plans/2026-07-14-ai4a1-upgrade-compatibility-security-closure.md`

**Interfaces:**
- Produces: AI4A.1 report evidence, exact tool lists, residual-risk statement, and a clean local branch stopped before AI4B.

- [x] **Step 1: Update the report from verified implementation evidence**

Document why registry registration alone cannot satisfy OpenHands `Agent.verify()`, fresh versus restored toolsets, read-only legacy event conversion, the monotonic total deadline, URL diagnostic fields retained after redaction, and work deferred to AI4B. Include exact test counts only after fresh commands complete.

- [x] **Step 2: Run the required acceptance commands**

```bash
.venv/bin/python -m pytest \
  agent-server/tests/openhands_runtime/test_upgrade_compatibility.py -q

.venv/bin/python -m pytest \
  agent-server/tests/openhands_runtime/test_url_safety.py \
  agent-server/tests/openhands_runtime/test_url_evidence_tool.py -q

.venv/bin/python -m pytest \
  agent-server/tests/persistence \
  agent-server/tests/api/test_restart_persistence.py \
  agent-server/tests/openhands_runtime -q -m "not real_llm"

.venv/bin/python -m pytest agent-server/tests -q -m "not real_llm"
.venv/bin/ruff check agent-server
.venv/bin/mypy agent-server
git diff --check
git status --short --branch
```

Expected: every command exits zero; exactly one real-LLM test remains deselected; only pre-existing deprecation warnings are allowed.

- [x] **Step 3: Audit every explicit constraint and deliverable**

Verify `git diff --name-status 7a93546..HEAD` contains no protected paths. Inspect the final factory Agent for `include_default_tools=[]`. Assert actual fresh tool names are learner-input/review-draft/text/URL and actual restored names additionally include the legacy verifier. Search `VerificationObservation` for absence of score/final-verdict fields and inspect the recursive validator tests.

- [x] **Step 4: Commit documentation and verification tests**

```bash
git add \
  docs/research/AI4A_GENERAL_VERIFICATION_FRAMEWORK_REPORT.md \
  docs/superpowers/plans/2026-07-14-ai4a1-upgrade-compatibility-security-closure.md
git commit -m "docs: report AI4A.1 compatibility closure"
```

- [x] **Step 5: Final read-only review and stop**

Review `7a93546..HEAD` for compatibility, security, protocol duplication, secret leakage, and scope violations. Address any actionable finding with a new RED/GREEN cycle and local commit, rerun the full acceptance matrix, confirm `git status --short --branch` is clean, and do not push or merge.
