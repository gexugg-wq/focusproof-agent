import pytest

from focusproof.openhands_runtime.capabilities import (
    VerificationCapabilityRegistry,
    build_builtin_capabilities,
)
from focusproof.openhands_runtime.tool_assembler import SessionToolAssembler
from focusproof.runtime.evidence import Evidence


class BoundRepository:
    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        raise KeyError((session_id, evidence_id))


def assembler() -> SessionToolAssembler:
    return SessionToolAssembler(
        VerificationCapabilityRegistry(build_builtin_capabilities())
    )


def test_general_session_gets_control_and_general_verification_tools() -> None:
    tools = assembler().assemble(
        "sess_1", "general", None, compatibility_mode=True
    )
    assert [tool.name for tool in tools] == [
        "FocusProofLearnerInputTool",
        "FocusProofReviewDraftTool",
        "FocusProofTextEvidenceVerificationTool",
        "FocusProofUrlEvidenceVerificationTool",
    ]


def test_session_without_evidence_types_gets_allowlisted_general_verifiers() -> None:
    tools = assembler().assemble(
        "sess_1", "general", set(), compatibility_mode=True
    )
    assert [tool.name for tool in tools] == [
        "FocusProofLearnerInputTool",
        "FocusProofReviewDraftTool",
        "FocusProofTextEvidenceVerificationTool",
        "FocusProofUrlEvidenceVerificationTool",
    ]


def test_known_text_evidence_narrows_general_verifiers() -> None:
    tools = assembler().assemble(
        "sess_1", "general", {"text"}, compatibility_mode=True
    )
    assert "FocusProofTextEvidenceVerificationTool" in {tool.name for tool in tools}
    assert "FocusProofUrlEvidenceVerificationTool" not in {tool.name for tool in tools}


def test_forbidden_default_tools_are_never_assembled() -> None:
    names = {
        tool.name.lower()
        for tool in assembler().assemble(
            "sess_1", "general", None, compatibility_mode=True
        )
    }
    assert names.isdisjoint(
        {"terminaltool", "fileeditortool", "browsertool", "applypatchtool"}
    )


def test_only_verifier_tools_receive_server_bound_repository() -> None:
    repository = BoundRepository()
    tools = assembler().assemble(
        "sess_1",
        "general",
        {"text", "url"},
        repository=repository,
    )
    control = tools[:2]
    verifiers = tools[2:]
    assert all(tool.params == {"session_id": "sess_1"} for tool in control)
    assert all(
        tool.params == {"session_id": "sess_1", "repository": repository}
        for tool in verifiers
    )


def test_missing_server_binding_requires_explicit_compatibility_mode() -> None:
    with pytest.raises(RuntimeError, match="server-bound repository"):
        assembler().assemble("sess_1", "general", {"text"})

    compatibility_tools = assembler().assemble(
        "sess_1",
        "general",
        {"text"},
        compatibility_mode=True,
    )
    assert all(tool.params == {"session_id": "sess_1"} for tool in compatibility_tools)


def test_toolset_version_is_stable_and_tracks_selected_capabilities() -> None:
    first = assembler().version("general", {"url", "text"})
    reordered = assembler().version("general", {"text", "url"})
    text_only = assembler().version("general", {"text"})
    assert first == reordered
    assert len(first) == 12
    assert first != text_only


def test_compatibility_restore_keeps_protocol_name_for_generic_verifier() -> None:
    tools = assembler().assemble(
        "sess_1",
        "general",
        None,
        compatibility_restore=True,
        compatibility_mode=True,
    )

    assert [tool.name for tool in tools] == [
        "FocusProofLearnerInputTool",
        "FocusProofReviewDraftTool",
        "FocusProofTextEvidenceVerificationTool",
        "FocusProofUrlEvidenceVerificationTool",
        "FocusProofEvidenceVerificationTool",
    ]
