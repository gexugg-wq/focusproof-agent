from __future__ import annotations

from base64 import b64decode
import json
from pathlib import Path
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = PROJECT_ROOT / "scripts/normalize_next_empty_action_manifest.mjs"
PLACEHOLDER = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(SCRIPT), str(path)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _run_manifest_canonicalizer(next_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(SCRIPT), "--canonicalize-build-manifests", str(next_root)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _write_build_manifests(next_root: Path) -> dict[Path, str]:
    payloads = {
        Path("app-build-manifest.json"): json.dumps(
            {"pages": {"/z": ["z.js", "a.js"], "/a": ["b.js"]}}
        ),
        Path("app-path-routes-manifest.json"): json.dumps({"/z": "/z", "/a": "/a"}),
        Path("server/app-paths-manifest.json"): json.dumps(
            {"/z/page": "app/z/page.js", "/a/page": "app/a/page.js"}
        ),
        Path("server/pages-manifest.json"): json.dumps(
            {"/_error": "pages/_error.js", "/404": "pages/404.html"}
        ),
    }
    for relative, payload in payloads.items():
        target = next_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
    return payloads


def test_empty_action_manifest_gets_public_fixed_valid_key(tmp_path: Path) -> None:
    manifest = tmp_path / "server-reference-manifest.json"
    manifest.write_text(
        json.dumps({"node": {}, "edge": {}, "encryptionKey": "random-round-key"}),
        encoding="utf-8",
    )

    result = _run(manifest)

    assert result.returncode == 0, result.stderr
    normalized = json.loads(manifest.read_text(encoding="utf-8"))
    assert normalized == {"node": {}, "edge": {}, "encryptionKey": PLACEHOLDER}
    assert len(PLACEHOLDER) == 44
    assert len(b64decode(PLACEHOLDER, validate=True)) == 32
    assert result.stdout == ""


@pytest.mark.parametrize("action_map", ("node", "edge"))
def test_nonempty_action_manifest_fails_closed_without_mutation(
    tmp_path: Path, action_map: str
) -> None:
    manifest = tmp_path / "server-reference-manifest.json"
    payload = {"node": {}, "edge": {}, "encryptionKey": "random-round-key"}
    payload[action_map] = {"action-id": {"workers": {"app/page": {"moduleId": "1"}}}}
    original = json.dumps(payload)
    manifest.write_text(original, encoding="utf-8")

    result = _run(manifest)

    assert result.returncode != 0
    assert manifest.read_text(encoding="utf-8") == original
    assert "action maps must be empty" in result.stderr
    assert "action-id" not in result.stderr


@pytest.mark.parametrize(
    "payload",
    (
        {"node": {}, "edge": {}},
        {"node": [], "edge": {}, "encryptionKey": "key"},
        {"node": {}, "edge": [], "encryptionKey": "key"},
        {"node": {}, "edge": {}, "encryptionKey": 1},
        {"node": {}, "edge": {}, "encryptionKey": "key", "unexpected": {}},
    ),
)
def test_manifest_schema_anomaly_fails_closed_without_mutation(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    manifest = tmp_path / "server-reference-manifest.json"
    original = json.dumps(payload)
    manifest.write_text(original, encoding="utf-8")

    result = _run(manifest)

    assert result.returncode != 0
    assert manifest.read_text(encoding="utf-8") == original
    assert "invalid server-reference manifest schema" in result.stderr


def test_build_manifest_canonicalizer_sorts_only_exact_allowlist_recursively(
    tmp_path: Path,
) -> None:
    next_root = tmp_path / ".next"
    _write_build_manifests(next_root)

    result = _run_manifest_canonicalizer(next_root)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    app_build = json.loads((next_root / "app-build-manifest.json").read_text(encoding="utf-8"))
    assert list(app_build) == ["pages"]
    assert list(app_build["pages"]) == ["/a", "/z"]
    assert app_build["pages"]["/z"] == ["z.js", "a.js"]
    assert list(json.loads((next_root / "server/pages-manifest.json").read_text())) == [
        "/404",
        "/_error",
    ]


def test_build_manifest_canonicalizer_fails_closed_before_any_write(
    tmp_path: Path,
) -> None:
    next_root = tmp_path / ".next"
    originals = _write_build_manifests(next_root)
    (next_root / "server/app-paths-manifest.json").write_text("[]", encoding="utf-8")

    result = _run_manifest_canonicalizer(next_root)

    assert result.returncode != 0
    assert "invalid allowlisted Next manifest schema" in result.stderr
    for relative, original in originals.items():
        if relative != Path("server/app-paths-manifest.json"):
            assert (next_root / relative).read_text(encoding="utf-8") == original


def test_build_manifest_canonicalizer_rejects_missing_allowlisted_path(
    tmp_path: Path,
) -> None:
    next_root = tmp_path / ".next"
    originals = _write_build_manifests(next_root)
    (next_root / "app-path-routes-manifest.json").unlink()

    result = _run_manifest_canonicalizer(next_root)

    assert result.returncode != 0
    assert "missing allowlisted Next manifest" in result.stderr
    for relative, original in originals.items():
        if relative != Path("app-path-routes-manifest.json"):
            assert (next_root / relative).read_text(encoding="utf-8") == original


def test_next_config_serializes_build_workers() -> None:
    script = (
        "import config from './next.config.mjs';"
        "process.stdout.write(JSON.stringify(config.experimental));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=PROJECT_ROOT / "frontend",
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"cpus": 1, "webpackBuildWorker": False}
