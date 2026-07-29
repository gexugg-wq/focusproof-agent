from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from typing import Final
from urllib.parse import unquote, urlsplit

from ai4c_staging_check import (
    MAINTENANCE_LOCK_NAME,
    maintenance_lock,
    recovery_outcome,
)


PROVIDER_KEYS: Final = frozenset(
    {
        "DASHSCOPE_API_KEY",
        "OPENAI_API_KEY",
        "FOCUSPROOF_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
    }
)
BACKUP_TIMEOUT_SECONDS: Final = 300.0


class RecoveryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RecoveryManifest:
    schema_version: int
    application_revision: str
    database_sha256: str
    openhands_archive_sha256: str
    created_at_utc: str


def current_application_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10.0,
        env=_minimal_environment(),
    )
    revision = completed.stdout.strip()
    if len(revision) != 40:
        raise RecoveryError("application revision is unavailable")
    return revision


def create_backup(
    *,
    database_url: str,
    openhands_data_dir: Path,
    output_dir: Path,
) -> RecoveryManifest:
    with recovery_outcome("backup"):
        return _create_backup(
            database_url=database_url,
            openhands_data_dir=openhands_data_dir,
            output_dir=output_dir,
        )


def _create_backup(
    *,
    database_url: str,
    openhands_data_dir: Path,
    output_dir: Path,
) -> RecoveryManifest:
    if ".." in output_dir.parts:
        raise RecoveryError("output path must not traverse parents")
    source = openhands_data_dir.resolve(strict=True)
    if not source.is_dir() or openhands_data_dir.is_symlink():
        raise RecoveryError("OpenHands persistence path must be a real directory")
    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if output_dir.exists():
        raise RecoveryError("output path already exists")
    lock = maintenance_lock(source)
    lock.__enter__()
    work_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent)
    )
    try:
        database_dump = work_dir / "database.dump"
        archive = work_dir / "openhands.tar.gz"
        manifest_path = work_dir / "manifest.json"
        _run_pg_dump(database_url, database_dump)
        _write_deterministic_archive(source, archive)
        manifest = RecoveryManifest(
            schema_version=1,
            application_revision=current_application_revision(),
            database_sha256=_file_sha256(database_dump),
            openhands_archive_sha256=_file_sha256(archive),
            created_at_utc=datetime.now(UTC).isoformat(),
        )
        manifest_path.write_text(
            json.dumps(asdict(manifest), sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        os.replace(work_dir, output_dir)
        return manifest
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        lock.__exit__(None, None, None)


def _run_pg_dump(database_url: str, destination: Path) -> None:
    parsed = urlsplit(database_url)
    if not _is_postgres_scheme(parsed.scheme):
        raise RecoveryError("database URL must use PostgreSQL")
    database_name = unquote(parsed.path.lstrip("/"))
    if not parsed.hostname or not database_name:
        raise RecoveryError("database URL is incomplete")
    args = ["pg_dump", "--format=custom", "--file", str(destination)]
    args.extend(["--host", parsed.hostname])
    if parsed.port is not None:
        args.extend(["--port", str(parsed.port)])
    if parsed.username is not None:
        args.extend(["--username", unquote(parsed.username)])
    args.append(database_name)
    env = _minimal_environment()
    if parsed.password is not None:
        env["PGPASSWORD"] = unquote(parsed.password)
    subprocess.run(
        args,
        check=True,
        timeout=BACKUP_TIMEOUT_SECONDS,
        env=env,
    )


def _is_postgres_scheme(scheme: str) -> bool:
    base, separator, driver = scheme.partition("+")
    return base in {"postgresql", "postgres"} and (
        not separator or bool(driver)
    )


def _minimal_environment() -> dict[str, str]:
    allowed = {"PATH", "LANG", "LC_ALL", "TZ"}
    return {
        key: value
        for key, value in os.environ.items()
        if key in allowed and key not in PROVIDER_KEYS
    }


def _write_deterministic_archive(source: Path, destination: Path) -> None:
    with destination.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
                    if path.is_symlink():
                        raise RecoveryError("OpenHands persistence contains a symlink")
                    if path.name == MAINTENANCE_LOCK_NAME:
                        continue
                    relative = path.relative_to(source)
                    info = tar.gettarinfo(str(path), arcname=relative.as_posix())
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    info.mode = 0o755 if path.is_dir() else 0o600
                    if path.is_file():
                        with path.open("rb") as payload:
                            tar.addfile(info, payload)
                    else:
                        tar.addfile(info)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
