# AI4A General Verification Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing OpenHands Conversation-backed review runtime with a FocusProof capability registry, deterministic session tool assembly, and safe text and URL evidence verification without creating a parallel runtime or scoring inside tools.

**Architecture:** OpenHands remains responsible for Agent, LocalConversation, ConversationState, native EventLog, ToolDefinition, ToolExecutor, ActionEvent, and ObservationEvent. FocusProof adds a policy catalog over the SDK tool registry, loads authoritative evidence by `session_id + evidence_id`, returns a shared fact-only Observation envelope, and keeps final scoring in the deterministic product domain layer.

**Tech Stack:** Python 3.12, OpenHands SDK local path dependency, Pydantic, FastAPI lifespan, SQLAlchemy repositories, httpx with explicit redirect handling, pytest, Ruff, Mypy.

## Global Constraints

- Work only in `/home/holy/web3/focusproof-agent`.
- Start from `main` at or after AI0 control commit containing this plan.
- Create branch `ai4a-general-verification-framework`; do not work directly on `main`.
- Read `docs/superpowers/specs/2026-07-13-ai4a-general-verification-framework-design.md` completely before editing.
- Continue using OpenHands `Agent`, `LocalConversation`, `ConversationState`, EventLog, `ToolDefinition`, `ToolExecutor`, Action, Observation, ActionEvent, and ObservationEvent directly.
- Do not implement a second Conversation, EventLog, agent loop, or executable tool protocol.
- Verification actions carry `evidence_id`; trusted server params inject `session_id`; the LLM never supplies authoritative evidence bodies.
- Verification observations contain facts and limitations only, never final scores, learning verdicts, or judgments of learner character.
- Keep `include_default_tools=[]`; never enable terminal, file editor, browser automation, patch, or workspace mutation tools.
- Text and URL are the only new real verification capabilities in AI4A.
- Code execution, Web3 RPC, OCR, ASR, PDF, contracts, deployment, frontend changes, and multimodal ingestion are out of scope.
- Do not modify `.env`, `var/`, `frontend/`, `contracts/`, or OpenHands SDK source.
- Default tests must not call a real LLM or consume a real API key.
- Use TDD: demonstrate the focused red state before each implementation change.
- Commit after each independently reviewable task; do not push.

---

## File Structure

- `agent-server/focusproof/openhands_runtime/capabilities.py`: immutable capability metadata and thread-safe FocusProof policy registry.
- `agent-server/focusproof/openhands_runtime/tool_assembler.py`: deterministic OpenHands `Tool` specification assembly for one session.
- `agent-server/focusproof/openhands_runtime/tool_registry.py`: one-time OpenHands SDK class registration and provider lifecycle.
- `agent-server/focusproof/openhands_runtime/tools/verification.py`: shared evidence-reference Action and fact-only Observation envelope.
- `agent-server/focusproof/openhands_runtime/tools/text_evidence.py`: deterministic repository-backed text verifier and ToolDefinition.
- `agent-server/focusproof/openhands_runtime/tools/url_safety.py`: URL normalization, IP/DNS policy, and redirect target validation.
- `agent-server/focusproof/openhands_runtime/tools/url_fetcher.py`: bounded HTTP retrieval with explicit redirect handling.
- `agent-server/focusproof/openhands_runtime/tools/url_evidence.py`: repository-backed URL verifier and ToolDefinition.
- `agent-server/focusproof/openhands_runtime/factory.py`: pass goal/domain and assembled tool specs to the OpenHands Agent.
- `agent-server/focusproof/openhands_runtime/prompts.py`: capability-neutral agent instructions.
- `agent-server/focusproof/openhands_runtime/result_extractor.py`: consume the shared verification Observation subtype.
- `agent-server/focusproof/domain/scoring.py`: remove Web3-specific rules from general scoring.
- `agent-server/tests/openhands_runtime/`: registry, verifier, URL safety, native event flow, assembly, and recovery tests.
- `agent-server/tests/domain/test_scoring.py`: general scoring boundary regressions.
- `docs/research/AI4A_GENERAL_VERIFICATION_FRAMEWORK_REPORT.md`: implementation and verification report.

---

### Task 1: Capability Metadata And Policy Registry

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/capabilities.py`
- Create: `agent-server/tests/openhands_runtime/test_capability_registry.py`

**Interfaces:**
- Produces: `VerificationCapability`, `VerificationCapabilityRegistry`, `build_builtin_capabilities()`.
- `VerificationCapabilityRegistry.select(domain: str, evidence_types: Collection[str] | None) -> tuple[VerificationCapability, ...]` is consumed by Task 5.
- This registry is a FocusProof policy catalog; it never invokes OpenHands `register_tool()` or executes tools.

- [ ] **Step 1: Write failing registry tests**

Create tests that exercise the exact public contract:

```python
from dataclasses import replace

import pytest

from focusproof.openhands_runtime.capabilities import (
    VerificationCapability,
    VerificationCapabilityRegistry,
)


def capability(
    name: str = "text",
    *,
    evidence_types: frozenset[str] = frozenset({"text"}),
    domains: frozenset[str] = frozenset({"*"}),
    priority: int = 100,
    enabled: bool = True,
) -> VerificationCapability:
    return VerificationCapability(
        registry_name=name,
        tool_class_name=f"{name.title()}Tool",
        supported_evidence_types=evidence_types,
        supported_domains=domains,
        priority=priority,
        read_only=True,
        requires_network=False,
        timeout_seconds=5.0,
        enabled=enabled,
        version="1",
    )


def test_registry_rejects_conflicting_duplicate_name() -> None:
    registry = VerificationCapabilityRegistry([capability()])
    with pytest.raises(ValueError, match="text"):
        registry.register(replace(capability(), tool_class_name="OtherTool"))


def test_registry_selection_is_filtered_and_stable() -> None:
    registry = VerificationCapabilityRegistry(
        [
            capability("url", evidence_types=frozenset({"url"}), priority=20),
            capability("text", priority=10),
            capability("disabled", priority=1, enabled=False),
            capability("web3", domains=frozenset({"web3"}), priority=5),
        ]
    )
    selected = registry.select("general", {"text", "url"})
    assert [item.registry_name for item in selected] == ["text", "url"]


def test_idempotent_registration_returns_existing_value() -> None:
    item = capability()
    registry = VerificationCapabilityRegistry([item])
    assert registry.register(item) is item
```

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run:

```bash
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_capability_registry.py -q
```

Expected: collection fails because `focusproof.openhands_runtime.capabilities` does not exist.

- [ ] **Step 3: Implement the immutable model and registry**

Implement these exact signatures:

```python
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True, slots=True)
class VerificationCapability:
    registry_name: str
    tool_class_name: str
    supported_evidence_types: frozenset[str]
    supported_domains: frozenset[str]
    priority: int
    read_only: bool
    requires_network: bool
    timeout_seconds: float
    enabled: bool
    version: str

    def __post_init__(self) -> None:
        if not self.registry_name.strip() or not self.tool_class_name.strip():
            raise ValueError("capability names must not be empty")
        if not self.supported_evidence_types:
            raise ValueError("supported_evidence_types must not be empty")
        if not self.supported_domains:
            raise ValueError("supported_domains must not be empty")
        if not self.read_only:
            raise ValueError("AI4A verification capabilities must be read-only")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


class VerificationCapabilityRegistry:
    def __init__(self, capabilities: Iterable[VerificationCapability] = ()) -> None:
        self._lock = RLock()
        self._items: dict[str, VerificationCapability] = {}
        for item in capabilities:
            self.register(item)

    def register(self, item: VerificationCapability) -> VerificationCapability:
        with self._lock:
            existing = self._items.get(item.registry_name)
            if existing is not None and existing != item:
                raise ValueError(f"conflicting capability: {item.registry_name}")
            if existing is None:
                self._items[item.registry_name] = item
                return item
            return existing

    def select(
        self,
        domain: str,
        evidence_types: Collection[str] | None,
    ) -> tuple[VerificationCapability, ...]:
        normalized_domain = domain.strip().lower()
        normalized_types = (
            {value.strip().lower() for value in evidence_types}
            if evidence_types is not None
            else None
        )
        with self._lock:
            items = tuple(self._items.values())
        selected = (
            item
            for item in items
            if item.enabled
            and ("*" in item.supported_domains or normalized_domain in item.supported_domains)
            and (
                normalized_types is None
                or bool(item.supported_evidence_types & normalized_types)
            )
        )
        return tuple(sorted(selected, key=lambda item: (item.priority, item.registry_name)))

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
```

Add `build_builtin_capabilities()` returning text and URL metadata with stable names, versions, and priorities. Keep construction side-effect free.

- [ ] **Step 4: Run registry tests, Ruff, and Mypy**

Run:

```bash
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_capability_registry.py -q
.venv/bin/ruff check agent-server/focusproof/openhands_runtime/capabilities.py agent-server/tests/openhands_runtime/test_capability_registry.py
.venv/bin/mypy agent-server/focusproof/openhands_runtime/capabilities.py
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit Task 1**

```bash
git add agent-server/focusproof/openhands_runtime/capabilities.py agent-server/tests/openhands_runtime/test_capability_registry.py
git commit -m "feat(runtime): add verification capability registry"
```

---

### Task 2: Shared Evidence Action And Verification Observation

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/tools/verification.py`
- Modify: `agent-server/focusproof/openhands_runtime/tools/__init__.py`
- Create: `agent-server/tests/openhands_runtime/test_verification_contract.py`

**Interfaces:**
- Produces: `EvidenceReferenceAction`, `VerificationObservation`, `VerificationStatus`, `utc_now()`.
- Text and URL ToolDefinitions in Tasks 3 and 4 use these exact types.
- Result extraction in Task 6 recognizes `VerificationObservation`.

- [ ] **Step 1: Write failing native-type and schema-boundary tests**

```python
from datetime import UTC, datetime

from openhands.sdk import Action as OpenHandsAction
from openhands.sdk import Observation as OpenHandsObservation

from focusproof.openhands_runtime.tools.verification import (
    EvidenceReferenceAction,
    VerificationObservation,
)


def test_verification_contract_uses_native_openhands_types() -> None:
    assert issubclass(EvidenceReferenceAction, OpenHandsAction)
    assert issubclass(VerificationObservation, OpenHandsObservation)


def test_observation_has_facts_without_score_or_learning_verdict() -> None:
    fields = set(VerificationObservation.model_fields)
    assert {"evidence_id", "capability", "status", "facts", "source_refs"} <= fields
    assert fields.isdisjoint({"score", "final_score", "learning_status", "verified_learning"})


def test_observation_timestamps_are_timezone_aware() -> None:
    started = datetime.now(UTC)
    completed = datetime.now(UTC)
    observation = VerificationObservation.from_text(
        "text facts",
        evidence_id="ev_1",
        capability="text",
        status="success",
        facts={"word_count": 12},
        weak_signals=[],
        source_refs=["ev_1"],
        verifier_version="1",
        started_at=started,
        completed_at=completed,
    )
    assert observation.started_at.tzinfo is UTC
    assert observation.completed_at.tzinfo is UTC
```

The local fixture creates the Observation with `VerificationObservation.from_text()` and all required fields.

- [ ] **Step 2: Run the focused tests and confirm the missing-module failure**

```bash
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_verification_contract.py -q
```

- [ ] **Step 3: Implement the shared SDK-native contract**

Use this public shape:

```python
from datetime import UTC, datetime
from typing import Any, Literal

from openhands.sdk.tool import Action, Observation

VerificationStatus = Literal["success", "failed", "inconclusive", "unsupported"]


class EvidenceReferenceAction(Action):
    evidence_id: str


class VerificationObservation(Observation):
    evidence_id: str
    capability: str
    status: VerificationStatus
    facts: dict[str, Any]
    weak_signals: list[str]
    source_refs: list[str]
    verifier_version: str
    started_at: datetime
    completed_at: datetime
    error_code: str | None = None
    safe_error_message: str | None = None


def utc_now() -> datetime:
    return datetime.now(UTC)
```

Do not add a session field to the Action. Validate non-empty `evidence_id`, capability, version, and source references with Pydantic field validators if native base validation does not reject blank strings.

- [ ] **Step 4: Run focused tests and static checks**

```bash
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_verification_contract.py -q
.venv/bin/ruff check agent-server/focusproof/openhands_runtime/tools agent-server/tests/openhands_runtime/test_verification_contract.py
.venv/bin/mypy agent-server/focusproof/openhands_runtime/tools/verification.py
```

- [ ] **Step 5: Commit Task 2**

```bash
git add agent-server/focusproof/openhands_runtime/tools agent-server/tests/openhands_runtime/test_verification_contract.py
git commit -m "feat(runtime): define verification observation contract"
```

---

### Task 3: Repository-Backed Text Verification Tool

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/tools/text_evidence.py`
- Create: `agent-server/tests/openhands_runtime/test_text_evidence_tool.py`
- Modify: `agent-server/tests/openhands_runtime/test_tool_execution.py`

**Interfaces:**
- Produces: `TextEvidenceVerificationExecutor` and `FocusProofTextEvidenceVerificationTool`.
- Consumes: `SessionEvidenceRepository`, `EvidenceReferenceAction`, and `VerificationObservation`.
- Tool registry registration in Task 5 uses class name `FocusProofTextEvidenceVerificationTool` and runtime tool name `focusproof_text_evidence_verification`.

- [ ] **Step 1: Write failing authority and fact tests**

Cover these behaviors with this in-memory recording repository and evidence
factory in the same test module:

```python
from focusproof.runtime.evidence import Evidence


class RecordingRepository:
    def __init__(self, stored: Evidence) -> None:
        self.stored = stored
        self.requested: list[tuple[str, str]] = []

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        self.requested.append((session_id, evidence_id))
        if evidence_id != self.stored.evidenceId:
            raise KeyError(evidence_id)
        return self.stored


def evidence(
    evidence_id: str,
    evidence_type: str,
    *,
    text: str | None = None,
    source_url: str | None = None,
) -> Evidence:
    return Evidence(
        evidenceId=evidence_id,
        evidenceType=evidence_type,
        contentHash=f"sha256:{evidence_id}",
        textContent=text,
        sourceUrl=source_url,
    )


def test_text_executor_reads_authoritative_evidence_by_reference() -> None:
    repository = RecordingRepository(
        evidence(
            "ev_text",
            "text",
            text="A concrete example explains event replay.",
        )
    )
    executor = TextEvidenceVerificationExecutor(repository, "sess_1")
    result = executor(EvidenceReferenceAction(evidence_id="ev_text"))
    assert repository.requested == [("sess_1", "ev_text")]
    assert result.status == "success"
    assert result.evidence_id == "ev_text"
    assert result.capability == "text"


def test_generic_short_text_returns_weak_signals_without_verdict() -> None:
    repository = RecordingRepository(
        evidence("ev_weak", "text", text="I learned a lot.")
    )
    result = TextEvidenceVerificationExecutor(repository, "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_weak")
    )
    assert "text_too_short" in result.weak_signals
    assert "generic_learning_claim" in result.weak_signals
    assert "score" not in result.model_dump()


def test_non_text_evidence_is_unsupported() -> None:
    repository = RecordingRepository(
        evidence("ev_url", "url", source_url="https://example.com")
    )
    result = TextEvidenceVerificationExecutor(repository, "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_url")
    )
    assert result.status == "unsupported"
    assert result.error_code == "evidence_type_unsupported"
```

Also test missing evidence, content hash/source refs, structure markers, and read-only annotations.

- [ ] **Step 2: Run tests and confirm red state**

```bash
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_text_evidence_tool.py -q
```

- [ ] **Step 3: Implement deterministic text facts**

The executor must:

```python
class TextEvidenceVerificationExecutor(
    ToolExecutor[EvidenceReferenceAction, VerificationObservation]
):
    def __init__(
        self,
        repository: SessionEvidenceRepository | None,
        session_id: str,
    ) -> None: ...

    def __call__(
        self,
        action: EvidenceReferenceAction,
        conversation: Any | None = None,
    ) -> VerificationObservation: ...
```

Return facts with stable keys:

```python
{
    "has_text": bool,
    "character_count": int,
    "word_count": int,
    "has_concrete_example": bool,
    "has_structured_output": bool,
    "content_hash": str,
}
```

Use structural signals such as headings, numbered steps, code fences, example markers, and cause/effect phrases. Do not embed Web3 vocabulary or call an LLM. Map missing evidence to `failed/evidence_not_found` without exposing the raw exception.

Define `FocusProofTextEvidenceVerificationTool.create()` using the existing read-only annotations and repository-provider fallback pattern. Its description must explicitly state that only `evidence_id` is accepted.

- [ ] **Step 4: Replace old execution tests with the new contract**

Update native-type and read-only assertions to include the text tool. Do not delete learner-input or review-draft coverage.

- [ ] **Step 5: Run text and existing tool tests**

```bash
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_text_evidence_tool.py agent-server/tests/openhands_runtime/test_tool_execution.py -q
.venv/bin/ruff check agent-server/focusproof/openhands_runtime/tools/text_evidence.py agent-server/tests/openhands_runtime
.venv/bin/mypy agent-server/focusproof/openhands_runtime/tools/text_evidence.py
```

- [ ] **Step 6: Commit Task 3**

```bash
git add agent-server/focusproof/openhands_runtime/tools/text_evidence.py agent-server/tests/openhands_runtime/test_text_evidence_tool.py agent-server/tests/openhands_runtime/test_tool_execution.py
git commit -m "feat(runtime): add text evidence verification tool"
```

---

### Task 4: URL Safety Policy, Bounded Fetcher, And URL Tool

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/tools/url_safety.py`
- Create: `agent-server/focusproof/openhands_runtime/tools/url_fetcher.py`
- Create: `agent-server/focusproof/openhands_runtime/tools/url_evidence.py`
- Create: `agent-server/tests/openhands_runtime/test_url_safety.py`
- Create: `agent-server/tests/openhands_runtime/test_url_evidence_tool.py`
- Modify: `pyproject.toml` only if `httpx` is not already a direct project dependency.

**Interfaces:**
- Produces: `UrlSafetyPolicy`, `SafeUrl`, `UrlPolicyError`, `BoundedUrlFetcher`, `FetchedUrl`, `UrlFetchError`, `FocusProofUrlEvidenceVerificationTool`.
- URL network behavior is injected so default tests never use the public network.

- [ ] **Step 1: Write failing pure URL policy tests**

Parameterize blocked targets:

```python
@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://localhost/admin",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[fe80::1]/",
        "https://user:secret@example.com/",
    ],
)
def test_policy_blocks_unsafe_targets(value: str) -> None:
    with pytest.raises(UrlPolicyError):
        UrlSafetyPolicy(allow_http=False).validate(value)
```

Add tests for normalized HTTPS URLs, a hostname resolver returning private IPv4/IPv6 addresses, and revalidation of each redirect target.

- [ ] **Step 2: Run policy tests and confirm red state**

```bash
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_url_safety.py -q
```

- [ ] **Step 3: Implement URL policy with injectable DNS resolution**

Use these signatures:

```python
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address

Address = IPv4Address | IPv6Address
Resolver = Callable[[str], tuple[Address, ...]]


@dataclass(frozen=True, slots=True)
class SafeUrl:
    normalized: str
    hostname: str
    addresses: tuple[str, ...]


class UrlPolicyError(ValueError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class UrlSafetyPolicy:
    def __init__(self, *, allow_http: bool, resolver: Resolver = resolve_host) -> None: ...
    def validate(self, value: str) -> SafeUrl: ...
```

Reject addresses when any resolved IP is private, loopback, link-local, multicast, unspecified, reserved, or the metadata address. Strip fragments, lowercase scheme/host, preserve path/query, reject credentials, and require HTTPS unless explicitly configured otherwise.

- [ ] **Step 4: Write failing bounded-fetch tests with fake transport**

Use `httpx.MockTransport` or an injected requester. Cover:

- redirect target policy revalidation;
- maximum three redirects;
- connect/read timeout mapping;
- `Content-Length` over 1 MiB rejection;
- streamed body crossing 1 MiB rejection;
- unsupported binary content type;
- title extraction from bounded HTML;
- no automatic redirect following.

- [ ] **Step 5: Implement explicit redirect and size handling**

Public result types:

```python
@dataclass(frozen=True, slots=True)
class FetchedUrl:
    final_url: str
    status_code: int
    content_type: str
    content_length: int
    redirect_chain: tuple[str, ...]
    title: str | None
    text_excerpt: str | None


class BoundedUrlFetcher:
    def __init__(
        self,
        *,
        policy: UrlSafetyPolicy,
        client: httpx.Client,
        max_redirects: int = 3,
        max_bytes: int = 1_048_576,
    ) -> None: ...

    def fetch(self, source_url: str) -> FetchedUrl: ...
```

The client must use `follow_redirects=False`. Validate before the first request and before every redirect request. Read response bytes incrementally and stop at the limit. Extract only bounded plain-text metadata; do not persist full page bodies.

- [ ] **Step 6: Write and implement repository-backed URL tool tests**

The URL executor reads `sourceUrl` from repository evidence, supports only `evidenceType == "url"`, maps policy errors to `failed`, transient network errors to `inconclusive`, unsupported content to `unsupported`, and successful fetch facts to `success`.

Stable successful facts:

```python
{
    "normalized_url": str,
    "hostname": str,
    "status_code": int,
    "content_type": str,
    "content_length": int,
    "redirect_chain": list[str],
    "title": str | None,
    "text_excerpt": str | None,
}
```

Define `FocusProofUrlEvidenceVerificationTool` with runtime name
`focusproof_url_evidence_verification` and read-only annotations. Its `create()`
method accepts `session_id` plus optional repository/fetcher overrides for direct
unit tests. Production construction resolves the repository and bounded fetcher
from the application-owned provider in `tool_registry.py`; do not place an
`httpx.Client`, resolver, repository object, or secret inside serialized
OpenHands `Tool.params`.

- [ ] **Step 7: Run URL tests and static checks**

```bash
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_url_safety.py agent-server/tests/openhands_runtime/test_url_evidence_tool.py -q
.venv/bin/ruff check agent-server/focusproof/openhands_runtime/tools agent-server/tests/openhands_runtime/test_url_safety.py agent-server/tests/openhands_runtime/test_url_evidence_tool.py
.venv/bin/mypy agent-server/focusproof/openhands_runtime/tools/url_safety.py agent-server/focusproof/openhands_runtime/tools/url_fetcher.py agent-server/focusproof/openhands_runtime/tools/url_evidence.py
```

- [ ] **Step 8: Commit Task 4**

```bash
git add pyproject.toml agent-server/focusproof/openhands_runtime/tools agent-server/tests/openhands_runtime/test_url_safety.py agent-server/tests/openhands_runtime/test_url_evidence_tool.py
git commit -m "feat(runtime): add safe URL evidence verification"
```

If `pyproject.toml` did not change, omit it from `git add`.

---

### Task 5: OpenHands Registration And Per-Session Tool Assembly

**Files:**
- Create: `agent-server/focusproof/openhands_runtime/tool_assembler.py`
- Modify: `agent-server/focusproof/openhands_runtime/tool_registry.py`
- Modify: `agent-server/focusproof/openhands_runtime/factory.py`
- Modify: `agent-server/focusproof/openhands_runtime/manager.py` only where evidence-type input is available before Conversation creation or restoration.
- Create: `agent-server/tests/openhands_runtime/test_tool_assembler.py`
- Modify: `agent-server/tests/openhands_runtime/test_factory.py`
- Modify: `agent-server/tests/openhands_runtime/test_tool_registry_lifecycle.py`

**Interfaces:**
- Produces: `SessionToolAssembler.assemble(session_id, domain, evidence_types) -> list[Tool]` and `toolset_version(...) -> str`.
- `ConversationFactory` receives the capability registry and assembler through constructor injection.
- Existing control tools remain universally available.

- [ ] **Step 1: Write failing assembly tests**

Assert exact tool-map behavior:

```python
from focusproof.openhands_runtime.capabilities import (
    VerificationCapabilityRegistry,
    build_builtin_capabilities,
)
from focusproof.openhands_runtime.tool_assembler import SessionToolAssembler


def assembler() -> SessionToolAssembler:
    return SessionToolAssembler(
        VerificationCapabilityRegistry(build_builtin_capabilities())
    )


def test_general_session_gets_control_and_general_verification_tools() -> None:
    tools = assembler().assemble("sess_1", "general", None)
    assert [tool.name for tool in tools] == [
        "FocusProofLearnerInputTool",
        "FocusProofReviewDraftTool",
        "FocusProofTextEvidenceVerificationTool",
        "FocusProofUrlEvidenceVerificationTool",
    ]


def test_known_text_evidence_narrows_general_verifiers() -> None:
    tools = assembler().assemble("sess_1", "general", {"text"})
    assert "FocusProofTextEvidenceVerificationTool" in {tool.name for tool in tools}
    assert "FocusProofUrlEvidenceVerificationTool" not in {tool.name for tool in tools}


def test_forbidden_default_tools_are_never_assembled() -> None:
    names = {tool.name.lower() for tool in assembler().assemble("sess_1", "general", None)}
    assert names.isdisjoint({"terminaltool", "fileeditortool", "browsertool", "applypatchtool"})
```

- [ ] **Step 2: Run assembly tests and confirm red state**

```bash
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_tool_assembler.py -q
```

- [ ] **Step 3: Implement assembler and deterministic toolset version**

Use:

```python
class SessionToolAssembler:
    def __init__(self, registry: VerificationCapabilityRegistry) -> None:
        self._registry = registry

    def assemble(
        self,
        session_id: str,
        domain: str,
        evidence_types: Collection[str] | None,
    ) -> list[Tool]: ...

    def version(
        self,
        domain: str,
        evidence_types: Collection[str] | None,
    ) -> str: ...
```

Construct control tool specs first, then selected capability specs. Every
serialized OpenHands tool spec receives only `session_id` and primitive safe
configuration values. Runtime repository and HTTP client objects come from the
application-owned provider configured when `ConversationFactory` is created.
Build the
version from sorted capability name/version pairs using SHA-256 and expose a
short stable digest for diagnostics.

- [ ] **Step 4: Register new ToolDefinition classes through the SDK registry**

Update `_TOOL_CLASSES` to include the text and URL classes. Remove the old monolithic verifier from the new session assembly path. Keep any compatibility import only if persisted historical events require the Python class to deserialize; document that decision in the report.

Do not manipulate OpenHands private registry state. Keep one-time registration
and provider lifecycle thread-safe. Extend the existing
`release_repository_provider()` cleanup path to release all AI4A tool runtime
dependencies, so the existing FastAPI lifespan call remains sufficient and
`agent-server/focusproof/api/app.py` does not need to change.

- [ ] **Step 5: Inject assembly into ConversationFactory**

Replace the fixed `_session_tools()` list. Stop deleting `goal`; pass `goal.domain` to the assembler. Add optional `evidence_types: Collection[str] | None = None` to `create()` so restoration can narrow capabilities when the manager already knows stored evidence types. New sessions with no evidence expose both allowlisted general verifiers.

Keep:

```python
Agent(
    llm=llm,
    tools=assembled_tools,
    include_default_tools=[],
    system_prompt=FOCUSPROOF_SYSTEM_PROMPT,
)
```

- [ ] **Step 6: Update factory, lifecycle, and restoration tests**

Verify `agent.tools_map` contains only the expected runtime names, tool order is stable, repeated Conversation creation does not duplicate SDK registrations, and restored sessions preserve Conversation ID and native history. Add toolset version diagnostics to `ConversationHandle` only if required; do not make it a new truth source.

- [ ] **Step 7: Run focused runtime tests**

```bash
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_tool_assembler.py agent-server/tests/openhands_runtime/test_factory.py agent-server/tests/openhands_runtime/test_tool_registry_lifecycle.py agent-server/tests/persistence/test_restart_recovery.py -q
```

- [ ] **Step 8: Commit Task 5**

```bash
git add agent-server/focusproof/openhands_runtime agent-server/tests/openhands_runtime agent-server/tests/persistence
git commit -m "feat(runtime): assemble session verification tools"
```

Before committing, inspect staged paths and unstage unrelated files.

---

### Task 6: Prompt, Native Event Extraction, And Projection Compatibility

**Files:**
- Modify: `agent-server/focusproof/openhands_runtime/prompts.py`
- Modify: `agent-server/focusproof/openhands_runtime/result_extractor.py`
- Modify: `agent-server/focusproof/openhands_runtime/projector.py` only if shared Observation fields need projection.
- Modify: `agent-server/tests/openhands_runtime/test_native_event_flow.py`
- Modify: `agent-server/tests/openhands_runtime/test_event_projection.py`
- Modify: `agent-server/tests/openhands_runtime/test_conversation_lifecycle.py`

**Interfaces:**
- Consumes: shared `VerificationObservation` from Task 2.
- Preserves: `RuntimeReviewResult`, existing API response fields, and native OpenHands event identity.

- [ ] **Step 1: Write failing prompt and native-event tests**

Assert the prompt does not contain `only the three` and does contain these semantic rules:

```python
assert "tools exposed" in FOCUSPROOF_SYSTEM_PROMPT
assert "inconclusive" in FOCUSPROOF_SYSTEM_PROMPT
assert "does not establish learner understanding" in FOCUSPROOF_SYSTEM_PROMPT
assert "numeric final score" in FOCUSPROOF_SYSTEM_PROMPT
```

Run a scripted OpenHands Conversation that calls a text verification tool and then review draft. Assert:

- the ActionEvent appears before its matching ObservationEvent;
- the Observation is a `VerificationObservation`;
- the result extractor sees the observation after the latest answer boundary;
- projected events retain `sourceOpenHandsEventId` and do not duplicate on reconcile.

- [ ] **Step 2: Run the focused tests and confirm failures reference old assumptions**

```bash
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_native_event_flow.py agent-server/tests/openhands_runtime/test_event_projection.py agent-server/tests/openhands_runtime/test_conversation_lifecycle.py -q
```

- [ ] **Step 3: Update the capability-neutral prompt**

The prompt must instruct the agent to:

- use only FocusProof tools exposed in the current Conversation;
- call a matching verifier for each evidence ID;
- never send evidence text as authoritative tool input;
- treat failed, unsupported, and inconclusive observations as limitations, not proof of falsity;
- ask one focused question when understanding is not established;
- submit a score-free review draft only when facts are sufficient.

- [ ] **Step 4: Generalize observation extraction**

Replace checks against the legacy `EvidenceVerificationObservation` with checks against `VerificationObservation`. Map stable fields into the existing FocusProof runtime Observation used by deterministic scoring and audit projection. Preserve source references and tool name. Do not parse arbitrary LLM text as a tool fact.

- [ ] **Step 5: Run runtime flow and projection tests**

```bash
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_native_event_flow.py agent-server/tests/openhands_runtime/test_event_projection.py agent-server/tests/openhands_runtime/test_conversation_lifecycle.py -q
.venv/bin/ruff check agent-server/focusproof/openhands_runtime agent-server/tests/openhands_runtime
.venv/bin/mypy agent-server/focusproof/openhands_runtime
```

- [ ] **Step 6: Commit Task 6**

```bash
git add agent-server/focusproof/openhands_runtime agent-server/tests/openhands_runtime
git commit -m "refactor(runtime): consume capability observations"
```

---

### Task 7: Remove Web3 Assumptions From General Scoring

**Files:**
- Modify: `agent-server/focusproof/domain/scoring.py`
- Modify: `agent-server/tests/domain/test_scoring.py`
- Create or modify: `agent-server/focusproof/domain/plugins/web3/` only to preserve clearly isolated Web3 helpers that remain necessary; do not add RPC behavior.

**Interfaces:**
- Preserves: `score_learning_session(goal, evidence, answers, observations) -> ReviewResult` and all public review fields.
- Produces: domain-general concept/specificity rules without nonce, gas, transaction, wallet, chain, or block-explorer vocabulary.

- [ ] **Step 1: Write failing generality regressions**

Add these complete tests alongside the existing weak-evidence regressions:

```python
from focusproof.domain.scoring import score_learning_session
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.observations import Observation


def general_goal(title: str, goal: str) -> LearningGoal:
    return LearningGoal(domain="general", title=title, goal=goal)


def text_evidence(evidence_id: str, text: str) -> Evidence:
    return Evidence(
        evidenceId=evidence_id,
        evidenceType="text",
        contentHash=f"sha256:{evidence_id}",
        textContent=text,
    )


def test_specific_non_web3_explanation_can_show_learning() -> None:
    goal = general_goal(
        "Understand photosynthesis",
        "Explain photosynthesis using a concrete example",
    )
    evidence = [
        text_evidence(
            "ev_photo",
            "Chlorophyll absorbs light; I compared a shaded leaf with a lit "
            "leaf and recorded the color change as a concrete example.",
        )
    ]
    result = score_learning_session(goal, evidence, ["The control isolates light as the changed variable."])
    assert result.score >= 60
    assert result.status == "LikelyLearning"


def test_web3_keywords_alone_do_not_raise_general_understanding() -> None:
    goal = general_goal("Understand transactions", "Explain transaction ordering")
    evidence = [text_evidence("ev_keywords", "nonce gas transaction block confirmation")]
    result = score_learning_session(goal, evidence, [])
    assert result.score < 60


def test_observation_success_does_not_assign_final_learning() -> None:
    goal = general_goal("Understand controls", "Explain why an experiment uses a control")
    evidence = [text_evidence("ev_control", "I compared two groups and changed one variable.")]
    observation = Observation(
        toolName="focusproof_text_evidence_verification",
        status="success",
        facts={"has_text": True, "word_count": 9},
        sourceRefs=["ev_control"],
    )
    result = score_learning_session(goal, evidence, [], [observation])
    assert result.status != "VerifiedLearning"
```

Keep existing weak-evidence and follow-up-answer regression tests.

- [ ] **Step 2: Run scoring tests and confirm at least the general-domain case fails**

```bash
.venv/bin/python -m pytest agent-server/tests/domain/test_scoring.py -q
```

- [ ] **Step 3: Implement targeted domain-general scoring cleanup**

Remove `_CONCEPT_TERMS` entries tied to Web3 and `_TX_RE`/`has_tx` branches from the generic path. Replace keyword counting with domain-neutral specificity signals already available from evidence, answers, and verification observations. Preserve dimension keys and score bounds. Do not redesign all scoring weights in this task.

- [ ] **Step 4: Run domain and API review tests**

```bash
.venv/bin/python -m pytest agent-server/tests/domain/test_scoring.py agent-server/tests/api/test_review_conversation_runtime.py agent-server/tests/api/test_api_sessions.py -q
.venv/bin/ruff check agent-server/focusproof/domain agent-server/tests/domain
.venv/bin/mypy agent-server/focusproof/domain
```

- [ ] **Step 5: Commit Task 7**

```bash
git add agent-server/focusproof/domain agent-server/tests/domain agent-server/tests/api
git commit -m "fix(scoring): keep general review domain neutral"
```

---

### Task 8: Full Regression, Recovery, Security Matrix, And Report

**Files:**
- Modify: tests only where full-suite failures reveal an AI4A contract update.
- Create: `docs/research/AI4A_GENERAL_VERIFICATION_FRAMEWORK_REPORT.md`

**Interfaces:**
- Validates the complete official API -> OpenHands Conversation -> native tool events -> FocusProof scoring flow.
- Produces the AI0 acceptance report; no new production interfaces are introduced in this task.

- [ ] **Step 1: Run the complete non-real-LLM backend suite**

```bash
.venv/bin/python -m pytest agent-server/tests -q -m "not real_llm"
```

Expected: all selected tests pass; the real-LLM test is deselected.

- [ ] **Step 2: Run complete static verification**

```bash
.venv/bin/ruff check agent-server
.venv/bin/mypy agent-server
```

Expected: both commands exit 0.

- [ ] **Step 3: Run migration and restart-focused suites**

```bash
.venv/bin/python -m pytest agent-server/tests/persistence agent-server/tests/api/test_restart_persistence.py agent-server/tests/openhands_runtime -q -m "not real_llm"
```

Verify that Conversation ID, native events, Review history, and audit projections remain stable after a fresh manager/engine restore.

- [ ] **Step 4: Run an explicit real-LLM smoke only when requested and configured**

Do not run this by default. If AI0 explicitly authorizes it and `.env` already contains a usable configuration, run only the existing marked test:

```bash
.venv/bin/python -m pytest agent-server/tests/openhands_runtime/test_real_llm.py -q -m real_llm
```

Never print, copy, edit, or commit the key. Record only pass/fail and model/provider identifiers that are already non-secret.

- [ ] **Step 5: Write the AI4A report**

The report must include:

- baseline and branch;
- changed files grouped by responsibility;
- exact OpenHands SDK types used directly;
- capability metadata and selection rules;
- SDK registration versus FocusProof policy registry boundary;
- session tool assembly and toolset version behavior;
- text facts and weak signals;
- URL schemes, IP classes, redirect, timeout, and size controls;
- shared Observation schema and prohibited verdict fields;
- EventLog, projection, and recovery evidence;
- scoring cleanup and API compatibility;
- exact commands and result counts;
- dependency changes;
- known limitations;
- protected directories untouched;
- confirmation that public protocol docs were not changed;
- deferred code, Web3 RPC, multimodal, contract, and deployment work.

- [ ] **Step 6: Inspect the complete diff and constraints**

```bash
git diff --check main...HEAD
git diff --name-status main...HEAD
git status --short --branch
git log --oneline --decorate main..HEAD
```

Reject the result if `frontend/`, `contracts/`, `.env`, `var/`, or OpenHands SDK source appears in the diff.

- [ ] **Step 7: Commit the report and final test-only corrections**

```bash
git add docs/research/AI4A_GENERAL_VERIFICATION_FRAMEWORK_REPORT.md agent-server/tests agent-server/focusproof
git commit -m "test(runtime): verify general evidence framework"
```

If production files are unchanged since Task 7, stage only the report and test files.

- [ ] **Step 8: Stop for AI0 acceptance**

Do not push, merge, begin AI4B, add code execution, add Web3 RPC, or modify frontend/contract files. Return the branch name, commit list, verification output, changed paths, and report path to AI0.

---

## AI0 Acceptance Checklist

- [ ] Official `/sessions/{id}/review` still uses OpenHands LocalConversation.
- [ ] Agent receives only explicitly assembled FocusProof tools.
- [ ] OpenHands default programming/workspace tools remain absent.
- [ ] Text and URL actions contain evidence references rather than authoritative bodies.
- [ ] Native ActionEvent precedes ObservationEvent.
- [ ] Native EventLog remains runtime truth and audit projection stays idempotent.
- [ ] URL verifier blocks loopback, private, link-local, metadata, unsafe redirects, oversized responses, and timeouts safely.
- [ ] Observations contain facts but no final score or learning verdict.
- [ ] General scoring no longer rewards Web3 vocabulary.
- [ ] Existing API and persistence recovery tests pass.
- [ ] Real LLM is excluded from default tests.
- [ ] No protected directory or secret file changed.
- [ ] AI4A report is complete and implementation stops before AI4B.
