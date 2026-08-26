from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar, Self

import pytest
from fastapi.testclient import TestClient
from openhands.sdk.tool import ToolDefinition

from focusproof.openhands_runtime.capabilities import (
    VerificationCapability,
    VerificationCapabilityRegistry,
    build_builtin_capabilities,
)
from focusproof.openhands_runtime.tools.verification import (
    EvidenceReferenceAction,
    VerificationObservation,
)
from focusproof.runtime.evidence import Evidence


class BoundRepository:
    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        raise KeyError((session_id, evidence_id))


class LifecycleEngine:
    def dispose(self) -> None:
        pass


class LifecycleUnitOfWorkFactory:
    pass


class LifecycleRunLock:
    def __init__(self, data_dir: Path, *, timeout_seconds: float) -> None:
        del data_dir, timeout_seconds


class LifecycleConversationManager:
    constructed: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.closed = False
        type(self).constructed.append(kwargs)

    def close_all(self) -> None:
        self.closed = True


@contextmanager
def _unchanged_environ(name: str) -> Iterator[None]:
    original = os.environ.get(name)
    try:
        yield
    finally:
        if original is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = original


class FirstMediaTool(ToolDefinition[EvidenceReferenceAction, VerificationObservation]):
    name: ClassVar[str] = "focusproof_first_media"

    @classmethod
    def create(
        cls,
        conv_state: Any | None = None,
        *,
        session_id: str,
        repository: object | None = None,
    ) -> Sequence[Self]:
        del conv_state, session_id, repository
        return []


class SecondMediaTool(ToolDefinition[EvidenceReferenceAction, VerificationObservation]):
    name: ClassVar[str] = "focusproof_second_media"

    @classmethod
    def create(
        cls,
        conv_state: Any | None = None,
        *,
        session_id: str,
        repository: object | None = None,
    ) -> Sequence[Self]:
        del conv_state, session_id, repository
        return []


def _capability(
    registry_name: str,
    tool_class_name: str,
    *,
    evidence_type: str = "image/png",
    priority: int = 30,
) -> VerificationCapability:
    return VerificationCapability(
        registry_name=registry_name,
        tool_class_name=tool_class_name,
        supported_evidence_types=frozenset({evidence_type}),
        supported_domains=frozenset({"*"}),
        priority=priority,
        read_only=True,
        requires_network=False,
        timeout_seconds=5.0,
        enabled=True,
        version="1",
    )


def test_runtime_contributions_register_tools_lazily_and_preserve_call_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from focusproof.openhands_runtime import tool_assembler as assembler_module
    from focusproof.openhands_runtime.runtime_contributions import RuntimeContribution
    from focusproof.openhands_runtime.tool_assembler import SessionToolAssembler

    register_calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        assembler_module,
        "register_tool",
        lambda name, definition: register_calls.append((name, definition)),
    )
    first = RuntimeContribution(
        capabilities=(_capability("image", "FocusProofFirstMediaTool", priority=30),),
        tool_definitions={"FocusProofFirstMediaTool": FirstMediaTool},
    )
    second = RuntimeContribution(
        capabilities=(
            _capability(
                "diagram-image",
                "FocusProofSecondMediaTool",
                evidence_type="image/webp",
                priority=31,
            ),
        ),
        tool_definitions={"FocusProofSecondMediaTool": SecondMediaTool},
    )

    assert register_calls == []

    assembler = SessionToolAssembler(
        VerificationCapabilityRegistry(build_builtin_capabilities()),
        runtime_contributions=(first, second),
    )

    assert register_calls == [
        ("FocusProofFirstMediaTool", FirstMediaTool),
        ("FocusProofSecondMediaTool", SecondMediaTool),
    ]
    tools = assembler.assemble(
        "sess_media",
        "general",
        {"image/png", "image/webp"},
        repository=BoundRepository(),
    )
    assert [tool.name for tool in tools] == [
        "FocusProofLearnerInputTool",
        "FocusProofReviewDraftTool",
        "FocusProofFirstMediaTool",
        "FocusProofSecondMediaTool",
    ]


def test_runtime_contribution_capability_conflict_fails_closed() -> None:
    from focusproof.openhands_runtime.runtime_contributions import RuntimeContribution
    from focusproof.openhands_runtime.tool_assembler import SessionToolAssembler

    contribution = RuntimeContribution(
        capabilities=(_capability("text", "FocusProofFirstMediaTool"),),
        tool_definitions={"FocusProofFirstMediaTool": FirstMediaTool},
    )

    with pytest.raises(ValueError, match="capability.*conflict"):
        SessionToolAssembler(
            VerificationCapabilityRegistry(build_builtin_capabilities()),
            runtime_contributions=(contribution,),
        )


def test_runtime_contribution_tool_name_conflict_fails_closed() -> None:
    from focusproof.openhands_runtime.runtime_contributions import RuntimeContribution
    from focusproof.openhands_runtime.tool_assembler import SessionToolAssembler

    contribution = RuntimeContribution(
        capabilities=(_capability("image", "FocusProofTextEvidenceVerificationTool"),),
        tool_definitions={"FocusProofTextEvidenceVerificationTool": FirstMediaTool},
    )

    with pytest.raises(ValueError, match="tool.*conflict"):
        SessionToolAssembler(
            VerificationCapabilityRegistry(build_builtin_capabilities()),
            runtime_contributions=(contribution,),
        )


def test_disabled_media_runtime_contribution_does_not_import_media_tools_or_adapters() -> None:
    before_modules = set(sys.modules)
    from focusproof.bootstrap.media_composition import (
        compose_optional_media_runtime_contribution,
    )

    contribution = compose_optional_media_runtime_contribution(
        enabled=False,
        repository=object(),
    )

    assert contribution is None
    imported = set(sys.modules) - before_modules
    assert "focusproof.openhands_runtime.tools.media_evidence" not in imported
    assert not any(name.startswith("focusproof.media_adapters") for name in imported)
    assert "focusproof.media_projection.image_narrative_provider" not in imported


def _install_lifespan_doubles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    media_enabled: bool,
) -> tuple[list[object], list[object]]:
    from focusproof.api import app as app_module
    from focusproof.config.identity import OidcSettings
    from focusproof.openhands_runtime import locks as locks_module
    from focusproof.openhands_runtime import manager as manager_module

    captured_contributions: list[object] = []
    captured_media_locks: list[object] = []
    LifecycleConversationManager.constructed = []
    monkeypatch.setattr(app_module, "create_database_engine", lambda url: LifecycleEngine())
    monkeypatch.setattr(app_module, "check_schema_revision", lambda engine, config: None)
    monkeypatch.setattr(app_module, "create_session_factory", lambda engine: object())
    monkeypatch.setattr(
        app_module, "UnitOfWorkFactory", lambda session_factory: LifecycleUnitOfWorkFactory()
    )
    monkeypatch.setattr(app_module, "UowPrincipalResolver", lambda uow_factory: object())
    monkeypatch.setattr(
        app_module,
        "load_oidc_settings",
        lambda environ, *, profile: OidcSettings(
            enabled=False,
            issuer=None,
            audience=None,
            jwks_uri=None,
            principal_fingerprint_key=None,
        ),
    )
    monkeypatch.setattr(app_module, "configure_token_verifier", lambda verifier: None)
    monkeypatch.setattr(app_module, "reset_token_verifier", lambda: None)
    monkeypatch.setattr(app_module, "PersistentAuditProjectionStore", lambda uow_factory: object())
    monkeypatch.setattr(app_module, "UowEvidenceProvider", lambda uow_factory: BoundRepository())
    monkeypatch.setattr(app_module, "load_evidence_plugin_providers", lambda environ: ())
    monkeypatch.setattr(app_module, "load_runtime_settings", lambda environ: None)
    monkeypatch.setattr(app_module, "collect_public_plugin_capabilities", lambda providers: [])
    monkeypatch.setattr(app_module, "FileSessionRunLock", LifecycleRunLock, raising=False)
    monkeypatch.setattr(locks_module, "FileSessionRunLock", LifecycleRunLock)
    monkeypatch.setattr(manager_module, "ConversationManager", LifecycleConversationManager)

    if media_enabled:
        import focusproof.bootstrap.media_composition as media_composition

        def fake_media_command(
            *,
            uow_factory: object,
            data_dir: Path,
            session_run_lock: object,
        ) -> object:
            del uow_factory, data_dir
            captured_media_locks.append(session_run_lock)
            return object()

        def fake_media_runtime_contribution(*, enabled: bool, repository: object) -> object | None:
            assert enabled is True
            assert isinstance(repository, BoundRepository)
            contribution = object()
            captured_contributions.append(contribution)
            return contribution

        monkeypatch.setattr(media_composition, "compose_media_command", fake_media_command)
        monkeypatch.setattr(
            media_composition,
            "compose_optional_media_runtime_contribution",
            fake_media_runtime_contribution,
        )

    monkeypatch.setenv("FOCUSPROOF_PROFILE", "local-dev")
    monkeypatch.setenv("FOCUSPROOF_MEDIA_ENABLED", "true" if media_enabled else "false")
    return captured_contributions, captured_media_locks


def test_create_app_enabled_media_injects_runtime_contribution_at_manager_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from focusproof.api import app as app_module

    captured_contributions, captured_media_locks = _install_lifespan_doubles(
        monkeypatch,
        tmp_path,
        media_enabled=True,
    )
    application = app_module.create_app(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'app.sqlite3'}",
        data_dir=tmp_path,
        llm_factory=lambda session_id: (_ for _ in ()).throw(RuntimeError(session_id)),
    )

    with TestClient(application):
        pass

    assert len(captured_contributions) == 1
    assert len(LifecycleConversationManager.constructed) == 1
    (kwargs,) = LifecycleConversationManager.constructed
    assert kwargs["runtime_contributions"] == tuple(captured_contributions)
    assert captured_media_locks == [kwargs["run_lock"]]
    assert application.state.media_ingestion_command is not None
    assert application.state.product_capabilities[0]["capabilityId"] == "image_evidence"


def test_create_app_disabled_media_injects_no_contribution_and_keeps_media_modules_cold(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from focusproof.api import app as app_module

    before_modules = set(sys.modules)
    _install_lifespan_doubles(monkeypatch, tmp_path, media_enabled=False)
    application = app_module.create_app(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'app.sqlite3'}",
        data_dir=tmp_path,
        llm_factory=lambda session_id: (_ for _ in ()).throw(RuntimeError(session_id)),
    )

    with TestClient(application):
        pass

    assert len(LifecycleConversationManager.constructed) == 1
    (kwargs,) = LifecycleConversationManager.constructed
    assert kwargs["runtime_contributions"] == ()
    assert not hasattr(application.state, "media_ingestion_command")
    assert application.state.product_capabilities == []
    imported_during_disabled_startup = set(sys.modules) - before_modules
    assert (
        "focusproof.openhands_runtime.tools.media_evidence" not in imported_during_disabled_startup
    )
    assert not any(
        name.startswith("focusproof.media_adapters") for name in imported_during_disabled_startup
    )
    assert (
        "focusproof.media_projection.image_narrative_provider"
        not in imported_during_disabled_startup
    )


def test_production_media_contribution_reaches_tool_assembler_and_result_extractor() -> None:
    from focusproof.bootstrap.media_composition import compose_media_runtime_contribution
    from focusproof.openhands_runtime.capabilities import (
        VerificationCapabilityRegistry,
        build_builtin_capabilities,
    )
    from focusproof.openhands_runtime.result_extractor import _RuntimeResultExtractor
    from focusproof.openhands_runtime.tool_assembler import SessionToolAssembler

    contribution = compose_media_runtime_contribution(repository=BoundRepository())
    assembler = SessionToolAssembler(
        VerificationCapabilityRegistry(build_builtin_capabilities()),
        runtime_contributions=(contribution,),
    )

    tools = assembler.assemble(
        "sess_image",
        "general",
        {"image/png"},
        repository=BoundRepository(),
    )
    extractor = _RuntimeResultExtractor(
        audit_log=object(),
        narrative_providers=contribution.narrative_providers,
    )

    assert "FocusProofMediaEvidenceVerificationTool" in {tool.name for tool in tools}
    assert {type(provider).__name__ for provider in extractor._narrative_projector._providers} == {
        "ImageNarrativeProvider"
    }
