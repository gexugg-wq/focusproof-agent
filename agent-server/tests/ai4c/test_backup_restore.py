from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
from threading import Event, Thread
from time import monotonic, sleep
from typing import Any, Final
from uuid import NAMESPACE_URL, uuid5

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.engine import URL

from focusproof import recovery as recovery_coordination

from .oidc_fixture import LocalOidcFixture, local_oidc_fixture
from .test_identity_end_to_end import _authorization, _install_jwks_fetch


PROJECT_ROOT: Final = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT: Final = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
backup: Any = importlib.import_module("ai4c_backup")
restore: Any = importlib.import_module("ai4c_restore")
staging_check: Any = importlib.import_module("ai4c_staging_check")
PROVIDER_KEYS: Final = (
    "DASHSCOPE_API_KEY",
    "OPENAI_API_KEY",
    "FOCUSPROOF_LLM_API_KEY",
    "ANTHROPIC_API_KEY",
)


@pytest.fixture(autouse=True)
def _enable_operations_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(staging_check.OPERATIONS_LOGGER, "disabled", False)


def test_backup_uses_sanitized_pg_dump_argv_and_publishes_paired_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_dir = tmp_path / "openhands"
    data_dir.mkdir()
    (data_dir / "conversation.json").write_text("native-state", encoding="utf-8")
    output_dir = tmp_path / "backup"
    calls: list[dict[str, Any]] = []
    for key in PROVIDER_KEYS:
        monkeypatch.setenv(key, f"secret-{key}")
    caplog.set_level(logging.INFO, logger="focusproof.operations")

    def fake_run(
        args: list[str],
        *,
        check: bool,
        timeout: float,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        calls.append(
            {"args": args, "check": check, "timeout": timeout, "env": env}
        )
        assert type(args) is list
        assert args[0] == "pg_dump"
        assert "--file" in args
        Path(args[args.index("--file") + 1]).write_bytes(b"postgres-dump")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    monkeypatch.setattr(backup, "current_application_revision", lambda: "rev-test")

    manifest = backup.create_backup(
        database_url="postgresql://focusproof:password@db/focusproof",
        openhands_data_dir=data_dir,
        output_dir=output_dir,
    )

    assert len(calls) == 1
    assert calls[0]["check"] is True
    assert 0 < calls[0]["timeout"] <= 300
    assert all(key not in calls[0]["env"] for key in PROVIDER_KEYS)
    assert "password" not in " ".join(calls[0]["args"])
    assert manifest.application_revision == "rev-test"
    assert len(manifest.database_sha256) == 64
    assert len(manifest.openhands_archive_sha256) == 64
    assert (output_dir / "database.dump").is_file()
    assert (output_dir / "openhands.tar.gz").is_file()
    assert (output_dir / "manifest.json").is_file()
    recovery_events = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "focusproof.operations"
    ]
    assert {"event": "recovery", "operation": "backup", "outcome": "completed"} in (
        recovery_events
    )


def test_postgres_driver_urls_are_normalized_to_secret_safe_cli_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(
        args: list[str],
        *,
        check: bool,
        timeout: float,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        assert check is True
        assert timeout > 0
        calls.append((args, env))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    monkeypatch.setattr(restore.subprocess, "run", fake_run)
    database_url = (
        "postgresql+psycopg://focusproof:local-password@127.0.0.1:5432/focusproof"
    )

    backup._run_pg_dump(database_url, tmp_path / "database.dump")
    restore._run_pg_restore(database_url, tmp_path / "database.dump")

    assert [args[0] for args, _env in calls] == ["pg_dump", "pg_restore"]
    assert all("+psycopg" not in " ".join(args) for args, _env in calls)
    assert all("local-password" not in " ".join(args) for args, _env in calls)
    assert all(env["PGPASSWORD"] == "local-password" for _args, env in calls)


def test_postgres_query_preserves_unix_socket_and_safe_libpq_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(
        args: list[str],
        *,
        check: bool,
        timeout: float,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        assert check is True
        assert timeout > 0
        calls.append((args, env))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    monkeypatch.setattr(restore.subprocess, "run", fake_run)
    database_url = (
        "postgresql+psycopg://focus%20proof:p%40ss%3Aword@/focus%2Fproof"
        "?host=%2Fvar%2Frun%2Fpostgresql&sslmode=verify-full"
        "&sslrootcert=%2Fetc%2Fssl%2Froot.pem&sslcert=%2Fetc%2Fssl%2Fclient.pem"
        "&sslkey=%2Frun%2Fsecrets%2Fclient.key&target_session_attrs=read-write"
        "&connect_timeout=7&options=-c%20statement_timeout%3D5000"
    )

    backup._run_pg_dump(database_url, tmp_path / "database.dump")
    restore._run_pg_restore(database_url, tmp_path / "database.dump")

    assert len(calls) == 2
    for args, env in calls:
        assert "/var/run/postgresql" in args
        assert "focus proof" in args
        assert "focus/proof" in args
        assert "p@ss:word" not in " ".join(args)
        assert env["PGPASSWORD"] == "p@ss:word"
        assert env["PGSSLMODE"] == "verify-full"
        assert env["PGSSLROOTCERT"] == "/etc/ssl/root.pem"
        assert env["PGSSLCERT"] == "/etc/ssl/client.pem"
        assert env["PGSSLKEY"] == "/run/secrets/client.key"
        assert env["PGTARGETSESSIONATTRS"] == "read-write"
        assert env["PGCONNECT_TIMEOUT"] == "7"
        assert env["PGOPTIONS"] == "-c statement_timeout=5000"


@pytest.mark.parametrize(
    "query",
    [
        "application_name=unsafe",
        "sslmode=require&sslmode=disable",
        "options=-c%20safe%0Ainjected",
    ],
)
def test_postgres_query_rejects_unknown_duplicate_or_control_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        backup.subprocess,
        "run",
        lambda args, **kwargs: calls.append(args),
    )
    monkeypatch.setattr(
        restore.subprocess,
        "run",
        lambda args, **kwargs: calls.append(args),
    )
    database_url = f"postgresql://focusproof@db/focusproof?{query}"

    with pytest.raises(backup.RecoveryError, match="query"):
        backup._run_pg_dump(database_url, tmp_path / "database.dump")
    with pytest.raises(restore.RecoveryValidationError, match="query"):
        restore._run_pg_restore(database_url, tmp_path / "database.dump")
    assert calls == []


def test_backup_publishes_the_paired_bundle_with_one_atomic_directory_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "openhands"
    data_dir.mkdir()
    (data_dir / "native-event.json").write_text("native", encoding="utf-8")
    output_dir = tmp_path / "paired"
    original_replace = backup.os.replace
    replacements: list[tuple[Path, Path]] = []

    def fake_dump(
        args: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        Path(args[args.index("--file") + 1]).write_bytes(b"postgres-dump")
        return subprocess.CompletedProcess(args, 0)

    def recording_replace(source: Path, target: Path) -> None:
        replacements.append((Path(source), Path(target)))
        original_replace(source, target)

    monkeypatch.setattr(backup.subprocess, "run", fake_dump)
    monkeypatch.setattr(backup, "current_application_revision", lambda: "rev-test")
    monkeypatch.setattr(backup.os, "replace", recording_replace)

    backup.create_backup(
        database_url="postgresql://db/focusproof",
        openhands_data_dir=data_dir,
        output_dir=output_dir,
    )

    assert len(replacements) == 1
    assert replacements[0][0].parent == output_dir.parent
    assert replacements[0][1] == output_dir
    assert {path.name for path in output_dir.iterdir()} == {
        "database.dump",
        "openhands.tar.gz",
        "manifest.json",
    }


def test_restore_rejects_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(restore.RecoveryValidationError, match="manifest"):
        restore.restore_backup(
            manifest_path=tmp_path / "missing.json",
            database_url="postgresql://db/focusproof",
            openhands_data_dir=tmp_path / "openhands",
        )


def test_restore_rejects_bundle_with_extra_member_before_pg_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, bundle = _paired_bundle(tmp_path, monkeypatch)
    (bundle / "operator-note.txt").write_text("unexpected", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(restore, "current_application_revision", lambda: "rev-test")
    monkeypatch.setattr(
        restore.subprocess,
        "run",
        lambda args, **kwargs: calls.append(args),
    )

    with pytest.raises(restore.RecoveryValidationError, match="exactly"):
        restore.restore_backup(
            manifest_path=manifest_path,
            database_url="postgresql://db/focusproof",
            openhands_data_dir=tmp_path / "restored",
        )
    assert calls == []


def test_backup_failure_removes_partial_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_dir = tmp_path / "openhands"
    data_dir.mkdir()
    (data_dir / "event.json").write_text("native", encoding="utf-8")
    output_dir = tmp_path / "paired"
    caplog.set_level(logging.INFO, logger="focusproof.operations")

    def fail_dump(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, ["pg_dump"])

    monkeypatch.setattr(backup.subprocess, "run", fail_dump)

    with pytest.raises(subprocess.CalledProcessError):
        backup.create_backup(
            database_url="postgresql://db/focusproof",
            openhands_data_dir=data_dir,
            output_dir=output_dir,
        )

    assert not output_dir.exists()
    assert list(tmp_path.glob(".paired-*")) == []
    recovery_events = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "focusproof.operations"
    ]
    assert {"event": "recovery", "operation": "backup", "outcome": "failed"} in (
        recovery_events
    )


def test_backup_rejects_symlink_in_openhands_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "openhands"
    data_dir.mkdir()
    secret = tmp_path / "evidence-secret"
    secret.write_text("must-not-archive", encoding="utf-8")
    (data_dir / "escape").symlink_to(secret)

    def fake_dump(
        args: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        Path(args[args.index("--file") + 1]).write_bytes(b"dump")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(backup.subprocess, "run", fake_dump)

    with pytest.raises(backup.RecoveryError, match="symlink"):
        backup.create_backup(
            database_url="postgresql://db/focusproof",
            openhands_data_dir=data_dir,
            output_dir=tmp_path / "paired",
        )


def _paired_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    data_dir = tmp_path / "source-openhands"
    data_dir.mkdir()
    (data_dir / "conversation.json").write_text("native-state", encoding="utf-8")
    output_dir = tmp_path / "paired"

    def fake_dump(
        args: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        Path(args[args.index("--file") + 1]).write_bytes(b"postgres-dump")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(backup.subprocess, "run", fake_dump)
    monkeypatch.setattr(backup, "current_application_revision", lambda: "rev-test")
    backup.create_backup(
        database_url="postgresql://db/focusproof",
        openhands_data_dir=data_dir,
        output_dir=output_dir,
    )
    return output_dir / "manifest.json", output_dir


def test_restore_rejects_revision_mismatch_before_pg_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, _ = _paired_bundle(tmp_path, monkeypatch)
    calls: list[list[str]] = []
    monkeypatch.setattr(restore, "current_application_revision", lambda: "other-rev")
    monkeypatch.setattr(
        restore.subprocess,
        "run",
        lambda args, **kwargs: calls.append(args),
    )

    with pytest.raises(restore.RecoveryValidationError, match="revision"):
        restore.restore_backup(
            manifest_path=manifest_path,
            database_url="postgresql://db/focusproof",
            openhands_data_dir=tmp_path / "restored",
        )
    assert calls == []


@pytest.mark.parametrize("member_name", ["../escape", "/absolute/path"])
def test_restore_rejects_traversal_and_absolute_archive_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    member_name: str,
) -> None:
    manifest_path, bundle = _paired_bundle(tmp_path, monkeypatch)
    archive = bundle / "openhands.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(member_name)
        info.size = 0
        tar.addfile(info)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["openhands_archive_sha256"] = backup._file_sha256(archive)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(restore, "current_application_revision", lambda: "rev-test")

    with pytest.raises(restore.RecoveryValidationError, match="archive"):
        restore.restore_backup(
            manifest_path=manifest_path,
            database_url="postgresql://db/focusproof",
            openhands_data_dir=tmp_path / "restored",
        )


def test_restore_uses_sanitized_pg_restore_argv_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manifest_path, _ = _paired_bundle(tmp_path, monkeypatch)
    target = tmp_path / "restored"
    calls: list[tuple[list[str], dict[str, str]]] = []
    caplog.set_level(logging.INFO, logger="focusproof.operations")
    monkeypatch.setattr(restore, "current_application_revision", lambda: "rev-test")

    def fake_restore(
        args: list[str], *, check: bool, timeout: float, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        assert check is True
        assert 0 < timeout <= 300
        calls.append((args, env))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(restore.subprocess, "run", fake_restore)
    for _ in range(2):
        restore.restore_backup(
            manifest_path=manifest_path,
            database_url="postgresql://focusproof:password@db/focusproof",
            openhands_data_dir=target,
        )

    assert len(calls) == 2
    assert all(call[0][0] == "pg_restore" for call in calls)
    assert all("password" not in " ".join(call[0]) for call in calls)
    assert all(key not in call[1] for call in calls for key in PROVIDER_KEYS)
    assert (target / "conversation.json").read_text(encoding="utf-8") == "native-state"
    recovery_events = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "focusproof.operations"
    ]
    assert recovery_events.count(
        {"event": "recovery", "operation": "restore", "outcome": "completed"}
    ) == 2


def test_maintenance_lock_is_exclusive_and_removed_without_secret_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = tmp_path / "openhands"
    data_dir.mkdir()

    with staging_check.maintenance_lock(data_dir) as lock_path:
        assert lock_path.is_file()
        assert staging_check.is_maintenance_locked(data_dir)
        with pytest.raises(staging_check.MaintenanceLockError):
            with staging_check.maintenance_lock(data_dir):
                pass

    assert not staging_check.is_maintenance_locked(data_dir)
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""


def test_maintenance_window_drains_entered_writer_and_rejects_new_writes(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "openhands"
    data_dir.mkdir()
    writer_entered = Event()
    release_writer = Event()
    maintenance_entered = Event()
    release_maintenance = Event()
    failures: list[BaseException] = []

    def entered_writer() -> None:
        try:
            with staging_check.writer_barrier(data_dir):
                writer_entered.set()
                assert release_writer.wait(5.0)
        except BaseException as exc:
            failures.append(exc)

    def recovery_operation() -> None:
        try:
            with staging_check.maintenance_window(data_dir):
                maintenance_entered.set()
                assert release_maintenance.wait(5.0)
        except BaseException as exc:
            failures.append(exc)

    writer_thread = Thread(target=entered_writer, daemon=True)
    recovery_thread = Thread(target=recovery_operation, daemon=True)
    writer_thread.start()
    assert writer_entered.wait(2.0)
    recovery_thread.start()
    deadline = monotonic() + 2.0
    while monotonic() < deadline and not staging_check.is_maintenance_locked(data_dir):
        sleep(0.01)

    try:
        assert staging_check.is_maintenance_locked(data_dir)
        assert not maintenance_entered.wait(0.1)
        with pytest.raises(staging_check.MaintenanceLockError):
            with staging_check.writer_barrier(data_dir):
                pass
        release_writer.set()
        assert maintenance_entered.wait(2.0)
        with pytest.raises(staging_check.MaintenanceLockError):
            with staging_check.writer_barrier(data_dir):
                pass
    finally:
        release_writer.set()
        release_maintenance.set()
        writer_thread.join(2.0)
        recovery_thread.join(2.0)

    assert not writer_thread.is_alive()
    assert not recovery_thread.is_alive()
    assert failures == []
    assert not staging_check.is_maintenance_locked(data_dir)


def test_default_recovery_coordination_path_is_external_and_gitignored() -> None:
    default_data_dir = PROJECT_ROOT / "var"
    coordination = recovery_coordination.coordination_dir_path(default_data_dir)

    assert coordination.parent == default_data_dir.parent
    assert not coordination.is_relative_to(default_data_dir)
    ignored = subprocess.run(
        [
            "git",
            "check-ignore",
            "--quiet",
            "--no-index",
            str(coordination / "writer-drain.lock"),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        timeout=10.0,
        env=backup._minimal_environment(),
    )
    assert ignored.returncode == 0


def test_restore_half_failure_keeps_incomplete_marker_and_app_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, _ = _paired_bundle(tmp_path, monkeypatch)
    target = tmp_path / "restored-openhands"
    target.mkdir()
    (target / "old-native.json").write_text("old", encoding="utf-8")
    monkeypatch.setattr(restore, "current_application_revision", lambda: "rev-test")
    monkeypatch.setattr(
        restore.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0),
    )

    def fail_after_postgres(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected OpenHands replacement failure")

    monkeypatch.setattr(restore, "_replace_openhands_persistence", fail_after_postgres)

    with pytest.raises(OSError, match="injected OpenHands"):
        restore.restore_backup(
            manifest_path=manifest_path,
            database_url="postgresql://db/focusproof",
            openhands_data_dir=target,
        )

    assert staging_check.is_recovery_incomplete(target)
    app = _recovery_app(
        f"sqlite+pysqlite:///{target / 'fail-closed.sqlite3'}",
        target,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        health = client.get("/health")
        readiness = client.get("/ready")
        rejected = client.post("/sessions", json={"evidence": "must-not-write"})

    assert health.status_code == 200
    assert readiness.status_code == 503
    assert readiness.json() == {"code": "recovery_incomplete", "retryable": True}
    assert rejected.status_code == 503
    assert rejected.json() == {"code": "recovery_incomplete", "retryable": True}


def test_backup_rejects_absolute_or_traversing_output_name(tmp_path: Path) -> None:
    data_dir = tmp_path / "openhands"
    data_dir.mkdir()
    with pytest.raises(backup.RecoveryError, match="output"):
        backup.create_backup(
            database_url="postgresql://db/focusproof",
            openhands_data_dir=data_dir,
            output_dir=tmp_path / "safe" / ".." / "escape",
        )


POSTGRES_IMAGE: Final = (
    "postgres:16-bookworm@sha256:"
    "ec2448d32297f61000a4b70edc2d27c9dfaedfc28cee3b827233fd4f05392dc8"
)
RECOVERY_EVIDENCE_TEXT: Final = "paired-recovery-evidence-sentinel"


def _external_run(
    args: list[str],
    *,
    timeout: float = 120.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=backup._minimal_environment(),
    )


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _start_postgres(
    *,
    container_name: str,
    volume_name: str,
    port: int,
    password_file: Path,
) -> None:
    _external_run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            container_name,
            "--publish",
            f"127.0.0.1:{port}:5432",
            "--volume",
            f"{volume_name}:/var/lib/postgresql/data",
            "--mount",
            f"type=bind,src={password_file},dst=/run/secrets/postgres_password,readonly",
            "--env",
            "POSTGRES_DB=focusproof",
            "--env",
            "POSTGRES_USER=focusproof",
            "--env",
            "POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password",
            POSTGRES_IMAGE,
        ]
    )
    deadline = monotonic() + 90.0
    while monotonic() < deadline:
        ready = _external_run(
            [
                "pg_isready",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--username",
                "focusproof",
                "--dbname",
                "focusproof",
            ],
            timeout=10.0,
            check=False,
        )
        if ready.returncode == 0:
            return
        sleep(0.25)
    pytest.fail("disposable_postgres_not_ready", pytrace=False)


def _docker_object_exists(kind: str, name: str) -> bool:
    inspected = _external_run(
        ["docker", kind, "inspect", name],
        timeout=30.0,
        check=False,
    )
    return inspected.returncode == 0


def _destroy_postgres_authoritatively(container_name: str, volume_name: str) -> None:
    _external_run(
        ["docker", "rm", "--force", container_name],
        timeout=30.0,
        check=True,
    )
    _external_run(
        ["docker", "volume", "rm", volume_name],
        timeout=30.0,
        check=True,
    )
    assert not _docker_object_exists("container", container_name)
    assert not _docker_object_exists("volume", volume_name)


def _cleanup_postgres(container_name: str, *volume_names: str) -> None:
    if _docker_object_exists("container", container_name):
        _external_run(
            ["docker", "rm", "--force", container_name],
            timeout=30.0,
            check=True,
        )
    for volume_name in volume_names:
        if _docker_object_exists("volume", volume_name):
            _external_run(
                ["docker", "volume", "rm", volume_name],
                timeout=30.0,
                check=True,
            )


def _migrate_external_database(database_url: str) -> None:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option(
        "script_location", str(PROJECT_ROOT / "agent-server/migrations")
    )
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def _recovery_app(database_url: str, data_dir: Path) -> Any:
    from focusproof.api import app as app_module

    return app_module.create_app(
        database_url=database_url,
        data_dir=data_dir,
        llm_factory=app_module.staging_test_llm,
    )


def _seed_two_owners_and_completed_review(
    database_url: str,
    data_dir: Path,
    fixture: LocalOidcFixture,
) -> dict[str, Any]:
    subjects = ("task5-owner-a", "task5-owner-b")
    tokens = tuple(fixture.token(subject=subject) for subject in subjects)
    session_ids: list[str] = []
    app = _recovery_app(database_url, data_dir)
    with TestClient(app) as client:
        for index, token in enumerate(tokens, start=1):
            created = client.post(
                "/sessions",
                headers=_authorization(token),
                json={
                    "domain": "general",
                    "title": f"Paired recovery owner {index}",
                    "goal": "Verify that paired recovery preserves learning facts.",
                    "expectedOutput": "A concise recovery explanation",
                    "plannedMinutes": 15,
                },
            )
            assert created.status_code == 200
            session_id = str(created.json()["sessionId"])
            session_ids.append(session_id)
            evidence = client.post(
                f"/sessions/{session_id}/evidence",
                headers=_authorization(token),
                json={
                    "evidenceType": "text",
                    "textContent": RECOVERY_EVIDENCE_TEXT,
                    "metadata": {"source": "paired-recovery"},
                },
            )
            assert evidence.status_code == 200
        first = client.post(
            f"/sessions/{session_ids[0]}/review",
            headers=_authorization(tokens[0]),
        )
        assert first.status_code == 200
        assert first.json()["reviewStatus"] == "awaiting_user"
        question_id = str(first.json()["agentQuestions"][0]["questionId"])
        owner_ids: list[str] = []
        with app.state.uow_factory() as uow:
            for subject in subjects:
                principal = uow.principals.get_exact(
                    issuer=fixture.issuer,
                    subject=subject,
                )
                assert principal is not None
                owner_ids.append(principal.principal_id)

    restored_app = _recovery_app(database_url, data_dir)
    with TestClient(restored_app) as client:
        answer = client.post(
            f"/sessions/{session_ids[0]}/answer",
            headers=_authorization(tokens[0]),
            json={
                "questionId": question_id,
                "answer": "Native event IDs remain durable across paired recovery.",
            },
        )
        assert answer.status_code == 200
        completed = client.post(
            f"/sessions/{session_ids[0]}/review",
            headers=_authorization(tokens[0]),
        )
        assert completed.status_code == 200
        assert completed.json()["reviewStatus"] == "completed"
    assert len(session_ids) == len(owner_ids) == 2
    return _recovery_snapshot(
        database_url,
        data_dir,
        (session_ids[0], session_ids[1]),
        (owner_ids[0], owner_ids[1]),
    )


def _recovery_snapshot(
    database_url: str,
    data_dir: Path,
    session_ids: tuple[str, str],
    owner_ids: tuple[str, str],
) -> dict[str, Any]:
    app = _recovery_app(database_url, data_dir)
    with TestClient(app):
        with app.state.uow_factory() as uow:
            sessions = [uow.sessions.get(session_id) for session_id in session_ids]
            evidence = [
                item
                for session_id in session_ids
                for item in uow.evidence.list_for_session(session_id)
            ]
            answers = [
                item
                for session_id in session_ids
                for item in uow.answers.list_for_session(session_id)
            ]
            reviews = uow.reviews.list_for_session(session_ids[0])
        native_events = []
        for session_id, owner_id in zip(session_ids, owner_ids, strict=True):
            handle = app.state.conversation_manager.get_or_restore(session_id, owner_id)
            native_events.append(
                [
                    (str(event.id), type(event).__name__)
                    for event in handle.conversation.state.events
                ]
            )
    assert all(session is not None for session in sessions)
    return {
        "sessions": [
            (session.session_id, session.owner_user_id, session.conversation_id)
            for session in sessions
            if session is not None
        ],
        "evidence": [
            (item.session_id, item.evidence_id, item.content_hash) for item in evidence
        ],
        "answers": [
            (item.session_id, item.question_id, item.answer, item.version)
            for item in answers
        ],
        "reviews": [
            (
                item.review_id,
                item.review_status,
                item.conversation_id,
                item.source_openhands_event_id,
            )
            for item in reviews
        ],
        "native_events": native_events,
    }


@pytest.mark.staging_external
def test_staging_external_restores_paired_product_and_native_state_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    import check_ai4c_capabilities

    report = check_ai4c_capabilities.detect_capabilities()
    check_ai4c_capabilities.require_capabilities(
        report, ("container_cli", "compose", "postgres_client")
    )
    suffix = uuid5(NAMESPACE_URL, str(tmp_path.resolve())).hex[:12]
    container_name = f"focusproof-task5-pg-{suffix}"
    volume_name = f"focusproof-task5-pgdata-{suffix}"
    restored_volume_name = f"focusproof-task5-restored-pgdata-{suffix}"
    password = "task5-local-postgres-password"
    password_file = tmp_path / "postgres_password"
    password_file.write_text(password, encoding="utf-8")
    password_file.chmod(0o600)
    port = _unused_loopback_port()
    database_url = URL.create(
        "postgresql+psycopg",
        username="focusproof",
        password=password,
        host="127.0.0.1",
        port=port,
        database="focusproof",
    ).render_as_string(hide_password=False)
    data_dir = tmp_path / "openhands"
    bundle_dir = tmp_path / "paired-backup"
    fixture = local_oidc_fixture()
    monkeypatch.setenv("FOCUSPROOF_PROFILE", "staging")
    monkeypatch.setenv("FOCUSPROOF_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("FOCUSPROOF_OIDC_ISSUER", fixture.issuer)
    monkeypatch.setenv("FOCUSPROOF_OIDC_AUDIENCE", fixture.audience)
    monkeypatch.setenv(
        "FOCUSPROOF_OIDC_JWKS_URI",
        "https://testserver/__test__/oidc/jwks",
    )
    monkeypatch.setenv("FOCUSPROOF_OIDC_ALLOWED_ALGORITHMS", "RS256")
    monkeypatch.setenv(
        "FOCUSPROOF_OIDC_FINGERPRINT_KEY",
        "task5-fingerprint-key-with-at-least-32-bytes",
    )
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    token_a = fixture.token(subject="task5-owner-a")

    try:
        _start_postgres(
            container_name=container_name,
            volume_name=volume_name,
            port=port,
            password_file=password_file,
        )
        _migrate_external_database(database_url)
        caplog.set_level(logging.WARNING, logger="sqlalchemy.engine")
        caplog.set_level(logging.WARNING, logger="sqlalchemy.engine.Engine")
        logging.disable(logging.INFO)
        expected = _seed_two_owners_and_completed_review(
            database_url,
            data_dir,
            fixture,
        )
        assert len(expected["sessions"]) == 2
        assert len({row[1] for row in expected["sessions"]}) == 2
        assert [row[1] for row in expected["reviews"]].count("completed") == 1
        assert all(expected["native_events"])
        session_ids = (
            str(expected["sessions"][0][0]),
            str(expected["sessions"][1][0]),
        )
        owner_ids = (
            str(expected["sessions"][0][1]),
            str(expected["sessions"][1][1]),
        )

        maintenance_app = _recovery_app(database_url, data_dir)
        with TestClient(maintenance_app) as client:
            with staging_check.maintenance_window(data_dir):
                rejected = client.post(
                    "/sessions",
                    headers=_authorization(token_a),
                    json={
                        "domain": "general",
                        "title": "Must be rejected",
                        "goal": "Prove maintenance rejects writes.",
                    },
                )
                healthy = client.get("/health")
        assert rejected.status_code == 503
        assert rejected.json() == {"code": "maintenance_mode", "retryable": True}
        assert healthy.status_code == 200

        backup.create_backup(
            database_url=database_url,
            openhands_data_dir=data_dir,
            output_dir=bundle_dir,
        )
        _destroy_postgres_authoritatively(container_name, volume_name)
        shutil.rmtree(data_dir)

        _start_postgres(
            container_name=container_name,
            volume_name=restored_volume_name,
            port=port,
            password_file=password_file,
        )
        restore.restore_backup(
            manifest_path=bundle_dir / "manifest.json",
            database_url=database_url,
            openhands_data_dir=data_dir,
        )
        first_restore = _recovery_snapshot(
            database_url,
            data_dir,
            session_ids,
            owner_ids,
        )
        assert first_restore == expected

        restore.restore_backup(
            manifest_path=bundle_dir / "manifest.json",
            database_url=database_url,
            openhands_data_dir=data_dir,
        )
        second_restore = _recovery_snapshot(
            database_url,
            data_dir,
            session_ids,
            owner_ids,
        )
        assert second_restore == expected
        assert len(second_restore["reviews"]) == len(expected["reviews"])
        assert [len(events) for events in second_restore["native_events"]] == [
            len(events) for events in expected["native_events"]
        ]
    finally:
        logging.disable(logging.NOTSET)
        _cleanup_postgres(container_name, volume_name, restored_volume_name)

    captured = capsys.readouterr()
    manifest_text = (bundle_dir / "manifest.json").read_text(encoding="utf-8")
    combined_output = captured.out + captured.err + caplog.text + manifest_text
    assert password not in combined_output
    assert RECOVERY_EVIDENCE_TEXT not in combined_output
