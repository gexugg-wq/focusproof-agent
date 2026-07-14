from focusproof.runtime.evidence import Evidence
from openhands.sdk import Action as OpenHandsAction
from openhands.sdk import Observation as OpenHandsObservation


class RecordingEvidenceRepository:
    def __init__(self, evidence: Evidence) -> None:
        self.evidence = evidence
        self.requested: list[tuple[str, str]] = []

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        self.requested.append((session_id, evidence_id))
        assert evidence_id == self.evidence.evidenceId
        return self.evidence


def test_verification_executor_loads_authoritative_evidence_by_id() -> None:
    from focusproof.openhands_runtime.tools.text_evidence import (
        TextEvidenceVerificationExecutor,
    )
    from focusproof.openhands_runtime.tools.verification import (
        EvidenceReferenceAction,
    )

    evidence = Evidence(
        evidenceId="ev_1",
        evidenceType="text",
        contentHash="sha256:test",
        textContent="Append-only events replay into a current immutable learning view.",
    )
    repository = RecordingEvidenceRepository(evidence)
    executor = TextEvidenceVerificationExecutor(repository, "sess_1")

    observation = executor(EvidenceReferenceAction(evidence_id="ev_1"))

    assert observation.evidence_id == "ev_1"
    assert observation.capability == "text"
    assert observation.source_refs == ["ev_1", "sha256:test"]
    assert repository.requested == [("sess_1", "ev_1")]


def test_focusproof_tool_models_are_native_openhands_types() -> None:
    from focusproof.openhands_runtime.tools.learner_input import (
        LearnerInputAction,
        LearnerInputObservation,
    )
    from focusproof.openhands_runtime.tools.review_draft import (
        ReviewDraftAction,
        ReviewDraftObservation,
    )
    from focusproof.openhands_runtime.tools.verification import (
        EvidenceReferenceAction,
        VerificationObservation,
    )

    assert issubclass(EvidenceReferenceAction, OpenHandsAction)
    assert issubclass(VerificationObservation, OpenHandsObservation)
    assert issubclass(LearnerInputAction, OpenHandsAction)
    assert issubclass(LearnerInputObservation, OpenHandsObservation)
    assert issubclass(ReviewDraftAction, OpenHandsAction)
    assert issubclass(ReviewDraftObservation, OpenHandsObservation)


def test_focusproof_tools_are_declared_read_only() -> None:
    from focusproof.openhands_runtime.tools.learner_input import FocusProofLearnerInputTool
    from focusproof.openhands_runtime.tools.review_draft import FocusProofReviewDraftTool
    from focusproof.openhands_runtime.tools.text_evidence import (
        FocusProofTextEvidenceVerificationTool,
    )

    for tool_class in (
        FocusProofTextEvidenceVerificationTool,
        FocusProofLearnerInputTool,
        FocusProofReviewDraftTool,
    ):
        annotations = tool_class.annotations_for_focusproof()
        assert annotations.readOnlyHint is True
        assert annotations.destructiveHint is False
        assert annotations.idempotentHint is True
        assert annotations.openWorldHint is False


def test_compatibility_executor_redacts_url_before_native_observation() -> None:
    from focusproof.openhands_runtime.tools.evidence_verification import (
        EvidenceVerificationAction,
        EvidenceVerificationExecutor,
    )

    source_url = (
        "https://credential-user:credential-password@example.com:8443/"
        "private/legacy-secret?token=query-secret#fragment-secret"
    )
    repository = RecordingEvidenceRepository(
        Evidence(
            evidenceId="ev_legacy_url",
            evidenceType="url",
            contentHash="sha256:legacy-url",
            sourceUrl=source_url,
        )
    )

    observation = EvidenceVerificationExecutor(repository, "sess_1")(
        EvidenceVerificationAction(evidence_id="ev_legacy_url")
    )

    serialized = observation.model_dump_json()
    assert observation.source_refs[0] == "ev_legacy_url"
    assert observation.source_refs[1].startswith("url-sha256:")
    for secret in (
        "credential-user",
        "credential-password",
        "private",
        "legacy-secret",
        "query-secret",
        "fragment-secret",
    ):
        assert secret not in serialized
    assert repository.evidence.sourceUrl == source_url
