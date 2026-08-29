from __future__ import annotations

from focusproof.api.app import _product_capabilities, _view
from focusproof.config.env import build_speech_capability
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
        "DASHSCOPE_API_KEY": "test-asr-secret",
        "FOCUSPROOF_ASR_E2E_TIMEOUT_SECONDS": "120",
        "FOCUSPROOF_ASR_MAX_CONCURRENCY": "4",
        "FOCUSPROOF_SPEECH_IDEMPOTENCY_HMAC_KEY": "test-hmac-secret",
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
