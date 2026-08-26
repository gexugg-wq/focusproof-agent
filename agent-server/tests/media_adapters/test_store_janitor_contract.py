from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import errno
import json
import os
from pathlib import Path
import stat
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

import focusproof.media_adapters.local_quarantine_store as quarantine_module
from focusproof.media_adapters.local_media_object_store import LocalMediaObjectStore
from focusproof.media_adapters.local_quarantine_store import (
    LocalQuarantineStore,
    QuarantinePublishError,
)
from focusproof.media_adapters.media_janitor import MediaJanitor
from focusproof.media_core.models import (
    MediaScanAttempt,
    PendingCleanReceipt,
    ScanResultKind,
)
from focusproof.persistence.database import create_database_engine, create_session_factory
from focusproof.persistence.models import Base, PendingCleanReceiptModel
from focusproof.persistence.unit_of_work import UnitOfWorkFactory


class Normalized:
    def __init__(self, payload: bytes = b"normalized") -> None:
        self.stream = BytesIO(payload)
        self.media_type = "image/png"
        self.byte_size = len(payload)
        self.normalized_sha256 = sha256(payload).hexdigest()

    def rewind(self) -> None:
        self.stream.seek(0)

    def close(self) -> None:
        self.stream.close()


def _promoted_pending_fixture(
    root: Path,
) -> tuple[LocalQuarantineStore, PendingCleanReceipt, bytes]:
    payload = b"formal payload"
    store = LocalQuarantineStore(root)
    writer = store.create_untrusted_scan_spool("formal-lookup-reservation")
    writer.write(payload)
    spool = writer.finalize()
    writer.close()
    created_at = datetime.now(UTC)
    pending = store.pending_clean_receipt(
        spool,
        receipt_id="receipt-formal-lookup",
        attempt_id="attempt-formal-lookup",
        artifact_sha256=sha256(payload).hexdigest(),
        receipt_hash="d" * 64,
        created_at=created_at,
    )
    formal = store.promote_clean_spool(
        spool,
        receipt_id=pending.receipt_id,
        receipt_hash=pending.receipt_hash,
        formal_artifact_id=pending.formal_artifact_id,
        quarantine_expires_at=pending.quarantine_expires_at,
    )
    formal.close()
    return store, pending, payload


def test_pending_identity_formal_lookup_returns_absent_or_exact_complete_triplet(
    tmp_path: Path,
) -> None:
    root = tmp_path / "quarantine"
    empty_store = LocalQuarantineStore(root)
    writer = empty_store.create_untrusted_scan_spool("formal-absent")
    writer.write(b"absent")
    spool = writer.finalize()
    writer.close()
    pending = empty_store.pending_clean_receipt(
        spool,
        receipt_id="receipt-formal-absent",
        attempt_id="attempt-formal-absent",
        artifact_sha256=sha256(b"absent").hexdigest(),
        receipt_hash="c" * 64,
        created_at=datetime.now(UTC),
    )
    assert empty_store.open_formal_clean_receipt(pending) is None

    store, pending, payload = _promoted_pending_fixture(tmp_path / "complete")
    formal = store.open_formal_clean_receipt(pending)
    assert formal is not None
    assert formal.quarantine_id == pending.formal_artifact_id
    with formal.open() as stream:
        assert stream.read() == payload


@pytest.mark.parametrize("missing", ["payload", "record", "commit"])
def test_pending_identity_formal_lookup_rejects_incomplete_triplet(
    tmp_path: Path,
    missing: str,
) -> None:
    root = tmp_path / "quarantine"
    store, pending, _ = _promoted_pending_fixture(root)
    paths = {
        "payload": root / "payloads" / pending.formal_artifact_id,
        "record": root / "records" / f"{pending.formal_artifact_id}.json",
        "commit": root / "commits" / f"{pending.formal_artifact_id}.commit",
    }
    paths[missing].unlink()

    with pytest.raises(ValueError, match="conflicting formal quarantine artifact"):
        store.open_formal_clean_receipt(pending)


def test_pending_identity_formal_lookup_rejects_conflicting_metadata(tmp_path: Path) -> None:
    root = tmp_path / "quarantine"
    store, pending, _ = _promoted_pending_fixture(root)
    record_path = root / "records" / f"{pending.formal_artifact_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["reservation_id"] = "promoted:conflicting-spool"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    record_path.chmod(0o600)

    with pytest.raises(ValueError, match="conflicting formal quarantine artifact"):
        store.open_formal_clean_receipt(pending)


def test_quarantine_key_is_opaque_and_roots_are_separate(tmp_path: Path) -> None:
    store = LocalQuarantineStore(tmp_path / "quarantine")
    writer = store.create("../../caller-reservation")
    writer.write(b"payload")
    item = writer.finalize()
    writer.close()
    assert "caller" not in item.quarantine_id
    assert "/" not in item.quarantine_id and "\\" not in item.quarantine_id
    object_store = LocalMediaObjectStore(tmp_path / "objects")
    staged = object_store.stage(Normalized(), "../../media", "/absolute/reservation")
    assert "media" not in staged.opaque_object_key
    assert "reservation" not in staged.opaque_object_key
    assert (tmp_path / "quarantine").resolve() != (tmp_path / "objects" / "staged").resolve()
    assert (tmp_path / "objects" / "staged").resolve() != (
        tmp_path / "objects" / "referenced"
    ).resolve()


def test_writer_abort_finalize_and_close_contract(tmp_path: Path) -> None:
    writer = LocalQuarantineStore(tmp_path / "quarantine").create("reservation")
    writer.write(b"partial")
    writer.abort()
    writer.abort()
    writer.close()
    with pytest.raises(ValueError):
        writer.finalize()

    complete = LocalQuarantineStore(tmp_path / "other").create("reservation")
    complete.write(b"complete")
    item = complete.finalize()
    complete.close()
    with item.open() as stream:
        assert stream.read() == b"complete"
    with pytest.raises(ValueError):
        complete.finalize()


def test_manifest_binds_all_identities_and_mark_is_atomic(tmp_path: Path) -> None:
    store = LocalMediaObjectStore(tmp_path / "objects")
    staged = store.stage(Normalized(), "media-item", "reservation")
    manifests = list((tmp_path / "objects" / "manifests").glob("*.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text(encoding="utf-8")) == {
        "schema": 2,
        "manifest_id": staged.manifest_id,
        "media_item_id": "media-item",
        "reservation_id": "reservation",
        "opaque_object_key": staged.opaque_object_key,
        "phase": "STAGED",
    }
    store.mark_referenced(staged)
    assert not (tmp_path / "objects" / "staged" / staged.opaque_object_key).exists()
    assert (tmp_path / "objects" / "referenced" / staged.opaque_object_key).is_file()
    assert not manifests[0].exists()


@pytest.mark.parametrize("key", ["../escape", "/absolute", "a/b", "", "a" * 33])
def test_open_and_delete_reject_non_opaque_keys(tmp_path: Path, key: str) -> None:
    store = LocalMediaObjectStore(tmp_path / "objects")
    with pytest.raises(ValueError):
        with store.open(key):
            pass
    with pytest.raises(ValueError):
        store.delete(key)


def test_symlink_escape_and_corrupt_or_forged_manifest_fail_closed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "objects"
    root.mkdir()
    (root / "staged").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        LocalMediaObjectStore(root)

    store = LocalMediaObjectStore(tmp_path / "clean")
    staged = store.stage(Normalized(), "media-item", "reservation")
    manifest = next((tmp_path / "clean" / "manifests").glob("*.json"))
    manifest.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError):
        store.mark_referenced(staged)
    manifest.write_text(
        json.dumps({"schema": 1, "media_item_id": "other", "reservation_id": "reservation", "opaque_object_key": staged.opaque_object_key}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        store.abort_staged(staged)


def test_store_never_deduplicates_equal_hash_without_database_intent(tmp_path: Path) -> None:
    store = LocalMediaObjectStore(tmp_path / "objects")
    first = store.stage(Normalized(b"same"), "one", "r1")
    second = store.stage(Normalized(b"same"), "two", "r2")
    assert first.opaque_object_key != second.opaque_object_key
    assert len(list((tmp_path / "objects" / "staged").iterdir())) == 2


@pytest.mark.parametrize("answer", [False, True, None])
def test_janitor_uses_fixed_stores_and_db_first(tmp_path: Path, answer: bool | None) -> None:
    quarantine = LocalQuarantineStore(tmp_path / "quarantine")
    objects = LocalMediaObjectStore(tmp_path / "objects")
    staged = objects.stage(Normalized(), "media-item", "reservation")
    manifest = tmp_path / "objects" / "manifests" / f"{staged.manifest_id}.json"
    old = time.time() - 1000
    os.utime(manifest, (old, old))
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    calls: list[str] = []
    janitor = MediaJanitor(
        quarantine_store=quarantine,
        object_store=objects,
        reference_checker=lambda key: calls.append(key) or answer,
        reservation_active_checker=lambda _: True,
    )
    janitor.sweep(older_than_seconds=100)
    assert calls == ["media-item"]
    assert outside.exists()
    assert manifest.exists() is (answer is None)


def test_quarantine_ttl_boundary_and_receipt_expiry_are_enforced(tmp_path: Path) -> None:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    store = LocalQuarantineStore(tmp_path / "quarantine", clock=lambda: now)
    writer = store.create(
        "reservation",
        quarantine_expires_at=now + timedelta(seconds=60),
    )
    writer.write(b"payload")
    item = writer.finalize()
    writer.close()

    assert item.is_expired(now + timedelta(seconds=59)) is False
    assert item.is_expired(now + timedelta(seconds=60)) is True
    assert item.is_expired(now + timedelta(seconds=61)) is True
    assert item.quarantine_expires_at == now + timedelta(seconds=60)


def test_quarantine_permissions_are_owner_only(tmp_path: Path) -> None:
    store = LocalQuarantineStore(tmp_path / "quarantine")
    writer = store.create("reservation")
    writer.write(b"payload")
    item = writer.finalize()
    writer.close()

    assert stat.S_IMODE((tmp_path / "quarantine").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "quarantine" / "payloads").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "quarantine" / "records").stat().st_mode) == 0o700
    payload = tmp_path / "quarantine" / "payloads" / item.quarantine_id
    record = tmp_path / "quarantine" / "records" / f"{item.quarantine_id}.json"
    assert stat.S_IMODE(payload.stat().st_mode) == 0o600
    assert stat.S_IMODE(record.stat().st_mode) == 0o600


def test_quarantine_open_rejects_symlink_and_permission_drift(tmp_path: Path) -> None:
    store = LocalQuarantineStore(tmp_path / "quarantine")
    writer = store.create("reservation")
    writer.write(b"payload")
    item = writer.finalize()
    writer.close()
    payload = tmp_path / "quarantine" / "payloads" / item.quarantine_id
    payload.chmod(0o644)
    with pytest.raises(ValueError, match="permission"):
        with item.open():
            pass


def test_expiry_sweep_deletes_only_managed_pairs_and_preserves_outside(tmp_path: Path) -> None:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    store = LocalQuarantineStore(tmp_path / "quarantine", clock=lambda: now)
    writer = store.create("reservation", quarantine_expires_at=now + timedelta(seconds=60))
    writer.write(b"payload")
    item = writer.finalize()
    writer.close()
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")

    assert store.remove_expired(now=now + timedelta(seconds=60)) == (item.quarantine_id,)
    assert outside.read_bytes() == b"keep"
    assert store.remove_expired(now=now + timedelta(seconds=61)) == ()


def _visible_quarantine_ids(root: Path) -> set[str]:
    commits = root / "commits"
    if not commits.exists():
        return set()
    return {path.stem for path in commits.glob("*.commit")}


def _part_files(root: Path) -> list[Path]:
    return sorted(root.glob("**/*.part"))


def test_formal_quarantine_requires_commit_marker_and_bound_record(tmp_path: Path) -> None:
    store = LocalQuarantineStore(tmp_path / "quarantine")
    writer = store.create("reservation", receipt_id="receipt-1", receipt_hash="a" * 64)
    writer.write(b"payload")
    item = writer.finalize()
    writer.close()

    root = tmp_path / "quarantine"
    record = root / "records" / f"{item.quarantine_id}.json"
    commit = root / "commits" / f"{item.quarantine_id}.commit"
    payload = root / "payloads" / item.quarantine_id

    assert commit.exists()
    assert item.receipt_id == "receipt-1"
    assert item.receipt_hash == "a" * 64
    stored = json.loads(record.read_text(encoding="utf-8"))
    assert stored["schema"] == 3
    assert stored["quarantine_id"] == item.quarantine_id
    assert stored["reservation_id"] == "reservation"
    assert stored["receipt_id"] == "receipt-1"
    assert stored["receipt_hash"] == "a" * 64
    assert stored["quarantine_expires_at"] == item.quarantine_expires_at.isoformat()
    assert stored["payload_name"] == item.quarantine_id
    assert stored["byte_size"] == len(b"payload")
    assert stored["sha256"] == sha256(b"payload").hexdigest()
    assert stored["mode"] == "0600"
    commit.unlink()
    with pytest.raises(ValueError, match="commit"):
        with item.open():
            pass
    assert payload.read_bytes() == b"payload"


@pytest.mark.parametrize(
    "stage",
    [
        "payload_file_fsync",
        "payload_dir_fsync",
        "record_file_fsync",
        "record_dir_fsync",
        "commit_file_fsync",
        "commit_dir_fsync",
    ],
)
def test_quarantine_publish_failure_never_leaves_visible_or_part_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    root = tmp_path / "quarantine"
    original_fsync = quarantine_module.os.fsync

    def fail_selected(fd: int) -> None:
        target = Path(os.readlink(f"/proc/self/fd/{fd}"))
        name = target.name
        parent = target.name if target.is_dir() else target.parent.name
        if (
            (stage == "payload_file_fsync" and parent == "payloads" and name.endswith(".part"))
            or (stage == "payload_dir_fsync" and name == "payloads")
            or (stage == "record_file_fsync" and parent == "records" and name.endswith(".part"))
            or (stage == "record_dir_fsync" and name == "records")
            or (stage == "commit_file_fsync" and parent == "commits" and name.endswith(".part"))
            or (stage == "commit_dir_fsync" and name == "commits")
        ):
            raise OSError(errno.EIO, f"injected {stage}")
        original_fsync(fd)

    monkeypatch.setattr(quarantine_module.os, "fsync", fail_selected)
    store = LocalQuarantineStore(root)
    writer = store.create("reservation", receipt_id="receipt-1", receipt_hash="b" * 64)
    writer.write(b"payload")

    with pytest.raises(OSError):
        writer.finalize()

    assert _visible_quarantine_ids(root) == set()
    assert _part_files(root) == []


def test_commit_fsync_failure_reports_marker_rollback_failure_without_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "quarantine"
    original_fsync = quarantine_module.os.fsync
    original_unlink = quarantine_module.Path.unlink
    fail_commit_fsync_once = True
    fail_marker_unlink_once = True

    def fail_selected_fsync(fd: int) -> None:
        nonlocal fail_commit_fsync_once
        target = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if fail_commit_fsync_once and target.is_dir() and target.name == "commits":
            fail_commit_fsync_once = False
            raise OSError(errno.EIO, "injected commit dir fsync")
        original_fsync(fd)

    def fail_selected_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal fail_marker_unlink_once
        if fail_marker_unlink_once and path.parent.name == "commits" and path.suffix == ".commit":
            fail_marker_unlink_once = False
            raise OSError(errno.EIO, "injected marker unlink")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(quarantine_module.os, "fsync", fail_selected_fsync)
    monkeypatch.setattr(quarantine_module.Path, "unlink", fail_selected_unlink)
    store = LocalQuarantineStore(root)
    writer = store.create("reservation", receipt_id="receipt-1", receipt_hash="b" * 64)
    writer.write(b"payload")

    with pytest.raises(QuarantinePublishError) as caught:
        writer.finalize()

    assert caught.value.rollback_failures[0].target == "commits"
    assert caught.value.rollback_failures[0].operation == "unlink"
    assert _part_files(root) == []
    assert all(str(tmp_path) not in failure.error_type for failure in caught.value.rollback_failures)


def test_rollback_directory_fsync_failure_is_reported_as_recoverable_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "quarantine"
    original_fsync = quarantine_module.os.fsync
    commit_seen = False
    fail_second_payload_fsync = True

    def fail_selected(fd: int) -> None:
        nonlocal commit_seen, fail_second_payload_fsync
        target = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if target.is_dir() and target.name == "commits":
            commit_seen = True
            raise OSError(errno.EIO, "publish commit fsync")
        if commit_seen and fail_second_payload_fsync and target.is_dir() and target.name == "payloads":
            fail_second_payload_fsync = False
            raise OSError(errno.EIO, "rollback payload fsync")
        original_fsync(fd)

    monkeypatch.setattr(quarantine_module.os, "fsync", fail_selected)
    store = LocalQuarantineStore(root)
    writer = store.create("reservation", receipt_id="receipt-1", receipt_hash="b" * 64)
    writer.write(b"payload")

    with pytest.raises(QuarantinePublishError) as caught:
        writer.finalize()

    assert any(
        failure.target == "payloads" and failure.operation == "directory_fsync"
        for failure in caught.value.rollback_failures
    )
    assert _visible_quarantine_ids(root) == set()


def test_recovery_removes_invisible_formal_quarantine_orphans(tmp_path: Path) -> None:
    store = LocalQuarantineStore(tmp_path / "quarantine")
    orphan = "c" * 32
    payload = tmp_path / "quarantine" / "payloads" / orphan
    record = tmp_path / "quarantine" / "records" / f"{orphan}.json"
    payload.write_bytes(b"payload")
    payload.chmod(0o600)
    record.write_text(
        json.dumps(
            {
                "schema": 3,
                "quarantine_id": orphan,
                "reservation_id": "reservation",
                "receipt_id": "receipt-1",
                "receipt_hash": "d" * 64,
                "created_at": datetime.now(UTC).isoformat(),
                "quarantine_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "payload_name": orphan,
                "byte_size": len(b"payload"),
                "sha256": sha256(b"payload").hexdigest(),
                "mode": "0600",
            }
        ),
        encoding="utf-8",
    )
    record.chmod(0o600)
    old = time.time() - 1000
    os.utime(payload, (old, old))
    os.utime(record, (old, old))

    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == (orphan,)
    assert not payload.exists()
    assert not record.exists()


def test_recovery_removes_marker_only_orphan_without_touching_valid_triplet(tmp_path: Path) -> None:
    store = LocalQuarantineStore(tmp_path / "quarantine")
    valid_writer = store.create("reservation", receipt_id="receipt-1", receipt_hash="a" * 64)
    valid_writer.write(b"valid")
    valid = valid_writer.finalize()
    valid_writer.close()

    orphan = "e" * 32
    marker = tmp_path / "quarantine" / "commits" / f"{orphan}.commit"
    marker.write_text(
        json.dumps({"schema": 1, "quarantine_id": orphan, "receipt_hash": "f" * 64}),
        encoding="utf-8",
    )
    marker.chmod(0o600)
    old = time.time() - 1000
    os.utime(marker, (old, old))

    assert store.recover_quarantine(lambda _: True, older_than_seconds=100) == (orphan,)
    assert not marker.exists()
    assert (tmp_path / "quarantine" / "commits" / f"{valid.quarantine_id}.commit").exists()
    with valid.open() as stream:
        assert stream.read() == b"valid"


@pytest.mark.parametrize(
    ("target", "path_kind"),
    [
        ("marker", "commit"),
        ("record", "record"),
        ("payload", "payload"),
        ("payload_part", "part"),
    ],
)
def test_recovery_reports_sanitized_diagnostics_and_retries_failed_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    path_kind: str,
) -> None:
    store = LocalQuarantineStore(tmp_path / "quarantine")
    orphan = "1" * 32
    payload = tmp_path / "quarantine" / "payloads" / orphan
    record = tmp_path / "quarantine" / "records" / f"{orphan}.json"
    marker = tmp_path / "quarantine" / "commits" / f"{orphan}.commit"
    part = tmp_path / "quarantine" / "payloads" / f".{orphan}.part"
    payload.write_bytes(b"payload")
    payload.chmod(0o600)
    record.write_text(
        json.dumps(
            {
                "schema": 3,
                "quarantine_id": orphan,
                "reservation_id": "reservation",
                "receipt_id": "receipt-1",
                "receipt_hash": "d" * 64,
                "created_at": datetime.now(UTC).isoformat(),
                "quarantine_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "payload_name": orphan,
                "byte_size": len(b"payload"),
                "sha256": "0" * 64,
                "mode": "0600",
            }
        ),
        encoding="utf-8",
    )
    record.chmod(0o600)
    marker.write_text(
        json.dumps({"schema": 1, "quarantine_id": orphan, "receipt_hash": "d" * 64}),
        encoding="utf-8",
    )
    marker.chmod(0o600)
    part.write_bytes(b"part")
    part.chmod(0o600)
    old = time.time() - 1000
    for candidate in (payload, record, marker, part):
        os.utime(candidate, (old, old))

    original_unlink = quarantine_module.Path.unlink
    fail_once = True

    def fail_selected_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal fail_once
        selected = {
            "commit": path == marker,
            "record": path == record,
            "payload": path == payload,
            "part": path == part,
        }[path_kind]
        if fail_once and selected:
            fail_once = False
            raise OSError(errno.EIO, "secret=/outside/path")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(quarantine_module.Path, "unlink", fail_selected_unlink)

    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == ()
    diagnostic = store.last_diagnostics[0]
    assert diagnostic.artifact_id == orphan
    assert diagnostic.target == target
    assert diagnostic.operation == "unlink"
    assert diagnostic.error_type == "OSError"
    assert diagnostic.retryable is True
    assert str(tmp_path) not in repr(diagnostic)
    assert "secret" not in repr(diagnostic)

    monkeypatch.setattr(quarantine_module.Path, "unlink", original_unlink)
    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == (orphan,)
    assert not marker.exists()
    assert not record.exists()
    assert not payload.exists()
    assert not part.exists()
    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == ()


def test_recovery_reports_parent_fsync_failure_as_retryable_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalQuarantineStore(tmp_path / "quarantine")
    part_id = "2" * 32
    part = tmp_path / "quarantine" / "payloads" / f".{part_id}.part"
    part.write_bytes(b"part")
    part.chmod(0o600)
    old = time.time() - 1000
    os.utime(part, (old, old))
    original_fsync = quarantine_module.os.fsync
    fail_once = True

    def fail_payload_dir(fd: int) -> None:
        nonlocal fail_once
        target = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if fail_once and target.is_dir() and target.name == "payloads":
            fail_once = False
            raise OSError(errno.EIO, "secret fsync path")
        original_fsync(fd)

    monkeypatch.setattr(quarantine_module.os, "fsync", fail_payload_dir)

    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == ()
    diagnostic = store.last_diagnostics[0]
    assert diagnostic.artifact_id == part_id
    assert diagnostic.target == "payload_part"
    assert diagnostic.operation == "fsync"
    assert diagnostic.retryable is True
    assert "secret" not in repr(diagnostic)

    monkeypatch.setattr(quarantine_module.os, "fsync", original_fsync)
    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == (part_id,)
    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == ()
    assert not any((tmp_path / "quarantine" / "deletion-journal").iterdir())


_PART_TARGET_CASES = (
    ("payload_part", "payloads", "a" * 32),
    ("record_part", "records", "b" * 32),
    ("commit_part", "commits", "c" * 32),
    ("spool_part", "untrusted-scan-spool", "d" * 32),
)

_PART_JOURNAL_REPLAY_STAGES = (
    "intent_write",
    "intent_fsync",
    "intent_rename",
    "intent_dir_fsync",
    "target_unlink",
    "target_dir_fsync",
    "journal_unlink",
    "journal_cleanup_dir_fsync",
)


def _write_old_part(root: Path, directory_name: str, artifact_id: str) -> Path:
    candidate = root / directory_name / f".{artifact_id}.part"
    candidate.write_bytes(b"old part")
    candidate.chmod(0o600)
    old = time.time() - 1000
    os.utime(candidate, (old, old))
    return candidate


@pytest.mark.parametrize(("target", "directory_name", "artifact_id"), _PART_TARGET_CASES)
def test_recover_quarantine_removes_old_parts_from_each_managed_directory(
    tmp_path: Path,
    target: str,
    directory_name: str,
    artifact_id: str,
) -> None:
    del target
    root = tmp_path / "quarantine"
    store = LocalQuarantineStore(root)
    candidate = _write_old_part(root, directory_name, artifact_id)

    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == (artifact_id,)
    assert not candidate.exists()
    assert _journal_files(root) == []
    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == ()


@pytest.mark.parametrize(("target", "directory_name", "artifact_id"), _PART_TARGET_CASES)
@pytest.mark.parametrize("fault", ["intent", "unlink", "target_dir_fsync", "journal_cleanup_fsync"])
def test_directory_specific_part_journal_fault_matrix_recovers_original_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    directory_name: str,
    artifact_id: str,
    fault: str,
) -> None:
    root = tmp_path / "quarantine"
    store = LocalQuarantineStore(root)
    candidate = _write_old_part(root, directory_name, artifact_id)
    diagnostics: list[quarantine_module.RecoveryDiagnostic] = []
    original_write_journal = store._write_journal
    original_unlink = quarantine_module.Path.unlink
    original_fsync = quarantine_module.os.fsync

    if fault == "intent":
        def write_intent_then_fail(path: Path, payload: dict[str, object]) -> None:
            original_write_journal(path, payload)
            raise OSError(errno.EIO, "secret intent")

        monkeypatch.setattr(store, "_write_journal", write_intent_then_fail)
    elif fault == "unlink":
        def fail_target_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path == candidate:
                raise OSError(errno.EIO, "secret unlink")
            original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(quarantine_module.Path, "unlink", fail_target_unlink)
    elif fault == "target_dir_fsync":
        def fail_target_dir_fsync(fd: int) -> None:
            selected = Path(os.readlink(f"/proc/self/fd/{fd}"))
            if selected == candidate.parent:
                raise OSError(errno.EIO, "secret target dir fsync")
            original_fsync(fd)

        monkeypatch.setattr(quarantine_module.os, "fsync", fail_target_dir_fsync)
    else:
        journal_unlinked = False

        def track_journal_unlink(path: Path, *args: object, **kwargs: object) -> None:
            nonlocal journal_unlinked
            original_unlink(path, *args, **kwargs)
            if path.parent.name == "deletion-journal" and not path.name.startswith("."):
                journal_unlinked = True

        def fail_journal_cleanup_fsync(fd: int) -> None:
            selected = Path(os.readlink(f"/proc/self/fd/{fd}"))
            if journal_unlinked and selected.name == "deletion-journal":
                raise OSError(errno.EIO, "secret journal cleanup")
            original_fsync(fd)

        monkeypatch.setattr(quarantine_module.Path, "unlink", track_journal_unlink)
        monkeypatch.setattr(quarantine_module.os, "fsync", fail_journal_cleanup_fsync)

    assert not store._recovery_unlink(
        candidate,
        artifact_id=artifact_id,
        target=target,
        diagnostics=diagnostics,
    )
    assert _journal_files(root) != []
    if fault in {"intent", "unlink"}:
        assert candidate.exists()
    else:
        assert not candidate.exists()
    assert diagnostics
    assert diagnostics[0].artifact_id == artifact_id
    assert diagnostics[0].target == target
    assert diagnostics[0].retryable is True
    assert str(tmp_path) not in repr(diagnostics)
    assert "secret" not in repr(diagnostics)

    monkeypatch.undo()
    restarted = LocalQuarantineStore(root)
    assert restarted.recover_quarantine(lambda _: False, older_than_seconds=100) == (artifact_id,)
    assert not candidate.exists()
    assert _journal_files(root) == []
    assert restarted.recover_quarantine(lambda _: False, older_than_seconds=100) == ()


@pytest.mark.parametrize(("target", "directory_name", "artifact_id"), _PART_TARGET_CASES)
@pytest.mark.parametrize("stage", _PART_JOURNAL_REPLAY_STAGES)
def test_directory_specific_part_recovery_journal_replay_matrix_recovers_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    directory_name: str,
    artifact_id: str,
    stage: str,
) -> None:
    root = tmp_path / "quarantine"
    store = LocalQuarantineStore(root)
    selected = _write_old_part(root, directory_name, artifact_id)
    _install_deletion_fault(monkeypatch, stage=stage, selected_path=selected)

    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == ()
    if stage in {
        "intent_write",
        "intent_fsync",
        "intent_rename",
        "intent_dir_fsync",
        "target_unlink",
    }:
        assert selected.exists()
    else:
        assert not selected.exists()
        assert _journal_files(root) != []
    assert store.last_diagnostics
    assert all(item.target in {target, "journal"} for item in store.last_diagnostics)
    assert str(tmp_path) not in repr(store.last_diagnostics)
    assert "injected" not in repr(store.last_diagnostics)

    monkeypatch.undo()
    restarted = LocalQuarantineStore(root)
    assert restarted.recover_quarantine(lambda _: False, older_than_seconds=100) == (artifact_id,)
    assert restarted.recover_quarantine(lambda _: False, older_than_seconds=100) == ()
    assert not selected.exists()
    assert _journal_files(root) == []


@pytest.mark.parametrize(("target", "directory_name", "artifact_id"), _PART_TARGET_CASES)
def test_old_part_recovery_rejects_valid_hex_part_without_managed_basename(
    tmp_path: Path,
    target: str,
    directory_name: str,
    artifact_id: str,
) -> None:
    root = tmp_path / "quarantine"
    store = LocalQuarantineStore(root)
    candidate = root / directory_name / f"{artifact_id}.part"
    candidate.write_bytes(b"keep")
    candidate.chmod(0o600)
    old = time.time() - 1000
    os.utime(candidate, (old, old))

    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == ()
    assert candidate.exists()
    assert _journal_files(root) == []
    assert store.last_diagnostics
    diagnostic = store.last_diagnostics[0]
    assert diagnostic.artifact_id == artifact_id
    assert diagnostic.target == target
    assert diagnostic.operation == "validate"
    assert diagnostic.retryable is False
    assert str(tmp_path) not in repr(store.last_diagnostics)


@pytest.mark.parametrize(("target", "directory_name", "artifact_id"), _PART_TARGET_CASES)
def test_old_part_recovery_rejects_illegal_basename_without_path_leak(
    tmp_path: Path,
    target: str,
    directory_name: str,
    artifact_id: str,
) -> None:
    del target, artifact_id
    root = tmp_path / "quarantine"
    store = LocalQuarantineStore(root)
    candidate = root / directory_name / "not-hex.part"
    candidate.write_bytes(b"keep")
    candidate.chmod(0o600)
    old = time.time() - 1000
    os.utime(candidate, (old, old))

    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == ()
    assert candidate.exists()
    assert store.last_diagnostics
    assert all(item.artifact_id == "invalid" for item in store.last_diagnostics)
    assert str(tmp_path) not in repr(store.last_diagnostics)


@pytest.mark.parametrize(
    ("directory_name", "artifact_id"),
    [
        ("records", "e" * 32),
        ("commits", "f" * 32),
        ("untrusted-scan-spool", "1" * 32),
    ],
)
def test_legacy_part_target_rejects_non_payload_part_paths_without_journal(
    tmp_path: Path,
    directory_name: str,
    artifact_id: str,
) -> None:
    root = tmp_path / "quarantine"
    store = LocalQuarantineStore(root)
    candidate = _write_old_part(root, directory_name, artifact_id)
    diagnostics: list[quarantine_module.RecoveryDiagnostic] = []

    assert not store._recovery_unlink(
        candidate,
        artifact_id=artifact_id,
        target="part",
        diagnostics=diagnostics,
    )
    assert candidate.exists()
    assert _journal_files(root) == []
    assert diagnostics == [
        quarantine_module.RecoveryDiagnostic(
            artifact_id, "part", "validate", "ValueError", False
        )
    ]



@pytest.mark.parametrize("target", ["marker", "record", "payload", "part"])
@pytest.mark.parametrize(
    "fault", ["intent", "unlink", "target_fsync", "journal_cleanup_fsync"]
)
def test_deletion_journal_fault_matrix_is_retryable_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    fault: str,
) -> None:
    store = LocalQuarantineStore(tmp_path / "quarantine")
    artifact_id = "3" * 32
    root = tmp_path / "quarantine"
    paths = {
        "marker": root / "commits" / f"{artifact_id}.commit",
        "record": root / "records" / f"{artifact_id}.json",
        "payload": root / "payloads" / artifact_id,
        "part": root / "payloads" / f".{artifact_id}.part",
    }
    candidate = paths[target]
    candidate.write_bytes(b"owned")
    candidate.chmod(0o600)
    diagnostics: list[quarantine_module.RecoveryDiagnostic] = []
    original_unlink = quarantine_module.Path.unlink
    original_fsync = quarantine_module.os.fsync

    if fault == "intent":
        def fail_intent(path: Path, payload: dict[str, object]) -> None:
            del path, payload
            raise OSError(errno.EIO, "secret intent")

        monkeypatch.setattr(store, "_write_journal", fail_intent)
        assert not store._recovery_unlink(
            candidate,
            artifact_id=artifact_id,
            target=target,
            diagnostics=diagnostics,
        )
        assert candidate.exists()
        assert diagnostics[0].operation == "journal"
        return

    if fault == "unlink":
        def fail_target_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path == candidate:
                raise OSError(errno.EIO, "secret unlink")
            original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(quarantine_module.Path, "unlink", fail_target_unlink)
    elif fault == "target_fsync":
        def fail_target_fsync(fd: int) -> None:
            selected = Path(os.readlink(f"/proc/self/fd/{fd}"))
            if selected == candidate.parent:
                raise OSError(errno.EIO, "secret target fsync")
            original_fsync(fd)

        monkeypatch.setattr(quarantine_module.os, "fsync", fail_target_fsync)
    elif fault == "journal_cleanup_fsync":
        journal_unlinked = False

        def track_journal_unlink(
            path: Path, *args: object, **kwargs: object
        ) -> None:
            nonlocal journal_unlinked
            original_unlink(path, *args, **kwargs)
            if path.parent.name == "deletion-journal" and not path.name.startswith("."):
                journal_unlinked = True

        def fail_journal_cleanup_fsync(fd: int) -> None:
            selected = Path(os.readlink(f"/proc/self/fd/{fd}"))
            if journal_unlinked and selected.name == "deletion-journal":
                raise OSError(errno.EIO, "secret journal cleanup")
            original_fsync(fd)

        monkeypatch.setattr(quarantine_module.Path, "unlink", track_journal_unlink)
        monkeypatch.setattr(quarantine_module.os, "fsync", fail_journal_cleanup_fsync)

    assert not store._recovery_unlink(
        candidate,
        artifact_id=artifact_id,
        target=target,
        diagnostics=diagnostics,
    )
    assert diagnostics[0].artifact_id == artifact_id
    assert diagnostics[0].target == target
    assert diagnostics[0].retryable is True
    assert not any("secret" in repr(item) for item in diagnostics)
    monkeypatch.setattr(quarantine_module.Path, "unlink", original_unlink)
    monkeypatch.setattr(quarantine_module.os, "fsync", original_fsync)
    diagnostics.clear()
    assert store._recover_deletion_journals(diagnostics) == [artifact_id]

    assert not candidate.exists()
    diagnostics.clear()
    assert store._recover_deletion_journals(diagnostics) == []
    assert store._recover_deletion_journals(diagnostics) == []
    assert not any((root / "deletion-journal").iterdir())



def _journal_files(root: Path) -> list[Path]:
    journal = root / "deletion-journal"
    if not journal.exists():
        return []
    return sorted(path for path in journal.iterdir() if not path.name.startswith("."))


def _make_deletion_candidate(root: Path, target: str) -> tuple[str, Path]:
    LocalQuarantineStore(root)
    item_id = {
        "marker": "3" * 32,
        "record": "4" * 32,
        "payload": "5" * 32,
        "part": "6" * 32,
    }[target]
    if target == "marker":
        path = root / "commits" / f"{item_id}.commit"
        path.write_text(
            json.dumps({"schema": 1, "quarantine_id": item_id, "receipt_hash": "d" * 64}),
            encoding="utf-8",
        )
    elif target == "record":
        path = root / "records" / f"{item_id}.json"
        path.write_text(
            json.dumps(
                {
                    "schema": 3,
                    "quarantine_id": item_id,
                    "reservation_id": "inactive-reservation",
                    "receipt_id": "receipt-1",
                    "receipt_hash": "d" * 64,
                    "created_at": datetime.now(UTC).isoformat(),
                    "quarantine_expires_at": (
                        datetime.now(UTC) + timedelta(hours=1)
                    ).isoformat(),
                    "payload_name": item_id,
                    "byte_size": 7,
                    "sha256": sha256(b"payload").hexdigest(),
                    "mode": "0600",
                }
            ),
            encoding="utf-8",
        )
    elif target == "payload":
        path = root / "payloads" / item_id
        path.write_bytes(b"payload")
    else:
        path = root / "payloads" / f".{item_id}.part"
        path.write_bytes(b"part")
    path.chmod(0o600)
    old = time.time() - 1000
    os.utime(path, (old, old))
    return item_id, path


def _install_deletion_fault(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stage: str,
    selected_path: Path,
) -> None:
    original_open = quarantine_module.os.open
    original_fsync = quarantine_module.os.fsync
    original_replace = quarantine_module.os.replace
    original_unlink = quarantine_module.Path.unlink
    failed = False
    journal_unlinked = False

    def fail_once() -> bool:
        nonlocal failed
        if failed:
            return False
        failed = True
        return True

    def open_fault(path: object, flags: int, *args: object, **kwargs: object) -> int:
        candidate = Path(path)
        if (
            stage == "intent_write"
            and candidate.parent.name == "deletion-journal"
            and candidate.name.startswith(".")
            and candidate.name.endswith(".part")
            and fail_once()
        ):
            raise OSError(errno.EIO, "injected intent write")
        return original_open(path, flags, *args, **kwargs)

    def fsync_fault(fd: int) -> None:
        target = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if (
            stage == "intent_fsync"
            and target.parent.name == "deletion-journal"
            and target.name.startswith(".")
            and target.name.endswith(".part")
            and fail_once()
        ):
            raise OSError(errno.EIO, "injected intent fsync")
        if (
            stage == "intent_dir_fsync"
            and target.is_dir()
            and target.name == "deletion-journal"
            and fail_once()
        ):
            raise OSError(errno.EIO, "injected journal intent fsync")
        if (
            stage == "target_dir_fsync"
            and target == selected_path.parent
            and target.is_dir()
            and fail_once()
        ):
            raise OSError(errno.EIO, "injected target dir fsync")
        if (
            stage == "journal_cleanup_dir_fsync"
            and journal_unlinked
            and target.is_dir()
            and target.name == "deletion-journal"
            and fail_once()
        ):
            raise OSError(errno.EIO, "injected journal cleanup fsync")
        original_fsync(fd)

    def replace_fault(src: object, dst: object, *args: object, **kwargs: object) -> None:
        destination = Path(dst)
        if (
            stage == "intent_rename"
            and destination.parent.name == "deletion-journal"
            and destination.name.endswith(".json")
            and fail_once()
        ):
            raise OSError(errno.EIO, "injected journal rename")
        original_replace(src, dst, *args, **kwargs)

    def unlink_fault(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal journal_unlinked
        if stage == "target_unlink" and path == selected_path and fail_once():
            raise OSError(errno.EIO, "injected target unlink")
        if (
            stage == "journal_unlink"
            and path.parent.name == "deletion-journal"
            and not path.name.startswith(".")
            and fail_once()
        ):
            raise OSError(errno.EIO, "injected journal unlink")
        original_unlink(path, *args, **kwargs)
        if path.parent.name == "deletion-journal" and not path.name.startswith("."):
            journal_unlinked = True

    monkeypatch.setattr(quarantine_module.os, "open", open_fault)
    monkeypatch.setattr(quarantine_module.os, "fsync", fsync_fault)
    monkeypatch.setattr(quarantine_module.os, "replace", replace_fault)
    monkeypatch.setattr(quarantine_module.Path, "unlink", unlink_fault)


@pytest.mark.parametrize("target", ["marker", "record", "payload", "part"])
@pytest.mark.parametrize(
    "stage",
    [
        "intent_write",
        "intent_fsync",
        "intent_rename",
        "intent_dir_fsync",
        "target_unlink",
        "target_dir_fsync",
        "journal_unlink",
        "journal_cleanup_dir_fsync",
    ],
)
def test_recovery_deletion_journal_fault_matrix_recovers_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    stage: str,
) -> None:
    root = tmp_path / "quarantine"
    item_id, selected = _make_deletion_candidate(root, target)
    store = LocalQuarantineStore(root)
    _install_deletion_fault(monkeypatch, stage=stage, selected_path=selected)

    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == ()
    if stage in {"intent_write", "intent_fsync", "intent_rename", "intent_dir_fsync", "target_unlink"}:
        assert selected.exists()
    else:
        assert not selected.exists()
        assert _journal_files(root) != []
    assert store.last_diagnostics

    monkeypatch.undo()
    restarted = LocalQuarantineStore(root)
    assert restarted.recover_quarantine(lambda _: False, older_than_seconds=100) == (item_id,)
    assert restarted.recover_quarantine(lambda _: False, older_than_seconds=100) == ()
    assert not selected.exists()
    assert _journal_files(root) == []


def test_deletion_journal_recovery_rejects_escaping_and_oversized_records(
    tmp_path: Path,
) -> None:
    root = tmp_path / "quarantine"
    store = LocalQuarantineStore(root)
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    journal = root / "deletion-journal"
    journal.mkdir(exist_ok=True)
    escaping = journal / "escape.json"
    escaping.write_text(
        json.dumps(
            {
                "schema": 1,
                "artifact_id": "7" * 32,
                "target": "payload",
                "relative_path": "../outside",
                "state": "intent",
            }
        ),
        encoding="utf-8",
    )
    escaping.chmod(0o600)
    oversized = journal / "oversized.json"
    oversized.write_text("{" + '"x":"' + ("a" * 5000) + '"}', encoding="utf-8")
    oversized.chmod(0o600)

    assert store.recover_quarantine(lambda _: False, older_than_seconds=0) == ()
    assert outside.read_bytes() == b"keep"
    assert len(store.last_diagnostics) >= 2


def test_deletion_journal_directory_replacement_reports_diagnostic_and_keeps_triplet(
    tmp_path: Path,
) -> None:
    root = tmp_path / "quarantine"
    store = LocalQuarantineStore(root)
    writer = store.create("reservation", receipt_id="receipt-1", receipt_hash="a" * 64)
    writer.write(b"valid")
    valid = writer.finalize()
    writer.close()
    journal = root / "deletion-journal"
    journal.rmdir()
    journal.write_text("not a directory", encoding="utf-8")
    journal.chmod(0o600)

    assert store.recover_quarantine(lambda _: False, older_than_seconds=0) == ()
    assert store.last_diagnostics
    diagnostic = store.last_diagnostics[0]
    assert diagnostic.target == "journal"
    assert diagnostic.operation == "validate"
    assert diagnostic.retryable is True
    assert str(tmp_path) not in repr(diagnostic)
    with valid.open() as stream:
        assert stream.read() == b"valid"


@pytest.mark.parametrize("case", ["journal_symlink", "wrong_mode", "malformed", "oversize", "traversal"])
def test_deletion_journal_record_direct_safety_is_fail_closed_and_redacted(
    tmp_path: Path,
    case: str,
) -> None:
    root = tmp_path / "quarantine"
    store = LocalQuarantineStore(root)
    writer = store.create("reservation", receipt_id="receipt-1", receipt_hash="a" * 64)
    writer.write(b"valid")
    valid = writer.finalize()
    writer.close()
    target_id, target = _make_deletion_candidate(root, "payload")
    journal = root / "deletion-journal"
    record = journal / "unsafe.json"
    payload: dict[str, object] = {
        "schema": 1,
        "state": "intent",
        "artifact_id": target_id,
        "target": "payload",
        "basename": target.name,
    }
    if case == "journal_symlink":
        outside = tmp_path / "outside-journal.json"
        outside.write_text(json.dumps(payload), encoding="utf-8")
        outside.chmod(0o600)
        record.symlink_to(outside)
    elif case == "wrong_mode":
        record.write_text(json.dumps(payload), encoding="utf-8")
        record.chmod(0o644)
    elif case == "malformed":
        record.write_text("{not-json", encoding="utf-8")
        record.chmod(0o600)
    elif case == "oversize":
        record.write_text(
            json.dumps(payload | {"padding": "x" * 5000}),
            encoding="utf-8",
        )
        record.chmod(0o600)
    else:
        record.write_text(
            json.dumps(payload | {"basename": "../outside"}),
            encoding="utf-8",
        )
        record.chmod(0o600)

    assert store.recover_quarantine(lambda _: False, older_than_seconds=0) == ()
    assert target.exists()
    assert store.last_diagnostics
    assert all(item.target == "journal" for item in store.last_diagnostics)
    assert all(item.retryable is False for item in store.last_diagnostics)
    assert str(tmp_path) not in repr(store.last_diagnostics)
    assert "outside" not in repr(store.last_diagnostics)
    with valid.open() as stream:
        assert stream.read() == b"valid"


def test_janitor_removes_expired_pending_clean_receipt_spool_and_db_row(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'pending-janitor.sqlite3'}"
    engine = create_database_engine(database_url)
    Base.metadata.create_all(engine)
    factory = UnitOfWorkFactory(create_session_factory(engine))
    clock_now = datetime.now(UTC) - timedelta(minutes=5)
    quarantine = LocalQuarantineStore(
        tmp_path / "quarantine",
        spool_ttl_seconds=1,
        clock=lambda: clock_now,
    )
    writer = quarantine.create_untrusted_scan_spool("reservation")
    writer.write(b"payload")
    spool = writer.finalize()
    writer.close()
    attempt = MediaScanAttempt(
        attempt_id="attempt-pending-janitor",
        artifact_sha256=spool.streaming_sha256,
        content_type="application/octet-stream",
        scanner_backend="clamd",
        definitions_version="daily-1",
        definitions_fresh_at=clock_now,
        definitions_age_seconds=0,
        max_bytes=10_000_000,
        max_concurrent_scans=1,
        deadline_ms=5000,
        socket_timeout_ms=2000,
        scan_result=ScanResultKind.CLEAN,
        rejection_code=None,
        rejection_detail=None,
        started_at=clock_now,
        finished_at=clock_now,
        idempotency_key="session:idem:fingerprint",
    )
    pending = quarantine.pending_clean_receipt(
        spool,
        receipt_id="receipt-pending-janitor",
        attempt_id=attempt.attempt_id,
        artifact_sha256=attempt.artifact_sha256,
        receipt_hash="a" * 64,
        created_at=clock_now,
    )
    spool_path = tmp_path / "quarantine" / "untrusted-scan-spool" / pending.spool_token
    with factory() as uow:
        uow.scan_audit.record_attempt(attempt)
        uow.scan_audit.record_pending_clean_receipt(pending)
        uow.commit()

    janitor = MediaJanitor(
        quarantine_store=quarantine,
        object_store=LocalMediaObjectStore(tmp_path / "objects"),
        reference_checker=lambda _: False,
        reservation_active_checker=lambda _: False,
        uow_factory=factory,
    )

    janitor.sweep(older_than_seconds=0)

    assert not spool_path.exists()
    with factory() as uow:
        session = uow._require_session()
        assert session.scalar(select(func.count()).select_from(PendingCleanReceiptModel)) == 0
        uow.commit()
    engine.dispose()
