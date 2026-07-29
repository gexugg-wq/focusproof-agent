from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from typing import Any


CANONICAL_RELEASE_SCHEMA = "focusproof.canonical-release.v1"
_PINNED_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_NEXT_ENTROPY_FIELDS = {
    "previewModeId": 32,
    "previewModeSigningKey": 64,
    "previewModeEncryptionKey": 64,
}


class ReproducibilityError(RuntimeError):
    """Raised when independently built staging images are not identical."""


ImageSnapshot = Mapping[str, object]


class CanonicalizationError(RuntimeError):
    """Raised when a release cannot be canonicalized without weakening policy."""


@dataclass(frozen=True, slots=True)
class CanonicalReleaseSnapshot:
    digest: str
    records: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class ReleaseDescriptor:
    schema: str
    platform: str
    pinned_base_images: tuple[tuple[str, str], ...]
    runtime_path: str
    config: Mapping[str, Any]
    normalization_profile: str | None = None


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise CanonicalizationError("release metadata must be strict JSON") from exc


def _validate_descriptor(descriptor: ReleaseDescriptor) -> None:
    if descriptor.schema != CANONICAL_RELEASE_SCHEMA:
        raise CanonicalizationError("unknown canonical release schema")
    if not re.fullmatch(r"linux/[a-z0-9_]+", descriptor.platform):
        raise CanonicalizationError("release platform must be an explicit Linux OCI platform")
    runtime = PurePosixPath(descriptor.runtime_path)
    if not runtime.is_absolute() or ".." in runtime.parts or runtime == PurePosixPath("/"):
        raise CanonicalizationError("runtime path must be a specific absolute path")
    if not descriptor.pinned_base_images:
        raise CanonicalizationError("at least one pinned base image is required")
    names: set[str] = set()
    for name, digest in descriptor.pinned_base_images:
        if not name or name in names or not _PINNED_DIGEST.fullmatch(digest):
            raise CanonicalizationError("base images must be unique and pinned by sha256")
        names.add(name)
    if descriptor.normalization_profile not in {None, "next@15.5.21"}:
        raise CanonicalizationError("unknown release normalization profile")
    encoded_config = _canonical_json(descriptor.config)
    if any(
        marker in encoded_config.upper()
        for marker in (b"PASSWORD", b"SECRET", b"CREDENTIAL", b"TOKEN")
    ):
        raise CanonicalizationError("secret-bearing configuration is prohibited")


def _normalize_next_prerender_manifest(payload: bytes) -> bytes:
    try:
        document = json.loads(payload)
        preview = document["preview"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CanonicalizationError("invalid Next prerender manifest") from exc
    if not isinstance(preview, dict):
        raise CanonicalizationError("invalid Next prerender preview metadata")
    normalized = payload
    for field, length in _NEXT_ENTROPY_FIELDS.items():
        value = preview.get(field)
        if not isinstance(value, str) or not re.fullmatch(f"[0-9a-f]{{{length}}}", value):
            raise CanonicalizationError(f"invalid allowlisted Next entropy field: {field}")
        pattern = re.compile(
            rb'("'
            + field.encode("ascii")
            + rb'"\s*:\s*")([0-9a-f]{'
            + str(length).encode("ascii")
            + rb'})(")'
        )
        matches = list(pattern.finditer(normalized))
        if len(matches) != 1:
            raise CanonicalizationError(f"ambiguous allowlisted Next entropy field: {field}")
        match = matches[0]
        normalized = normalized[: match.start(2)] + (b"0" * length) + normalized[match.end(2) :]
    return normalized


def canonical_release_snapshot(
    rootfs_tar: Path, descriptor: ReleaseDescriptor
) -> CanonicalReleaseSnapshot:
    """Digest a complete flattened runtime filesystem and explicit release metadata."""
    _validate_descriptor(descriptor)
    runtime_prefix = descriptor.runtime_path.lstrip("/").rstrip("/")
    prerender_path = f"{runtime_prefix}/.next/prerender-manifest.json"
    entries: list[dict[str, object]] = []
    saw_prerender_manifest = False
    seen: set[str] = set()
    try:
        archive = tarfile.open(rootfs_tar, mode="r:*")
    except (OSError, tarfile.TarError) as exc:
        raise CanonicalizationError("invalid root filesystem archive") from exc
    with archive:
        for member in archive:
            raw_name = member.name.removeprefix("./").rstrip("/")
            path = PurePosixPath(raw_name)
            if not raw_name or path.is_absolute() or ".." in path.parts or raw_name in seen:
                raise CanonicalizationError("unsafe or duplicate root filesystem path")
            seen.add(raw_name)
            if descriptor.normalization_profile == "next@15.5.21" and (
                raw_name == "root/.npm"
                or raw_name.startswith("root/.npm/")
                or raw_name == "tmp/node-compile-cache"
                or raw_name.startswith("tmp/node-compile-cache/")
            ):
                raise CanonicalizationError("forbidden runtime build cache")
            record: dict[str, object] = {
                "path": raw_name,
                "mode": member.mode,
                "uid": member.uid,
                "gid": member.gid,
            }
            if member.isdir():
                record["type"] = "directory"
            elif member.issym():
                record.update(type="symlink", target=member.linkname)
            elif member.islnk():
                record.update(type="hardlink", target=member.linkname)
            elif member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise CanonicalizationError("regular file has no bytes")
                payload = stream.read()
                if (
                    raw_name == prerender_path
                    and descriptor.normalization_profile == "next@15.5.21"
                ):
                    payload = _normalize_next_prerender_manifest(payload)
                    saw_prerender_manifest = True
                record.update(type="file", size=len(payload), sha256=sha256(payload).hexdigest())
            else:
                raise CanonicalizationError(f"unsupported root filesystem entry: {raw_name}")
            entries.append(record)
    if not entries:
        raise CanonicalizationError("empty root filesystem archive")
    if descriptor.normalization_profile == "next@15.5.21" and not saw_prerender_manifest:
        raise CanonicalizationError("Next normalization profile requires its prerender manifest")
    ordered_entries = sorted(entries, key=lambda item: str(item["path"]))
    artifact = {
        "schema": descriptor.schema,
        "platform": descriptor.platform,
        "pinnedBaseImages": sorted(descriptor.pinned_base_images),
        "runtimePath": descriptor.runtime_path,
        "normalizationProfile": descriptor.normalization_profile,
        "config": descriptor.config,
        "filesystem": ordered_entries,
    }
    digest = "sha256:" + sha256(_canonical_json(artifact)).hexdigest()
    return CanonicalReleaseSnapshot(digest=digest, records=tuple(ordered_entries))


def canonical_release_digest(rootfs_tar: Path, descriptor: ReleaseDescriptor) -> str:
    return canonical_release_snapshot(rootfs_tar, descriptor).digest


def _safe_record(record: Mapping[str, object]) -> str:
    fields = [f"path={record.get('path')}"]
    for name in ("type", "mode", "uid", "gid", "size", "sha256"):
        if name in record:
            fields.append(f"{name}={record[name]}")
    target = record.get("target")
    if isinstance(target, str):
        fields.append(f"target_sha256={sha256(target.encode('utf-8')).hexdigest()}")
    return " ".join(fields)


def safe_record_differences(
    round_one: tuple[Mapping[str, object], ...],
    round_two: tuple[Mapping[str, object], ...],
) -> list[str]:
    first = {str(record.get("path")): record for record in round_one}
    second = {str(record.get("path")): record for record in round_two}
    differences: list[str] = []
    for path in sorted(set(first) | set(second)):
        if path not in second:
            differences.append(f"{_safe_record(first[path])} only_in=round1")
        elif path not in first:
            differences.append(f"{_safe_record(second[path])} only_in=round2")
        elif dict(first[path]) != dict(second[path]):
            differences.append(
                f"round1[{_safe_record(first[path])}] round2[{_safe_record(second[path])}]"
            )
    return differences


def pinned_base_images_from_dockerfile(dockerfile: Path) -> tuple[tuple[str, str], ...]:
    """Return every build stage base, preserving stage identity and pinned digest."""
    bases: list[tuple[str, str]] = []
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        tokens = line.strip().split()
        if not tokens or tokens[0].upper() != "FROM":
            continue
        reference = tokens[1]
        if "@" not in reference:
            raise CanonicalizationError("every Dockerfile base must be pinned by digest")
        name, digest = reference.rsplit("@", 1)
        if not name or not _PINNED_DIGEST.fullmatch(digest):
            raise CanonicalizationError("every Dockerfile base must be pinned by sha256")
        bases.append((f"stage-{len(bases)}:{name}", digest))
    if not bases:
        raise CanonicalizationError("Dockerfile contains no base image")
    return tuple(bases)


def _mapping(snapshot: ImageSnapshot, key: str) -> Mapping[str, object]:
    value = snapshot.get(key)
    return value if isinstance(value, Mapping) else {}


def _layers(snapshot: ImageSnapshot) -> list[str]:
    layers = _mapping(snapshot, "RootFS").get("Layers")
    if not isinstance(layers, list) or not all(isinstance(layer, str) for layer in layers):
        return []
    return [layer for layer in layers if isinstance(layer, str)]


def _first_layer_difference(round_one: list[str], round_two: list[str]) -> str:
    for index, (first, second) in enumerate(zip(round_one, round_two, strict=False)):
        if first != second:
            return f"RootFS.Layers first differs at index {index}: round1={first}; round2={second}"
    if len(round_one) != len(round_two):
        index = min(len(round_one), len(round_two))
        first = round_one[index] if index < len(round_one) else "<missing>"
        second = round_two[index] if index < len(round_two) else "<missing>"
        return f"RootFS.Layers first differs at index {index}: round1={first}; round2={second}"
    return "RootFS.Layers identical"


def _first_config_difference(round_one: ImageSnapshot, round_two: ImageSnapshot) -> str:
    first_config = _mapping(round_one, "Config")
    second_config = _mapping(round_two, "Config")
    for key in sorted(set(first_config) | set(second_config)):
        first = first_config.get(key)
        second = second_config.get(key)
        if first != second:
            return f"Config first differs for {key}: round1={first!r}; round2={second!r}"
    return "Config identical"


def _image_diagnostic(image: str, round_one: ImageSnapshot, round_two: ImageSnapshot) -> str:
    created = (
        "Created equal"
        if round_one.get("Created") == round_two.get("Created")
        else f"Created differs: round1={round_one.get('Created')!r}; round2={round_two.get('Created')!r}"
    )
    return "; ".join(
        (
            image,
            created,
            _first_layer_difference(_layers(round_one), _layers(round_two)),
            _first_config_difference(round_one, round_two),
        )
    )


def compare_round_digests(
    round_one: Mapping[str, str],
    round_two: Mapping[str, str],
    *,
    round_one_snapshots: Mapping[str, ImageSnapshot] | None = None,
    round_two_snapshots: Mapping[str, ImageSnapshot] | None = None,
    round_one_records: Mapping[str, tuple[Mapping[str, object], ...]] | None = None,
    round_two_records: Mapping[str, tuple[Mapping[str, object], ...]] | None = None,
) -> None:
    required = {"agent-server", "frontend"}
    if set(round_one) != required or set(round_two) != required:
        raise ReproducibilityError("each build round must contain both staging images")
    if any(
        not digest.startswith("sha256:") for digest in (*round_one.values(), *round_two.values())
    ):
        raise ReproducibilityError("staging image identity must use immutable sha256 digests")
    if dict(round_one) != dict(round_two):
        diagnostics: list[str] = []
        if round_one_snapshots is not None and round_two_snapshots is not None:
            for image in sorted(required):
                if round_one[image] != round_two[image]:
                    first = round_one_snapshots.get(image)
                    second = round_two_snapshots.get(image)
                    if first is not None and second is not None:
                        diagnostics.append(_image_diagnostic(image, first, second))
                    else:
                        diagnostics.append(f"{image}; forensic snapshot unavailable")
        if round_one_records is not None and round_two_records is not None:
            for image in sorted(required):
                if round_one[image] == round_two[image]:
                    continue
                record_differences = safe_record_differences(
                    round_one_records.get(image, ()), round_two_records.get(image, ())
                )
                displayed = record_differences[:100]
                diagnostics.extend(f"{image}; record; {line}" for line in displayed)
                if len(record_differences) > len(displayed):
                    diagnostics.append(
                        f"{image}; record differences omitted={len(record_differences) - len(displayed)}"
                    )
        detail = f"; forensic diagnostics: {' | '.join(diagnostics)}" if diagnostics else ""
        raise ReproducibilityError(f"independent staging build digests differ{detail}")
