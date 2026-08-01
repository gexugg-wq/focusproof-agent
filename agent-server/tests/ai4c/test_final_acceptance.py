from __future__ import annotations

import ast
from pathlib import Path
import re
import shlex
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REPORT = PROJECT_ROOT / "docs/research/AI4C_PRODUCTION_READINESS_REPORT.md"

REQUIRED_REQUIREMENTS = {
    "AI4C-RUNTIME-REUSE",
    "AI4C-PROVIDER-BOUNDS",
    "AI4C-PROVIDER-FAILURES",
    "AI4C-AUTH-401",
    "AI4C-AUTH-403",
    "AI4C-AUTH-404",
    "AI4C-SPOOF-RESISTANCE",
    "AI4C-ANONYMOUS-ISOLATION",
    "AI4C-SDK-EQUIVALENCE",
    "AI4C-POSTGRESQL",
    "AI4C-CLEAN-STACK",
    "AI4C-PAIRED-RESTORE",
    "AI4C-REDACTION",
    "AI4C-ACCESSIBILITY",
    "AI4C-DETERMINISTIC-GATES",
    "AI4C-REAL-PROVIDER",
    "AI4C-EXTERNAL-OIDC-STAGING",
    "AI4C-PROTOCOL-FREEZE",
    "AI4C-EXCLUSIONS",
}
VALID_STATUSES = {"pass", "fail", "blocked", "not-authorized"}
EVIDENCE_TYPES = {"pytest-node", "command", "doc", "digest", "artifact"}


def _requirement_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not re.match(r"^\| AI4C-[A-Z0-9-]+ \|", line):
            continue
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def _project_path(value: str) -> Path:
    path = (PROJECT_ROOT / value).resolve()
    assert path.is_relative_to(PROJECT_ROOT.resolve()), f"evidence path escapes project: {value}"
    assert path.exists(), f"evidence path does not exist: {value}"
    return path


def _python_test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def _validate_pytest_node(value: str) -> None:
    parts = value.split("::")
    assert len(parts) >= 2 and all(parts), f"pytest evidence needs an exact node: {value}"
    path = _project_path(parts[0])
    assert path.suffix == ".py", f"pytest node must reference a Python test file: {value}"
    test_name = re.sub(r"\[.*\]$", "", parts[-1])
    assert test_name in _python_test_names(path), f"pytest test function does not exist: {value}"


def _validate_command(value: str) -> None:
    tokens = shlex.split(value)
    assert len(tokens) >= 2, f"command evidence is incomplete: {value}"
    assert not any("<" in token or ">" in token for token in tokens), (
        f"command evidence contains a placeholder: {value}"
    )
    pytest_nodes = [token for token in tokens if "::" in token]
    if "pytest" in tokens or any(token.endswith("pytest") for token in tokens):
        assert pytest_nodes, f"pytest command needs an exact node: {value}"
    for node in pytest_nodes:
        _validate_pytest_node(node)
    for token in tokens:
        candidate = token.split("::", 1)[0]
        if candidate.startswith(("http://", "https://")) or "=" in candidate:
            continue
        if "/" in candidate and not candidate.startswith("-"):
            _project_path(candidate)


def _markdown_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            continue
        heading = line.lstrip("#").strip().lower()
        anchor = re.sub(r"[^a-z0-9 _-]", "", heading)
        anchors.add(re.sub(r"[ _]+", "-", anchor).strip("-"))
    return anchors


def _validate_evidence(value: str) -> None:
    assert "<" not in value and ">" not in value, f"evidence contains a placeholder: {value}"
    prefix, separator, locator = value.partition(":")
    assert separator and prefix in EVIDENCE_TYPES and locator.strip(), (
        f"evidence needs one allowed typed locator: {value}"
    )
    locator = locator.strip()
    if prefix == "pytest-node":
        _validate_pytest_node(locator)
    elif prefix == "command":
        _validate_command(locator)
    elif prefix == "doc":
        path_text, marker, section = locator.partition("#")
        assert marker and section, f"doc evidence needs a section: {value}"
        path = _project_path(path_text)
        assert path.suffix == ".md", f"doc evidence must reference Markdown: {value}"
        assert section.lower() in _markdown_anchors(path), f"document section does not exist: {value}"
    elif prefix == "digest":
        assert re.fullmatch(r"sha256:[0-9a-fA-F]{64}", locator), (
            f"digest evidence must be sha256 plus 64 hex characters: {value}"
        )
    else:
        _project_path(locator)


def test_final_report_has_unique_auditable_requirement_evidence() -> None:
    assert REPORT.is_file(), f"missing AI4C.4 closure report: {REPORT}"
    text = REPORT.read_text(encoding="utf-8")
    rows = _requirement_rows(text)
    ids = [row[0] for row in rows]

    assert set(ids) == REQUIRED_REQUIREMENTS
    assert len(ids) == len(set(ids)), "requirement rows must be unique"
    assert all(len(row) == 6 for row in rows), "matrix rows need six columns"
    assert all(row[1] in VALID_STATUSES for row in rows)
    assert all(row[3] for row in rows), "every row must name its owning phase"
    assert all(row[5] for row in rows), "every row must state residual risk or None"
    for row in rows:
        _validate_evidence(row[2])


@pytest.mark.parametrize(
    "invalid_evidence",
    [
        'pytest <agent-server/tests/ai4c/test_file.py::test_name> -q',
        'docs/nonexistent.md#Missing',
        'pytest-node: agent-server/tests/ai4c/test_final_acceptance.py::test_nonexistent',
        'pytest agent-server/tests/ai4c/test_final_acceptance.py -q',
    ],
)
def test_evidence_lint_rejects_non_executable_locators(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_evidence: str,
) -> None:
    text = REPORT.read_text(encoding="utf-8")
    original = next(
        line
        for line in text.splitlines()
        if line.startswith("| AI4C-RUNTIME-REUSE |")
    )
    cells = [cell.strip() for cell in original.strip().strip("|").split("|")]
    cells[2] = invalid_evidence
    invalid_report = tmp_path / "invalid-report.md"
    invalid_report.write_text(text.replace(original, "| " + " | ".join(cells) + " |"), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "REPORT", invalid_report)

    with pytest.raises(AssertionError):
        test_final_report_has_unique_auditable_requirement_evidence()


def test_final_report_declares_honest_release_bounds() -> None:
    text = REPORT.read_text(encoding="utf-8")

    assert "ReleaseClassification: `staging-ready with blockers`" in text
    assert "| AI4C-REAL-PROVIDER | not-authorized |" in text
    assert "| AI4C-EXTERNAL-OIDC-STAGING | blocked |" in text
    assert "public-launch-ready" not in text
