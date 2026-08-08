from __future__ import annotations

from dataclasses import fields
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from typing import Any

from ai4c_backup import (
    BACKUP_TIMEOUT_SECONDS,
    PostgresUrlError,
    RecoveryManifest,
    _file_sha256,
    _minimal_environment,
    _postgres_cli_connection,
    current_application_revision,
)
from ai4c_staging_check import recovery_outcome
from ai4c_staging_check import maintenance_window
from focusproof.recovery import MaintenanceWindow


class RecoveryValidationError(RuntimeError):
    pass


def restore_backup(
    *,
    manifest_path: Path,
    database_url: str,
    openhands_data_dir: Path,
) -> None:
    with recovery_outcome("restore"):
        _restore_backup(
            manifest_path=manifest_path,
            database_url=database_url,
            openhands_data_dir=openhands_data_dir,
        )


def _restore_backup(
    *,
    manifest_path: Path,
    database_url: str,
    openhands_data_dir: Path,
) -> None:
    with maintenance_window(openhands_data_dir) as window:
        _restore_backup_in_window(
            manifest_path=manifest_path,
            database_url=database_url,
            openhands_data_dir=openhands_data_dir,
            window=window,
        )


def _restore_backup_in_window(
    *,
    manifest_path: Path,
    database_url: str,
    openhands_data_dir: Path,
    window: MaintenanceWindow,
) -> None:
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise RecoveryValidationError("manifest does not exist or is not regular")
    manifest = _read_manifest(manifest_path)
    bundle_dir = manifest_path.parent.resolve(strict=True)
    _require_exact_bundle(bundle_dir)
    database_dump = bundle_dir / "database.dump"
    archive = bundle_dir / "openhands.tar.gz"
    _require_regular_bundle_file(database_dump, bundle_dir)
    _require_regular_bundle_file(archive, bundle_dir)
    if _file_sha256(database_dump) != manifest.database_sha256:
        raise RecoveryValidationError("database digest mismatch")
    if _file_sha256(archive) != manifest.openhands_archive_sha256:
        raise RecoveryValidationError("OpenHands archive digest mismatch")
    if current_application_revision() != manifest.application_revision:
        raise RecoveryValidationError("application revision mismatch")
    _validate_archive(archive)
    window.begin_recovery()
    _run_pg_restore(database_url, database_dump)
    _replace_openhands_persistence(archive, openhands_data_dir)
    _verify_openhands_persistence(archive, openhands_data_dir)
    window.complete_recovery()


def _read_manifest(path: Path) -> RecoveryManifest:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryValidationError("manifest is invalid") from exc
    expected = {field.name for field in fields(RecoveryManifest)}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise RecoveryValidationError("manifest fields are invalid")
    try:
        manifest = RecoveryManifest(**payload)
    except TypeError as exc:
        raise RecoveryValidationError("manifest values are invalid") from exc
    if manifest.schema_version != 1:
        raise RecoveryValidationError("manifest schema is unsupported")
    for digest in (manifest.database_sha256, manifest.openhands_archive_sha256):
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise RecoveryValidationError("manifest digest is invalid")
    return manifest


def _require_regular_bundle_file(path: Path, bundle_dir: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise RecoveryValidationError("paired artifact is not a regular file")
    if path.resolve().parent != bundle_dir:
        raise RecoveryValidationError("paired artifact escapes bundle")


def _require_exact_bundle(bundle_dir: Path) -> None:
    expected = {"database.dump", "openhands.tar.gz", "manifest.json"}
    try:
        actual = {path.name for path in bundle_dir.iterdir()}
    except OSError as exc:
        raise RecoveryValidationError("paired bundle is unavailable") from exc
    if actual != expected:
        raise RecoveryValidationError("paired bundle must contain exactly three files")


def _validate_archive(path: Path) -> None:
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                candidate = Path(member.name)
                if (
                    candidate.is_absolute()
                    or ".." in candidate.parts
                    or member.issym()
                    or member.islnk()
                    or not (member.isfile() or member.isdir())
                ):
                    raise RecoveryValidationError("OpenHands archive member is unsafe")
    except (tarfile.TarError, OSError) as exc:
        raise RecoveryValidationError("OpenHands archive is invalid") from exc


def _run_pg_restore(database_url: str, source: Path) -> None:
    try:
        connection = _postgres_cli_connection(database_url)
    except PostgresUrlError as exc:
        raise RecoveryValidationError(str(exc)) from exc
    args = ["pg_restore", "--clean", "--if-exists", "--exit-on-error"]
    args.extend(["--host", connection.host])
    if connection.port is not None:
        args.extend(["--port", str(connection.port)])
    if connection.username is not None:
        args.extend(["--username", connection.username])
    args.extend(["--dbname", connection.database_name, str(source)])
    env = _minimal_environment()
    env.update(connection.libpq_environment)
    if connection.password is not None:
        env["PGPASSWORD"] = connection.password
    subprocess.run(args, check=True, timeout=BACKUP_TIMEOUT_SECONDS, env=env)


def _replace_openhands_persistence(archive: Path, target: Path) -> None:
    resolved_parent = target.resolve().parent
    resolved_parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=f".{target.name}-restore-", dir=resolved_parent))
    extracted = work_dir / "new"
    previous = work_dir / "previous"
    extracted.mkdir()
    try:
        with tarfile.open(archive, "r:gz") as source:
            source.extractall(extracted, filter="data")
        if target.is_symlink():
            raise RecoveryValidationError("OpenHands target must not be a symlink")
        if target.exists():
            os.replace(target, previous)
        try:
            os.replace(extracted, target)
        except BaseException:
            if previous.exists():
                os.replace(previous, target)
            raise
        if previous.exists():
            shutil.rmtree(previous)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _verify_openhands_persistence(archive: Path, target: Path) -> None:
    if target.is_symlink() or not target.is_dir():
        raise RecoveryValidationError("restored OpenHands target is invalid")
    restored_members = {
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
    }
    with tarfile.open(archive, "r:gz") as source:
        expected_members = {member.name.rstrip("/") for member in source.getmembers()}
        if restored_members != expected_members:
            raise RecoveryValidationError("restored OpenHands members differ")
        for member in source.getmembers():
            if not member.isfile():
                continue
            archived = source.extractfile(member)
            if archived is None:
                raise RecoveryValidationError("restored OpenHands file is missing")
            archived_digest = _stream_sha256(archived)
            with (target / member.name).open("rb") as restored:
                if _stream_sha256(restored) != archived_digest:
                    raise RecoveryValidationError("restored OpenHands digest differs")


def _stream_sha256(stream: Any) -> str:
    digest = sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()
