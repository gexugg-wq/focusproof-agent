from datetime import UTC, datetime
from typing import Any, cast

import pytest
from openhands.sdk import Action as OpenHandsAction
from openhands.sdk import Observation as OpenHandsObservation
from pydantic import ValidationError

from focusproof.openhands_runtime.tools.verification import (
    EvidenceReferenceAction,
    VerificationObservation,
)


def test_verification_contract_uses_native_openhands_types() -> None:
    assert issubclass(EvidenceReferenceAction, OpenHandsAction)
    assert issubclass(VerificationObservation, OpenHandsObservation)


def test_observation_has_facts_without_score_or_learning_verdict() -> None:
    fields = set(VerificationObservation.model_fields)
    assert {"evidence_id", "capability", "status", "facts", "source_refs"} <= fields
    assert fields.isdisjoint(
        {"score", "final_score", "learning_status", "verified_learning"}
    )


def test_observation_timestamps_are_timezone_aware() -> None:
    started = datetime.now(UTC)
    completed = datetime.now(UTC)
    observation = VerificationObservation.from_text(
        "text facts",
        evidence_id="ev_1",
        capability="text",
        status="success",
        facts={"word_count": 12},
        weak_signals=[],
        source_refs=["ev_1"],
        verifier_version="1",
        started_at=started,
        completed_at=completed,
    )
    assert observation.started_at.tzinfo is UTC
    assert observation.completed_at.tzinfo is UTC


@pytest.mark.parametrize(
    "reserved_key",
    [
        "score",
        "final_score",
        "learning_status",
        "verified_learning",
        "honest",
        "dishonest",
        "fake_learning",
    ],
)
def test_observation_rejects_nested_reserved_verdict_keys(
    reserved_key: str,
) -> None:
    now = datetime.now(UTC)

    with pytest.raises(ValidationError, match="reserved verdict field"):
        VerificationObservation.from_text(
            "unsafe",
            evidence_id="ev_unsafe",
            capability="synthetic",
            status="success",
            facts={"outer": [{"inner": {reserved_key: "untrusted"}}]},
            weak_signals=[],
            source_refs=["ev_unsafe"],
            verifier_version="test",
            started_at=now,
            completed_at=now,
        )


def test_observation_checks_raw_weak_signals_before_string_coercion() -> None:
    now = datetime.now(UTC)
    raw_weak_signals = cast(
        Any,
        [{"nested": [{"verified_learning": True}]}],
    )

    with pytest.raises(ValidationError, match="reserved verdict field"):
        VerificationObservation.from_text(
            "unsafe",
            evidence_id="ev_unsafe",
            capability="synthetic",
            status="success",
            facts={},
            weak_signals=raw_weak_signals,
            source_refs=["ev_unsafe"],
            verifier_version="test",
            started_at=now,
            completed_at=now,
        )
