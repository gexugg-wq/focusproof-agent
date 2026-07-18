from __future__ import annotations

import ast
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
    "agent-server/focusproof/tools/__init__.py",
    "agent-server/focusproof/tools/fake_tools.py",
}
FORBIDDEN_RUNTIME_MARKERS = (
    "FocusProofLearningConversation",
    "DeterministicLearningAgentFallback",
    "OpenHandsAgentAdapter",
    "_agent.step",
    "execute_focusproof_tool",
    "FakeTextEvidenceTool",
    "FakeWeb3TxTool",
    "monad-testnet-mock",
)


def _annotation_name(annotation: ast.expr | None, imports: dict[str, str]) -> str | None:
    if isinstance(annotation, ast.Name):
        return imports.get(annotation.id, annotation.id)
    if isinstance(annotation, ast.Attribute):
        parts: list[str] = []
        current: ast.expr = annotation
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            prefix = imports.get(current.id, current.id)
            return ".".join((prefix, *reversed(parts)))
    return None


def _local_action_observation_dispatchers(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name] = alias.name

    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parameter_types = {
            _annotation_name(argument.annotation, imports)
            for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        }
        return_type = _annotation_name(node.returns, imports)
        if (
            "focusproof.runtime.actions.Action" in parameter_types
            and return_type == "focusproof.runtime.observations.Observation"
        ):
            findings.append(f"{node.name} at line {node.lineno}")
    return findings


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
        for dispatcher in _local_action_observation_dispatchers(path):
            findings.append(f"{relative}: local Action-to-Observation dispatcher {dispatcher}")

    assert findings == []


def test_build_metadata_excludes_deleted_runtime_and_tracks_projection_stores() -> None:
    packaged_sources = set(SOURCES.read_text(encoding="utf-8").splitlines())

    assert packaged_sources.isdisjoint(FORBIDDEN_SOURCE_PATHS)
    assert "agent-server/focusproof/runtime/audit_projection.py" in packaged_sources
    assert "agent-server/focusproof/persistence/audit_projection.py" in packaged_sources
