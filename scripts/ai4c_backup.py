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
from urllib.parse import parse_qsl, unquote, urlsplit

from ai4c_staging_check import (
    MAINTENANCE_LOCK_NAME,
    maintenance_window,
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
LIBPQ_QUERY_ENV: Final = {
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "options": "PGOPTIONS",
}


class RecoveryError(RuntimeError):
    pass


class PostgresUrlError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RecoveryManifest:
    schema_version: int
    application_revision: str
    database_sha256: str
    openhands_archive_sha256: str
    created_at_utc: str


@dataclass(frozen=True, slots=True)
class PostgresCliConnection:
    database_name: str
    host: str
    port: int | None
    username: str | None
    password: str | None
    libpq_environment: dict[str, str]


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
    with maintenance_window(source):
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


def _run_pg_dump(database_url: str, destination: Path) -> None:
    try:
        connection = _postgres_cli_connection(database_url)
    except PostgresUrlError as exc:
        raise RecoveryError(str(exc)) from exc
    args = ["pg_dump", "--format=custom", "--file", str(destination)]
    args.extend(["--host", connection.host])
    if connection.port is not None:
        args.extend(["--port", str(connection.port)])
    if connection.username is not None:
        args.extend(["--username", connection.username])
    args.append(connection.database_name)
    env = _minimal_environment()
    env.update(connection.libpq_environment)
    if connection.password is not None:
        env["PGPASSWORD"] = connection.password
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


def _postgres_cli_connection(database_url: str) -> PostgresCliConnection:
    parsed = urlsplit(database_url)
    if not _is_postgres_scheme(parsed.scheme):
        raise PostgresUrlError("database URL must use PostgreSQL")
    if parsed.fragment:
        raise PostgresUrlError("database URL query is invalid")
    try:
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError as exc:
        raise PostgresUrlError("database URL query is invalid") from exc
    query: dict[str, str] = {}
    allowed = set(LIBPQ_QUERY_ENV) | {"host"}
    for key, value in pairs:
        if key not in allowed or key in query or not _safe_connection_value(value):
            raise PostgresUrlError("database URL query is invalid")
        query[key] = value
    authority_host = unquote(parsed.hostname) if parsed.hostname is not None else None
    if authority_host is not None and "host" in query:
        raise PostgresUrlError("database URL query is invalid")
    host = query.pop("host", authority_host)
    database_name = unquote(parsed.path.lstrip("/"))
    username = unquote(parsed.username) if parsed.username is not None else None
    password = unquote(parsed.password) if parsed.password is not None else None
    try:
        port = parsed.port
    except ValueError as exc:
        raise PostgresUrlError("database URL is incomplete") from exc
    values = (host, database_name, username, password)
    if (
        not host
        or not database_name
        or any(value is not None and not _safe_connection_value(value) for value in values)
    ):
        raise PostgresUrlError("database URL is incomplete")
    if "sslmode" in query and query["sslmode"] not in {
        "disable",
        "allow",
        "prefer",
        "require",
        "verify-ca",
        "verify-full",
    }:
        raise PostgresUrlError("database URL query is invalid")
    if "target_session_attrs" in query and query["target_session_attrs"] not in {
        "any",
        "read-write",
        "read-only",
        "primary",
        "standby",
        "prefer-standby",
    }:
        raise PostgresUrlError("database URL query is invalid")
    timeout = query.get("connect_timeout")
    if timeout is not None and (not timeout.isascii() or not timeout.isdigit()):
        raise PostgresUrlError("database URL query is invalid")
    return PostgresCliConnection(
        database_name=database_name,
        host=host,
        port=port,
        username=username,
        password=password,
        libpq_environment={LIBPQ_QUERY_ENV[key]: value for key, value in query.items()},
    )


def _safe_connection_value(value: str) -> bool:
    return bool(value) and len(value) <= 4096 and all(
        ord(character) >= 32 and ord(character) != 127 for character in value
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
