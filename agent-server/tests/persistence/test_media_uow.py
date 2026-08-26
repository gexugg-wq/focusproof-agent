from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from dataclasses import replace

import pytest
from sqlalchemy import delete, func, select, text

from focusproof.media_core.models import (
    FinalizeMediaRequest,
    FinalizeMediaOutcome,
    IngestedEvidenceResult,
    MediaCleanReceipt,
    MediaLease,
    MediaReferenceIntent,
    MediaReservationRequest,
    MediaScanAttempt,
    PendingCleanReceipt,
    ScanResultKind,
    StagedMediaObject,
)
from focusproof.persistence.models import (
    EvidenceModel,
    LearningSessionModel,
    MediaArtifactModel,
    MediaIngestionReservationModel,
)
from focusproof.persistence.repositories import (
    IdempotencyConflictError,
    MediaAuthorizationError,
    MediaLeaseStateError,
    MediaQuotaExceededError,
    StoredEvidence,
)
from focusproof.persistence.unit_of_work import UnitOfWorkFactory

from .test_session_repository import _session

MiB = 1024 * 1024


def _create_session(factory: UnitOfWorkFactory, session_id: str = "sess_1") -> None:
    with factory() as uow:
        uow.sessions.create(_session(session_id).model_copy(update={"owner_user_id": "owner"}))
        uow.commit()


def _reserve(factory: UnitOfWorkFactory, key: str, session_id: str = "sess_1") -> MediaLease:
    with factory() as uow:
        lease = uow.media.reserve(MediaReservationRequest("owner", session_id, key, f"fp-{key}"))
        uow.commit()
        return lease


def _request(lease: MediaLease, digest: str = "a" * 64, size: int = 10) -> FinalizeMediaRequest:
    return FinalizeMediaRequest(
        lease=lease,
        staged_media_item_id=lease.media_item_id,
        opaque_object_key=f"private-{lease.media_item_id}",
        manifest_id=f"manifest-{lease.media_item_id}",
        media_type="application/test",
        normalized_sha256=digest,
        normalized_byte_size=size,
        learner_explanation="specific explanation",
        attributes={"safe": True, "width": 1, "height": 1},
    )


def _finalize(factory: UnitOfWorkFactory, request: FinalizeMediaRequest) -> FinalizeMediaOutcome:
    with factory() as uow:
        outcome = uow.media.finalize(request)
        uow.commit()
        return outcome


def _confirm(factory: UnitOfWorkFactory, outcome: FinalizeMediaOutcome) -> IngestedEvidenceResult:
    with factory() as uow:
        result = uow.media.confirm_reference(outcome.reference_intent)
        uow.commit()
        return result


def _record_clean_receipt(
    factory: UnitOfWorkFactory,
    outcome: FinalizeMediaOutcome,
    key: str,
    digest: str,
    *,
    session_id: str = "sess_1",
) -> None:
    now = datetime.now(UTC)
    attempt = MediaScanAttempt(
        attempt_id=f"attempt-{key}",
        artifact_sha256=digest,
        content_type=outcome.result.media_type,
        scanner_backend="clamd",
        definitions_version="daily-1",
        definitions_fresh_at=now - timedelta(seconds=30),
        definitions_age_seconds=30,
        max_bytes=10_000_000,
        max_concurrent_scans=4,
        deadline_ms=5_000,
        socket_timeout_ms=2_000,
        scan_result=ScanResultKind.CLEAN,
        rejection_code=None,
        rejection_detail=None,
        started_at=now,
        finished_at=now + timedelta(milliseconds=40),
        idempotency_key=f"{session_id}:{key}:fp-{key}",
    )
    receipt_id = f"receipt-{key}"
    receipt_hash = sha256(receipt_id.encode("utf-8")).hexdigest()
    expires_at = attempt.finished_at + timedelta(hours=1)
    with factory() as uow:
        stored_attempt = uow.scan_audit.record_attempt(attempt)
        uow.scan_audit.record_pending_clean_receipt(
            PendingCleanReceipt(
                receipt_id=receipt_id,
                attempt_id=stored_attempt.attempt_id,
                artifact_sha256=digest,
                receipt_hash=receipt_hash,
                spool_token="c" * 32,
                spool_byte_size=outcome.result.byte_size,
                spool_sha256=digest,
                spool_expires_at=expires_at,
                quarantine_expires_at=expires_at,
                created_at=attempt.finished_at,
            )
        )
        uow.scan_audit.record_clean_receipt(
            MediaCleanReceipt.from_attempt(
                stored_attempt,
                receipt_id=receipt_id,
                receipt_hash=receipt_hash,
                quarantine_path="quarantine/private-object",
                quarantine_expires_at=expires_at,
                created_at=attempt.finished_at,
            )
        )
        uow.commit()


def test_adopted_artifact_is_invisible_until_confirmed(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    outcome = _finalize(uow_factory, _request(_reserve(uow_factory, "new")))

    assert outcome.reference_intent.action == "MARK_REFERENCED"
    assert outcome.evidence_visible is False
    assert outcome.result.artifact_ref == (f"focusproof-artifact://{outcome.result.media_item_id}")
    with uow_factory() as uow:
        session = uow._require_session()
        assert session.scalar(select(func.count(EvidenceModel.evidence_id))) == 0
        pending = uow.media.list_pending_reference_outcomes(10)
    assert pending == (outcome,)

    assert _confirm(uow_factory, outcome) == outcome.result
    assert _confirm(uow_factory, outcome) == outcome.result
    with uow_factory() as uow:
        replay = uow.media.find_idempotent_outcome("owner", "sess_1", "new", "fp-new")
        count = uow._require_session().scalar(select(func.count(EvidenceModel.evidence_id)))
        uow.commit()
    assert replay is not None
    assert replay.reference_intent.action == "NOOP"
    assert replay.evidence_visible is True
    assert count == 1


def test_media_message_artifact_query_enforces_owner_binding_and_uses_formal_id(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    payload_digest = "1" * 64
    outcome = _finalize(
        uow_factory,
        _request(_reserve(uow_factory, "message"), payload_digest, 17),
    )
    result = _confirm(uow_factory, outcome)
    _record_clean_receipt(uow_factory, outcome, "message", payload_digest)
    with uow_factory() as uow:
        session = uow._require_session()
        model = session.get(EvidenceModel, result.evidence_id)
        assert model is not None
        model.metadata_json = {**model.metadata_json, "artifact_ref": "forged://value"}
        uow.commit()
    with uow_factory() as uow:
        facts = uow.evidence.get_media_message_artifact("owner", "sess_1", result.evidence_id)
    assert facts.model_dump() == {
        "evidence_id": result.evidence_id,
        "receipt_id": "receipt-message",
        "attempt_id": "attempt-message",
        "scan_result": "clean",
        "artifact_ref": f"focusproof-artifact://{result.media_item_id}",
        "artifact_sha256": payload_digest,
        "opaque_object_key": f"private-{result.media_item_id}",
        "media_type": "application/test",
        "normalized_sha256": payload_digest,
        "byte_size": 17,
        "width": 1,
        "height": 1,
    }

    with uow_factory() as uow:
        with pytest.raises(PermissionError) as denied:
            uow.evidence.get_media_message_artifact("other-owner", "sess_1", result.evidence_id)
    message = str(denied.value)
    assert "owner" not in message
    assert result.media_item_id not in message
    assert f"private-{result.media_item_id}" not in message


def test_media_message_artifact_query_fails_closed_for_missing_or_unready_relations(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    with uow_factory() as uow:
        uow.evidence.add(
            StoredEvidence(
                evidence_id="ev-text",
                session_id="sess_1",
                evidence_type="text",
                content_hash="literal",
                text_content="text",
                source_url=None,
                metadata={},
                conversation_synced_at=None,
                created_at=datetime.now(UTC),
            )
        )
        uow.commit()
    pending = _finalize(uow_factory, _request(_reserve(uow_factory, "pending")))
    _confirm(uow_factory, pending)
    with uow_factory() as uow:
        artifact = uow._require_session().get(MediaArtifactModel, pending.result.media_item_id)
        assert artifact is not None
        artifact.state = "PENDING_REFERENCE"
        uow.commit()

    with uow_factory() as uow:
        for session_id, evidence_id, match in (
            ("missing-session", "ev-text", "session"),
            ("sess_1", "missing-evidence", "evidence"),
            ("sess_1", "ev-text", "artifact"),
            ("sess_1", pending.result.evidence_id, "consumable"),
        ):
            with pytest.raises(KeyError, match=match):
                uow.evidence.get_media_message_artifact("owner", session_id, evidence_id)


def test_media_message_artifact_query_fails_closed_when_artifact_row_is_missing(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    outcome = _finalize(uow_factory, _request(_reserve(uow_factory, "corrupt")))
    result = _confirm(uow_factory, outcome)
    with uow_factory() as uow:
        session = uow._require_session()
        session.execute(text("PRAGMA foreign_keys=OFF"))
        session.execute(
            delete(MediaArtifactModel).where(
                MediaArtifactModel.media_item_id == result.media_item_id
            )
        )
        session.commit()
        session.execute(text("PRAGMA foreign_keys=ON"))

    with uow_factory() as uow:
        with pytest.raises(KeyError, match="artifact"):
            uow.evidence.get_media_message_artifact("owner", "sess_1", result.evidence_id)


def test_referenced_artifact_reuse_aborts_staged_and_creates_evidence(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    first = _finalize(uow_factory, _request(_reserve(uow_factory, "first")))
    _confirm(uow_factory, first)
    second = _finalize(uow_factory, _request(_reserve(uow_factory, "second")))

    assert second.reference_intent.action == "ABORT_STAGED"
    assert second.evidence_visible is True
    assert second.result.media_item_id == first.result.media_item_id
    assert second.result.evidence_id != first.result.evidence_id
    assert second.reference_intent.staged.media_item_id != second.result.media_item_id
    with uow_factory() as uow:
        replay = uow.media.find_idempotent_outcome("owner", "sess_1", "second", "fp-second")
        uow.commit()
    assert replay is not None
    assert replay.reference_intent.action == "ABORT_STAGED"
    assert replay.evidence_visible is True
    with uow_factory() as uow:
        repeated = uow.media.find_idempotent_outcome("owner", "sess_1", "second", "fp-second")
        uow.commit()
    assert repeated == replay


def test_follower_pending_replay_completes_after_canonical_confirmation(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory, "sess_1")
    _create_session(uow_factory, "sess_2")
    winner = _finalize(uow_factory, _request(_reserve(uow_factory, "winner", "sess_1"), "b" * 64))
    follower = _finalize(
        uow_factory, _request(_reserve(uow_factory, "follower", "sess_2"), "b" * 64)
    )
    assert follower.reference_intent.action == "ABORT_STAGED"
    assert follower.evidence_visible is False
    with uow_factory() as uow:
        assert uow.media.list_pending_reference_outcomes(10) == (winner,)
        still_pending = uow.media.find_idempotent_outcome(
            "owner", "sess_2", "follower", "fp-follower"
        )
        uow.commit()
    assert still_pending == follower

    _confirm(uow_factory, winner)
    with uow_factory() as uow:
        completed = uow.media.find_idempotent_outcome("owner", "sess_2", "follower", "fp-follower")
        uow.commit()
    assert completed is not None
    assert completed.reference_intent.action == "NOOP"
    assert completed.evidence_visible is True
    with uow_factory() as uow:
        session = uow._require_session()
        assert session.scalar(select(func.count(MediaArtifactModel.media_item_id))) == 1
        assert session.scalar(select(func.count(EvidenceModel.evidence_id))) == 2


def test_completed_adopted_rejects_forged_confirm_intent(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    adopted = _finalize(uow_factory, _request(_reserve(uow_factory, "adopted")))
    _confirm(uow_factory, adopted)
    forged = MediaReferenceIntent(
        staged=StagedMediaObject(
            media_item_id="media_forged",
            reservation_id=adopted.reference_intent.staged.reservation_id,
            opaque_object_key="forged",
            manifest_id="forged",
        ),
        action="MARK_REFERENCED",
    )
    with uow_factory() as uow, pytest.raises(MediaLeaseStateError):
        uow.media.confirm_reference(forged)
    with uow_factory() as uow:
        replay = uow.media.find_idempotent_outcome("owner", "sess_1", "adopted", "fp-adopted")
        uow.commit()
    assert replay is not None and replay.reference_intent.action == "NOOP"


def test_pending_reservations_hold_item_and_distinct_byte_quota(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    _finalize(
        uow_factory,
        _request(_reserve(uow_factory, "large"), "c" * 64, 15 * MiB),
    )
    with pytest.raises(MediaQuotaExceededError):
        _finalize(
            uow_factory,
            _request(_reserve(uow_factory, "overflow"), "d" * 64, 6 * MiB),
        )
    _reserve(uow_factory, "three")
    _reserve(uow_factory, "four")
    with pytest.raises(MediaQuotaExceededError):
        _reserve(uow_factory, "five")


def test_finalize_expiry_is_persisted_by_followup_reject(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    lease = _reserve(uow_factory, "expired")
    with uow_factory() as uow:
        reservation = uow._require_session().get(
            MediaIngestionReservationModel, lease.reservation_id
        )
        assert reservation is not None
        reservation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        uow.commit()
    with pytest.raises(MediaLeaseStateError):
        _finalize(uow_factory, _request(lease))
    with uow_factory() as uow:
        uow.media.reject(lease, "expired during finalize")
        uow.commit()
    with uow_factory() as uow:
        reservation = uow._require_session().get(
            MediaIngestionReservationModel, lease.reservation_id
        )
        assert reservation is not None and reservation.status == "EXPIRED"


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("reservation_id", "res_forged"),
        ("idempotency_key", "forged-key"),
        ("slot", 3),
    ],
)
def test_finalize_rejects_incompletely_bound_lease_identity(
    uow_factory: UnitOfWorkFactory, field: str, forged_value: object
) -> None:
    _create_session(uow_factory)
    lease = _reserve(uow_factory, "identity")
    if field == "reservation_id":
        forged = replace(lease, reservation_id=str(forged_value))
    elif field == "idempotency_key":
        forged = replace(lease, idempotency_key=str(forged_value))
    else:
        assert isinstance(forged_value, int)
        forged = replace(lease, slot=forged_value)
    with pytest.raises(MediaLeaseStateError):
        _finalize(uow_factory, _request(forged))
    with uow_factory() as uow:
        reservation = uow._require_session().get(
            MediaIngestionReservationModel, lease.reservation_id
        )
        assert reservation is not None and reservation.status == "ACTIVE"


def test_finalize_rejects_staged_media_item_identity_before_writes(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    lease = _reserve(uow_factory, "staged-identity")
    forged = replace(_request(lease), staged_media_item_id="media_forged")

    with pytest.raises(MediaLeaseStateError):
        _finalize(uow_factory, forged)

    with uow_factory() as uow:
        session = uow._require_session()
        reservation = session.get(MediaIngestionReservationModel, lease.reservation_id)
        assert session.scalar(select(func.count(MediaArtifactModel.media_item_id))) == 0
        assert session.scalar(select(func.count(EvidenceModel.evidence_id))) == 0
        assert reservation is not None
        assert reservation.status == "ACTIVE"
        assert reservation.active is True
        assert reservation.canonical_artifact_id is None
        assert reservation.evidence_id is None
        assert reservation.result_json is None


def test_reject_requires_complete_lease_identity(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    lease = _reserve(uow_factory, "reject-identity")
    forged = replace(lease, idempotency_key="forged-key")
    with uow_factory() as uow, pytest.raises(MediaLeaseStateError):
        uow.media.reject(forged, "forged")
    with uow_factory() as uow:
        reservation = uow._require_session().get(
            MediaIngestionReservationModel, lease.reservation_id
        )
        assert reservation is not None and reservation.status == "ACTIVE"


def test_pending_finalize_replay_requires_complete_lease_identity(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    lease = _reserve(uow_factory, "pending-identity")
    _finalize(uow_factory, _request(lease))
    with pytest.raises(MediaLeaseStateError):
        _finalize(uow_factory, _request(replace(lease, slot=lease.slot + 1)))


def test_terminal_reject_requires_complete_lease_identity(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    lease = _reserve(uow_factory, "terminal-identity")
    outcome = _finalize(uow_factory, _request(lease))
    _confirm(uow_factory, outcome)
    with uow_factory() as uow, pytest.raises(MediaLeaseStateError):
        uow.media.reject(replace(lease, idempotency_key="forged"), "forged")
    with uow_factory() as uow:
        uow.media.reject(lease, "idempotent")
        uow.commit()


def test_idempotent_replay_rejects_owner_mismatch(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    _finalize(uow_factory, _request(_reserve(uow_factory, "replay-owner")))

    with uow_factory() as uow, pytest.raises(MediaAuthorizationError):
        uow.media.find_idempotent_outcome(
            "other-owner",
            "sess_1",
            "replay-owner",
            "fp-replay-owner",
        )


def test_confirm_reference_rejects_session_owner_drift(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    pending = _finalize(uow_factory, _request(_reserve(uow_factory, "confirm-owner")))
    with uow_factory() as uow:
        session = uow._require_session().get(LearningSessionModel, "sess_1")
        assert session is not None
        session.owner_user_id = "other-owner"
        uow.commit()

    with uow_factory() as uow, pytest.raises(MediaAuthorizationError):
        uow.media.confirm_reference(pending.reference_intent)

    with uow_factory() as uow:
        reservation = uow._require_session().get(
            MediaIngestionReservationModel,
            pending.reference_intent.staged.reservation_id,
        )
        assert reservation is not None
        assert reservation.status == "PENDING_REFERENCE"


def test_reject_rejects_lease_owner_mismatch_before_terminal_noop(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    lease = _reserve(uow_factory, "reject-owner")
    pending = _finalize(uow_factory, _request(lease))
    _confirm(uow_factory, pending)

    with uow_factory() as uow, pytest.raises(MediaAuthorizationError):
        uow.media.reject(replace(lease, owner_id="other-owner"), "forged")


def test_same_key_different_fingerprint_and_canonical_mismatch_fail_closed(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory)
    _reserve(uow_factory, "key")
    with uow_factory() as uow, pytest.raises(IdempotencyConflictError):
        uow.media.find_idempotent_outcome("owner", "sess_1", "key", "different")
    canonical = _finalize(uow_factory, _request(_reserve(uow_factory, "canonical"), "e" * 64, 10))
    _confirm(uow_factory, canonical)
    with pytest.raises(MediaLeaseStateError):
        _finalize(
            uow_factory,
            _request(_reserve(uow_factory, "mismatch"), "e" * 64, 11),
        )


def test_cross_session_reuse_counts_bytes_and_survives_creator_deletion(
    uow_factory: UnitOfWorkFactory,
) -> None:
    _create_session(uow_factory, "sess_1")
    _create_session(uow_factory, "sess_2")
    first = _finalize(
        uow_factory,
        _request(_reserve(uow_factory, "first", "sess_1"), "f" * 64, 15 * MiB),
    )
    _confirm(uow_factory, first)
    second = _finalize(
        uow_factory,
        _request(_reserve(uow_factory, "second", "sess_2"), "f" * 64, 15 * MiB),
    )
    assert second.evidence_visible is True
    with pytest.raises(MediaQuotaExceededError):
        _finalize(
            uow_factory,
            _request(_reserve(uow_factory, "overflow", "sess_2"), "1" * 64, 6 * MiB),
        )
    with uow_factory() as uow:
        uow._require_session().execute(
            delete(LearningSessionModel).where(LearningSessionModel.session_id == "sess_1")
        )
        uow.commit()
    with uow_factory() as uow:
        session = uow._require_session()
        artifact = session.get(MediaArtifactModel, first.result.media_item_id)
        evidence = session.scalar(
            select(EvidenceModel).where(EvidenceModel.evidence_id == second.result.evidence_id)
        )
        assert artifact is not None and artifact.creator_reservation_id is None
        assert evidence is not None and evidence.artifact_id == artifact.media_item_id
