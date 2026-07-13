from pathlib import Path
from uuid import uuid4

import pytest

from focusproof.openhands_adapter.llm_config import get_llm_config_status
from focusproof.openhands_runtime.manager import ConversationManager
from focusproof.runtime.event_log import InMemoryEventLog
from focusproof.runtime.evidence import Evidence, LearningGoal


class RealLLMEvidenceRepository:
    def __init__(self, evidence: Evidence) -> None:
        self._evidence = evidence

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        del session_id
        if evidence_id != self._evidence.evidenceId:
            raise KeyError(evidence_id)
        return self._evidence.model_copy(deep=True)


@pytest.mark.real_llm
def test_real_llm_runs_local_conversation_and_native_tool_flow(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    if request.config.option.markexpr != "real_llm":
        pytest.skip("select explicitly with -m real_llm")
    project_root = Path(__file__).resolve().parents[3]
    if not get_llm_config_status(project_root)["canBuildConfig"]:
        pytest.skip("real LLM configuration unavailable")

    evidence = Evidence(
        evidenceId="ev_real_llm",
        evidenceType="text",
        contentHash="sha256:real-llm-test",
        textContent=(
            "Append-only events preserve facts, while replay derives the current view "
            "without mutating prior records."
        ),
    )
    manager = ConversationManager(
        repository=RealLLMEvidenceRepository(evidence),
        audit_log=InMemoryEventLog(),
        project_root=project_root,
        data_dir=tmp_path / "real-llm-runtime",
    )
    session_id = f"sess_real_llm_{uuid4().hex}"
    manager.create(
        session_id,
        LearningGoal(
            domain="general",
            title="Understand event replay",
            goal="Explain why append-only replay is auditable.",
        ),
    )
    manager.send_evidence(session_id, evidence)
    try:
        result = manager.run_review(session_id)

        assert result.conversationMode == "openhands-local-real"
        assert result.usedOpenHandsConversation is True
        assert result.messageEventsCount >= 2
        assert result.actionEventsCount >= 1
        assert result.observationEventsCount >= 1
        assert result.reviewStatus in {"completed", "awaiting_user"}
    finally:
        manager.close(session_id)
