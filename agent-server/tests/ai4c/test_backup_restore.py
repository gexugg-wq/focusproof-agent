from __future__ import annotations

from datetime import UTC, datetime
import importlib
import json
import logging
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
from time import monotonic, sleep
from typing import Any, Final
from uuid import NAMESPACE_URL, uuid5

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.engine import URL


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


def _remove_postgres(container_name: str, volume_name: str) -> None:
    _external_run(
        ["docker", "rm", "--force", container_name],
        timeout=30.0,
        check=False,
    )
    _external_run(
        ["docker", "volume", "rm", "--force", volume_name],
        timeout=30.0,
        check=False,
    )


def _migrate_external_database(database_url: str) -> None:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option(
        "script_location", str(PROJECT_ROOT / "agent-server/migrations")
    )
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def _stored_session(session_id: str, owner_id: str) -> Any:
    from focusproof.persistence.repositories import StoredSession

    now = datetime.now(UTC)
    return StoredSession(
        session_id=session_id,
        owner_user_id=owner_id,
        status="running",
        adapter_mode="openhands-local-scripted-test",
        domain="general",
        title="Paired recovery exercise",
        goal="Verify that paired recovery preserves learning facts",
        expected_output="A concise recovery explanation",
        planned_minutes=15,
        conversation_id=str(uuid5(NAMESPACE_URL, f"focusproof:{session_id}")),
        runtime_mode="openhands-local-scripted-test",
        review_result=None,
        goal_conversation_synced_at=None,
        version=1,
        created_at=now,
        updated_at=now,
    )


def _stored_evidence(session_id: str, evidence_id: str) -> Any:
    from focusproof.persistence.repositories import StoredEvidence

    return StoredEvidence(
        evidence_id=evidence_id,
        session_id=session_id,
        evidence_type="text",
        content_hash=f"sha256:{evidence_id}",
        text_content=RECOVERY_EVIDENCE_TEXT,
        source_url=None,
        metadata={"source": "paired-recovery"},
        conversation_synced_at=None,
        created_at=datetime.now(UTC),
    )


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
) -> dict[str, Any]:
    session_ids = ("task5-owner-a-session", "task5-owner-b-session")
    owner_ids = ("task5-owner-a", "task5-owner-b")
    app = _recovery_app(database_url, data_dir)
    with TestClient(app):
        uow_factory = app.state.uow_factory
        with uow_factory() as uow:
            for index, (session_id, owner_id) in enumerate(
                zip(session_ids, owner_ids, strict=True), start=1
            ):
                uow.sessions.create(_stored_session(session_id, owner_id))
                uow.evidence.add(_stored_evidence(session_id, f"task5-evidence-{index}"))
                if index == 2:
                    uow.answers.upsert(
                        session_id,
                        "recovery-question",
                        "owner-2-answer",
                    )
            uow.commit()
        manager = app.state.conversation_manager
        first = manager.run_review(session_ids[0], owner_ids[0])
        assert first.reviewStatus == "awaiting_user"
        question_id = first.agentQuestions[0]["questionId"]
        manager.get_or_restore(session_ids[1], owner_ids[1])

    restored_app = _recovery_app(database_url, data_dir)
    with TestClient(restored_app):
        with restored_app.state.uow_factory() as uow:
            uow.answers.upsert(
                session_ids[0],
                question_id,
                "owner-1-native-continuity-answer",
            )
            uow.commit()
        restored_manager = restored_app.state.conversation_manager
        restored_manager.send_answer(session_ids[0], owner_ids[0])
        completed = restored_manager.run_review(session_ids[0], owner_ids[0])
        assert completed.reviewStatus == "completed"
    return _recovery_snapshot(database_url, data_dir)


def _recovery_snapshot(database_url: str, data_dir: Path) -> dict[str, Any]:
    session_ids = ("task5-owner-a-session", "task5-owner-b-session")
    owner_ids = ("task5-owner-a", "task5-owner-b")
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
    monkeypatch.setenv("FOCUSPROOF_PROFILE", "local-dev")
    monkeypatch.setenv("FOCUSPROOF_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", database_url)

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
        expected = _seed_two_owners_and_completed_review(database_url, data_dir)
        assert len(expected["sessions"]) == 2
        assert {row[1] for row in expected["sessions"]} == {
            "task5-owner-a",
            "task5-owner-b",
        }
        assert [row[1] for row in expected["reviews"]].count("completed") == 1
        assert all(expected["native_events"])

        maintenance_app = _recovery_app(database_url, data_dir)
        with TestClient(maintenance_app) as client:
            with staging_check.maintenance_lock(data_dir):
                rejected = client.post(
                    "/sessions", json={"evidence": RECOVERY_EVIDENCE_TEXT}
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
        _remove_postgres(container_name, volume_name)
        shutil.rmtree(data_dir)

        _start_postgres(
            container_name=container_name,
            volume_name=volume_name,
            port=port,
            password_file=password_file,
        )
        restore.restore_backup(
            manifest_path=bundle_dir / "manifest.json",
            database_url=database_url,
            openhands_data_dir=data_dir,
        )
        first_restore = _recovery_snapshot(database_url, data_dir)
        assert first_restore == expected

        restore.restore_backup(
            manifest_path=bundle_dir / "manifest.json",
            database_url=database_url,
            openhands_data_dir=data_dir,
        )
        second_restore = _recovery_snapshot(database_url, data_dir)
        assert second_restore == expected
        assert len(second_restore["reviews"]) == len(expected["reviews"])
        assert [len(events) for events in second_restore["native_events"]] == [
            len(events) for events in expected["native_events"]
        ]
    finally:
        logging.disable(logging.NOTSET)
        _remove_postgres(container_name, volume_name)

    captured = capsys.readouterr()
    manifest_text = (bundle_dir / "manifest.json").read_text(encoding="utf-8")
    combined_output = captured.out + captured.err + caplog.text + manifest_text
    assert password not in combined_output
    assert RECOVERY_EVIDENCE_TEXT not in combined_output
