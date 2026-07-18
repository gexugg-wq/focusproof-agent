from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from focusproof.persistence.repositories import StoredPrincipal
from focusproof.persistence.unit_of_work import UnitOfWorkFactoryLike
from focusproof.runtime.evidence import Evidence

_IDENTITY_UNIQUE_CONSTRAINT = "uq_verified_principals_issuer_subject"


class PrincipalIdentityError(RuntimeError):
    code: str

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class InvalidPrincipalIdentityError(PrincipalIdentityError):
    def __init__(self) -> None:
        super().__init__("invalid_principal_identity")


class PrincipalDisabledError(PrincipalIdentityError):
    def __init__(self) -> None:
        super().__init__("principal_disabled")


class IdentityStorageIsolationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IdentityStoragePaths:
    database_url: str
    conversation_root: Path


def select_identity_storage_paths(
    profile: str,
    *,
    anonymous_local_dev: IdentityStoragePaths,
    verified: IdentityStoragePaths,
) -> IdentityStoragePaths:
    if _database_key(anonymous_local_dev.database_url) == _database_key(
        verified.database_url
    ):
        raise IdentityStorageIsolationError("anonymous and verified databases overlap")
    if anonymous_local_dev.conversation_root.resolve() == verified.conversation_root.resolve():
        raise IdentityStorageIsolationError(
            "anonymous and verified conversation roots overlap"
        )
    if profile == "local-dev":
        return anonymous_local_dev
    if profile in {"staging", "production"}:
        return verified
    raise IdentityStorageIsolationError(
        "anonymous storage is available only to the explicit local-dev profile"
    )


def _database_key(database_url: str) -> tuple[object, ...]:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        database = url.database
        if database in {None, "", ":memory:"}:
            return ("sqlite", database)
        return ("sqlite", Path(database).resolve())
    return (
        url.get_backend_name(),
        url.host,
        url.port,
        url.database,
    )


class UowPrincipalResolver:
    def __init__(self, uow_factory: UnitOfWorkFactoryLike) -> None:
        self._uow_factory = uow_factory

    def resolve(self, *, issuer: str, subject: str) -> str:
        _validate_exact_identity_value(issuer)
        _validate_exact_identity_value(subject)
        now = datetime.now(UTC)
        candidate = StoredPrincipal(
            principal_id=f"principal_{uuid4().hex}",
            issuer=issuer,
            subject=subject,
            active=True,
            created_at=now,
            state_changed_at=now,
        )
        conflict: IntegrityError | None = None
        with self._uow_factory() as uow:
            existing = uow.principals.get_exact(issuer=issuer, subject=subject)
            if existing is not None:
                return _active_principal_id(existing)
            try:
                uow.principals.add(candidate)
                uow.commit()
                return candidate.principal_id
            except IntegrityError as exc:
                uow.rollback()
                if not _is_exact_identity_uniqueness_conflict(exc):
                    raise
                conflict = exc

        with self._uow_factory() as winner_uow:
            winner = winner_uow.principals.get_exact(
                issuer=issuer,
                subject=subject,
            )
        if winner is None:
            if conflict is None:
                raise RuntimeError("identity resolution lost its database result")
            raise conflict
        return _active_principal_id(winner)


def _validate_exact_identity_value(value: str) -> None:
    if not value or not value.strip() or value != value.strip():
        raise InvalidPrincipalIdentityError()


def _active_principal_id(principal: StoredPrincipal) -> str:
    if not principal.active:
        raise PrincipalDisabledError()
    return principal.principal_id


def _is_exact_identity_uniqueness_conflict(exc: IntegrityError) -> bool:
    diagnostic = getattr(exc.orig, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == _IDENTITY_UNIQUE_CONSTRAINT:
        return True
    message = str(exc.orig)
    return (
        "UNIQUE constraint failed: "
        "verified_principals.issuer, verified_principals.subject"
    ) in message


class UowEvidenceProvider:
    def __init__(self, uow_factory: UnitOfWorkFactoryLike) -> None:
        self._uow_factory = uow_factory

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        with self._uow_factory() as uow:
            stored = uow.evidence.get(session_id, evidence_id)
        if stored is None:
            raise KeyError(f"Evidence {evidence_id} does not exist")
        return Evidence(
            evidenceId=stored.evidence_id,
            evidenceType=stored.evidence_type,
            contentHash=stored.content_hash,
            textContent=stored.text_content,
            sourceUrl=stored.source_url,
            metadata=stored.metadata,
        )
