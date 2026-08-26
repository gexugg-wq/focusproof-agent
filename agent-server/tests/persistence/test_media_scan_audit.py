from __future__ import annotations

from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from io import StringIO

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, insert, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from focusproof.contracts import media_scan as scan_contract
from focusproof.media_core import models as media_models
from focusproof.media_core.models import (
    MediaCleanReceipt,
    MediaScanAttempt,
    PendingCleanReceipt,
    ScanRejectionCode,
    ScanResultKind,
)
from focusproof.persistence.audit_projection import MediaScanAuditRepository
from focusproof.persistence.models import (
    Base,
    MediaCleanReceiptModel,
    MediaScanAttemptModel,
    PendingCleanReceiptModel,
)


def _attempt(*, attempt_id: str = "attempt-1", idempotency_key: str = "same-key") -> MediaScanAttempt:
    started_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    return MediaScanAttempt(
        attempt_id=attempt_id,
        artifact_sha256="a" * 64,
        content_type="image/png",
        scanner_backend="clamd",
        definitions_version="daily-1",
        definitions_fresh_at=started_at - timedelta(seconds=30),
        definitions_age_seconds=30,
        max_bytes=10_000_000,
        max_concurrent_scans=4,
        deadline_ms=5_000,
        socket_timeout_ms=2_000,
        scan_result=ScanResultKind.CLEAN,
        rejection_code=None,
        rejection_detail=None,
        started_at=started_at,
        finished_at=started_at + timedelta(milliseconds=40),
        idempotency_key=idempotency_key,
    )


def _receipt(
    attempt: MediaScanAttempt,
    *,
    receipt_id: str = "receipt-1",
    receipt_hash: str = "b" * 64,
) -> MediaCleanReceipt:
    return MediaCleanReceipt.from_attempt(
        attempt,
        receipt_id=receipt_id,
        receipt_hash=receipt_hash,
        quarantine_path="quarantine/object-1",
        quarantine_expires_at=attempt.finished_at + timedelta(hours=1),
        created_at=attempt.finished_at,
    )


def _pending(receipt: MediaCleanReceipt) -> PendingCleanReceipt:
    return PendingCleanReceipt(
        receipt_id=receipt.receipt_id,
        attempt_id=receipt.attempt_id,
        artifact_sha256=receipt.artifact_sha256,
        receipt_hash=receipt.receipt_hash,
        spool_token="c" * 32,
        spool_byte_size=123,
        spool_sha256=receipt.artifact_sha256,
        spool_expires_at=receipt.created_at + timedelta(minutes=1),
        quarantine_expires_at=receipt.quarantine_expires_at,
        created_at=receipt.created_at,
    )


def test_media_scan_audit_migration_upgrade_downgrade_reupgrade(
    alembic_config: Config,
    database_url: str,
) -> None:
    from focusproof.persistence.database import create_database_engine

    command.upgrade(alembic_config, "head")
    engine = create_database_engine(database_url)
    assert {"scan_attempts", "pending_clean_receipts", "clean_receipts"} <= set(
        inspect(engine).get_table_names()
    )
    engine.dispose()

    command.downgrade(alembic_config, "base")
    engine = create_database_engine(database_url)
    assert {"scan_attempts", "pending_clean_receipts", "clean_receipts"}.isdisjoint(
        inspect(engine).get_table_names()
    )
    engine.dispose()

    command.upgrade(alembic_config, "head")
    engine = create_database_engine(database_url)
    assert {"scan_attempts", "pending_clean_receipts", "clean_receipts"} <= set(
        inspect(engine).get_table_names()
    )
    engine.dispose()


def test_media_scan_schema_constraints_and_postgresql_compilation(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    attempt_unique = {item["name"] for item in inspector.get_unique_constraints("scan_attempts")}
    receipt_unique = {item["name"] for item in inspector.get_unique_constraints("clean_receipts")}
    pending_unique = {
        item["name"] for item in inspector.get_unique_constraints("pending_clean_receipts")
    }
    assert {"uq_scan_attempts_attempt_id", "uq_scan_attempts_idempotency_key"} <= attempt_unique
    assert {"uq_clean_receipts_attempt_id", "uq_clean_receipts_receipt_hash"} <= receipt_unique
    assert {
        "uq_pending_clean_receipts_attempt_id",
        "uq_pending_clean_receipts_receipt_hash",
    } <= pending_unique
    assert {
        "definitions_version",
        "definitions_fresh_at",
        "definitions_age_seconds",
        "max_bytes",
        "max_concurrent_scans",
        "deadline_ms",
        "socket_timeout_ms",
    } <= {column["name"] for column in inspector.get_columns("scan_attempts")}
    assert {
        "definitions_version",
        "definitions_fresh_at",
        "definitions_age_seconds",
        "max_bytes",
        "max_concurrent_scans",
        "deadline_ms",
        "socket_timeout_ms",
    } <= {column["name"] for column in inspector.get_columns("clean_receipts")}
    assert {
        "spool_token",
        "spool_byte_size",
        "spool_sha256",
        "spool_expires_at",
        "quarantine_expires_at",
        "formal_artifact_id",
        "publication_status",
        "publication_owner",
        "publication_lease_expires_at",
        "publication_version",
        "published_at",
        "publication_failure",
        "updated_at",
    } <= {column["name"] for column in inspector.get_columns("pending_clean_receipts")}

    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    ddl = [
        str(CreateTable(table).compile(dialect=dialect))
        for table in (MediaScanAttemptModel.__table__, MediaCleanReceiptModel.__table__)
    ]
    assert all("TIMESTAMP WITH TIME ZONE" in statement for statement in ddl)


LEGAL_RESULT_REJECTION_PAIRS = (
    (ScanResultKind.CLEAN, None),
    (ScanResultKind.MALICIOUS, ScanRejectionCode.MALWARE_SIGNATURE_DETECTED),
    (ScanResultKind.OVERSIZE, ScanRejectionCode.PAYLOAD_TOO_LARGE),
    (ScanResultKind.TIMEOUT, ScanRejectionCode.DEADLINE_EXCEEDED),
    (ScanResultKind.UNAVAILABLE, ScanRejectionCode.DAEMON_UNAVAILABLE),
    (ScanResultKind.ERROR, ScanRejectionCode.DAEMON_ERROR),
    (ScanResultKind.ERROR, ScanRejectionCode.LEGACY_UNKNOWN_UNCLASSIFIED),
)


def _load_media_scan_migration(project_root: object) -> ModuleType:
    migration_path = (
        Path(project_root)
        / "agent-server/migrations/versions/0006_media_scan_audit_and_receipts.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_0006_media_scan_audit_and_receipts", migration_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("scan_result", "rejection_code"), LEGAL_RESULT_REJECTION_PAIRS)
def test_every_legal_result_rejection_pair_persists(
    migrated_engine: Engine,
    scan_result: ScanResultKind,
    rejection_code: ScanRejectionCode | None,
) -> None:
    suffix = f"{scan_result.value}-{rejection_code.value if rejection_code else 'none'}"
    original = _attempt(attempt_id=f"attempt-{suffix}", idempotency_key=f"key-{suffix}")
    attempt = MediaScanAttempt(
        **{
            field: getattr(original, field)
            for field in original.__dataclass_fields__
            if field not in {"scan_result", "rejection_code"}
        },
        scan_result=scan_result,
        rejection_code=rejection_code,
    )
    with Session(migrated_engine) as session:
        MediaScanAuditRepository(session).record_attempt(attempt)
        session.commit()


def _core_attempt_values(
    *,
    scan_result: str,
    rejection_code: str | None,
    attempt_id: str,
) -> dict[str, object]:
    attempt = _attempt(attempt_id=attempt_id, idempotency_key=f"key-{attempt_id}")
    return {
        field: getattr(attempt, field) for field in attempt.__dataclass_fields__
    } | {"scan_result": scan_result, "rejection_code": rejection_code}


@pytest.mark.parametrize(
    ("scan_result", "rejection_code"),
    [
        ("malicious", "daemon_error"),
        ("oversize", "deadline_exceeded"),
        ("timeout", "daemon_unavailable"),
        ("unavailable", "payload_too_large"),
        ("error", "malware_signature_detected"),
        ("clean", "daemon_error"),
        ("malicious", "not_a_rejection_code"),
    ],
)
def test_sqlite_check_rejects_cross_pair_and_unknown_rejection_code(
    migrated_engine: Engine,
    scan_result: str,
    rejection_code: str,
) -> None:
    attempt_id = f"bad-{scan_result}-{rejection_code}"
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(
            insert(MediaScanAttemptModel),
            _core_attempt_values(
                scan_result=scan_result,
                rejection_code=rejection_code,
                attempt_id=attempt_id,
            ),
        )


def test_low_level_scan_contract_import_does_not_load_media_core(
    project_root: object,
) -> None:
    code = """
import json
import sys
from sqlalchemy import CheckConstraint

from focusproof.contracts import media_scan
from focusproof.persistence.models import MediaScanAttemptModel

constraints = [
    constraint.sqltext.text
    for constraint in MediaScanAttemptModel.__table__.constraints
    if isinstance(constraint, CheckConstraint)
    and constraint.name == "ck_scan_attempts_result_rejection_code"
]
loaded = sorted(
    name for name in sys.modules
    if name == "focusproof.media_core" or name.startswith("focusproof.media_core.")
)
print(json.dumps({
    "contract_sql": media_scan.SCAN_RESULT_REJECTION_CODE_CHECK_SQL,
    "constraints": constraints,
    "loaded": loaded,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    audit = json.loads(completed.stdout)
    assert audit["loaded"] == []
    assert audit["constraints"] == [audit["contract_sql"]]


def test_domain_orm_and_migration_scan_check_share_neutral_contract(
    project_root: object,
) -> None:
    from sqlalchemy import CheckConstraint

    orm_checks = [
        constraint.sqltext.text
        for constraint in MediaScanAttemptModel.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_scan_attempts_result_rejection_code"
    ]
    migration = _load_media_scan_migration(project_root)
    migration_source = (
        Path(project_root)
        / "agent-server/migrations/versions/0006_media_scan_audit_and_receipts.py"
    ).read_text()

    assert media_models.SCAN_RESULT_REJECTION_CODE_CHECK_SQL == (
        scan_contract.SCAN_RESULT_REJECTION_CODE_CHECK_SQL
    )
    assert orm_checks == [scan_contract.SCAN_RESULT_REJECTION_CODE_CHECK_SQL]
    assert migration.SCAN_RESULT_REJECTION_CODE_CHECK_SQL == (
        scan_contract.SCAN_RESULT_REJECTION_CODE_CHECK_SQL
    )
    assert "from focusproof.contracts.media_scan import" in migration_source
    assert "SCAN_RESULT_REJECTION_CODE_CHECK_SQL" in migration_source
    assert "scan_result = 'malicious'" not in migration_source
    assert "malware_signature_detected" not in migration_source


def test_migration_imports_scan_contract_without_media_core(project_root: object) -> None:
    code = """
import importlib.util
import json
import pathlib
import sys

from focusproof.contracts import media_scan

path = pathlib.Path("agent-server/migrations/versions/0006_media_scan_audit_and_receipts.py")
spec = importlib.util.spec_from_file_location("migration_0006_media_scan_audit_and_receipts", path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
loaded = sorted(
    name for name in sys.modules
    if name == "focusproof.media_core" or name.startswith("focusproof.media_core.")
)
print(json.dumps({
    "migration_sql": getattr(module, "SCAN_RESULT_REJECTION_CODE_CHECK_SQL", None),
    "contract_sql": media_scan.SCAN_RESULT_REJECTION_CODE_CHECK_SQL,
    "loaded": loaded,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    audit = json.loads(completed.stdout)
    assert audit["migration_sql"] == audit["contract_sql"]
    assert audit["loaded"] == []


def test_postgresql_offline_migration_contains_authoritative_pair_check(
    project_root: object,
) -> None:
    assert hasattr(media_models, "SCAN_RESULT_REJECTION_CODE_CHECK_SQL")
    buffer = StringIO()
    config = Config(str(project_root) + "/alembic.ini", output_buffer=buffer)
    config.set_main_option("script_location", str(project_root) + "/agent-server/migrations")
    config.set_main_option(
        "sqlalchemy.url",
        "postgresql+psycopg://offline:offline@invalid/focusproof",
    )
    command.upgrade(config, "head", sql=True)
    expected = " ".join(media_models.SCAN_RESULT_REJECTION_CODE_CHECK_SQL.split())
    assert expected in " ".join(buffer.getvalue().split())


def test_replay_same_attempt_and_receipt_is_idempotent(migrated_engine: Engine) -> None:

    attempt = _attempt()
    receipt = _receipt(attempt)
    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        first_attempt = repository.record_attempt(attempt)
        first_pending = repository.record_pending_clean_receipt(_pending(receipt))
        first_receipt = repository.record_clean_receipt(receipt)
        session.commit()
    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        second_attempt = repository.record_attempt(attempt)
        second_pending = repository.record_pending_clean_receipt(_pending(receipt))
        second_receipt = repository.record_clean_receipt(receipt)
        session.commit()
        assert first_attempt == second_attempt == attempt
        assert first_pending == second_pending == _pending(receipt)
        assert first_receipt == second_receipt == receipt
        assert session.scalar(select(func.count()).select_from(MediaScanAttemptModel)) == 1
        assert session.scalar(select(func.count()).select_from(PendingCleanReceiptModel)) == 1
        assert session.scalar(select(func.count()).select_from(MediaCleanReceiptModel)) == 1


def test_pending_clean_receipt_can_be_found_by_idempotency_key(migrated_engine: Engine) -> None:
    attempt = _attempt(idempotency_key="session-1:idem-1:fingerprint-1")
    receipt = _receipt(attempt)
    pending = _pending(receipt)
    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        repository.record_attempt(attempt)
        repository.record_pending_clean_receipt(pending)
        session.commit()

    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        assert repository.find_pending_clean_receipt("session-1:idem-1:fingerprint-1") == (
            attempt,
            pending,
        )
        assert repository.find_pending_clean_receipt("missing") is None


def test_pending_publication_claim_is_database_cas_and_replays_published_state(
    migrated_engine: Engine,
) -> None:
    attempt = _attempt(idempotency_key="session-1:idem-1:fingerprint-1")
    receipt = _receipt(attempt)
    pending = _pending(receipt)
    now = pending.created_at + timedelta(seconds=1)

    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        repository.record_attempt(attempt)
        repository.record_pending_clean_receipt(pending)
        session.commit()

    def claim(owner: str) -> tuple[bool, str, int]:
        with Session(migrated_engine) as session:
            repository = MediaScanAuditRepository(session)
            result = repository.claim_pending_clean_publication(
                pending.receipt_id,
                owner_token=owner,
                now=now,
                lease_expires_at=now + timedelta(seconds=30),
            )
            session.commit()
            assert result is not None
            return (
                result.acquired,
                result.pending.publication_status,
                result.pending.publication_version,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(pool.map(claim, ("owner-1", "owner-2")))

    assert sorted(item[0] for item in (first, second)) == [False, True]
    assert {item[1] for item in (first, second)} == {"publishing"}

    winner = "owner-1" if first[0] else "owner-2"
    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        locked = repository.refresh_pending_clean_publication_lease(
            pending.receipt_id,
            owner_token=winner,
            now=now + timedelta(seconds=2),
            lease_expires_at=now + timedelta(seconds=32),
        )
        assert locked is not None
        published = repository.mark_pending_clean_publication_published(
            pending.receipt_id,
            owner_token=winner,
            formal_artifact_id=locked.formal_artifact_id,
            now=now + timedelta(seconds=3),
        )
        session.commit()

    assert published.publication_status == "published"
    assert published.published_at == now + timedelta(seconds=3)

    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        replay = repository.claim_pending_clean_publication(
            pending.receipt_id,
            owner_token="late-follower",
            now=now + timedelta(seconds=4),
            lease_expires_at=now + timedelta(seconds=34),
        )
        session.commit()
    assert replay is not None
    assert replay.acquired is False
    assert replay.pending.publication_status == "published"
    assert replay.pending.formal_artifact_id == published.formal_artifact_id


def test_stale_publication_claim_can_be_taken_over_by_database_cas(
    migrated_engine: Engine,
) -> None:
    attempt = _attempt(idempotency_key="session-1:idem-1:fingerprint-1")
    receipt = _receipt(attempt)
    pending = _pending(receipt)
    now = pending.created_at + timedelta(seconds=1)

    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        repository.record_attempt(attempt)
        repository.record_pending_clean_receipt(pending)
        first = repository.claim_pending_clean_publication(
            pending.receipt_id,
            owner_token="owner-1",
            now=now,
            lease_expires_at=now + timedelta(seconds=1),
        )
        session.commit()
    assert first is not None
    assert first.acquired is True

    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        second = repository.claim_pending_clean_publication(
            pending.receipt_id,
            owner_token="owner-2",
            now=now + timedelta(seconds=2),
            lease_expires_at=now + timedelta(seconds=32),
        )
        session.commit()

    assert second is not None
    assert second.acquired is True
    assert second.pending.publication_owner == "owner-2"
    assert second.pending.publication_version == first.pending.publication_version + 1


def test_concurrent_same_key_attempt_get_or_create_returns_single_winner(
    migrated_engine: Engine,
) -> None:
    attempt = _attempt(idempotency_key="session-1:idem-1:fingerprint-1")

    def worker() -> MediaScanAttempt:
        with Session(migrated_engine) as session:
            repository = MediaScanAuditRepository(session)
            stored = repository.record_attempt(attempt)
            session.commit()
            return stored

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: worker(), range(2)))

    assert results == (attempt, attempt)
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(MediaScanAttemptModel)) == 1


def test_active_receipt_retry_ignores_created_at_drift_after_existing_active_commit(
    migrated_engine: Engine,
) -> None:
    attempt = _attempt(idempotency_key="session-1:idem-1:fingerprint-1")
    first = _receipt(attempt)
    retry = MediaCleanReceipt.from_attempt(
        attempt,
        receipt_id=first.receipt_id,
        receipt_hash=first.receipt_hash,
        quarantine_path=first.quarantine_path,
        quarantine_expires_at=first.quarantine_expires_at,
        created_at=first.created_at + timedelta(seconds=5),
    )

    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        repository.record_attempt(attempt)
        repository.record_pending_clean_receipt(_pending(first))
        assert repository.record_clean_receipt(first) == first
        session.commit()

    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        assert repository.record_clean_receipt(retry) == first
        session.commit()
        assert session.scalar(select(func.count()).select_from(MediaCleanReceiptModel)) == 1


def test_expired_pending_receipts_are_listed_and_deleted_without_active(
    migrated_engine: Engine,
) -> None:
    attempt = _attempt(idempotency_key="session-1:idem-1:fingerprint-1")
    receipt = _receipt(attempt)
    pending = _pending(receipt)
    now = pending.spool_expires_at + timedelta(seconds=1)

    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        repository.record_attempt(attempt)
        repository.record_pending_clean_receipt(pending)
        session.commit()

    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        assert repository.list_expired_pending_clean_receipts(now=now, limit=10) == (pending,)
        assert repository.delete_pending_clean_receipt(pending.receipt_id) is True
        assert repository.delete_pending_clean_receipt(pending.receipt_id) is False
        session.commit()

    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        assert repository.list_expired_pending_clean_receipts(now=now, limit=10) == ()


def test_expired_pending_receipt_with_active_binding_is_not_listed_for_janitor(
    migrated_engine: Engine,
) -> None:
    attempt = _attempt(idempotency_key="session-1:idem-1:fingerprint-1")
    receipt = _receipt(attempt)
    pending = _pending(receipt)
    now = pending.spool_expires_at + timedelta(seconds=1)

    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        repository.record_attempt(attempt)
        repository.record_pending_clean_receipt(pending)
        repository.record_clean_receipt(receipt)
        session.commit()

    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        assert repository.list_expired_pending_clean_receipts(now=now, limit=10) == ()


def test_idempotency_key_cannot_be_reused_for_different_attempt(migrated_engine: Engine) -> None:
    first = _attempt()
    conflicting = _attempt(attempt_id="attempt-2")
    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        repository.record_attempt(first)
        session.commit()
    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        with pytest.raises(IntegrityError):
            repository.record_attempt(conflicting)


def test_receipt_hash_is_unique_across_attempts(migrated_engine: Engine) -> None:
    first = _attempt()
    second = _attempt(attempt_id="attempt-2", idempotency_key="other-key")
    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        repository.record_attempt(first)
        repository.record_attempt(second)
        first_receipt = _receipt(first)
        second_receipt = _receipt(second, receipt_id="receipt-2")
        repository.record_pending_clean_receipt(_pending(first_receipt))
        with pytest.raises(IntegrityError):
            repository.record_pending_clean_receipt(_pending(second_receipt))


def test_receipt_requires_matching_persisted_clean_attempt(migrated_engine: Engine) -> None:
    attempt = _attempt()
    with Session(migrated_engine) as session:
        repository = MediaScanAuditRepository(session)
        with pytest.raises(IntegrityError):
            repository.record_clean_receipt(_receipt(attempt))


def test_orm_metadata_schema_matches_migration(migrated_engine: Engine) -> None:
    database_tables = set(inspect(migrated_engine).get_table_names())
    assert {"scan_attempts", "pending_clean_receipts", "clean_receipts"} <= database_tables
    assert {"scan_attempts", "pending_clean_receipts", "clean_receipts"} <= set(Base.metadata.tables)
