from __future__ import annotations

from io import BytesIO

import pytest

from focusproof.config.profiles import MediaSecurityPolicy, load_media_security_policy
from focusproof.media_adapters.fake_malware_scanner import FakeMalwareScanner
from focusproof.media_adapters.clamd_malware_scanner import ClamdMalwareScanner
from focusproof.media_core.ports import ReadOnlyMediaSource


def test_staging_and_production_require_complete_clamd_policy() -> None:
    for profile in ("staging", "production"):
        for mode in (None, "disabled", "fake-clean", "fake-malicious"):
            environ = {} if mode is None else {"FOCUSPROOF_MEDIA_SCANNER_MODE": mode}
            with pytest.raises(ValueError):
                load_media_security_policy(profile, environ)


def test_valid_clamd_policy_is_frozen_and_secret_safe() -> None:
    policy = load_media_security_policy(
        "production",
        {
            "FOCUSPROOF_MEDIA_SCANNER_MODE": "clamd",
            "FOCUSPROOF_CLAMD_ENDPOINT": "unix:///run/clamav/clamd.sock",
            "FOCUSPROOF_MEDIA_SCAN_CONNECT_TIMEOUT_SECONDS": "1",
            "FOCUSPROOF_MEDIA_SCAN_TOTAL_TIMEOUT_SECONDS": "5",
            "FOCUSPROOF_MEDIA_SCAN_ADMISSION_TIMEOUT_SECONDS": "2",
            "FOCUSPROOF_MEDIA_SCAN_MAX_BYTES": str(10 * 1024 * 1024),
            "FOCUSPROOF_MEDIA_SCAN_MAX_CONCURRENCY": "4",
        },
    )
    assert policy.mode == "clamd"
    assert policy.upload_enabled is True
    assert policy.visual_provider_enabled is False
    assert "clamd.sock" not in repr(policy)
    with pytest.raises(Exception):
        policy.mode = "disabled"  # type: ignore[misc]


def test_clamd_capacity_must_cover_authoritative_source_limit() -> None:
    with pytest.raises(ValueError):
        MediaSecurityPolicy(
            mode="clamd",
            endpoint="tcp://127.0.0.1:3310",
            connect_timeout_seconds=1,
            total_timeout_seconds=5,
            admission_timeout_seconds=1,
            max_scan_bytes=10 * 1024 * 1024 - 1,
            max_concurrent_scans=1,
        )


def test_local_and_test_disabled_means_upload_off() -> None:
    for profile in ("local-dev", "deterministic-test"):
        policy = load_media_security_policy(profile, {"FOCUSPROOF_MEDIA_SCANNER_MODE": "disabled"})
        assert policy.upload_enabled is False
        assert policy.mode == "disabled"


@pytest.mark.parametrize(
    ("mode", "status", "raises"),
    [
        ("fake-clean", "clean", False),
        ("fake-malicious", "malicious", False),
        ("fake-unavailable", "unavailable", False),
        ("fake-timeout", "timeout", False),
        ("fake-error", "error", False),
        ("fake-unknown", "unknown", False),
        ("fake-raises", None, True),
    ],
)
def test_explicit_fake_is_deterministic(mode: str, status: str | None, raises: bool) -> None:
    policy = load_media_security_policy(
        "deterministic-test", {"FOCUSPROOF_MEDIA_SCANNER_MODE": mode}
    )
    scanner = FakeMalwareScanner.from_mode(policy.mode)
    source = ReadOnlyMediaSource(stream=BytesIO(b"x"), byte_size=1, streaming_sha256="0" * 64)
    if raises:
        with pytest.raises(RuntimeError, match="deterministic scanner failure"):
            scanner.scan(source)
    else:
        assert scanner.scan(source).status == status


def test_disabled_cannot_construct_fake_scanner() -> None:
    with pytest.raises(ValueError):
        FakeMalwareScanner.from_mode("disabled")


def test_staging_compose_uses_private_healthy_clamd_sidecar() -> None:
    from pathlib import Path

    import yaml

    compose_path = Path(__file__).parents[3] / "deploy" / "compose.staging.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    clamd = compose["services"]["clamd"]
    agent_server = compose["services"]["agent-server"]
    environment = agent_server["environment"]
    assert environment["FOCUSPROOF_MEDIA_SCANNER_MODE"] == "clamd"
    assert environment["FOCUSPROOF_CLAMD_ENDPOINT"] == "tcp://clamd:3310"
    assert agent_server["depends_on"]["clamd"]["condition"] == "service_healthy"
    assert set(agent_server["networks"]) == set(clamd["networks"]) == {"private"}
    assert "ports" not in clamd


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
@pytest.mark.parametrize(
    "key",
    [
        "FOCUSPROOF_MEDIA_SCAN_CONNECT_TIMEOUT_SECONDS",
        "FOCUSPROOF_MEDIA_SCAN_TOTAL_TIMEOUT_SECONDS",
        "FOCUSPROOF_MEDIA_SCAN_ADMISSION_TIMEOUT_SECONDS",
    ],
)
def test_media_security_policy_rejects_non_finite_timeouts(key: str, value: str) -> None:
    environ = {
        "FOCUSPROOF_MEDIA_SCANNER_MODE": "clamd",
        "FOCUSPROOF_CLAMD_ENDPOINT": "tcp://127.0.0.1:3310",
        key: value,
    }
    with pytest.raises(ValueError):
        load_media_security_policy("production", environ)


@pytest.mark.parametrize(
    "endpoint",
    [
        "bogus",
        "http://clamd:3310",
        "https://clamd:3310",
        "tcp://",
        "tcp://clamd",
        "tcp://user:pass@clamd:3310",
        "tcp://clamd:0",
        "tcp://clamd:65536",
        "tcp://clamd:3310/path",
        "tcp://clamd:3310?query=yes",
        "tcp://clamd:3310#fragment",
        "unix://",
        "unix://host/run/clamd.sock",
        "unix:///run/clamd.sock?query=yes",
        "unix:///run/clamd.sock#fragment",
    ],
)
def test_policy_and_scanner_reject_invalid_endpoint_at_construction(endpoint: str) -> None:
    with pytest.raises(ValueError, match="invalid clamd endpoint"):
        MediaSecurityPolicy(
            mode="clamd",
            endpoint=endpoint,
            max_scan_bytes=10 * 1024 * 1024,
        )
    with pytest.raises(ValueError, match="invalid clamd endpoint"):
        ClamdMalwareScanner(
            endpoint=endpoint,
            connect_timeout_seconds=1,
            total_timeout_seconds=2,
            admission_timeout_seconds=1,
            max_scan_bytes=10 * 1024 * 1024,
            max_concurrent_scans=1,
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "tcp://clamd.internal:3310",
        "tcp://127.0.0.1:3310",
        "unix:///run/clamav/clamd.sock",
    ],
)
def test_policy_accepts_canonical_clamd_endpoints(endpoint: str) -> None:
    policy = MediaSecurityPolicy(
        mode="clamd",
        endpoint=endpoint,
        max_scan_bytes=10 * 1024 * 1024,
    )
    assert policy.endpoint == endpoint
