from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import UUID

import pytest

from focusproof.config.env import build_speech_capability, load_speech_settings
from focusproof.speech_core.models import (
    AudioFacts,
    AudioFormat,
    LanguageHint,
    TranscriptionRequest,
    TranscriptionResult,
)


def real_asr_env() -> dict[str, str]:
    return {
        "FOCUSPROOF_ASR_PROVIDER": "dashscope",
        "FOCUSPROOF_ASR_MODEL": "qwen3-asr-flash",
        "FOCUSPROOF_ASR_BASE_URL": (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        "DASHSCOPE_API_KEY": "placeholder",
        "FOCUSPROOF_ASR_E2E_TIMEOUT_SECONDS": "120",
        "FOCUSPROOF_ASR_MAX_CONCURRENCY": "4",
        "FOCUSPROOF_SPEECH_IDEMPOTENCY_HMAC_KEY": "test-hmac-secret",
    }


def test_missing_real_asr_configuration_disables_only_speech() -> None:
    assert build_speech_capability({}) == {
        "capabilityId": "speech_transcription",
        "schemaVersion": 1,
        "enabled": False,
        "reasonCode": "asr_not_configured",
    }


def test_review_llm_settings_cannot_silently_enable_speech() -> None:
    capability = build_speech_capability(
        {
            "DASHSCOPE_API_KEY": "placeholder",
            "DASHSCOPE_BASE_URL": "https://example.invalid/v1",
            "DASHSCOPE_MODEL": "openai/qwen-plus",
        }
    )

    assert capability["enabled"] is False
    assert capability["reasonCode"] == "asr_not_configured"


def test_enabled_capability_is_versioned_and_metadata_hint_is_explicit() -> None:
    assert build_speech_capability(real_asr_env()) == {
        "capabilityId": "speech_transcription",
        "schemaVersion": 1,
        "enabled": True,
        "formats": ["audio/webm;codecs=opus", "audio/wav", "audio/mpeg"],
        "maxAudioBytes": 10_485_760,
        "maxDurationSeconds": 120,
        "languageHintsAccepted": ["auto", "zh", "en"],
        "languageHintEffect": "metadata_only",
    }


def test_invalid_real_asr_configuration_fails_closed_without_secret_leakage() -> None:
    environment = real_asr_env() | {"FOCUSPROOF_ASR_MODEL": "qwen3.5-omni-plus"}

    capability = build_speech_capability(environment)

    assert capability == {
        "capabilityId": "speech_transcription",
        "schemaVersion": 1,
        "enabled": False,
        "reasonCode": "asr_configuration_invalid",
    }
    assert "placeholder" not in repr(capability)


def test_speech_settings_are_frozen_and_keep_secrets_out_of_repr() -> None:
    settings = load_speech_settings(real_asr_env())

    assert settings is not None
    assert settings.provider == "dashscope"
    assert settings.model == "qwen3-asr-flash"
    assert settings.e2e_timeout_seconds == 120
    assert settings.business_timeout_seconds == 115
    assert settings.max_concurrency == 4
    assert "placeholder" not in repr(settings)
    assert "test-hmac-secret" not in repr(settings)
    with pytest.raises(FrozenInstanceError):
        settings.model = "different"  # type: ignore[misc]


def test_transcription_value_objects_preserve_candidate_text_exactly() -> None:
    facts = AudioFacts(
        audio_format=AudioFormat.WEBM_OPUS,
        media_type="audio/webm;codecs=opus",
        codec="Opus",
        byte_size=4_096,
        duration_ms=1_250,
    )
    request = TranscriptionRequest(
        request_id=UUID("00000000-0000-4000-8000-000000000001"),
        audio_path=Path("/tmp/00000000-0000-4000-8000-000000000001.audio"),
        facts=facts,
        language_hint=LanguageHint.ZH,
    )
    result = TranscriptionResult(
        request_id=request.request_id,
        transcript="  原样文本\n",
        provider="dashscope",
        model="qwen3-asr-flash",
    )

    assert result.transcript == "  原样文本\n"
    with pytest.raises(FrozenInstanceError):
        facts.duration_ms = 2_000  # type: ignore[misc]


def test_audio_facts_reject_out_of_contract_duration() -> None:
    with pytest.raises(ValueError, match="duration_ms"):
        AudioFacts(
            audio_format=AudioFormat.MP3,
            media_type="audio/mpeg",
            codec="MPEG Audio",
            byte_size=1,
            duration_ms=120_001,
        )


def test_speech_core_has_no_evidence_scoring_or_openhands_imports() -> None:
    package_root = Path(__file__).resolve().parents[2] / "focusproof" / "speech_core"
    forbidden = (
        "focusproof.domain.scoring",
        "focusproof.persistence",
        "focusproof.runtime.evidence",
        "focusproof.openhands_adapter",
        "focusproof.openhands_runtime",
    )

    imported: set[str] = set()
    for path in package_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    assert not any(
        name == prefix or name.startswith(f"{prefix}.")
        for name in imported
        for prefix in forbidden
    )
