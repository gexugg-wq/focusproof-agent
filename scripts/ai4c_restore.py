from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, fields
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from typing import Any, cast
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from openhands.sdk.event import Event, MessageEvent
from openhands.sdk.llm import ImageContent
from scripts.ai4c_backup import (
    BACKUP_TIMEOUT_SECONDS,
    PostgresUrlError,
    RecoveryManifest,
    _file_sha256,
    _minimal_environment,
    _postgres_cli_connection,
    current_application_revision,
)
from scripts.ai4c_staging_check import recovery_outcome
from scripts.ai4c_staging_check import maintenance_window
from focusproof.openhands_runtime.evidence_messages import FocusProofMessageEnvelope
from focusproof.recovery import MaintenanceWindow


MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_MEMBER_SIZE = 512 * 1024 * 1024
MAX_ARCHIVE_TOTAL_SIZE = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 200


LOGGER = logging.getLogger("focusproof.operations")


@dataclass(frozen=True, slots=True)
class DatabaseArtifactFact:
    owner_user_id: str
    session_id: str
    conversation_id: str
    evidence_id: str
    artifact_id: str
    opaque_object_key: str
    media_type: str
    normalized_sha256: str
    normalized_byte_size: int


@dataclass(frozen=True, slots=True)
class EventArtifactFact:
    owner_user_id: str
    session_id: str
    conversation_id: str
    evidence_id: str
    artifact_id: str
    media_type: str
    normalized_sha256: str
    normalized_byte_size: int


class RecoveryValidationError(RuntimeError):
    pass


def _recovery_payload_layout(
    coordination_data_dir: Path,
    openhands_data_dir: Path,
    media_data_dir: Path,
) -> tuple[Path, Path]:
    try:
        coordination = coordination_data_dir.resolve(strict=True)
        openhands = openhands_data_dir.resolve(strict=False)
        media = media_data_dir.resolve(strict=False)
    except OSError as exc:
        raise RecoveryValidationError("recovery payload layout is invalid") from exc
    if coordination_data_dir.is_symlink() or not coordination.is_dir():
        raise RecoveryValidationError("recovery payload layout is invalid")
    expected_openhands = coordination / "conversations"
    expected_media = coordination / "media" / "objects"
    if openhands != expected_openhands or media != expected_media:
        raise RecoveryValidationError("recovery payload layout is invalid")
    for payload in (openhands_data_dir, media_data_dir):
        current = payload
        while current != coordination_data_dir and current != current.parent:
            if current.is_symlink():
                raise RecoveryValidationError("recovery payload layout is invalid")
            current = current.parent
    if openhands == media or openhands in media.parents or media in openhands.parents:
        raise RecoveryValidationError("recovery payload layout is invalid")
    return openhands, media


def _recovery_database_names() -> tuple[str, str]:
    token = uuid4().hex
    return (
        f"focusproof_shadow_{token}",
        f"focusproof_rollback_{token}",
    )


def _database_url_with_name(database_url: str, database_name: str) -> str:
    parsed = urlsplit(database_url)
    scheme = parsed.scheme.split("+", 1)[0]
    return urlunsplit(
        (
            scheme,
            parsed.netloc,
            f"/{quote(database_name, safe='')}",
            parsed.query,
            "",
        )
    )


def _recovery_connections(
    database_url: str,
    recovery_admin_url: str,
) -> tuple[Any, Any]:
    try:
        target = _postgres_cli_connection(database_url)
        admin = _postgres_cli_connection(recovery_admin_url)
    except PostgresUrlError as exc:
        raise RecoveryValidationError(str(exc)) from exc
    if target.database_name == admin.database_name:
        raise RecoveryValidationError(
            "recovery maintenance database must differ from target database"
        )
    if target.host != admin.host:
        raise RecoveryValidationError("recovery admin host differs from target host")
    target_port = target.port if target.port is not None else 5432
    admin_port = admin.port if admin.port is not None else 5432
    if target_port != admin_port:
        raise RecoveryValidationError("recovery admin port differs from target port")
    tls_keys = {
        "PGSSLMODE",
        "PGSSLROOTCERT",
        "PGSSLCERT",
        "PGSSLKEY",
    }
    target_tls = {key: value for key, value in target.libpq_environment.items() if key in tls_keys}
    admin_tls = {key: value for key, value in admin.libpq_environment.items() if key in tls_keys}
    if target_tls != admin_tls:
        raise RecoveryValidationError("recovery admin TLS settings differ from target")
    return target, admin


def _create_database(cursor: Any, database_name: str, owner_name: str) -> None:
    statement = sql.SQL("CREATE DATABASE {} OWNER {}").format(
        sql.Identifier(database_name),
        sql.Identifier(owner_name),
    )
    cursor.execute(statement)


def _rename_database(cursor: Any, source_name: str, target_name: str) -> None:
    statement = sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
        sql.Identifier(source_name),
        sql.Identifier(target_name),
    )
    cursor.execute(statement)


def _drop_database(cursor: Any, database_name: str) -> None:
    statement = sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
    cursor.execute(statement)


def _terminate_database_connections(cursor: Any, database_name: str) -> None:
    cursor.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (database_name,),
    )


def _cutover_database(
    cursor: Any,
    *,
    live_name: str,
    shadow_name: str,
    rollback_name: str,
) -> None:
    _terminate_database_connections(cursor, live_name)
    _rename_database(cursor, live_name, rollback_name)
    try:
        _terminate_database_connections(cursor, shadow_name)
        _rename_database(cursor, shadow_name, live_name)
    except BaseException:
        _terminate_database_connections(cursor, rollback_name)
        _rename_database(cursor, rollback_name, live_name)
        raise


def _rollback_database(
    cursor: Any,
    live_name: str,
    rollback_name: str,
    failed_name: str,
) -> None:
    _terminate_database_connections(cursor, live_name)
    _rename_database(cursor, live_name, failed_name)
    _terminate_database_connections(cursor, rollback_name)
    _rename_database(cursor, rollback_name, live_name)
    _drop_database(cursor, failed_name)


def _connect_admin(recovery_admin_url: str) -> Any:
    connection = psycopg.connect(
        _database_url_with_name(recovery_admin_url, urlsplit(recovery_admin_url).path.lstrip("/"))
    )
    connection.autocommit = True
    return connection


def _reconcile_three_sources(
    database_rows: list[DatabaseArtifactFact] | list[tuple[str, str, str]],
    event_artifact_refs: list[EventArtifactFact] | list[str],
    media_root: Path,
) -> None:
    database_modes = {
        "structured"
        if isinstance(row, DatabaseArtifactFact)
        else "legacy"
        if (
            isinstance(row, tuple)
            and len(row) == 3
            and all(isinstance(value, str) for value in row)
        )
        else "unknown"
        for row in database_rows
    }
    event_modes = {
        "structured"
        if isinstance(row, EventArtifactFact)
        else "legacy"
        if isinstance(row, str)
        else "unknown"
        for row in event_artifact_refs
    }
    if (
        "unknown" in database_modes
        or "unknown" in event_modes
        or len(database_modes) > 1
        or len(event_modes) > 1
    ):
        raise RecoveryValidationError("reconciliation inputs contain mixed representations")

    database_mode = next(iter(database_modes), "empty")
    event_mode = next(iter(event_modes), "empty")
    if database_mode != "empty" and event_mode != "empty" and database_mode != event_mode:
        raise RecoveryValidationError("reconciliation inputs contain mixed representations")
    if database_mode == "structured" or event_mode == "structured":
        structured_database_rows = cast(list[DatabaseArtifactFact], database_rows)
        structured_event_rows = cast(list[EventArtifactFact], event_artifact_refs)
        _reconcile_structured_facts(structured_database_rows, structured_event_rows, media_root)
        return

    legacy_database_rows = cast(list[tuple[str, str, str]], database_rows)
    legacy_event_artifact_refs = cast(list[str], event_artifact_refs)
    artifacts: dict[str, tuple[str, str]] = {}
    object_keys: set[str] = set()
    for artifact_id, object_key, content_hash in legacy_database_rows:
        candidate = Path(object_key)
        if (
            not artifact_id
            or not object_key
            or candidate.is_absolute()
            or ".." in candidate.parts
            or len(content_hash) != 64
            or any(character not in "0123456789abcdef" for character in content_hash)
        ):
            raise RecoveryValidationError("database media artifact path or hash is invalid")
        if artifact_id in artifacts or object_key in object_keys:
            raise RecoveryValidationError("database media artifacts contain duplicate")
        artifacts[artifact_id] = (object_key, content_hash)
        object_keys.add(object_key)

    event_ids: list[str] = []
    prefix = "focusproof-artifact://"
    for artifact_ref in legacy_event_artifact_refs:
        if not artifact_ref.startswith(prefix) or not artifact_ref.removeprefix(prefix):
            raise RecoveryValidationError("EventLog artifact reference is invalid")
        event_ids.append(artifact_ref.removeprefix(prefix))
    if len(event_ids) != len(set(event_ids)):
        raise RecoveryValidationError("EventLog contains duplicate artifact reference")
    if set(event_ids) != set(artifacts):
        raise RecoveryValidationError("EventLog artifact references differ from database")

    actual_files: set[str] = set()
    for path in media_root.rglob("*"):
        if path.is_symlink():
            raise RecoveryValidationError("media path is unsafe")
        if path.is_file():
            actual_files.add(path.relative_to(media_root).as_posix())
        elif not path.is_dir():
            raise RecoveryValidationError("media path is unsafe")
    if actual_files - object_keys:
        raise RecoveryValidationError("media contains extra object")
    if object_keys - actual_files:
        raise RecoveryValidationError("media object is missing")
    for object_key, content_hash in artifacts.values():
        if _file_sha256(media_root / object_key) != content_hash:
            raise RecoveryValidationError("media object hash differs")


def _reconcile_structured_facts(database_rows: Any, event_rows: Any, media_root: Path) -> None:
    database: dict[tuple[str, str, str, str, str], DatabaseArtifactFact] = {}
    object_keys: set[str] = set()
    for fact in database_rows:
        key = (
            fact.owner_user_id,
            fact.session_id,
            fact.conversation_id,
            fact.evidence_id,
            fact.artifact_id,
        )
        object_key = f"referenced/{fact.opaque_object_key}"
        candidate = Path(object_key)
        if candidate.is_absolute() or ".." in candidate.parts or len(fact.normalized_sha256) != 64:
            raise RecoveryValidationError("database media artifact path or hash is invalid")
        if key in database or object_key in object_keys:
            raise RecoveryValidationError("database media artifacts contain duplicate")
        database[key] = fact
        object_keys.add(object_key)
    events: dict[tuple[str, str, str, str, str], EventArtifactFact] = {}
    for fact in event_rows:
        key = (
            fact.owner_user_id,
            fact.session_id,
            fact.conversation_id,
            fact.evidence_id,
            fact.artifact_id,
        )
        if key in events:
            raise RecoveryValidationError("EventLog contains duplicate structured fact")
        events[key] = fact
    if set(database) != set(events):
        raise RecoveryValidationError("EventLog structured facts differ from database")
    for key, db_fact in database.items():
        event_fact = events[key]
        if (
            db_fact.media_type != event_fact.media_type
            or db_fact.normalized_byte_size != event_fact.normalized_byte_size
            or db_fact.normalized_sha256 != event_fact.normalized_sha256
        ):
            raise RecoveryValidationError("EventLog media facts differ from database")
    actual_files: set[str] = set()
    for path in media_root.rglob("*"):
        if path.is_symlink():
            raise RecoveryValidationError("media path is unsafe")
        if path.is_file():
            actual_files.add(path.relative_to(media_root).as_posix())
        elif not path.is_dir():
            raise RecoveryValidationError("media path is unsafe")
    if actual_files - object_keys:
        raise RecoveryValidationError("media contains extra object")
    if object_keys - actual_files:
        raise RecoveryValidationError("media object is missing")
    for fact in database.values():
        path = media_root / "referenced" / fact.opaque_object_key
        if path.stat().st_size != fact.normalized_byte_size:
            raise RecoveryValidationError("media object size differs")
        if _file_sha256(path) != fact.normalized_sha256:
            raise RecoveryValidationError("media object hash differs")


def _openhands_artifact_refs(openhands_root: Path) -> list[EventArtifactFact]:
    refs: list[EventArtifactFact] = []
    event_files = sorted(openhands_root.rglob("events/event-*.json"))
    for event_file in event_files:
        if event_file.is_symlink() or not event_file.is_file():
            raise RecoveryValidationError("EventLog file is unsafe")
        try:
            event = Event.model_validate_json(event_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise RecoveryValidationError("EventLog event is invalid") from exc
        if not isinstance(event, MessageEvent):
            continue
        text_items = [item.text for item in event.llm_message.content if hasattr(item, "text")]
        images = [
            url
            for item in event.llm_message.content
            if isinstance(item, ImageContent)
            for url in item.image_urls
        ]
        try:
            envelope = FocusProofMessageEnvelope.model_validate_json("".join(text_items))
        except ValueError:
            continue
        if envelope.kind != "evidence" or not envelope.message_key.startswith("evidence:"):
            continue
        if event.source != "user":
            raise RecoveryValidationError("EventLog evidence source is invalid")
        evidence_id = envelope.message_key.removeprefix("evidence:")
        if not evidence_id or envelope.message_key != f"evidence:{evidence_id}":
            raise RecoveryValidationError("EventLog evidence message key is invalid")
        artifact_ref = envelope.payload.get("artifact_ref")
        if not isinstance(artifact_ref, str):
            if images:
                raise RecoveryValidationError("EventLog image has no artifact envelope")
            continue
        media_type = envelope.payload.get("media_type")
        content_hash = envelope.payload.get("normalized_sha256")
        byte_size = envelope.payload.get("byte_size")
        if (
            not artifact_ref.startswith("focusproof-artifact://")
            or media_type not in {"image/png", "image/jpeg", "image/webp"}
            or not isinstance(content_hash, str)
            or len(content_hash) != 64
            or not isinstance(byte_size, int)
            or isinstance(byte_size, bool)
            or byte_size <= 0
            or byte_size > 10 * 1024 * 1024
            or len(images) != 1
        ):
            raise RecoveryValidationError("EventLog media envelope is invalid")
        prefix = f"data:{media_type};base64,"
        if not images[0].startswith(prefix):
            raise RecoveryValidationError("EventLog image MIME differs")
        try:
            payload = base64.b64decode(images[0][len(prefix) :], validate=True)
        except (ValueError, TypeError) as exc:
            raise RecoveryValidationError("EventLog image encoding is invalid") from exc
        if len(payload) != byte_size:
            raise RecoveryValidationError("EventLog image size differs")
        if sha256(payload).hexdigest() != content_hash:
            raise RecoveryValidationError("EventLog image hash differs")
        sender = event.sender
        if sender is None:
            raise RecoveryValidationError("EventLog evidence sender is missing")
        try:
            conversation_id = event_file.parent.parent.name
            if len(conversation_id) != 32:
                raise ValueError(conversation_id)
            int(conversation_id, 16)
        except ValueError as exc:
            raise RecoveryValidationError("EventLog conversation path is invalid") from exc
        refs.append(
            EventArtifactFact(
                owner_user_id=str(sender),
                session_id=envelope.session_id,
                conversation_id=conversation_id,
                evidence_id=evidence_id,
                artifact_id=artifact_ref.removeprefix("focusproof-artifact://"),
                media_type=media_type,
                normalized_sha256=content_hash,
                normalized_byte_size=byte_size,
            )
        )
    return refs


def _shadow_artifact_rows(shadow_url: str) -> list[DatabaseArtifactFact]:
    query = """
        SELECT s.owner_user_id, m.owner_id, s.session_id, s.conversation_id,
               e.evidence_id, m.media_item_id, m.opaque_object_key,
               m.media_type, m.normalized_sha256, m.normalized_byte_size
        FROM learning_sessions AS s
        JOIN evidence AS e ON e.session_id = s.session_id
        JOIN media_artifacts AS m ON e.artifact_id = m.media_item_id
        ORDER BY s.session_id, e.evidence_id
    """
    try:
        with psycopg.connect(shadow_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query)
                facts: list[DatabaseArtifactFact] = []
                for (
                    owner,
                    media_owner,
                    session,
                    conversation,
                    evidence,
                    artifact,
                    object_key,
                    media_type,
                    digest,
                    size,
                ) in cursor.fetchall():
                    if str(owner) != str(media_owner):
                        raise RecoveryValidationError("shadow database media owner differs")
                    facts.append(
                        DatabaseArtifactFact(
                            str(owner),
                            str(session),
                            str(conversation),
                            str(evidence),
                            str(artifact),
                            str(object_key),
                            str(media_type),
                            str(digest),
                            int(size),
                        )
                    )
                return facts
    except psycopg.Error as exc:
        raise RecoveryValidationError("shadow database media facts are unavailable") from exc


def restore_backup(
    *,
    manifest_path: Path,
    database_url: str,
    recovery_admin_url: str,
    coordination_data_dir: Path,
    openhands_data_dir: Path,
    media_data_dir: Path,
) -> None:
    with recovery_outcome("restore"):
        _restore_backup(
            manifest_path=manifest_path,
            database_url=database_url,
            recovery_admin_url=recovery_admin_url,
            coordination_data_dir=coordination_data_dir,
            openhands_data_dir=openhands_data_dir,
            media_data_dir=media_data_dir,
        )


def _restore_backup(
    *,
    manifest_path: Path,
    database_url: str,
    recovery_admin_url: str,
    coordination_data_dir: Path,
    openhands_data_dir: Path,
    media_data_dir: Path,
) -> None:
    if coordination_data_dir.is_symlink():
        raise RecoveryValidationError("coordination directory is unsafe")
    coordination_data_dir.mkdir(parents=True, exist_ok=True)
    if not coordination_data_dir.is_dir():
        raise RecoveryValidationError("coordination directory is unsafe")
    with maintenance_window(coordination_data_dir) as window:
        _restore_backup_in_window(
            manifest_path=manifest_path,
            database_url=database_url,
            recovery_admin_url=recovery_admin_url,
            coordination_data_dir=coordination_data_dir,
            openhands_data_dir=openhands_data_dir,
            media_data_dir=media_data_dir,
            window=window,
        )


def _restore_backup_in_window(
    *,
    manifest_path: Path,
    database_url: str,
    recovery_admin_url: str,
    coordination_data_dir: Path,
    openhands_data_dir: Path,
    media_data_dir: Path,
    window: MaintenanceWindow,
) -> None:
    openhands_data_dir, media_data_dir = _recovery_payload_layout(
        coordination_data_dir, openhands_data_dir, media_data_dir
    )
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise RecoveryValidationError("manifest does not exist or is not regular")
    manifest = _read_manifest(manifest_path)
    bundle_dir = manifest_path.parent.resolve(strict=True)
    _require_exact_bundle(bundle_dir)
    database_dump = bundle_dir / "database.dump"
    archive = bundle_dir / "openhands.tar.gz"
    media_archive = bundle_dir / "media.tar.gz"
    _require_regular_bundle_file(database_dump, bundle_dir)
    _require_regular_bundle_file(archive, bundle_dir)
    _require_regular_bundle_file(media_archive, bundle_dir)
    if _file_sha256(database_dump) != manifest.database_sha256:
        raise RecoveryValidationError("database digest mismatch")
    if _file_sha256(archive) != manifest.openhands_archive_sha256:
        raise RecoveryValidationError("OpenHands archive digest mismatch")
    if _file_sha256(media_archive) != manifest.media_archive_sha256:
        raise RecoveryValidationError("media archive digest mismatch")
    if current_application_revision() != manifest.application_revision:
        raise RecoveryValidationError("application revision mismatch")
    target_connection, _ = _recovery_connections(database_url, recovery_admin_url)
    if target_connection.username is None:
        raise RecoveryValidationError("target database user is required")
    _validate_archive(archive)
    _validate_archive(media_archive)
    shadow_name, rollback_name = _recovery_database_names()
    failed_name = f"focusproof_failed_{uuid4().hex}"
    shadow_url = _database_url_with_name(database_url, shadow_name)
    work_parent = openhands_data_dir.parent.resolve()
    work_parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=".focusproof-restore-", dir=work_parent))
    prepared_openhands = work_dir / "openhands"
    prepared_media = work_dir / "media"
    previous_openhands = openhands_data_dir.parent / f".{openhands_data_dir.name}-{rollback_name}"
    previous_media = media_data_dir.parent / f".{media_data_dir.name}-{rollback_name}"
    failed_openhands = openhands_data_dir.parent / f".{openhands_data_dir.name}-{failed_name}"
    failed_media = media_data_dir.parent / f".{media_data_dir.name}-{failed_name}"
    shadow_created = False
    database_cutover = False
    openhands_cutover = False
    media_cutover = False
    openhands_had_previous = False
    media_had_previous = False
    admin = None
    committed = False
    try:
        _extract_validated_archive(archive, prepared_openhands)
        _extract_validated_archive(media_archive, prepared_media)
        admin = _connect_admin(recovery_admin_url)
        with admin.cursor() as cursor:
            _create_database(cursor, shadow_name, target_connection.username)
        shadow_created = True
        _run_pg_restore(shadow_url, database_dump)
        rows = _shadow_artifact_rows(shadow_url)
        refs = _openhands_artifact_refs(prepared_openhands)
        _reconcile_three_sources(rows, refs, prepared_media)

        window.begin_recovery()
        with admin.cursor() as cursor:
            _cutover_database(
                cursor,
                live_name=target_connection.database_name,
                shadow_name=shadow_name,
                rollback_name=rollback_name,
            )
        database_cutover = True
        shadow_created = False
        openhands_had_previous = _swap_directory(
            prepared_openhands, openhands_data_dir, previous_openhands
        )
        openhands_cutover = True
        media_had_previous = _swap_directory(prepared_media, media_data_dir, previous_media)
        media_cutover = True
        live_rows = _shadow_artifact_rows(
            _database_url_with_name(database_url, target_connection.database_name)
        )
        live_refs = _openhands_artifact_refs(openhands_data_dir)
        _reconcile_three_sources(live_rows, live_refs, media_data_dir)
        window.complete_recovery()
        committed = True
        cleanup_actions: dict[str, Callable[[], None]] = {
            "rollback_database": lambda: _drop_rollback_database(admin, rollback_name),
        }
        if openhands_had_previous:
            cleanup_actions[str(previous_openhands)] = lambda: shutil.rmtree(
                previous_openhands, ignore_errors=False
            )
        if media_had_previous:
            cleanup_actions[str(previous_media)] = lambda: shutil.rmtree(
                previous_media, ignore_errors=False
            )
        _best_effort_post_commit_cleanup(cleanup_actions=cleanup_actions)
    except BaseException as primary:
        if committed:
            raise
        rollback_errors: list[BaseException] = []
        if media_cutover:
            try:
                _rollback_directory(
                    media_data_dir,
                    previous_media,
                    failed_media,
                    had_previous=media_had_previous,
                )
            except BaseException as exc:
                rollback_errors.append(exc)
        if openhands_cutover:
            try:
                _rollback_directory(
                    openhands_data_dir,
                    previous_openhands,
                    failed_openhands,
                    had_previous=openhands_had_previous,
                )
            except BaseException as exc:
                rollback_errors.append(exc)
        if database_cutover and admin is not None:
            try:
                with admin.cursor() as cursor:
                    _rollback_database(
                        cursor,
                        target_connection.database_name,
                        rollback_name,
                        failed_name,
                    )
            except BaseException as exc:
                rollback_errors.append(exc)
        if shadow_created and admin is not None:
            try:
                with admin.cursor() as cursor:
                    _terminate_database_connections(cursor, shadow_name)
                    _drop_database(cursor, shadow_name)
            except BaseException as exc:
                rollback_errors.append(exc)
        if rollback_errors:
            raise RecoveryValidationError(
                "recovery rollback failed; retained databases "
                f"{rollback_name}, {failed_name}, {shadow_name}"
            ) from primary
        raise
    finally:
        if admin is not None:
            admin.close()
        shutil.rmtree(work_dir, ignore_errors=True)


def _drop_rollback_database(admin: Any, rollback_name: str) -> None:
    with admin.cursor() as cursor:
        _terminate_database_connections(cursor, rollback_name)
        _drop_database(cursor, rollback_name)


def _best_effort_post_commit_cleanup(
    *,
    cleanup_actions: dict[str, Callable[[], None]],
    rollback: Callable[[], None] | None = None,
) -> list[str]:
    retained: list[str] = []
    for resource, action in cleanup_actions.items():
        try:
            action()
        except BaseException:
            retained.append(resource)
            LOGGER.warning(
                "post-commit recovery cleanup failed; retained resource %s",
                resource,
                exc_info=True,
            )
    return retained


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
    if manifest.schema_version != 2:
        raise RecoveryValidationError("manifest schema is unsupported")
    if manifest.openhands_tree_version != 1 or manifest.media_tree_version != 1:
        raise RecoveryValidationError("manifest tree version is unsupported")
    if (
        manifest.openhands_relative_path != "conversations"
        or manifest.media_relative_path != "media/objects"
    ):
        raise RecoveryValidationError("manifest payload layout is unsupported")
    for digest in (
        manifest.database_sha256,
        manifest.openhands_archive_sha256,
        manifest.media_archive_sha256,
    ):
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise RecoveryValidationError("manifest digest is invalid")
    return manifest


def _require_regular_bundle_file(path: Path, bundle_dir: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise RecoveryValidationError("paired artifact is not a regular file")
    if path.resolve().parent != bundle_dir:
        raise RecoveryValidationError("paired artifact escapes bundle")


def _require_exact_bundle(bundle_dir: Path) -> None:
    expected = {"database.dump", "openhands.tar.gz", "media.tar.gz", "manifest.json"}
    try:
        actual = {path.name for path in bundle_dir.iterdir()}
    except OSError as exc:
        raise RecoveryValidationError("paired bundle is unavailable") from exc
    if actual != expected:
        raise RecoveryValidationError("paired bundle must contain exactly four files")


def _validate_archive(path: Path) -> None:
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise RecoveryValidationError("archive member count exceeds limit")
            names: set[str] = set()
            total_size = 0
            for member in members:
                candidate = Path(member.name)
                normalized_name = member.name.rstrip("/")
                if (
                    not normalized_name
                    or candidate.is_absolute()
                    or ".." in candidate.parts
                    or member.issym()
                    or member.islnk()
                    or not (member.isfile() or member.isdir())
                ):
                    raise RecoveryValidationError("archive member is unsafe")
                if normalized_name in names:
                    raise RecoveryValidationError("archive contains duplicate member")
                names.add(normalized_name)
                if member.size > MAX_ARCHIVE_MEMBER_SIZE:
                    raise RecoveryValidationError("archive member size exceeds limit")
                total_size += member.size
                if total_size > MAX_ARCHIVE_TOTAL_SIZE:
                    raise RecoveryValidationError("archive total size exceeds limit")
            compressed_size = path.stat().st_size
            if compressed_size <= 0 or (
                total_size > compressed_size * MAX_ARCHIVE_COMPRESSION_RATIO
            ):
                raise RecoveryValidationError("archive compression ratio exceeds limit")
    except (tarfile.TarError, OSError) as exc:
        raise RecoveryValidationError("archive is invalid") from exc


def _extract_validated_archive(archive: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise RecoveryValidationError("isolated extraction target already exists")
    destination.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:gz") as source:
            source.extractall(destination, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise RecoveryValidationError("archive extraction failed") from exc
    for path in destination.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise RecoveryValidationError("extracted archive path is unsafe")


def _run_pg_restore(database_url: str, source: Path) -> None:
    try:
        connection = _postgres_cli_connection(database_url)
    except PostgresUrlError as exc:
        raise RecoveryValidationError(str(exc)) from exc
    args = ["pg_restore", "--exit-on-error", "--no-owner", "--no-privileges"]
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


def _swap_directory(prepared: Path, target: Path, previous: Path) -> bool:
    if prepared.is_symlink() or not prepared.is_dir():
        raise RecoveryValidationError("prepared persistence directory is invalid")
    if target.parent.is_symlink():
        raise RecoveryValidationError("live persistence parent is unsafe")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.parent.is_dir():
        raise RecoveryValidationError("live persistence parent is unsafe")
    if target.is_symlink() or previous.exists() or previous.is_symlink():
        raise RecoveryValidationError("live persistence directory is unsafe")
    if target.exists():
        os.replace(target, previous)
        had_previous = True
    else:
        had_previous = False
    try:
        os.replace(prepared, target)
    except BaseException:
        if previous.exists():
            os.replace(previous, target)
        raise
    return had_previous


def _rollback_directory(
    target: Path,
    previous: Path,
    failed: Path,
    *,
    had_previous: bool,
) -> None:
    if target.is_symlink() or previous.is_symlink() or failed.exists():
        raise RecoveryValidationError("persistence rollback path is unsafe")
    if target.exists():
        os.replace(target, failed)
    if had_previous and previous.exists():
        os.replace(previous, target)
    elif had_previous:
        raise RecoveryValidationError("previous persistence directory is missing")


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
    restored_members = {path.relative_to(target).as_posix() for path in target.rglob("*")}
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
