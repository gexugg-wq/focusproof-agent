from __future__ import annotations

from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_ROOT = PROJECT_ROOT / "agent-server/focusproof"
SOURCES = PROJECT_ROOT / "agent-server/focusproof_agent.egg-info/SOURCES.txt"
FORBIDDEN_SOURCE_PATHS = {
    "agent-server/focusproof/openhands_adapter/agent.py",
    "agent-server/focusproof/openhands_adapter/learning_conversation.py",
    "agent-server/focusproof/runtime/event_log.py",
    "agent-server/focusproof/persistence/event_log.py",
}
FORBIDDEN_RUNTIME_MARKERS = (
    "FocusProofLearningConversation",
    "DeterministicLearningAgentFallback",
    "OpenHandsAgentAdapter",
    "_agent.step",
    "execute_focusproof_tool",
)


def test_production_package_contains_no_parallel_event_log_or_agent_loop() -> None:
    findings: list[str] = []
    for path in sorted(PRODUCTION_ROOT.rglob("*.py")):
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if re.search(r"^class\s+\w*EventLog\b", text, flags=re.MULTILINE):
            findings.append(f"{relative}: product class is still named EventLog")
        for marker in FORBIDDEN_RUNTIME_MARKERS:
            if marker in text:
                findings.append(f"{relative}: contains {marker}")

    assert findings == []


def test_build_metadata_excludes_deleted_runtime_and_tracks_projection_stores() -> None:
    packaged_sources = set(SOURCES.read_text(encoding="utf-8").splitlines())

    assert packaged_sources.isdisjoint(FORBIDDEN_SOURCE_PATHS)
    assert "agent-server/focusproof/runtime/audit_projection.py" in packaged_sources
    assert "agent-server/focusproof/persistence/audit_projection.py" in packaged_sources
