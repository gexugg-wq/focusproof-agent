from __future__ import annotations

from dataclasses import replace
from typing import Sequence, Union
from io import BytesIO
from pathlib import Path
import sys
import tarfile

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from check_staging_reproducibility import (  # noqa: E402
    CANONICAL_RELEASE_SCHEMA,
    CanonicalizationError,
    ReleaseDescriptor,
    canonical_release_digest,
    safe_record_differences,
)


Entry = tuple[str, Union[bytes, str], int, int, int]


def _tar(path: Path, entries: Sequence[Entry]) -> Path:
    with tarfile.open(path, "w") as archive:
        for name, payload, mode, uid, gid in entries:
            info = tarfile.TarInfo(name)
            info.mode = mode
            info.uid = uid
            info.gid = gid
            info.mtime = 1735689600
            if isinstance(payload, str):
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                archive.addfile(info)
            else:
                info.size = len(payload)
                archive.addfile(info, BytesIO(payload))
    return path


@pytest.fixture
def descriptor() -> ReleaseDescriptor:
    return ReleaseDescriptor(
        schema=CANONICAL_RELEASE_SCHEMA,
        platform="linux/amd64",
        pinned_base_images=(("node:22-bookworm-slim", "sha256:" + "a" * 64),),
        runtime_path="/app",
        config={
            "Cmd": ["node_modules/.bin/next", "start", "-p", "3000"],
            "Env": ["NODE_ENV=production"],
            "User": "focusproof",
            "WorkingDir": "/app",
        },
    )


def _manifest(*, preview_id: str = "1" * 32, route: str = "/") -> bytes:
    return (
        '{"version":4,"routes":{"%s":{}},"preview":'
        '{"previewModeId":"%s","previewModeSigningKey":"%s",'
        '"previewModeEncryptionKey":"%s"}}' % (route, preview_id, "2" * 64, "3" * 64)
    ).encode()


def test_canonical_release_digest_is_versioned_and_normalizes_only_allowlisted_next_entropy(
    tmp_path: Path, descriptor: ReleaseDescriptor
) -> None:
    first = _tar(
        tmp_path / "one.tar",
        [("app/.next/prerender-manifest.json", _manifest(), 0o644, 10001, 10001)],
    )
    second = _tar(
        tmp_path / "two.tar",
        [
            (
                "app/.next/prerender-manifest.json",
                _manifest(preview_id="f" * 32),
                0o644,
                10001,
                10001,
            )
        ],
    )

    next_descriptor = replace(descriptor, normalization_profile="next@15.5.21")
    assert canonical_release_digest(first, next_descriptor) == canonical_release_digest(
        second, next_descriptor
    )
    assert CANONICAL_RELEASE_SCHEMA == "focusproof.canonical-release.v1"


@pytest.mark.parametrize(
    "mutation", ("bytes", "mode", "uid", "gid", "symlink", "config", "platform", "base")
)
def test_canonical_release_digest_changes_for_every_non_allowlisted_release_input(
    tmp_path: Path, descriptor: ReleaseDescriptor, mutation: str
) -> None:
    base_entries: list[Entry] = [("app/code.js", b"release", 0o644, 10001, 10001)]
    changed_entries: list[Entry] = list(base_entries)
    changed_descriptor = descriptor
    if mutation == "bytes":
        changed_entries[0] = ("app/code.js", b"changed", 0o644, 10001, 10001)
    elif mutation == "mode":
        changed_entries[0] = ("app/code.js", b"release", 0o600, 10001, 10001)
    elif mutation == "uid":
        changed_entries[0] = ("app/code.js", b"release", 0o644, 10002, 10001)
    elif mutation == "gid":
        changed_entries[0] = ("app/code.js", b"release", 0o644, 10001, 10002)
    elif mutation == "symlink":
        base_entries = [("app/current", "code-a.js", 0o777, 10001, 10001)]
        changed_entries = [("app/current", "code-b.js", 0o777, 10001, 10001)]
    elif mutation == "config":
        changed_descriptor = replace(descriptor, config={**descriptor.config, "User": "root"})
    elif mutation == "platform":
        changed_descriptor = replace(descriptor, platform="linux/arm64")
    else:
        changed_descriptor = replace(
            descriptor, pinned_base_images=(("node:22-bookworm-slim", "sha256:" + "b" * 64),)
        )

    first = _tar(tmp_path / "base.tar", base_entries)
    second = _tar(tmp_path / "changed.tar", changed_entries)
    assert canonical_release_digest(first, descriptor) != canonical_release_digest(
        second, changed_descriptor
    )


def test_canonical_release_digest_preserves_non_allowlisted_manifest_bytes(
    tmp_path: Path, descriptor: ReleaseDescriptor
) -> None:
    first = _tar(
        tmp_path / "one.tar",
        [("app/.next/prerender-manifest.json", _manifest(route="/"), 0o644, 10001, 10001)],
    )
    second = _tar(
        tmp_path / "two.tar",
        [("app/.next/prerender-manifest.json", _manifest(route="/changed"), 0o644, 10001, 10001)],
    )
    assert canonical_release_digest(first, descriptor) != canonical_release_digest(
        second, descriptor
    )


@pytest.mark.parametrize(
    "bad_schema", ("", "focusproof.canonical-release.v2", "canonical-release-v1")
)
def test_canonical_release_digest_fails_closed_on_unknown_schema(
    tmp_path: Path, descriptor: ReleaseDescriptor, bad_schema: str
) -> None:
    archive = _tar(tmp_path / "release.tar", [("app/code.js", b"release", 0o644, 10001, 10001)])
    with pytest.raises(CanonicalizationError):
        canonical_release_digest(archive, replace(descriptor, schema=bad_schema))


def test_next_entropy_normalization_is_bound_to_next_15_5_18(
    tmp_path: Path, descriptor: ReleaseDescriptor
) -> None:
    archive = _tar(
        tmp_path / "release.tar",
        [("app/.next/prerender-manifest.json", _manifest(), 0o644, 10001, 10001)],
    )
    with pytest.raises(CanonicalizationError):
        canonical_release_digest(archive, replace(descriptor, normalization_profile="next@15.5.19"))


def test_next_runtime_rootfs_rejects_build_tool_caches(
    tmp_path: Path, descriptor: ReleaseDescriptor
) -> None:
    next_descriptor = replace(descriptor, normalization_profile="next@15.5.21")
    required_manifest: Entry = (
        "app/.next/prerender-manifest.json",
        _manifest(),
        0o644,
        10001,
        10001,
    )
    for forbidden_path in (
        "root/.npm/_logs/debug-0.log",
        "tmp/node-compile-cache/v22/cache-entry",
    ):
        archive = _tar(
            tmp_path / (forbidden_path.replace("/", "-") + ".tar"),
            [required_manifest, (forbidden_path, b"cache", 0o600, 0, 0)],
        )
        with pytest.raises(CanonicalizationError, match="forbidden runtime build cache"):
            canonical_release_digest(archive, next_descriptor)


def test_record_diagnostics_are_path_complete_and_secret_safe() -> None:
    first = (
        {
            "path": "app/code.js",
            "type": "file",
            "mode": 0o644,
            "uid": 1,
            "gid": 2,
            "size": 3,
            "sha256": "a" * 64,
        },
        {"path": "app/link", "type": "symlink", "mode": 0o777, "uid": 1, "gid": 2, "target": "one"},
        {
            "path": "only-first",
            "type": "file",
            "mode": 0o600,
            "uid": 0,
            "gid": 0,
            "size": 1,
            "sha256": "b" * 64,
        },
    )
    second = (
        {
            "path": "app/code.js",
            "type": "file",
            "mode": 0o600,
            "uid": 3,
            "gid": 4,
            "size": 5,
            "sha256": "c" * 64,
        },
        {"path": "app/link", "type": "symlink", "mode": 0o777, "uid": 1, "gid": 2, "target": "two"},
        {"path": "only-second", "type": "directory", "mode": 0o755, "uid": 0, "gid": 0},
    )

    diagnostic = "\n".join(safe_record_differences(first, second))

    for expected in (
        "path=app/code.js",
        "type=file",
        "mode=420",
        "uid=1",
        "gid=2",
        "size=3",
        "sha256=" + "a" * 64,
        "path=app/link",
        "target_sha256=" + __import__("hashlib").sha256(b"one").hexdigest(),
        "path=only-first",
        "only_in=round1",
        "path=only-second",
        "only_in=round2",
    ):
        assert expected in diagnostic
    for forbidden in ("one", "two", "OPENAI_API_KEY", "secret-value", "content="):
        assert forbidden not in diagnostic
