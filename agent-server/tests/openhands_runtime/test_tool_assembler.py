from focusproof.openhands_runtime.capabilities import (
    VerificationCapability,
    VerificationCapabilityRegistry,
    build_builtin_capabilities,
)
from focusproof.openhands_runtime.tool_assembler import SessionToolAssembler


def assembler() -> SessionToolAssembler:
    return SessionToolAssembler(
        VerificationCapabilityRegistry(build_builtin_capabilities())
    )


def test_general_session_gets_control_and_general_verification_tools() -> None:
    tools = assembler().assemble("sess_1", "general", None)
    assert [tool.name for tool in tools] == [
        "FocusProofLearnerInputTool",
        "FocusProofReviewDraftTool",
        "FocusProofTextEvidenceVerificationTool",
        "FocusProofUrlEvidenceVerificationTool",
    ]


def test_session_without_evidence_types_gets_allowlisted_general_verifiers() -> None:
    tools = assembler().assemble("sess_1", "general", set())
    assert [tool.name for tool in tools] == [
        "FocusProofLearnerInputTool",
        "FocusProofReviewDraftTool",
        "FocusProofTextEvidenceVerificationTool",
        "FocusProofUrlEvidenceVerificationTool",
    ]


def test_known_text_evidence_narrows_general_verifiers() -> None:
    tools = assembler().assemble("sess_1", "general", {"text"})
    assert "FocusProofTextEvidenceVerificationTool" in {tool.name for tool in tools}
    assert "FocusProofUrlEvidenceVerificationTool" not in {tool.name for tool in tools}


def test_forbidden_default_tools_are_never_assembled() -> None:
    names = {
        tool.name.lower()
        for tool in assembler().assemble("sess_1", "general", None)
    }
    assert names.isdisjoint(
        {"terminaltool", "fileeditortool", "browsertool", "applypatchtool"}
    )


def test_tool_specs_contain_only_trusted_session_id() -> None:
    tools = assembler().assemble("sess_1", "general", {"text", "url"})
    assert all(tool.params == {"session_id": "sess_1"} for tool in tools)


def test_toolset_version_is_stable_and_tracks_selected_capabilities() -> None:
    first = assembler().version("general", {"url", "text"})
    reordered = assembler().version("general", {"text", "url"})
    text_only = assembler().version("general", {"text"})
    assert first == reordered
    assert len(first) == 12
    assert first != text_only


def test_compatibility_restore_deduplicates_registered_legacy_tool() -> None:
    capabilities = (*build_builtin_capabilities(), VerificationCapability(
        registry_name="legacy",
        tool_class_name="FocusProofEvidenceVerificationTool",
        supported_evidence_types=frozenset({"legacy"}),
        supported_domains=frozenset({"*"}),
        priority=30,
        read_only=True,
        requires_network=False,
        timeout_seconds=5.0,
        enabled=True,
        version="1",
    ))
    tools = SessionToolAssembler(
        VerificationCapabilityRegistry(capabilities)
    ).assemble(
        "sess_1",
        "general",
        None,
        compatibility_restore=True,
    )

    assert [tool.name for tool in tools].count(
        "FocusProofEvidenceVerificationTool"
    ) == 1
