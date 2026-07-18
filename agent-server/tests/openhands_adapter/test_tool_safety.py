import pytest
from pydantic import BaseModel, ValidationError

from focusproof.openhands_adapter.errors import UnsafeOpenHandsToolError
from focusproof.openhands_adapter.tools import assert_openhands_tool_allowed, is_openhands_tool_allowed
from focusproof.openhands_runtime.tools.evidence_verification import (
    EvidenceVerificationAction,
)
from focusproof.openhands_runtime.tools.learner_input import LearnerInputAction
from focusproof.openhands_runtime.tools.review_draft import ReviewDraftAction
from focusproof.openhands_runtime.tools.verification import EvidenceReferenceAction
from focusproof.runtime.actions import Action as ProductAction


def test_terminal_tool_is_disabled_by_default() -> None:
    assert is_openhands_tool_allowed("TerminalTool") is False


def test_file_editor_tool_is_disabled_by_default() -> None:
    assert is_openhands_tool_allowed("FileEditorTool") is False


def test_unsafe_tool_request_raises_clear_error() -> None:
    with pytest.raises(UnsafeOpenHandsToolError) as exc_info:
        assert_openhands_tool_allowed("TerminalTool")

    assert "TerminalTool" in str(exc_info.value)


_ACTION_CASES = (
    (EvidenceReferenceAction, {"evidence_id": "ev_safe"}),
    (EvidenceVerificationAction, {"evidence_id": "ev_safe"}),
    (
        LearnerInputAction,
        {
            "question": "What did you learn?",
            "reason": "Check understanding",
            "requested_evidence_type": "text",
        },
    ),
    (
        ReviewDraftAction,
        {
            "credibility_findings": ["Source is attributable"],
            "understanding_findings": ["Explanation is specific"],
            "contradictions": [],
            "recommended_next_step": "Compare another source",
            "confidence": 0.8,
        },
    ),
    (ProductAction, {"type": "finish_review"}),
)


@pytest.mark.parametrize(("action_type", "valid_payload"), _ACTION_CASES)
@pytest.mark.parametrize(
    "identity_field",
    ["principal_id", "user_id", "owner", "session_id"],
)
def test_focusproof_action_models_reject_identity_extras(
    action_type: type[BaseModel],
    valid_payload: dict[str, object],
    identity_field: str,
) -> None:
    with pytest.raises(ValidationError):
        action_type.model_validate(
            {**valid_payload, identity_field: "attacker-controlled-principal"}
        )


def test_product_action_still_accepts_its_complete_legal_payload() -> None:
    action = ProductAction.model_validate(
        {
            "type": "verify_evidence",
            "reason": "Check the submitted source",
            "relatedEvidenceIds": ["ev_safe"],
            "toolName": "focusproof_text_evidence_verification",
            "input": {"text": "ordinary tool payload", "owner": "content-only"},
            "evidenceIds": ["ev_safe"],
        }
    )

    assert action.type == "verify_evidence"
    assert action.input == {"text": "ordinary tool payload", "owner": "content-only"}
