from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from openhands.sdk.tool import ToolDefinition

if TYPE_CHECKING:
    from focusproof.openhands_runtime.capabilities import VerificationCapability
    from focusproof.openhands_runtime.tools import SessionEvidenceRepository
    from focusproof.persistence.unit_of_work import UnitOfWorkFactoryLike


ToolDefinitionClass = type[ToolDefinition[Any, Any]]


class EvidencePluginProvider(Protocol):
    """Core-neutral contribution to the official OpenHands tool registry."""

    plugin_id: str

    def tool_definitions(self) -> Mapping[str, ToolDefinitionClass]: ...

    def capability_definitions(self) -> Sequence[VerificationCapability]: ...


@dataclass(frozen=True, slots=True)
class PublicPluginCapability:
    plugin_id: str
    capability_id: str
    enabled: bool
    metadata: Mapping[str, object]


class PublicCapabilityProvider(Protocol):
    def public_capabilities(self) -> Sequence[PublicPluginCapability]: ...


class EvidenceSubmissionNormalizer(Protocol):
    def normalize_evidence_submission(self, request: object) -> object: ...


class SessionRepositoryBinder(Protocol):
    def bind_session_repository(
        self,
        repository: SessionEvidenceRepository,
        *,
        session_id: str,
        principal_id: str,
        uow_factory: UnitOfWorkFactoryLike,
    ) -> SessionEvidenceRepository: ...


def collect_public_plugin_capabilities(
    providers: Iterable[EvidencePluginProvider],
) -> tuple[PublicPluginCapability, ...]:
    public_capabilities: list[PublicPluginCapability] = []
    for provider in providers:
        contributor = cast(PublicCapabilityProvider | None, provider)
        method = getattr(contributor, "public_capabilities", None)
        if callable(method):
            public_capabilities.extend(method())
    return tuple(public_capabilities)


def normalize_evidence_submission_plugins(
    request: object,
    *,
    providers: Iterable[EvidencePluginProvider],
) -> object:
    normalized = request
    for provider in providers:
        normalizer = cast(EvidenceSubmissionNormalizer | None, provider)
        method = getattr(normalizer, "normalize_evidence_submission", None)
        if callable(method):
            normalized = method(normalized)
    return normalized


def bind_session_repository_plugins(
    repository: SessionEvidenceRepository,
    *,
    providers: Iterable[EvidencePluginProvider],
    session_id: str,
    principal_id: str,
    uow_factory: UnitOfWorkFactoryLike,
) -> SessionEvidenceRepository:
    bound = repository
    for provider in providers:
        binder = cast(SessionRepositoryBinder | None, provider)
        method = getattr(binder, "bind_session_repository", None)
        if callable(method):
            bound = method(
                bound,
                session_id=session_id,
                principal_id=principal_id,
                uow_factory=uow_factory,
            )
    return bound
