from __future__ import annotations

from pathlib import Path

from focusproof.api.app import _product_capabilities, _view
import pytest

from focusproof.config.env import build_speech_capability, load_speech_settings
from focusproof.runtime.evidence import LearningGoal


IMAGE_CAPABILITY = {
    "capabilityId": "image_evidence",
    "enabled": True,
    "formats": ["image/png", "image/jpeg", "image/webp"],
    "maxCount": 4,
    "maxOriginalBytes": 10_485_760,
    "maxNormalizedBytesPerSession": 20_971_520,
    "explanationRequired": True,
}


def _real_asr_env() -> dict[str, str]:
    return {
        "FOCUSPROOF_ASR_PROVIDER": "dashscope",
        "FOCUSPROOF_ASR_MODEL": "qwen3-asr-flash",
        "FOCUSPROOF_ASR_BASE_URL": (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        "DASHSCOPE_API_KEY": "placeholder",
        "FOCUSPROOF_ASR_E2E_TIMEOUT_SECONDS": "120",
        "FOCUSPROOF_ASR_MAX_CONCURRENCY": "4",
        "FOCUSPROOF_SPEECH_IDEMPOTENCY_HMAC_ACTIVE_VERSION": "2026-08",
        "FOCUSPROOF_SPEECH_IDEMPOTENCY_HMAC_KEYRING_JSON": (
            '{"2026-07":"retained-hmac-secret",'
            '"2026-08":"active-hmac-secret"}'
        ),
    }


def _goal() -> LearningGoal:
    return LearningGoal(domain="general", title="Speech", goal="Explain aloud")


def test_missing_asr_config_publishes_only_a_disabled_speech_variant() -> None:
    capabilities = _product_capabilities(
        media_enabled=False,
        speech_capability=build_speech_capability({}),
    )

    assert capabilities == [
        {
            "capabilityId": "speech_transcription",
            "schemaVersion": 1,
            "enabled": False,
            "reasonCode": "asr_not_configured",
        }
    ]


def test_enabled_speech_appends_without_changing_image_capability() -> None:
    capabilities = _product_capabilities(
        media_enabled=True,
        speech_capability=build_speech_capability(_real_asr_env()),
    )

    assert capabilities[0] == IMAGE_CAPABILITY
    assert capabilities[1]["capabilityId"] == "speech_transcription"
    assert capabilities[1]["enabled"] is True
    assert capabilities[1]["languageHintEffect"] == "metadata_only"


def test_speech_settings_exposes_explicit_active_hmac_version_and_keyring() -> None:
    settings = load_speech_settings(_real_asr_env())

    assert settings is not None
    assert settings.idempotency_hmac_active_version == "2026-08"
    assert dict(settings.idempotency_hmac_keyring) == {
        "2026-07": "retained-hmac-secret",
        "2026-08": "active-hmac-secret",
    }


def test_speech_settings_rejects_an_active_hmac_version_missing_from_keyring() -> None:
    environ = _real_asr_env()
    environ["FOCUSPROOF_SPEECH_IDEMPOTENCY_HMAC_ACTIVE_VERSION"] = "2026-09"

    with pytest.raises(ValueError, match="speech HMAC configuration is invalid"):
        load_speech_settings(environ)


def test_runtime_composition_uses_configured_hmac_version_and_keyring() -> None:
    app_source = (
        Path(__file__).resolve().parents[2] / "focusproof" / "api" / "app.py"
    ).read_text(encoding="utf-8")

    assert 'active_hmac_key_version="v1"' not in app_source
    assert "settings.idempotency_hmac_active_version" in app_source
    assert "settings.idempotency_hmac_keyring" in app_source


def test_session_view_projects_disabled_speech_without_affecting_plugins() -> None:
    plugin = {
        "pluginId": "existing",
        "capabilityId": "claim",
        "enabled": True,
        "metadata": {"stable": True},
    }
    speech = build_speech_capability({})

    rendered = _view(
        "sess-1",
        "running",
        _goal(),
        [],
        None,
        [plugin],
        product_capabilities=[speech],
    )

    assert rendered["productCapabilities"] == [speech]
    assert rendered["pluginCapabilities"] == [plugin]
