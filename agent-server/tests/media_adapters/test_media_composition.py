from __future__ import annotations

from pathlib import Path

import pytest

from focusproof.bootstrap.media_composition import (
    build_malware_scanner,
    compose_media_command,
)
from focusproof.config.profiles import MediaSecurityPolicy
from focusproof.media_adapters.clamd_malware_scanner import ClamdMalwareScanner
from focusproof.media_adapters.fake_malware_scanner import FakeMalwareScanner
from focusproof.media_application import MediaDisabledError


def policy(mode: str, *, endpoint: str | None = None) -> MediaSecurityPolicy:
    return MediaSecurityPolicy(
        mode=mode,  # type: ignore[arg-type]
        endpoint=endpoint,
        max_scan_bytes=10 * 1024 * 1024,
    )


def test_composition_selects_only_explicit_adapter() -> None:
    assert isinstance(
        build_malware_scanner(policy("clamd", endpoint="tcp://127.0.0.1:3310")),
        ClamdMalwareScanner,
    )
    assert isinstance(build_malware_scanner(policy("fake-clean")), FakeMalwareScanner)
    with pytest.raises(ValueError):
        build_malware_scanner(policy("disabled"))


def test_clamd_composition_passes_auditable_definitions_snapshot() -> None:
    scanner = build_malware_scanner(
        policy("clamd", endpoint="tcp://127.0.0.1:3310")
    )
    assert isinstance(scanner, ClamdMalwareScanner)
    assert scanner._limits.definitions_version != "legacy-unverified"


def test_disabled_command_has_zero_composition_and_execution_side_effects(tmp_path: Path) -> None:
    class ExplodingUow:
        def __call__(self):
            raise AssertionError("disabled media entered persistence")

    command = compose_media_command(
        uow_factory=ExplodingUow(),  # type: ignore[arg-type]
        data_dir=tmp_path,
        security_policy=policy("disabled"),
    )
    with pytest.raises(MediaDisabledError):
        command.execute(
            owner_id="owner",
            session_id="session",
            stream=None,
            declared_media_type="image/png",
            explanation="x",
            idempotency_key="key",
        )
    assert list(tmp_path.iterdir()) == []
