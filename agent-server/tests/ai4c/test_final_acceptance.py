from __future__ import annotations

from pathlib import Path
import re


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
EVIDENCE_MARKERS = (
    "pytest ",
    "npm ",
    "test_",
    ".spec.ts",
    "sha256:",
    "docs/",
    ".png",
)


def _requirement_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not re.match(r"^\| AI4C-[A-Z0-9-]+ \|", line):
            continue
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


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
    assert all(
        any(marker in row[2] for marker in EVIDENCE_MARKERS) for row in rows
    ), "prose-only evidence is not auditable"


def test_final_report_declares_honest_release_bounds() -> None:
    text = REPORT.read_text(encoding="utf-8")

    assert "ReleaseClassification: `staging-ready with blockers`" in text
    assert "| AI4C-REAL-PROVIDER | not-authorized |" in text
    assert "| AI4C-EXTERNAL-OIDC-STAGING | blocked |" in text
    assert "public-launch-ready" not in text
