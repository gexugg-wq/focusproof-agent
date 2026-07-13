from focusproof.openhands_runtime.tools.verification import EvidenceReferenceAction
from focusproof.runtime.evidence import Evidence


class RecordingRepository:
    def __init__(self, stored: Evidence) -> None:
        self.stored = stored
        self.requested: list[tuple[str, str]] = []

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        self.requested.append((session_id, evidence_id))
        if evidence_id != self.stored.evidenceId:
            raise KeyError(evidence_id)
        return self.stored


def evidence(
    evidence_id: str,
    evidence_type: str,
    *,
    text: str | None = None,
    source_url: str | None = None,
) -> Evidence:
    return Evidence(
        evidenceId=evidence_id,
        evidenceType=evidence_type,
        contentHash=f"sha256:{evidence_id}",
        textContent=text,
        sourceUrl=source_url,
    )


def test_text_executor_reads_authoritative_evidence_by_reference() -> None:
    from focusproof.openhands_runtime.tools.text_evidence import (
        TextEvidenceVerificationExecutor,
    )

    repository = RecordingRepository(
        evidence(
            "ev_text",
            "text",
            text="A concrete example explains event replay.",
        )
    )
    executor = TextEvidenceVerificationExecutor(repository, "sess_1")
    result = executor(EvidenceReferenceAction(evidence_id="ev_text"))
    assert repository.requested == [("sess_1", "ev_text")]
    assert result.status == "success"
    assert result.evidence_id == "ev_text"
    assert result.capability == "text"


def test_generic_short_text_returns_weak_signals_without_verdict() -> None:
    from focusproof.openhands_runtime.tools.text_evidence import (
        TextEvidenceVerificationExecutor,
    )

    repository = RecordingRepository(
        evidence("ev_weak", "text", text="I learned a lot.")
    )
    result = TextEvidenceVerificationExecutor(repository, "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_weak")
    )
    assert "text_too_short" in result.weak_signals
    assert "generic_learning_claim" in result.weak_signals
    assert "score" not in result.model_dump()


def test_non_text_evidence_is_unsupported() -> None:
    from focusproof.openhands_runtime.tools.text_evidence import (
        TextEvidenceVerificationExecutor,
    )

    repository = RecordingRepository(
        evidence("ev_url", "url", source_url="https://example.com")
    )
    result = TextEvidenceVerificationExecutor(repository, "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_url")
    )
    assert result.status == "unsupported"
    assert result.error_code == "evidence_type_unsupported"


def test_missing_evidence_returns_safe_failed_observation() -> None:
    from focusproof.openhands_runtime.tools.text_evidence import (
        TextEvidenceVerificationExecutor,
    )

    repository = RecordingRepository(evidence("ev_known", "text", text="Known"))
    result = TextEvidenceVerificationExecutor(repository, "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_missing")
    )
    assert result.status == "failed"
    assert result.error_code == "evidence_not_found"
    assert result.safe_error_message == "Evidence was not found."
    assert "ev_missing" not in result.content


def test_text_facts_include_structure_examples_and_source_hash() -> None:
    from focusproof.openhands_runtime.tools.text_evidence import (
        TextEvidenceVerificationExecutor,
    )

    repository = RecordingRepository(
        evidence(
            "ev_structured",
            "text",
            text=(
                "## Replay example\n1. Append the event.\n2. Rebuild the view because "
                "each stored fact remains immutable."
            ),
        )
    )
    result = TextEvidenceVerificationExecutor(repository, "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_structured")
    )
    assert result.facts == {
        "has_text": True,
        "character_count": 102,
        "word_count": 17,
        "has_concrete_example": True,
        "has_structured_output": True,
        "content_hash": "sha256:ev_structured",
    }
    assert result.source_refs == ["ev_structured", "sha256:ev_structured"]


def test_text_tool_accepts_only_evidence_reference_and_is_read_only() -> None:
    from focusproof.openhands_runtime.tools.text_evidence import (
        FocusProofTextEvidenceVerificationTool,
    )

    assert set(EvidenceReferenceAction.model_fields) == {"evidence_id"}
    annotations = FocusProofTextEvidenceVerificationTool.annotations_for_focusproof()
    assert annotations.readOnlyHint is True
    assert annotations.destructiveHint is False
    assert annotations.idempotentHint is True
    assert annotations.openWorldHint is False
