from __future__ import annotations

import base64
from hashlib import sha256
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


def _canonical_payload_roots(
    tmp_path: Path,
    name: str,
) -> tuple[Path, Path, Path]:
    coordination = tmp_path / name
    openhands = coordination / "conversations"
    media = coordination / "media" / "objects"
    openhands.mkdir(parents=True)
    media.mkdir(parents=True)
    return coordination, openhands, media


@pytest.mark.parametrize(
    ("openhands_relative", "media_relative"),
    [
        ("wrong", "media/objects"),
        ("conversations", "wrong"),
        ("conversations", "conversations/media"),
        ("media/objects/conversations", "media/objects"),
    ],
)
def test_recovery_payload_layout_is_exact_and_non_nested(
    tmp_path: Path,
    openhands_relative: str,
    media_relative: str,
) -> None:
    coordination = tmp_path / "data"
    openhands = coordination / openhands_relative
    media = coordination / media_relative
    openhands.mkdir(parents=True, exist_ok=True)
    media.mkdir(parents=True, exist_ok=True)

    with pytest.raises(backup.RecoveryError, match="layout"):
        backup._recovery_payload_layout(coordination, openhands, media)
    with pytest.raises(restore.RecoveryValidationError, match="layout"):
        restore._recovery_payload_layout(coordination, openhands, media)


def test_recovery_payload_layout_accepts_canonical_roots(tmp_path: Path) -> None:
    coordination = tmp_path / "data"
    openhands = coordination / "conversations"
    media = coordination / "media" / "objects"
    openhands.mkdir(parents=True)
    media.mkdir(parents=True)

    assert backup._recovery_payload_layout(coordination, openhands, media) == (
        openhands.resolve(),
        media.resolve(),
    )
    assert restore._recovery_payload_layout(coordination, openhands, media) == (
        openhands.resolve(),
        media.resolve(),
    )


def test_recovery_payload_layout_rejects_symlink_payload_root(tmp_path: Path) -> None:
    coordination = tmp_path / "data"
    coordination.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    openhands = coordination / "conversations"
    openhands.symlink_to(outside, target_is_directory=True)
    media = coordination / "media" / "objects"
    media.mkdir(parents=True)

    with pytest.raises(backup.RecoveryError, match="layout"):
        backup._recovery_payload_layout(coordination, openhands, media)
    with pytest.raises(restore.RecoveryValidationError, match="layout"):
        restore._recovery_payload_layout(coordination, openhands, media)


def test_v2_backup_requires_explicit_media_directory_before_pg_dump(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openhands_dir = tmp_path / "openhands"
    openhands_dir.mkdir()
    calls: list[list[str]] = []
    monkeypatch.setattr(
        backup.subprocess,
        "run",
        lambda args, **kwargs: calls.append(args),
    )

    with pytest.raises(TypeError, match="media_data_dir"):
        backup.create_backup(
            database_url="postgresql://db/focusproof",
            openhands_data_dir=openhands_dir,
            output_dir=tmp_path / "paired",
        )
    assert calls == []


def test_backup_uses_sanitized_pg_dump_argv_and_publishes_paired_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    coordination_dir, data_dir, media_dir = _canonical_payload_roots(tmp_path, "data")
    coordination_sentinel = coordination_dir / "coordination-only.marker"
    coordination_sentinel.write_text("not-a-payload", encoding="utf-8")
    (data_dir / "conversation.json").write_text("native-state", encoding="utf-8")
    (media_dir / "artifact.bin").write_bytes(b"normalized-media")
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
        calls.append({"args": args, "check": check, "timeout": timeout, "env": env})
        assert type(args) is list
        assert args[0] == "pg_dump"
        assert "--file" in args
        Path(args[args.index("--file") + 1]).write_bytes(b"postgres-dump")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    monkeypatch.setattr(backup, "current_application_revision", lambda: "rev-test")

    manifest = backup.create_backup(
        database_url="postgresql://focusproof:password@db/focusproof",
        coordination_data_dir=coordination_dir,
        openhands_data_dir=data_dir,
        media_data_dir=media_dir,
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
    assert manifest.schema_version == 2
    assert manifest.openhands_tree_version == 1
    assert manifest.media_tree_version == 1
    assert len(manifest.media_archive_sha256) == 64
    assert (output_dir / "database.dump").is_file()
    assert (output_dir / "openhands.tar.gz").is_file()
    assert (output_dir / "media.tar.gz").is_file()
    assert (output_dir / "manifest.json").is_file()
    recovery_events = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "focusproof.operations"
    ]
    assert {"event": "recovery", "operation": "backup", "outcome": "completed"} in (recovery_events)


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
    database_url = "postgresql+psycopg://focusproof:local-password@127.0.0.1:5432/focusproof"

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
    coordination_dir, data_dir, media_dir = _canonical_payload_roots(tmp_path, "data")
    coordination_sentinel = coordination_dir / "coordination-only.marker"
    coordination_sentinel.write_text("not-a-payload", encoding="utf-8")
    (data_dir / "native-event.json").write_text("native", encoding="utf-8")
    (media_dir / "artifact.bin").write_bytes(b"normalized-media")
    output_dir = tmp_path / "paired"
    original_replace = backup.os.replace
    replacements: list[tuple[Path, Path]] = []

    def fake_dump(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
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
        coordination_data_dir=coordination_dir,
        openhands_data_dir=data_dir,
        media_data_dir=media_dir,
        output_dir=output_dir,
    )

    assert len(replacements) == 1
    assert replacements[0][0].parent == output_dir.parent
    assert replacements[0][1] == output_dir
    assert coordination_sentinel.read_text(encoding="utf-8") == "not-a-payload"
    with tarfile.open(output_dir / "openhands.tar.gz", "r:gz") as archive:
        openhands_members = {member.name for member in archive.getmembers()}
    with tarfile.open(output_dir / "media.tar.gz", "r:gz") as archive:
        media_members = {member.name for member in archive.getmembers()}
    assert openhands_members == {"native-event.json"}
    assert media_members == {"artifact.bin"}
    assert all(not name.startswith("media/") for name in openhands_members)
    assert all(not name.startswith("conversations/") for name in media_members)
    assert "coordination-only.marker" not in openhands_members | media_members
    assert {path.name for path in output_dir.iterdir()} == {
        "database.dump",
        "openhands.tar.gz",
        "manifest.json",
        "media.tar.gz",
    }


def test_restore_rejects_missing_manifest(tmp_path: Path) -> None:
    coordination, openhands, media = _canonical_payload_roots(tmp_path, "data")
    with pytest.raises(restore.RecoveryValidationError, match="manifest"):
        restore.restore_backup(
            manifest_path=tmp_path / "missing.json",
            database_url="postgresql://db/focusproof",
            recovery_admin_url="postgresql://admin@db/postgres",
            coordination_data_dir=coordination,
            openhands_data_dir=openhands,
            media_data_dir=media,
        )


def test_v2_restore_requires_explicit_media_and_recovery_admin_url(
    tmp_path: Path,
) -> None:
    common = {
        "manifest_path": tmp_path / "manifest.json",
        "database_url": "postgresql://app@db/focusproof",
        "openhands_data_dir": tmp_path / "openhands",
    }

    with pytest.raises(TypeError, match="media_data_dir"):
        restore.restore_backup(
            **common,
            recovery_admin_url="postgresql://admin@db/postgres",
        )
    with pytest.raises(TypeError, match="recovery_admin_url"):
        restore.restore_backup(
            **common,
            media_data_dir=tmp_path / "media",
        )


def test_restore_layout_accepts_destroyed_canonical_payload_roots(tmp_path: Path) -> None:
    coordination = tmp_path / "data"
    coordination.mkdir()
    openhands = coordination / "conversations"
    media = coordination / "media" / "objects"

    assert restore._recovery_payload_layout(coordination, openhands, media) == (
        openhands.resolve(),
        media.resolve(),
    )


@pytest.mark.parametrize(
    ("admin_url", "match"),
    [
        ("postgresql://admin@db/focusproof", "maintenance"),
        ("postgresql://admin@other/postgres", "host"),
        ("postgresql://admin@db:5433/postgres", "port"),
        (
            "postgresql://admin@db/postgres?sslmode=disable",
            "TLS",
        ),
    ],
)
def test_recovery_admin_url_must_match_target_cluster_but_not_target_database(
    admin_url: str,
    match: str,
) -> None:
    target_url = "postgresql://app@db:5432/focusproof?sslmode=require"

    with pytest.raises(restore.RecoveryValidationError, match=match):
        restore._recovery_connections(target_url, admin_url)


def test_recovery_admin_url_accepts_distinct_cluster_maintenance_database() -> None:
    target, admin = restore._recovery_connections(
        "postgresql://app:app-secret@db:5432/focusproof?sslmode=require",
        "postgresql://admin:admin-secret@db:5432/postgres?sslmode=require",
    )

    assert target.database_name == "focusproof"
    assert admin.database_name == "postgres"


def test_recovery_database_names_are_random_and_whitelisted() -> None:
    first = restore._recovery_database_names()
    second = restore._recovery_database_names()

    assert first != second
    for shadow, rollback in (first, second):
        assert shadow.startswith("focusproof_shadow_")
        assert rollback.startswith("focusproof_rollback_")
        assert shadow.removeprefix("focusproof_shadow_").isalnum()
        assert rollback.removeprefix("focusproof_rollback_").isalnum()
        assert len(shadow) <= 63
        assert len(rollback) <= 63


def test_database_ddl_uses_structured_identifiers() -> None:
    rendered: list[str] = []

    class Cursor:
        def execute(self, statement: Any, params: Any = None) -> None:
            assert not isinstance(statement, str)
            rendered.append(statement.as_string())

    restore._create_database(
        Cursor(),
        "focusproof_shadow_deadbeef",
        "focusproof_app",
    )
    restore._rename_database(Cursor(), "focusproof", "focusproof_rollback_deadbeef")
    restore._drop_database(Cursor(), "focusproof_shadow_deadbeef")

    assert rendered == [
        'CREATE DATABASE "focusproof_shadow_deadbeef" OWNER "focusproof_app"',
        'ALTER DATABASE "focusproof" RENAME TO "focusproof_rollback_deadbeef"',
        'DROP DATABASE IF EXISTS "focusproof_shadow_deadbeef"',
    ]


@pytest.mark.parametrize(
    ("db_rows", "event_refs", "media_files", "match"),
    [
        (
            [
                (
                    "artifact-a",
                    "referenced/a.bin",
                    "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
                )
            ],
            [],
            {"referenced/a.bin": b"a"},
            "EventLog",
        ),
        (
            [],
            [],
            {"referenced/extra.bin": b"x"},
            "extra",
        ),
        (
            [
                (
                    "artifact-a",
                    "referenced/a.bin",
                    "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
                )
            ],
            ["focusproof-artifact://artifact-a", "focusproof-artifact://artifact-a"],
            {"referenced/a.bin": b"a"},
            "duplicate",
        ),
        (
            [
                (
                    "artifact-a",
                    "../escape",
                    "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
                )
            ],
            ["focusproof-artifact://artifact-a"],
            {},
            "path",
        ),
        (
            [
                (
                    "artifact-a",
                    "referenced/a.bin",
                    "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
                )
            ],
            ["focusproof-artifact://artifact-a"],
            {"referenced/a.bin": b"different"},
            "hash",
        ),
    ],
)
def test_three_source_reconciliation_rejects_every_mismatch(
    tmp_path: Path,
    db_rows: list[tuple[str, str, str]],
    event_refs: list[str],
    media_files: dict[str, bytes],
    match: str,
) -> None:
    for relative, payload in media_files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    with pytest.raises(restore.RecoveryValidationError, match=match):
        restore._reconcile_three_sources(db_rows, event_refs, tmp_path)


def test_openhands_event_refs_use_official_event_models(tmp_path: Path) -> None:
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import ImageContent, Message, TextContent
    from focusproof.openhands_runtime.synchronizer import serialize_message_envelope

    payload = b"official-image"
    conversation_id = "11111111111111111111111111111111"
    events_dir = tmp_path / "session-a" / "persistence" / conversation_id / "events"
    events_dir.mkdir(parents=True)
    event = MessageEvent(
        source="user",
        sender="owner-a",
        llm_message=Message(
            role="user",
            content=[
                TextContent(
                    text=serialize_message_envelope(
                        schema_version=1,
                        message_key="evidence:ev-a",
                        kind="evidence",
                        session_id="session-a",
                        payload={
                            "artifact_ref": "focusproof-artifact://artifact-a",
                            "media_type": "image/png",
                            "normalized_sha256": sha256(payload).hexdigest(),
                            "byte_size": len(payload),
                        },
                    )
                ),
                ImageContent(
                    image_urls=[
                        "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
                    ]
                ),
            ],
        ),
    )
    (events_dir / f"event-00000-{event.id}.json").write_text(
        event.model_dump_json(exclude_none=True),
        encoding="utf-8",
    )

    assert restore._openhands_artifact_refs(tmp_path) == [
        restore.EventArtifactFact(
            owner_user_id="owner-a",
            session_id="session-a",
            conversation_id=conversation_id,
            evidence_id="ev-a",
            artifact_id="artifact-a",
            media_type="image/png",
            normalized_sha256=sha256(payload).hexdigest(),
            normalized_byte_size=len(payload),
        )
    ]


def test_shadow_artifact_rows_rejects_media_owner_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "a" * 64
    row = (
        "owner-a",
        "owner-b",
        "session-a",
        "1" * 32,
        "ev-a",
        "artifact-a",
        "a.bin",
        "image/png",
        digest,
        1,
    )

    class FakeCursor:
        def execute(self, _query: Any, _params: Any = None) -> None:
            return None

        def fetchall(self) -> list[tuple[str, str, str, str, str, str, str, str, str, int]]:
            return [row]

        def __enter__(self) -> FakeCursor:
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

    class FakeConnection:
        def cursor(self) -> FakeCursor:
            return FakeCursor()

        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

    monkeypatch.setattr(restore.psycopg, "connect", lambda _url: FakeConnection())

    with pytest.raises(restore.RecoveryValidationError, match="media owner differs"):
        restore._shadow_artifact_rows("postgresql://db/focusproof")


def test_openhands_event_refs_reject_agent_source_even_with_assistant_message(
    tmp_path: Path,
) -> None:
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import ImageContent, Message, TextContent
    from focusproof.openhands_runtime.synchronizer import serialize_message_envelope

    payload = b"official-image"
    conversation_id = "1" * 32
    events_dir = tmp_path / "session-a" / "persistence" / conversation_id / "events"
    events_dir.mkdir(parents=True)
    event = MessageEvent(
        source="agent",
        sender="owner-a",
        llm_message=Message(
            role="assistant",
            content=[
                TextContent(
                    text=serialize_message_envelope(
                        schema_version=1,
                        message_key="evidence:ev-a",
                        kind="evidence",
                        session_id="session-a",
                        payload={
                            "artifact_ref": "focusproof-artifact://artifact-a",
                            "media_type": "image/png",
                            "normalized_sha256": sha256(payload).hexdigest(),
                            "byte_size": len(payload),
                        },
                    )
                ),
                ImageContent(
                    image_urls=[
                        "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
                    ]
                ),
            ],
        ),
    )
    (events_dir / f"event-00000-{event.id}.json").write_text(
        event.model_dump_json(exclude_none=True),
        encoding="utf-8",
    )

    with pytest.raises(restore.RecoveryValidationError, match="source"):
        restore._openhands_artifact_refs(tmp_path)


def test_openhands_event_refs_reject_invalid_conversation_path_for_official_event(
    tmp_path: Path,
) -> None:
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import ImageContent, Message, TextContent
    from focusproof.openhands_runtime.synchronizer import serialize_message_envelope

    payload = b"official-image"
    events_dir = tmp_path / "session-a" / "persistence" / "abc" / "events"
    events_dir.mkdir(parents=True)
    event = MessageEvent(
        source="user",
        sender="owner-a",
        llm_message=Message(
            role="user",
            content=[
                TextContent(
                    text=serialize_message_envelope(
                        schema_version=1,
                        message_key="evidence:ev-a",
                        kind="evidence",
                        session_id="session-a",
                        payload={
                            "artifact_ref": "focusproof-artifact://artifact-a",
                            "media_type": "image/png",
                            "normalized_sha256": sha256(payload).hexdigest(),
                            "byte_size": len(payload),
                        },
                    )
                ),
                ImageContent(
                    image_urls=[
                        "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
                    ]
                ),
            ],
        ),
    )
    (events_dir / "event-0.json").write_text(
        event.model_dump_json(exclude_none=True),
        encoding="utf-8",
    )

    with pytest.raises(restore.RecoveryValidationError, match="conversation path"):
        restore._openhands_artifact_refs(tmp_path)


def test_create_session_persists_canonical_conversation_identity_and_restore_keeps_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_url = f"sqlite+pysqlite:///{data_dir / 'canonical-conversation.sqlite3'}"
    fixture = local_oidc_fixture()
    _migrate_external_database(database_url)
    monkeypatch.setenv("FOCUSPROOF_PROFILE", "staging")
    monkeypatch.setenv("FOCUSPROOF_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOCUSPROOF_MEDIA_ENABLED", "true")
    monkeypatch.setenv("FOCUSPROOF_MEDIA_SCANNER_MODE", "clamd")
    monkeypatch.setenv("FOCUSPROOF_CLAMD_ENDPOINT", "tcp://127.0.0.1:3310")
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
    token = fixture.token(subject="canonical-owner")

    app = _recovery_app(database_url, data_dir)
    with TestClient(app) as client:
        created = client.post(
            "/sessions",
            headers=_authorization(token),
            json={
                "domain": "general",
                "title": "Canonical identity",
                "goal": "Persist the OpenHands conversation identity canonically.",
            },
        )
        assert created.status_code == 200
        session_id = str(created.json()["sessionId"])
        with app.state.uow_factory() as uow:
            session = uow.sessions.get(session_id)
            assert session is not None
            owner_id = session.owner_user_id
            persisted_conversation_id = session.conversation_id
        created_handle = app.state.conversation_manager.get_or_restore(session_id, owner_id)

    assert persisted_conversation_id == created_handle.conversation_id.hex
    assert len(persisted_conversation_id) == 32
    assert "-" not in persisted_conversation_id
    assert (data_dir / "conversations" / session_id / "persistence" / persisted_conversation_id).is_dir()


def _structured_fact(
    *,
    owner: str = "owner-a",
    session: str = "session-a",
    conversation: str = "11111111111111111111111111111111",
    evidence: str = "ev-a",
    media_type: str = "image/png",
    byte_size: int = 1,
) -> tuple[Any, Any]:
    digest = sha256(b"a").hexdigest()
    db_fact = restore.DatabaseArtifactFact(
        owner_user_id=owner,
        session_id=session,
        conversation_id=conversation,
        evidence_id=evidence,
        artifact_id="artifact-a",
        opaque_object_key="a.bin",
        media_type=media_type,
        normalized_sha256=digest,
        normalized_byte_size=byte_size,
    )
    event_fact = restore.EventArtifactFact(
        owner_user_id=owner,
        session_id=session,
        conversation_id=conversation,
        evidence_id=evidence,
        artifact_id="artifact-a",
        media_type=media_type,
        normalized_sha256=digest,
        normalized_byte_size=byte_size,
    )
    return db_fact, event_fact


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("owner_user_id", "owner-b"),
        ("session_id", "session-b"),
        ("conversation_id", "22222222222222222222222222222222"),
        ("evidence_id", "ev-wrong"),
        ("media_type", "image/jpeg"),
        ("normalized_byte_size", 2),
    ],
)
def test_structured_reconciliation_rejects_binding_and_metadata_mismatch(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    from dataclasses import replace

    db_fact, event_fact = _structured_fact()
    event_fact = replace(event_fact, **{field: replacement})
    target = tmp_path / "referenced" / "a.bin"
    target.parent.mkdir()
    target.write_bytes(b"a")

    with pytest.raises(restore.RecoveryValidationError, match="differ"):
        restore._reconcile_three_sources([db_fact], [event_fact], tmp_path)


def test_structured_reconciliation_rejects_equivalent_hyphenated_conversation_uuid(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    db_fact, event_fact = _structured_fact()
    db_fact = replace(
        db_fact,
        conversation_id="11111111-1111-1111-1111-111111111111",
    )
    target = tmp_path / "referenced" / "a.bin"
    target.parent.mkdir()
    target.write_bytes(b"a")

    with pytest.raises(restore.RecoveryValidationError, match="structured facts differ"):
        restore._reconcile_three_sources([db_fact], [event_fact], tmp_path)


def test_structured_reconciliation_accepts_two_sessions_and_owners(tmp_path: Path) -> None:
    first_db, first_event = _structured_fact()
    second_db, second_event = _structured_fact(
        owner="owner-b",
        session="session-b",
        conversation="22222222222222222222222222222222",
        evidence="ev-b",
    )
    from dataclasses import replace

    second_db = replace(
        second_db, artifact_id="artifact-b", opaque_object_key="b.bin"
    )
    second_event = replace(second_event, artifact_id="artifact-b")
    referenced = tmp_path / "referenced"
    referenced.mkdir()
    (referenced / "a.bin").write_bytes(b"a")
    (referenced / "b.bin").write_bytes(b"a")

    restore._reconcile_three_sources(
        [first_db, second_db], [first_event, second_event], tmp_path
    )


def test_reconciliation_rejects_mixed_structured_and_legacy_inputs(tmp_path: Path) -> None:
    db_fact, event_fact = _structured_fact()
    legacy_db = ("artifact-b", "referenced/b.bin", sha256(b"b").hexdigest())
    with pytest.raises(restore.RecoveryValidationError, match="mixed"):
        restore._reconcile_three_sources([db_fact, legacy_db], [event_fact], tmp_path)
    with pytest.raises(restore.RecoveryValidationError, match="mixed"):
        restore._reconcile_three_sources(
            [db_fact], [event_fact, "focusproof-artifact://artifact-b"], tmp_path
        )


@pytest.mark.parametrize(
    ("db_rows", "event_refs"),
    [
        (lambda db_fact, _event_fact: [db_fact], lambda _db_fact, _event_fact: []),
        (lambda _db_fact, _event_fact: [], lambda _db_fact, event_fact: [event_fact]),
    ],
)
def test_reconciliation_rejects_structured_vs_empty_inputs(
    tmp_path: Path,
    db_rows: Any,
    event_refs: Any,
) -> None:
    db_fact, event_fact = _structured_fact()
    target = tmp_path / "referenced" / "a.bin"
    target.parent.mkdir()
    target.write_bytes(b"a")

    with pytest.raises(restore.RecoveryValidationError, match="facts differ"):
        restore._reconcile_three_sources(
            db_rows(db_fact, event_fact),
            event_refs(db_fact, event_fact),
            tmp_path,
        )


@pytest.mark.parametrize(
    "resource",
    ["rollback_database", "previous_openhands", "previous_media"],
)
def test_post_commit_cleanup_failure_is_warned_without_rollback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    resource: str,
) -> None:
    rollback_calls: list[str] = []
    retained = restore._best_effort_post_commit_cleanup(
        cleanup_actions={
            resource: lambda: (_ for _ in ()).throw(OSError("cleanup failed"))
        },
        rollback=lambda: rollback_calls.append("rollback"),
    )

    assert retained == [resource]
    assert rollback_calls == []
    assert "cleanup" in caplog.text
    assert resource in caplog.text


def test_openhands_event_refs_reject_invalid_official_event(tmp_path: Path) -> None:
    event_file = tmp_path / "events" / "event-00000-invalid.json"
    event_file.parent.mkdir()
    event_file.write_text('{"invented":"protocol"}', encoding="utf-8")

    with pytest.raises(restore.RecoveryValidationError, match="EventLog"):
        restore._openhands_artifact_refs(tmp_path)


def test_second_database_rename_failure_restores_live_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations: list[tuple[str, str]] = []
    rename_calls = 0

    class Cursor:
        pass

    def rename(_cursor: Any, source: str, target: str) -> None:
        nonlocal rename_calls
        rename_calls += 1
        operations.append((source, target))
        if rename_calls == 2:
            raise OSError("shadow promotion failed")

    monkeypatch.setattr(restore, "_rename_database", rename)
    monkeypatch.setattr(
        restore,
        "_terminate_database_connections",
        lambda cursor, name: None,
    )
    monkeypatch.setattr(restore, "_drop_database", lambda cursor, name: None)

    with pytest.raises(OSError, match="shadow promotion"):
        restore._cutover_database(
            Cursor(),
            live_name="focusproof",
            shadow_name="focusproof_shadow_deadbeef",
            rollback_name="focusproof_rollback_deadbeef",
        )

    assert operations == [
        ("focusproof", "focusproof_rollback_deadbeef"),
        ("focusproof_shadow_deadbeef", "focusproof"),
        ("focusproof_rollback_deadbeef", "focusproof"),
    ]


@pytest.mark.parametrize(
    "failure_stage",
    ["shadow_restore", "database_cutover", "openhands_swap", "media_swap", "postverify"],
)
def test_restore_failure_matrix_preserves_or_fully_restores_live_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    manifest_path, bundle = _paired_bundle(tmp_path, monkeypatch)
    coordination, live_openhands, live_media = _canonical_payload_roots(tmp_path, "live")
    (live_openhands / "old-event.json").write_text("old-openhands", encoding="utf-8")
    (live_media / "old-media.bin").write_bytes(b"old-media")
    database_state = {"live": "old", "shadow": "new", "rollback": None}

    class CursorContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: Any) -> None:
            return None

    class Admin:
        def cursor(self) -> CursorContext:
            return CursorContext()

        def close(self) -> None:
            return None

    class Window:
        started = False
        completed = False

        def begin_recovery(self) -> None:
            self.started = True

        def complete_recovery(self) -> None:
            self.completed = True

    window = Window()
    monkeypatch.setattr(restore, "current_application_revision", lambda: "rev-test")
    monkeypatch.setattr(restore, "_connect_admin", lambda url: Admin())
    monkeypatch.setattr(restore, "_create_database", lambda cursor, name, owner: None)
    monkeypatch.setattr(restore, "_drop_database", lambda cursor, name: None)
    monkeypatch.setattr(restore, "_terminate_database_connections", lambda cursor, name: None)
    monkeypatch.setattr(
        restore,
        "_recovery_database_names",
        lambda: ("focusproof_shadow_deadbeef", "focusproof_rollback_deadbeef"),
    )
    monkeypatch.setattr(restore, "_shadow_artifact_rows", lambda url: [])
    monkeypatch.setattr(restore, "_openhands_artifact_refs", lambda root: [])
    reconcile_calls = 0

    def reconcile(rows: Any, refs: Any, media: Path) -> None:
        nonlocal reconcile_calls
        reconcile_calls += 1
        if failure_stage == "postverify" and reconcile_calls == 2:
            raise OSError("postverify failed")

    monkeypatch.setattr(restore, "_reconcile_three_sources", reconcile)

    def pg_restore(url: str, source: Path) -> None:
        if failure_stage == "shadow_restore":
            raise OSError("shadow restore failed")

    monkeypatch.setattr(restore, "_run_pg_restore", pg_restore)

    def cutover(cursor: Any, **kwargs: str) -> None:
        if failure_stage == "database_cutover":
            raise OSError("database cutover failed")
        database_state["rollback"] = database_state["live"]
        database_state["live"] = database_state["shadow"]
        database_state["shadow"] = None

    monkeypatch.setattr(restore, "_cutover_database", cutover)

    def rollback_database(cursor: Any, live: str, rollback: str, failed: str) -> None:
        database_state["shadow"] = database_state["live"]
        database_state["live"] = database_state["rollback"]
        database_state["rollback"] = None

    monkeypatch.setattr(restore, "_rollback_database", rollback_database)
    real_swap = restore._swap_directory

    def swap(prepared: Path, target: Path, previous: Path) -> bool:
        if failure_stage == "openhands_swap" and target == live_openhands:
            raise OSError("OpenHands swap failed")
        if failure_stage == "media_swap" and target == live_media:
            raise OSError("media swap failed")
        return real_swap(prepared, target, previous)

    monkeypatch.setattr(restore, "_swap_directory", swap)

    with pytest.raises(OSError, match="failed"):
        restore._restore_backup_in_window(
            manifest_path=manifest_path,
            database_url="postgresql://app@db/focusproof",
            recovery_admin_url="postgresql://admin@db/postgres",
            coordination_data_dir=coordination,
            openhands_data_dir=live_openhands,
            media_data_dir=live_media,
            window=window,
        )

    assert (live_openhands / "old-event.json").read_text(encoding="utf-8") == "old-openhands"
    assert (live_media / "old-media.bin").read_bytes() == b"old-media"
    assert database_state["live"] == "old"
    assert window.completed is False
    assert window.started is (failure_stage != "shadow_restore")


def test_admin_create_database_permission_failure_leaves_live_sources_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, _ = _paired_bundle(tmp_path, monkeypatch)
    coordination, live_openhands, live_media = _canonical_payload_roots(tmp_path, "live")
    (live_openhands / "old-event.json").write_text("old", encoding="utf-8")
    (live_media / "old-media.bin").write_bytes(b"old-media")
    calls: list[str] = []

    class CursorContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: Any) -> None:
            return None

    class Admin:
        def cursor(self) -> CursorContext:
            return CursorContext()

        def close(self) -> None:
            calls.append("close")

    class Window:
        started = False

        def begin_recovery(self) -> None:
            self.started = True

        def complete_recovery(self) -> None:
            pytest.fail("permission failure must not complete recovery")

    window = Window()
    monkeypatch.setattr(restore, "current_application_revision", lambda: "rev-test")
    monkeypatch.setattr(restore, "_connect_admin", lambda url: Admin())

    def deny_create(cursor: Any, name: str, owner: str) -> None:
        raise PermissionError("permission denied")

    monkeypatch.setattr(restore, "_create_database", deny_create)
    monkeypatch.setattr(
        restore,
        "_run_pg_restore",
        lambda *args: pytest.fail("pg_restore must not run"),
    )

    with pytest.raises(PermissionError, match="permission denied"):
        restore._restore_backup_in_window(
            manifest_path=manifest_path,
            database_url="postgresql://app@db/focusproof",
            recovery_admin_url="postgresql://admin@db/postgres",
            coordination_data_dir=coordination,
            openhands_data_dir=live_openhands,
            media_data_dir=live_media,
            window=window,
        )

    assert (live_openhands / "old-event.json").read_text(encoding="utf-8") == "old"
    assert (live_media / "old-media.bin").read_bytes() == b"old-media"
    assert window.started is False
    assert calls == ["close"]


def test_rollback_failure_reports_safe_names_and_keeps_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, _ = _paired_bundle(tmp_path, monkeypatch)
    coordination, live_openhands, live_media = _canonical_payload_roots(tmp_path, "live")
    marker = {"started": False, "completed": False}

    class CursorContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: Any) -> None:
            return None

    class Admin:
        def cursor(self) -> CursorContext:
            return CursorContext()

        def close(self) -> None:
            return None

    class Window:
        def begin_recovery(self) -> None:
            marker["started"] = True

        def complete_recovery(self) -> None:
            marker["completed"] = True

    monkeypatch.setattr(restore, "current_application_revision", lambda: "rev-test")
    monkeypatch.setattr(restore, "_connect_admin", lambda url: Admin())
    monkeypatch.setattr(restore, "_create_database", lambda cursor, name, owner: None)
    monkeypatch.setattr(restore, "_drop_database", lambda cursor, name: None)
    monkeypatch.setattr(restore, "_run_pg_restore", lambda url, source: None)
    monkeypatch.setattr(restore, "_shadow_artifact_rows", lambda url: [])
    monkeypatch.setattr(restore, "_openhands_artifact_refs", lambda root: [])
    reconciliations = 0

    def reconcile(rows: Any, refs: Any, media: Path) -> None:
        nonlocal reconciliations
        reconciliations += 1
        if reconciliations == 2:
            raise OSError("postverify failed")

    monkeypatch.setattr(restore, "_reconcile_three_sources", reconcile)
    monkeypatch.setattr(
        restore,
        "_recovery_database_names",
        lambda: ("focusproof_shadow_deadbeef", "focusproof_rollback_deadbeef"),
    )
    monkeypatch.setattr(restore, "_cutover_database", lambda cursor, **kwargs: None)
    monkeypatch.setattr(restore, "_rollback_database", lambda *args, **kwargs: None)
    real_rollback = restore._rollback_directory

    def fail_media_rollback(
        target: Path,
        previous: Path,
        failed: Path,
        *,
        had_previous: bool,
    ) -> None:
        if target == live_media:
            raise OSError("media rollback failed")
        real_rollback(target, previous, failed, had_previous=had_previous)

    monkeypatch.setattr(restore, "_rollback_directory", fail_media_rollback)

    with pytest.raises(restore.RecoveryValidationError, match="retained databases") as exc:
        restore._restore_backup_in_window(
            manifest_path=manifest_path,
            database_url="postgresql://app@db/focusproof",
            recovery_admin_url="postgresql://admin@db/postgres",
            coordination_data_dir=coordination,
            openhands_data_dir=live_openhands,
            media_data_dir=live_media,
            window=Window(),
        )

    message = str(exc.value)
    assert "focusproof_rollback_deadbeef" in message
    assert "postgresql://" not in message
    assert "secret" not in message
    assert marker == {"started": True, "completed": False}


def test_restore_rejects_bundle_with_extra_member_before_pg_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, bundle = _paired_bundle(tmp_path, monkeypatch)
    coordination, openhands, media = _canonical_payload_roots(tmp_path, "restored")
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
            recovery_admin_url="postgresql://admin@db/postgres",
            coordination_data_dir=coordination,
            openhands_data_dir=openhands,
            media_data_dir=media,
        )
    assert calls == []


def test_backup_failure_removes_partial_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    coordination, data_dir, media_dir = _canonical_payload_roots(tmp_path, "data")
    (data_dir / "event.json").write_text("native", encoding="utf-8")
    output_dir = tmp_path / "paired"
    caplog.set_level(logging.INFO, logger="focusproof.operations")

    def fail_dump(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, ["pg_dump"])

    monkeypatch.setattr(backup.subprocess, "run", fail_dump)

    with pytest.raises(subprocess.CalledProcessError):
        backup.create_backup(
            database_url="postgresql://db/focusproof",
            coordination_data_dir=coordination,
            openhands_data_dir=data_dir,
            media_data_dir=media_dir,
            output_dir=output_dir,
        )

    assert not output_dir.exists()
    assert list(tmp_path.glob(".paired-*")) == []
    recovery_events = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "focusproof.operations"
    ]
    assert {"event": "recovery", "operation": "backup", "outcome": "failed"} in (recovery_events)


def test_backup_rejects_symlink_in_openhands_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordination, data_dir, media_dir = _canonical_payload_roots(tmp_path, "data")
    secret = tmp_path / "evidence-secret"
    secret.write_text("must-not-archive", encoding="utf-8")
    (data_dir / "escape").symlink_to(secret)

    def fake_dump(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        Path(args[args.index("--file") + 1]).write_bytes(b"dump")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(backup.subprocess, "run", fake_dump)

    with pytest.raises(backup.RecoveryError, match="symlink"):
        backup.create_backup(
            database_url="postgresql://db/focusproof",
            coordination_data_dir=coordination,
            openhands_data_dir=data_dir,
            media_data_dir=media_dir,
            output_dir=tmp_path / "paired",
        )


def _paired_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    coordination_dir = tmp_path / "source-data"
    data_dir = coordination_dir / "conversations"
    data_dir.mkdir(parents=True)
    (data_dir / "conversation.json").write_text("native-state", encoding="utf-8")
    media_dir = coordination_dir / "media" / "objects"
    media_object = media_dir / "referenced" / "ab" / "artifact.bin"
    media_object.parent.mkdir(parents=True)
    media_object.write_bytes(b"normalized-image-bytes")
    output_dir = tmp_path / "paired"

    def fake_dump(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        Path(args[args.index("--file") + 1]).write_bytes(b"postgres-dump")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(backup.subprocess, "run", fake_dump)
    monkeypatch.setattr(backup, "current_application_revision", lambda: "rev-test")
    backup.create_backup(
        database_url="postgresql://db/focusproof",
        coordination_data_dir=coordination_dir,
        openhands_data_dir=data_dir,
        media_data_dir=media_dir,
        output_dir=output_dir,
    )
    return output_dir / "manifest.json", output_dir


def test_restore_rejects_revision_mismatch_before_pg_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, _ = _paired_bundle(tmp_path, monkeypatch)
    coordination, openhands, media = _canonical_payload_roots(tmp_path, "restored")
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
            recovery_admin_url="postgresql://admin@db/postgres",
            coordination_data_dir=coordination,
            openhands_data_dir=openhands,
            media_data_dir=media,
        )
    assert calls == []


@pytest.mark.parametrize("member_name", ["../escape", "/absolute/path"])
def test_restore_rejects_traversal_and_absolute_archive_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    member_name: str,
) -> None:
    manifest_path, bundle = _paired_bundle(tmp_path, monkeypatch)
    coordination, openhands, media = _canonical_payload_roots(tmp_path, "restored")
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
            database_url="postgresql://app@db/focusproof",
            recovery_admin_url="postgresql://admin@db/postgres",
            coordination_data_dir=coordination,
            openhands_data_dir=openhands,
            media_data_dir=media,
        )


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_archive_validation_rejects_link_and_special_members(
    tmp_path: Path,
    member_type: bytes,
) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as payload:
        member = tarfile.TarInfo("unsafe")
        member.type = member_type
        member.linkname = "target"
        payload.addfile(member)

    with pytest.raises(restore.RecoveryValidationError, match="unsafe"):
        restore._validate_archive(archive)

    extraction = tmp_path / "extraction"
    assert not extraction.exists()


@pytest.mark.parametrize(
    ("members", "compressed_size", "match"),
    [
        ([tarfile.TarInfo("same"), tarfile.TarInfo("same")], 1, "duplicate"),
        ([tarfile.TarInfo("a"), tarfile.TarInfo("b")], 1, "member count"),
        ([tarfile.TarInfo("large")], 1, "member size"),
        ([tarfile.TarInfo("a"), tarfile.TarInfo("b")], 1, "total size"),
        ([tarfile.TarInfo("ratio")], 1, "compression ratio"),
    ],
)
def test_archive_validation_rejects_duplicates_and_resource_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    members: list[tarfile.TarInfo],
    compressed_size: int,
    match: str,
) -> None:
    if match == "member size":
        members[0].size = 11
    elif match == "total size":
        members[0].size = 6
        members[1].size = 6
    elif match == "compression ratio":
        members[0].size = 9
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"x" * compressed_size)
    fake = type(
        "FakeArchive",
        (),
        {
            "getmembers": lambda self: members,
            "__enter__": lambda self: self,
            "__exit__": lambda self, *args: None,
        },
    )()
    monkeypatch.setattr(restore.tarfile, "open", lambda *args, **kwargs: fake)
    monkeypatch.setattr(restore, "MAX_ARCHIVE_MEMBERS", 1 if match == "member count" else 10)
    monkeypatch.setattr(restore, "MAX_ARCHIVE_MEMBER_SIZE", 10)
    monkeypatch.setattr(restore, "MAX_ARCHIVE_TOTAL_SIZE", 10)
    monkeypatch.setattr(restore, "MAX_ARCHIVE_COMPRESSION_RATIO", 8)

    with pytest.raises(restore.RecoveryValidationError, match=match):
        restore._validate_archive(archive)


def test_restore_uses_sanitized_pg_restore_argv_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manifest_path, _ = _paired_bundle(tmp_path, monkeypatch)
    coordination, target, media_target = _canonical_payload_roots(tmp_path, "restored")
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

    class CursorContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: Any) -> None:
            return None

    class Admin:
        def cursor(self) -> CursorContext:
            return CursorContext()

        def close(self) -> None:
            return None

    monkeypatch.setattr(restore, "_connect_admin", lambda url: Admin())
    monkeypatch.setattr(restore, "_create_database", lambda cursor, name, owner: None)
    monkeypatch.setattr(restore, "_drop_database", lambda cursor, name: None)
    monkeypatch.setattr(restore, "_terminate_database_connections", lambda cursor, name: None)
    monkeypatch.setattr(restore, "_cutover_database", lambda cursor, **kwargs: None)
    monkeypatch.setattr(restore, "_shadow_artifact_rows", lambda url: [])
    monkeypatch.setattr(restore, "_openhands_artifact_refs", lambda root: [])
    monkeypatch.setattr(restore, "_reconcile_three_sources", lambda rows, refs, root: None)
    for _ in range(2):
        restore.restore_backup(
            manifest_path=manifest_path,
            database_url="postgresql://focusproof:password@db/focusproof",
            recovery_admin_url="postgresql://admin:admin-password@db/postgres",
            coordination_data_dir=coordination,
            openhands_data_dir=target,
            media_data_dir=media_target,
        )

    assert len(calls) == 2
    assert all(call[0][0] == "pg_restore" for call in calls)
    assert all("focusproof_shadow_" in " ".join(call[0]) for call in calls)
    assert all("--clean" not in call[0] for call in calls)
    assert all("password" not in " ".join(call[0]) for call in calls)
    assert all(key not in call[1] for call in calls for key in PROVIDER_KEYS)
    assert (target / "conversation.json").read_text(encoding="utf-8") == "native-state"
    assert (
        media_target / "referenced" / "ab" / "artifact.bin"
    ).read_bytes() == b"normalized-image-bytes"
    recovery_events = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "focusproof.operations"
    ]
    assert (
        recovery_events.count({"event": "recovery", "operation": "restore", "outcome": "completed"})
        == 2
    )


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
    coordination, target, media_target = _canonical_payload_roots(tmp_path, "restored")
    (target / "old-native.json").write_text("old", encoding="utf-8")
    (media_target / "old-media.bin").write_bytes(b"old-media")
    monkeypatch.setattr(restore, "current_application_revision", lambda: "rev-test")
    monkeypatch.setattr(
        restore.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0),
    )

    class CursorContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: Any) -> None:
            return None

    class Admin:
        def cursor(self) -> CursorContext:
            return CursorContext()

        def close(self) -> None:
            return None

    monkeypatch.setattr(restore, "_connect_admin", lambda url: Admin())
    monkeypatch.setattr(restore, "_create_database", lambda cursor, name, owner: None)
    monkeypatch.setattr(restore, "_drop_database", lambda cursor, name: None)
    monkeypatch.setattr(restore, "_terminate_database_connections", lambda cursor, name: None)
    monkeypatch.setattr(restore, "_cutover_database", lambda cursor, **kwargs: None)
    monkeypatch.setattr(restore, "_rollback_database", lambda *args, **kwargs: None)
    monkeypatch.setattr(restore, "_shadow_artifact_rows", lambda url: [])
    monkeypatch.setattr(restore, "_openhands_artifact_refs", lambda root: [])
    monkeypatch.setattr(restore, "_reconcile_three_sources", lambda rows, refs, root: None)

    def fail_after_postgres(*args: Any, **kwargs: Any) -> bool:
        raise OSError("injected OpenHands replacement failure")

    monkeypatch.setattr(restore, "_swap_directory", fail_after_postgres)

    with pytest.raises(OSError, match="injected OpenHands"):
        restore.restore_backup(
            manifest_path=manifest_path,
            database_url="postgresql://app@db/focusproof",
            recovery_admin_url="postgresql://admin@db/postgres",
            coordination_data_dir=coordination,
            openhands_data_dir=target,
            media_data_dir=media_target,
        )

    assert (target / "old-native.json").read_text(encoding="utf-8") == "old"
    assert (media_target / "old-media.bin").read_bytes() == b"old-media"
    assert staging_check.is_recovery_incomplete(coordination)
    assert not staging_check.is_recovery_incomplete(target)
    app = _recovery_app(
        f"sqlite+pysqlite:///{target / 'fail-closed.sqlite3'}",
        coordination,
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
    coordination, data_dir, media_dir = _canonical_payload_roots(tmp_path, "data")
    with pytest.raises(backup.RecoveryError, match="output"):
        backup.create_backup(
            database_url="postgresql://db/focusproof",
            coordination_data_dir=coordination,
            openhands_data_dir=data_dir,
            media_data_dir=media_dir,
            output_dir=tmp_path / "safe" / ".." / "escape",
        )


POSTGRES_IMAGE: Final = (
    "postgres:16-bookworm@sha256:ec2448d32297f61000a4b70edc2d27c9dfaedfc28cee3b827233fd4f05392dc8"
)
RECOVERY_EVIDENCE_TEXT: Final = "paired-recovery-evidence-sentinel"
RECOVERY_PNG: Final = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


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
    config.set_main_option("script_location", str(PROJECT_ROOT / "agent-server/migrations"))
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
        image = client.post(
            f"/sessions/{session_ids[0]}/evidence/image",
            headers=_authorization(tokens[0]),
            files={"file": ("recovery.png", RECOVERY_PNG, "image/png")},
            data={
                "explanation": "A recovery pixel used to prove durable image persistence.",
                "idempotency_key": "recovery-image-1",
            },
        )
        assert image.status_code == 200
        assert image.json()["mediaType"] == "image/png"
        assert image.json()["normalizedBytes"] > 0
        restored_app.state.conversation_manager.get_or_restore(session_ids[0], owner_ids[0])
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
        "evidence": [(item.session_id, item.evidence_id, item.content_hash) for item in evidence],
        "answers": [
            (item.session_id, item.question_id, item.answer, item.version) for item in answers
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
        "image_restore_marker": _image_restore_marker_snapshot(
            database_url,
            data_dir,
            session_ids,
            owner_ids,
        ),
    }


def _image_restore_marker_snapshot(
    database_url: str,
    data_dir: Path,
    session_ids: tuple[str, str],
    owner_ids: tuple[str, str],
) -> dict[str, Any]:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from focusproof.media_adapters.local_media_object_store import LocalMediaObjectStore
    from focusproof.openhands_runtime.evidence_messages import FocusProofMessageEnvelope
    from focusproof.persistence.models import (
        EvidenceModel,
        LearningSessionModel,
        MediaArtifactModel,
    )
    from openhands.sdk.event import Event, MessageEvent
    from openhands.sdk.llm import ImageContent, TextContent

    engine = create_engine(database_url)
    try:
        with Session(engine) as db_session:
            rows = list(
                db_session.execute(
                    select(
                        LearningSessionModel.owner_user_id,
                        LearningSessionModel.session_id,
                        LearningSessionModel.conversation_id,
                        EvidenceModel.evidence_id,
                        EvidenceModel.artifact_id,
                        MediaArtifactModel.opaque_object_key,
                        MediaArtifactModel.media_type,
                        MediaArtifactModel.normalized_byte_size,
                        MediaArtifactModel.normalized_sha256,
                    )
                    .join(
                        EvidenceModel,
                        EvidenceModel.session_id == LearningSessionModel.session_id,
                    )
                    .join(
                        MediaArtifactModel,
                        MediaArtifactModel.media_item_id == EvidenceModel.artifact_id,
                    )
                    .where(LearningSessionModel.session_id.in_(session_ids))
                    .where(LearningSessionModel.owner_user_id.in_(owner_ids))
                    .where(EvidenceModel.evidence_type.like("image/%"))
                    .order_by(
                        LearningSessionModel.session_id,
                        EvidenceModel.evidence_id,
                    )
                )
            )
    finally:
        engine.dispose()
    assert len(rows) == 1
    (
        owner_user_id,
        session_id,
        conversation_id,
        evidence_id,
        artifact_id,
        opaque_object_key,
        media_type,
        normalized_byte_size,
        normalized_sha256,
    ) = rows[0]
    assert artifact_id is not None
    snapshot_id = f"{owner_user_id}:{session_id}:{evidence_id}:{artifact_id}"
    db_snapshot = {
        "snapshot_id": snapshot_id,
        "owner_user_id": str(owner_user_id),
        "session_id": str(session_id),
        "conversation_id": str(conversation_id),
        "evidence_id": str(evidence_id),
        "artifact_id": str(artifact_id),
        "opaque_object_key": str(opaque_object_key),
        "media_type": str(media_type),
        "byte_size": int(normalized_byte_size),
        "normalized_sha256": str(normalized_sha256),
    }

    media_store = LocalMediaObjectStore(data_dir / "media" / "objects")
    with media_store.open(str(opaque_object_key)) as stream:
        media_payload = stream.read()
    media_snapshot = {
        "opaque_object_key": str(opaque_object_key),
        "byte_size": len(media_payload),
        "normalized_sha256": sha256(media_payload).hexdigest(),
    }
    assert media_snapshot["byte_size"] == db_snapshot["byte_size"]
    assert media_snapshot["normalized_sha256"] == db_snapshot["normalized_sha256"]

    artifact_ref = f"focusproof-artifact://{artifact_id}"
    event_dir = data_dir / "conversations"
    matched_events: list[dict[str, Any]] = []
    for event_file in sorted(event_dir.rglob("events/event-*.json")):
        assert event_file.is_file()
        assert not event_file.is_symlink()
        event = Event.model_validate_json(event_file.read_text(encoding="utf-8"))
        if not isinstance(event, MessageEvent):
            continue
        if event.llm_message is None:
            continue
        text_items = [item for item in event.llm_message.content if isinstance(item, TextContent)]
        image_items = [item for item in event.llm_message.content if isinstance(item, ImageContent)]
        if not text_items and not image_items:
            continue
        matched_envelopes: list[FocusProofMessageEnvelope] = []
        for text_item in text_items:
            try:
                envelope = FocusProofMessageEnvelope.model_validate_json(text_item.text)
            except ValueError:
                continue
            if envelope.payload.get("artifact_ref") == artifact_ref:
                matched_envelopes.append(envelope)
        if not matched_envelopes:
            continue
        assert len(matched_envelopes) == 1
        assert len(event.llm_message.content) == 2
        assert len(text_items) == 1
        assert len(image_items) == 1
        assert len(image_items[0].image_urls) == 1
        envelope = matched_envelopes[0]
        data_url = image_items[0].image_urls[0]
        prefix = f"data:{db_snapshot['media_type']};base64,"
        assert data_url.startswith(prefix)
        payload = base64.b64decode(data_url[len(prefix) :], validate=True)
        matched_events.append(
            {
                "message_key": str(envelope.message_key),
                "artifact_ref": artifact_ref,
                "media_type": str(db_snapshot["media_type"]),
                "byte_size": len(payload),
                "normalized_sha256": sha256(payload).hexdigest(),
            }
        )
    assert len(matched_events) == 1
    openhands_snapshot = matched_events[0]
    assert openhands_snapshot["byte_size"] == db_snapshot["byte_size"]
    assert openhands_snapshot["normalized_sha256"] == db_snapshot["normalized_sha256"]

    return {
        "db": db_snapshot,
        "media": media_snapshot,
        "openhands": openhands_snapshot,
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
    data_dir = tmp_path / "data"
    openhands_dir = data_dir / "conversations"
    media_dir = data_dir / "media" / "objects"
    media_dir.mkdir(parents=True)
    bundle_dir = tmp_path / "paired-backup"
    fixture = local_oidc_fixture()
    monkeypatch.setenv("FOCUSPROOF_PROFILE", "staging")
    monkeypatch.setenv("FOCUSPROOF_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOCUSPROOF_MEDIA_ENABLED", "true")
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
            coordination_data_dir=data_dir,
            openhands_data_dir=openhands_dir,
            media_data_dir=media_dir,
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
            recovery_admin_url=restore._database_url_with_name(database_url, "postgres"),
            coordination_data_dir=data_dir,
            openhands_data_dir=openhands_dir,
            media_data_dir=media_dir,
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
            recovery_admin_url=restore._database_url_with_name(database_url, "postgres"),
            coordination_data_dir=data_dir,
            openhands_data_dir=openhands_dir,
            media_data_dir=media_dir,
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
