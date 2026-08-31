from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from focusproof.persistence.models import SpeechTranscriptionRequestModel
from focusproof.persistence.repositories import (
    ResourceSlotLease,
    SpeechAdmissionToken,
    SpeechHmacReadinessError,
    SpeechQuotaExceededError,
)
from focusproof.speech_core.errors import SpeechAdmissionError, SpeechErrorCode

from .test_session_repository import _session

KEYS = {"v2": b"active-secret", "v1": b"retained-secret"}


def _factory(uow_factory: object) -> object:
    uow_factory.configure_speech(active_hmac_key_version="v2", hmac_keys=KEYS)
    return uow_factory


def _admit(
    uow: object, key: str | None = None, fingerprint: str = "f" * 64
) -> SpeechAdmissionToken:
    return uow.speech_requests.admit(
        owner_user_id="dev-anonymous-user",
        session_id="sess_1",
        idempotency_key=key or str(uuid4()),
        request_fingerprint=fingerprint,
        lease_owner="worker-a",
    )


def test_uow_admission_hashes_key_and_returns_immutable_token(uow_factory: object) -> None:
    factory = _factory(uow_factory)
    raw_key = str(uuid4())
    with factory() as uow:
        uow.sessions.create(_session())
        token = _admit(uow, raw_key)
        uow.commit()
    with pytest.raises(FrozenInstanceError):
        token.lease_generation = 2  # type: ignore[misc]
    with factory() as uow:
        row = uow._require_session().scalar(select(SpeechTranscriptionRequestModel))
        assert row is not None
        assert row.idempotency_key_hash != raw_key
        assert len(row.idempotency_key_hash) == 64
        assert row.hmac_key_version == "v2"


def test_retained_hmac_version_detects_duplicate_after_rotation(uow_factory: object) -> None:
    uow_factory.configure_speech(
        active_hmac_key_version="v1", hmac_keys={"v1": KEYS["v1"]}
    )
    key = str(uuid4())
    with uow_factory() as uow:
        uow.sessions.create(_session())
        _admit(uow, key)
        uow.commit()
    factory = _factory(uow_factory)
    with factory() as uow, pytest.raises(SpeechAdmissionError) as caught:
        _admit(uow, key)
    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_IN_PROGRESS


def test_missing_historical_hmac_key_fails_readiness(uow_factory: object) -> None:
    uow_factory.configure_speech(
        active_hmac_key_version="v1", hmac_keys={"v1": KEYS["v1"]}
    )
    with uow_factory() as uow:
        uow.sessions.create(_session())
        _admit(uow)
        uow.commit()
    uow_factory.configure_speech(
        active_hmac_key_version="v2", hmac_keys={"v2": KEYS["v2"]}
    )
    with uow_factory() as uow, pytest.raises(SpeechHmacReadinessError):
        uow.speech_requests.assert_hmac_readiness()


def test_duplicate_semantics_and_explicit_new_key(uow_factory: object) -> None:
    factory = _factory(uow_factory)
    key = str(uuid4())
    with factory() as uow:
        uow.sessions.create(_session())
        token = _admit(uow, key)
        uow.commit()
    with factory() as uow, pytest.raises(SpeechAdmissionError) as conflict:
        _admit(uow, key, "e" * 64)
    assert conflict.value.code is SpeechErrorCode.IDEMPOTENCY_CONFLICT
    with factory() as uow, pytest.raises(SpeechAdmissionError) as active:
        _admit(uow, key)
    assert active.value.code is SpeechErrorCode.TRANSCRIPTION_IN_PROGRESS
    with factory() as uow:
        uploading = uow.speech_requests.transition(token, "uploading")
        scanning = uow.speech_requests.transition(uploading, "scanning")
        inspecting = uow.speech_requests.transition(scanning, "inspecting")
        dispatched = uow.speech_requests.mark_dispatching(inspecting)
        uow.commit()
    with factory() as uow:
        assert uow.speech_requests.finalize(dispatched, state="succeeded", latency_ms=9)
        uow.commit()
    with factory() as uow, pytest.raises(SpeechAdmissionError) as succeeded:
        _admit(uow, key)
    assert succeeded.value.code is SpeechErrorCode.TRANSCRIPTION_RESULT_UNAVAILABLE
    terminal_key = str(uuid4())
    with factory() as uow:
        terminal_token = _admit(uow, terminal_key)
        uow.commit()
    with factory() as uow:
        assert uow.speech_requests.finalize(
            terminal_token, state="failed_terminal", outcome_code="invalid_audio"
        )
        uow.commit()
    with factory() as uow, pytest.raises(SpeechAdmissionError):
        _admit(uow, terminal_key)
    with factory() as uow:
        replacement = _admit(uow, str(uuid4()))
        uow.commit()
    assert replacement.request_id != terminal_token.request_id


def test_real_entry_can_atomically_bind_request_fingerprint_during_upload(
    uow_factory: object,
) -> None:
    factory = _factory(uow_factory)
    with factory() as uow:
        uow.sessions.create(_session())
        token = uow.speech_requests.admit(
            owner_user_id="dev-anonymous-user",
            session_id="sess_1",
            idempotency_key=str(uuid4()),
            request_fingerprint=None,
            lease_owner="worker-a",
        )
        uploading = uow.speech_requests.transition(token, "uploading")
        fingerprint = "a" * 64
        uow.speech_requests.transition(
            uploading,
            "scanning",
            request_fingerprint=fingerprint,
        )
        uow.commit()

    with factory() as uow:
        row = uow._require_session().get(
            SpeechTranscriptionRequestModel, token.request_id
        )
        assert row is not None
        assert row.request_fingerprint == fingerprint


def test_session_lifetime_quota_is_charged_for_every_request(uow_factory: object) -> None:
    factory = _factory(uow_factory)
    with factory() as uow:
        uow.sessions.create(_session())
        uow.commit()
    for _ in range(20):
        with factory() as uow:
            _admit(uow)
            uow.commit()
    with factory() as uow, pytest.raises(SpeechQuotaExceededError):
        _admit(uow)


def test_slot_reconcile_claim_release_retire_and_stale_fencing(uow_factory: object) -> None:
    factory = _factory(uow_factory)
    with factory() as uow:
        uow.resource_slots.reconcile("asr", configured_count=2, config_generation=1)
        first = uow.resource_slots.claim(
            "asr", work_kind="speech", work_id="r1", lease_seconds=60
        )
        second = uow.resource_slots.claim(
            "asr", work_kind="speech", work_id="r2", lease_seconds=60
        )
        assert isinstance(first, ResourceSlotLease)
        assert isinstance(second, ResourceSlotLease)
        assert uow.resource_slots.claim(
            "asr", work_kind="speech", work_id="r3", lease_seconds=60
        ) is None
        uow.commit()
    with factory() as uow:
        uow.resource_slots.reconcile("asr", configured_count=1, config_generation=2)
        stale = ResourceSlotLease(
            first.resource_kind,
            first.slot_number,
            first.lease_owner_token,
            first.lease_generation - 1,
        )
        assert not uow.resource_slots.release(stale)
        assert uow.resource_slots.release(first)
        assert uow.resource_slots.release(second)
        assert uow.resource_slots.claim(
            "asr", work_kind="speech", work_id="r4", lease_seconds=60
        ) is not None
        assert uow.resource_slots.claim(
            "asr", work_kind="speech", work_id="r5", lease_seconds=60
        ) is None
        uow.commit()


def test_expired_request_recovery_fences_stale_token(uow_factory: object) -> None:
    factory = _factory(uow_factory)
    past = datetime.now(UTC) - timedelta(minutes=5)
    with factory() as uow:
        uow.sessions.create(_session())
        token = uow.speech_requests.admit(
            owner_user_id="dev-anonymous-user",
            session_id="sess_1",
            idempotency_key=str(uuid4()),
            request_fingerprint="f" * 64,
            lease_owner="worker-a",
            now=past,
            lease_seconds=1,
        )
        uow.commit()
    with factory() as uow:
        assert uow.speech_requests.recover_expired(now=datetime.now(UTC)) == 1
        assert not uow.speech_requests.finalize(
            token, state="failed_terminal", outcome_code="invalid_audio"
        )
        uow.commit()


def test_unconfigured_uow_exposes_fail_closed_speech_repository(
    uow_factory: object,
) -> None:
    with uow_factory() as uow, pytest.raises(SpeechHmacReadinessError):
        uow.speech_requests.assert_hmac_readiness()
