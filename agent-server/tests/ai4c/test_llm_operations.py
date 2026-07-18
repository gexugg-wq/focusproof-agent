from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from openhands.sdk import LLM
import openhands.sdk.conversation as conversation_module
from openhands.sdk.conversation import LocalConversation as PublicLocalConversation
from pydantic import ValidationError

from focusproof.config.profiles import RealLlmPolicy, load_runtime_settings
from focusproof.openhands_adapter.llm_config import build_openhands_llm
from focusproof.openhands_runtime.factory import ConversationFactory
from focusproof.openhands_runtime.handle import (
    ConversationHandle,
    ProviderUsageSnapshot,
)
from focusproof.openhands_runtime.provider_admission import (
    BoundedProviderAdmission,
    ProviderAdmissionUnavailableError,
)
from focusproof.runtime.evidence import Evidence, LearningGoal


class EmptyRepository:
    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        raise KeyError((session_id, evidence_id))


def complete_fake_dashscope_environment() -> dict[str, str]:
    return {
        "FOCUSPROOF_PROFILE": "staging",
        "FOCUSPROOF_LLM_PROVIDER": "openai-compatible",
        "FOCUSPROOF_LLM_MODEL": "qwen-plus",
        "FOCUSPROOF_LLM_BASE_URL": (
            "https://dashscope.example.test/compatible-mode/v1"
        ),
        "FOCUSPROOF_LLM_API_KEY": "placeholder",
        "FOCUSPROOF_LLM_REQUEST_TIMEOUT_SECONDS": "30",
        "FOCUSPROOF_LLM_NUM_RETRIES": "1",
        "FOCUSPROOF_LLM_RETRY_MIN_WAIT_SECONDS": "1",
        "FOCUSPROOF_LLM_RETRY_MAX_WAIT_SECONDS": "4",
        "FOCUSPROOF_LLM_CONTEXT_WINDOW_TOKENS": "16384",
        "FOCUSPROOF_LLM_MAX_OUTPUT_TOKENS": "1024",
        "FOCUSPROOF_LLM_MAX_ITERATIONS": "6",
        "FOCUSPROOF_LLM_MAX_REVIEW_SECONDS": "60",
        "FOCUSPROOF_LLM_MAX_CONCURRENT_REVIEWS": "1",
        "FOCUSPROOF_LLM_ADMISSION_TIMEOUT_SECONDS": "1",
        "FOCUSPROOF_LLM_MAX_CALLS_PER_REVIEW": "4",
        "FOCUSPROOF_LLM_MAX_COST_USD": "0.10",
        "FOCUSPROOF_LLM_INPUT_COST_PER_TOKEN": "0.000001",
        "FOCUSPROOF_LLM_OUTPUT_COST_PER_TOKEN": "0.000002",
        "LITELLM_LOCAL_MODEL_COST_MAP": "true",
    }


def fake_real_llm_policy(**overrides: object) -> RealLlmPolicy:
    values: dict[str, object] = dict(complete_fake_dashscope_environment())
    values.update(overrides)
    settings = load_runtime_settings(
        {key: str(value) for key, value in values.items()}
    )
    assert settings.real_llm is not None
    return settings.real_llm


def create_real_mode_handle_with_fake_sdk_llm(
    tmp_path: Path,
    *,
    max_cost_usd: float,
) -> ConversationHandle:
    values = complete_fake_dashscope_environment()
    values["FOCUSPROOF_LLM_MAX_COST_USD"] = str(max_cost_usd)
    settings = load_runtime_settings(values)
    return ConversationFactory(
        repository=EmptyRepository(),
        compatibility_mode=True,
        project_root=tmp_path,
        runtime_settings=settings,
    ).create(
        "sess_usage",
        LearningGoal(
            domain="general",
            title="Learn bounded providers",
            goal="Explain why provider calls need explicit bounds.",
        ),
    )


def handle_with_recorded_sdk_metrics(tmp_path: Path) -> ConversationHandle:
    handle = create_real_mode_handle_with_fake_sdk_llm(
        tmp_path,
        max_cost_usd=0.10,
    )
    llm = handle.conversation.agent.llm
    metrics = llm.metrics
    metrics.add_token_usage(50, 10, 0, 0, 16_384, "response-1")
    metrics.add_token_usage(70, 20, 0, 0, 16_384, "response-2")
    metrics.add_cost(0.004)
    metrics.add_response_latency(0.25, "response-1")
    metrics.add_response_latency(0.75, "response-2")
    handle.conversation.state.stats.usage_to_metrics[llm.usage_id] = metrics
    return handle


def usage_snapshot_from(handle: ConversationHandle) -> ProviderUsageSnapshot:
    return handle.provider_usage_snapshot()


def _load_probe_module(module_name: str, source_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deterministic_profile_ignores_provider_values() -> None:
    settings = load_runtime_settings(
        {
            "FOCUSPROOF_PROFILE": "deterministic-test",
            "DASHSCOPE_API_KEY": "placeholder",
        }
    )

    assert settings.profile == "deterministic-test"
    assert settings.real_llm is None


def test_staging_profile_requires_every_real_llm_bound() -> None:
    with pytest.raises(ValidationError, match="FOCUSPROOF_LLM_MODEL"):
        load_runtime_settings({"FOCUSPROOF_PROFILE": "staging"})


def test_staging_profile_builds_provider_neutral_policy() -> None:
    settings = load_runtime_settings(complete_fake_dashscope_environment())

    assert settings.real_llm is not None
    assert settings.real_llm.provider == "openai-compatible"
    assert settings.real_llm.api_key.get_secret_value() == "placeholder"
    assert "placeholder" not in settings.model_dump_json()


def test_short_context_window_override_is_rejected() -> None:
    values = complete_fake_dashscope_environment()
    values["ALLOW_SHORT_CONTEXT_WINDOWS"] = "true"

    with pytest.raises(ValidationError, match="ALLOW_SHORT_CONTEXT_WINDOWS"):
        load_runtime_settings(values)


def test_build_openhands_llm_uses_sdk_and_every_bound() -> None:
    policy = fake_real_llm_policy()

    llm = build_openhands_llm(policy, usage_id="focusproof-test")

    assert isinstance(llm, LLM)
    assert llm.model == policy.model
    assert llm.base_url == policy.base_url
    assert llm.num_retries == policy.num_retries
    assert llm.retry_min_wait == policy.retry_min_wait_seconds
    assert llm.retry_max_wait == policy.retry_max_wait_seconds
    assert llm.timeout == policy.request_timeout_seconds
    assert llm.max_input_tokens == policy.context_window_tokens == 16384
    assert llm.max_output_tokens == policy.max_output_tokens
    assert llm.log_completions is False
    assert llm.stream is False
    assert llm.input_cost_per_token == policy.input_cost_per_token
    assert llm.output_cost_per_token == policy.output_cost_per_token


def test_llm_config_and_repr_do_not_expose_api_key() -> None:
    llm = build_openhands_llm(fake_real_llm_policy(), usage_id="safe")

    rendered = repr(llm) + llm.model_dump_json()

    assert "placeholder" not in rendered


def test_factory_uses_validated_runtime_settings_without_dotenv(
    tmp_path: Path,
) -> None:
    settings = load_runtime_settings(complete_fake_dashscope_environment())
    factory = ConversationFactory(
        repository=EmptyRepository(),
        compatibility_mode=True,
        project_root=tmp_path,
        runtime_settings=settings,
    )

    handle = factory.create(
        "sess_real_config",
        LearningGoal(
            domain="general",
            title="Learn bounded providers",
            goal="Explain why provider calls need explicit bounds.",
        ),
    )
    try:
        assert handle.runtime_mode == "openhands-local-real"
        assert handle.conversation.agent.llm.max_input_tokens == 16_384
        assert handle.conversation.agent.llm.max_output_tokens == 1_024
    finally:
        handle.conversation.close()


def test_runtime_modules_bind_public_local_conversation_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from focusproof.openhands_runtime import factory as factory_module
    from focusproof.openhands_runtime import handle as handle_module

    class SentinelLocalConversation(PublicLocalConversation):
        pass

    assert SentinelLocalConversation is not PublicLocalConversation

    with monkeypatch.context() as context:
        context.setattr(
            conversation_module,
            "LocalConversation",
            SentinelLocalConversation,
        )
        probe_handle = _load_probe_module(
            "focusproof_openhands_runtime_handle_probe",
            Path(handle_module.__file__).resolve(),
        )
        probe_factory = _load_probe_module(
            "focusproof_openhands_runtime_factory_probe",
            Path(factory_module.__file__).resolve(),
        )

        assert probe_handle.LocalConversation is SentinelLocalConversation
        assert probe_factory.LocalConversation is SentinelLocalConversation


def test_factory_sets_public_local_conversation_budget(tmp_path: Path) -> None:
    handle = create_real_mode_handle_with_fake_sdk_llm(
        tmp_path,
        max_cost_usd=0.10,
    )
    try:
        assert handle.conversation.max_budget_per_run == 0.10
    finally:
        handle.conversation.close()


def test_factory_caps_native_iterations_to_provider_call_limit(tmp_path: Path) -> None:
    handle = create_real_mode_handle_with_fake_sdk_llm(
        tmp_path,
        max_cost_usd=0.10,
    )
    try:
        assert handle.conversation.max_iteration_per_run == 4
    finally:
        handle.conversation.close()


def test_usage_snapshot_contains_aggregates_only(tmp_path: Path) -> None:
    handle = handle_with_recorded_sdk_metrics(tmp_path)
    try:
        snapshot = usage_snapshot_from(handle)

        assert snapshot.call_count == 2
        assert snapshot.input_tokens == 120
        assert snapshot.output_tokens == 30
        assert snapshot.cost_usd == pytest.approx(0.004)
        assert snapshot.latency_seconds == pytest.approx(1.0)
        assert set(snapshot.model_dump()) == {
            "call_count",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "latency_seconds",
        }
    finally:
        handle.conversation.close()


def test_global_provider_admission_rejects_second_paid_run_before_llm() -> None:
    admission = BoundedProviderAdmission(
        max_concurrent=1,
        acquire_timeout_seconds=0.01,
    )

    with admission.acquire():
        with pytest.raises(ProviderAdmissionUnavailableError):
            with admission.acquire():
                raise AssertionError("second run entered")
