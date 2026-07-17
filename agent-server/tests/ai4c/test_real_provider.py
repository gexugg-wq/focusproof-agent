from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from focusproof.config.profiles import RuntimeSettings, load_runtime_settings
from focusproof.openhands_runtime.manager import ConversationManager
from focusproof.openhands_runtime.provider_admission import BoundedProviderAdmission
from focusproof.runtime.event_log import InMemoryEventLog
from focusproof.runtime.evidence import Evidence, LearningGoal

from .test_llm_operations import complete_fake_dashscope_environment


class RealLLMEvidenceRepository:
    def __init__(self, evidence: Evidence) -> None:
        self._evidence = evidence

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        del session_id
        if evidence_id != self._evidence.evidenceId:
            raise KeyError(evidence_id)
        return self._evidence.model_copy(deep=True)


def require_exact_real_llm_selection(
    request: pytest.FixtureRequest,
) -> RuntimeSettings:
    if request.config.option.markexpr != "real_llm":
        raise pytest.UsageError("select exactly with -m real_llm")
    if "ALLOW_SHORT_CONTEXT_WINDOWS" in os.environ:
        raise pytest.UsageError("ALLOW_SHORT_CONTEXT_WINDOWS is forbidden")

    settings = load_runtime_settings(os.environ)
    policy = settings.real_llm
    if settings.profile != "staging" or policy is None:
        raise pytest.UsageError("real provider smoke requires the staging profile")

    expected_limits: dict[str, int | float] = {
        "max_concurrent_reviews": 1,
        "num_retries": 1,
        "max_calls_per_review": 4,
        "context_window_tokens": 16_384,
        "max_output_tokens": 1_024,
        "max_cost_usd": 0.10,
        "request_timeout_seconds": 30,
        "max_review_seconds": 60,
    }
    invalid_limits = [
        name
        for name, expected in expected_limits.items()
        if getattr(policy, name) != expected
    ]
    if invalid_limits:
        raise pytest.UsageError(
            "real provider smoke has unsafe bounds: " + ", ".join(invalid_limits)
        )
    return settings


def test_real_provider_guard_rejects_non_exact_marker_selection(
    request: pytest.FixtureRequest,
) -> None:
    with pytest.raises(pytest.UsageError, match="exactly with -m real_llm"):
        require_exact_real_llm_selection(request)


def test_real_provider_guard_accepts_bounded_placeholder_configuration(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(request.config.option, "markexpr", "real_llm")
    for name, value in complete_fake_dashscope_environment().items():
        monkeypatch.setenv(name, value)

    settings = require_exact_real_llm_selection(request)

    assert settings.profile == "staging"
    assert settings.real_llm is not None
    assert settings.real_llm.context_window_tokens == 16_384


def test_real_provider_guard_rejects_short_context_override(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(request.config.option, "markexpr", "real_llm")
    for name, value in complete_fake_dashscope_environment().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ALLOW_SHORT_CONTEXT_WINDOWS", "true")

    with pytest.raises(pytest.UsageError, match="forbidden"):
        require_exact_real_llm_selection(request)


@pytest.mark.real_llm
def test_dashscope_smoke_uses_native_bounded_conversation(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    settings = require_exact_real_llm_selection(request)
    policy = settings.real_llm
    assert policy is not None
    evidence = Evidence(
        evidenceId="ev_real_provider",
        evidenceType="text",
        contentHash="sha256:real-provider-smoke",
        textContent=(
            "Append-only events preserve facts, while replay derives the current "
            "view without mutating prior records."
        ),
    )
    manager = ConversationManager(
        repository=RealLLMEvidenceRepository(evidence),
        audit_log=InMemoryEventLog(),
        project_root=Path(__file__).resolve().parents[3],
        data_dir=tmp_path / "real-provider-runtime",
        review_timeout_seconds=policy.max_review_seconds,
        provider_admission=BoundedProviderAdmission(
            max_concurrent=policy.max_concurrent_reviews,
            acquire_timeout_seconds=policy.admission_timeout_seconds,
        ),
        runtime_settings=settings,
    )
    session_id = f"sess_real_provider_{uuid4().hex}"
    handle = manager.create(
        session_id,
        LearningGoal(
            domain="general",
            title="Understand event replay",
            goal="Explain why append-only replay is auditable.",
        ),
    )
    manager.send_evidence(session_id, evidence)
    try:
        result = manager.run_review(session_id)
        usage = handle.provider_usage_snapshot()

        assert result.usedOpenHandsConversation is True
        assert result.actionEventsCount >= 1
        assert result.observationEventsCount >= 1
        assert usage.call_count <= 4
        assert usage.output_tokens <= 1_024 * 4
        assert usage.cost_usd <= 0.10
        assert usage.latency_seconds <= 60
    finally:
        manager.close(session_id)
