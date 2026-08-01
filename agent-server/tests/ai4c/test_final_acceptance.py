from __future__ import annotations

from functools import lru_cache
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
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
EVIDENCE_TYPES = {
    "accepted-evidence",
    "artifact",
    "command",
    "digest",
    "doc",
    "pytest-node",
}


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


@lru_cache(maxsize=None)
def _collected_pytest_nodes(test_path: str) -> frozenset[str]:
    env = os.environ.copy()
    for key in (
        "DASHSCOPE_API_KEY",
        "OPENAI_API_KEY",
        "FOCUSPROOF_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        env.pop(key, None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "real_llm or not real_llm",
            test_path,
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"pytest collection failed for: {test_path}"
    return frozenset(
        line.strip() for line in result.stdout.splitlines() if "::" in line
    )


def _validate_pytest_node(value: str) -> None:
    parts = value.split("::")
    assert len(parts) >= 2 and all(parts), f"pytest evidence needs an exact node: {value}"
    path = _project_path(parts[0])
    assert path.suffix == ".py", f"pytest node must reference a Python test file: {value}"
    assert value in _collected_pytest_nodes(parts[0]), (
        f"pytest node is not collectable: {value}"
    )


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


def _anchors_from_markdown(text: str) -> set[str]:
    anchors: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        heading = line.lstrip("#").strip().lower()
        anchor = re.sub(r"[^a-z0-9 _-]", "", heading)
        anchors.add(re.sub(r"[ _]+", "-", anchor).strip("-"))
    return anchors


def _validate_accepted_evidence(locator: str) -> None:
    revision_path, marker, section = locator.partition("#")
    revision, separator, path_text = revision_path.partition(":")
    assert (
        marker
        and section
        and separator
        and re.fullmatch(r"[0-9a-f]{40}", revision)
    ), f"accepted evidence needs full commit, repository path, and section: {locator}"
    _project_path(path_text)
    result = subprocess.run(
        ["git", "show", f"{revision}:{path_text}"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, f"accepted evidence object does not exist: {locator}"
    assert section.lower() in _anchors_from_markdown(result.stdout), (
        f"accepted evidence section does not exist: {locator}"
    )


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
        path_text, marker, digest = locator.partition("#")
        assert marker and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest), (
            f"digest evidence needs artifact path and sha256: {value}"
        )
        path = _project_path(path_text)
        actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest.lower() == actual, f"digest does not match artifact: {value}"
    elif prefix == "accepted-evidence":
        _validate_accepted_evidence(locator)
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


def _requirements_by_id(text: str) -> dict[str, list[str]]:
    return {row[0]: row for row in _requirement_rows(text)}


def _accepted_evidence_section(locator: str) -> str:
    revision_path, _, section = locator.partition("#")
    revision, _, path_text = revision_path.partition(":")
    result = subprocess.run(
        ["git", "show", f"{revision}:{path_text}"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    lines = result.stdout.splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if section.lower() in _anchors_from_markdown(line)
    )
    heading_level = len(lines[start]) - len(lines[start].lstrip("#"))
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("#")
            and len(lines[index]) - len(lines[index].lstrip("#")) <= heading_level
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_provider_failures_pass_points_to_the_safe_failure_path() -> None:
    rows = _requirements_by_id(REPORT.read_text(encoding="utf-8"))

    assert rows["AI4C-PROVIDER-FAILURES"][1:3] == [
        "pass",
        "pytest-node: agent-server/tests/openhands_runtime/test_runtime_failure.py::test_run_failure_never_reports_openhands_usage",
    ]


@pytest.mark.parametrize(
    "requirement_id",
    ["AI4C-SDK-EQUIVALENCE", "AI4C-CLEAN-STACK"],
)
def test_external_artifact_requirements_fail_closed_without_bound_artifacts(
    requirement_id: str,
) -> None:
    rows = _requirements_by_id(REPORT.read_text(encoding="utf-8"))
    row = rows[requirement_id]

    assert row[1] == "blocked"
    assert row[2] == (
        "doc: docs/research/AI4C_PRODUCTION_READINESS_REPORT.md"
        "#current-external-artifact-blockers"
    )


def test_passed_historical_digest_claims_require_artifact_bound_evidence() -> None:
    rows = _requirement_rows(REPORT.read_text(encoding="utf-8"))

    for row in rows:
        prefix, _, locator = row[2].partition(":")
        if row[1] != "pass" or prefix != "accepted-evidence":
            continue
        section = _accepted_evidence_section(locator.strip())
        if re.search(r"(?:sha256:)?[0-9a-f]{64}", section, re.IGNORECASE):
            pytest.fail(
                f"{row[0]} passes from a historical digest claim without a "
                "recomputable digest: artifact locator"
            )


@pytest.mark.parametrize(
    ("invalid_evidence", "reason"),
    [
        ('pytest-node: <agent-server/tests/ai4c/test_final_acceptance.py::test_final_report_declares_honest_release_bounds>', "placeholder"),
        ('artifact: ../outside.json', "escapes project"),
        ('doc: docs/nonexistent.md#architecture-and-scope', "does not exist"),
        ('doc: docs/research/AI4C_PRODUCTION_READINESS_REPORT.md#missing-anchor', "document section does not exist"),
        ('command: .venv/bin/python -m pytest agent-server/tests/ai4c/test_final_acceptance.py -q', "pytest command needs an exact node"),
        ('pytest-node: agent-server/tests/ai4c/test_final_acceptance.py::test_nonexistent', "pytest node is not collectable"),
        ('pytest-node: agent-server/tests/ai4c/test_final_acceptance.py::FakeAcceptanceClass::test_final_report_declares_honest_release_bounds', "pytest node is not collectable"),
        ('pytest-node: agent-server/tests/ai4c/test_openhands_release_equivalence.py::test_parse_probe_payload_fail_closed_for_invalid_pass_payload[payload99]', "pytest node is not collectable"),
        ('digest: docs/research/AI4C_PRODUCTION_READINESS_REPORT.md#sha256:not-a-digest', "digest evidence needs artifact path and sha256"),
        ('digest: docs/research/AI4C_PRODUCTION_READINESS_REPORT.md#sha256:0000000000000000000000000000000000000000000000000000000000000000', "digest does not match artifact"),
        ('digest: docs/research/missing-artifact.json#sha256:0000000000000000000000000000000000000000000000000000000000000000', "does not exist"),
    ],
)
def test_evidence_lint_rejects_non_executable_locators(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_evidence: str,
    reason: str,
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

    with pytest.raises(AssertionError, match=reason):
        test_final_report_has_unique_auditable_requirement_evidence()


def test_final_report_declares_honest_release_bounds() -> None:
    text = REPORT.read_text(encoding="utf-8")

    assert "ReleaseClassification: `staging-ready with blockers`" in text
    assert "| AI4C-REAL-PROVIDER | not-authorized |" in text
    assert "| AI4C-EXTERNAL-OIDC-STAGING | blocked |" in text
    assert "public-launch-ready" not in text
