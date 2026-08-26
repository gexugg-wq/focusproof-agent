from __future__ import annotations

from hashlib import sha256
import errno
from io import BytesIO
import json
import os
from pathlib import Path
import time

import pytest

import focusproof.media_adapters.local_media_object_store as object_module
from focusproof.media_adapters.local_media_object_store import LocalMediaObjectStore
from focusproof.media_adapters.local_quarantine_store import LocalQuarantineStore
from focusproof.media_adapters.pillow_image_codec import recover_orphan_decoder_jobs


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


def _old(path: Path) -> None:
    timestamp = time.time() - 1000
    os.utime(path, (timestamp, timestamp))


def test_decoder_recovery_removes_only_old_private_managed_jobs(tmp_path: Path) -> None:
    root = tmp_path / "decoder"
    root.mkdir(mode=0o700)
    orphan = root / "focusproof-decoder-orphan"
    orphan.mkdir(mode=0o700)
    artifact = orphan / "input"
    artifact.write_bytes(b"payload")
    artifact.chmod(0o600)
    _old(artifact)
    _old(orphan)
    recent = root / "focusproof-decoder-recent"
    recent.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.write_text("keep", encoding="utf-8")
    (root / "focusproof-decoder-symlink").symlink_to(outside)

    assert recover_orphan_decoder_jobs(root, older_than_seconds=100) == (orphan.name,)
    assert not orphan.exists()
    assert recent.exists()
    assert outside.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("failure_call", range(1, 4))
def test_stage_failure_never_leaves_undiscoverable_final_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_call: int
) -> None:
    store = LocalMediaObjectStore(tmp_path / "objects")
    original = object_module._durable_publish
    calls = 0

    def fail_at(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("injected publish failure")
        original(*args, **kwargs)

    monkeypatch.setattr(object_module, "_durable_publish", fail_at)
    with pytest.raises(OSError):
        store.stage(Normalized(), "media-item", "reservation")
    objects = list((tmp_path / "objects" / "staged").glob("[!.]*"))
    manifests = list((tmp_path / "objects" / "manifests").glob("*.json"))
    assert not objects or manifests


def test_manifest_only_crash_record_is_discoverable_and_reclaimable(tmp_path: Path) -> None:
    store = LocalMediaObjectStore(tmp_path / "objects")
    manifest_id = "a" * 32
    manifest = tmp_path / "objects" / "manifests" / f"{manifest_id}.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 2,
                "manifest_id": manifest_id,
                "opaque_object_key": "b" * 32,
                "media_item_id": "media-item",
                "reservation_id": "reservation",
                "phase": "MANIFEST_ONLY",
            }
        ),
        encoding="utf-8",
    )
    _old(manifest)
    assert store.recover_staged(lambda _: False, older_than_seconds=100) == (manifest_id,)
    assert not manifest.exists()


def test_staged_recovery_deletes_object_and_manifest_only_after_db_false(tmp_path: Path) -> None:
    store = LocalMediaObjectStore(tmp_path / "objects")
    staged = store.stage(Normalized(), "media-item", "reservation")
    manifest = tmp_path / "objects" / "manifests" / f"{staged.manifest_id}.json"
    payload = tmp_path / "objects" / "staged" / staged.opaque_object_key
    _old(manifest)
    events: list[str] = []

    def checker(media_item_id: str) -> bool:
        events.append(media_item_id)
        assert manifest.exists() and payload.exists()
        return False

    assert store.recover_staged(checker, older_than_seconds=100) == (staged.manifest_id,)
    assert events == ["media-item"]
    assert not manifest.exists() and not payload.exists()


@pytest.mark.parametrize("answer", [True, None])
def test_staged_recovery_fails_closed_for_referenced_or_unknown(
    tmp_path: Path, answer: bool | None
) -> None:
    store = LocalMediaObjectStore(tmp_path / "objects")
    staged = store.stage(Normalized(), "media-item", "reservation")
    manifest = tmp_path / "objects" / "manifests" / f"{staged.manifest_id}.json"
    payload = tmp_path / "objects" / "staged" / staged.opaque_object_key
    _old(manifest)
    recovered = store.recover_staged(lambda _: answer, older_than_seconds=100)
    if answer is True:
        assert recovered == (staged.manifest_id,)
        assert not manifest.exists() and not payload.exists()
        with store.open(staged.opaque_object_key) as stream:
            assert stream.read() == b"normalized"
    else:
        assert recovered == ()
        assert manifest.exists() and payload.exists()


def test_corrupt_forged_and_object_only_records_are_never_deleted(tmp_path: Path) -> None:
    store = LocalMediaObjectStore(tmp_path / "objects")
    corrupt = tmp_path / "objects" / "manifests" / f"{'a' * 32}.json"
    corrupt.write_text("not-json", encoding="utf-8")
    legacy = tmp_path / "objects" / "staged" / ("b" * 32)
    legacy.write_bytes(b"legacy")
    _old(corrupt)
    _old(legacy)
    assert store.recover_staged(lambda _: False, older_than_seconds=100) == ()
    assert corrupt.exists() and legacy.exists()


def test_raw_external_paths_are_not_part_of_recovery_interface(tmp_path: Path) -> None:
    store = LocalMediaObjectStore(tmp_path / "objects")
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    assert not hasattr(store, "delete_candidate")
    store.recover_staged(lambda _: False, older_than_seconds=0)
    assert outside.exists()


def test_quarantine_recovery_uses_reservation_binding_and_cleans_pair(tmp_path: Path) -> None:
    store = LocalQuarantineStore(tmp_path / "quarantine")
    writer = store.create("reservation")
    writer.write(b"payload")
    item = writer.finalize()
    writer.close()
    records = list((tmp_path / "quarantine" / "records").glob("*.json"))
    payloads = list((tmp_path / "quarantine" / "payloads").iterdir())
    assert len(records) == len(payloads) == 1
    _old(records[0])
    checked: list[str] = []

    def inactive(reservation_id: str) -> bool:
        checked.append(reservation_id)
        return False

    assert store.recover_quarantine(inactive, older_than_seconds=100) == (item.quarantine_id,)
    assert checked == ["reservation"]
    assert not records[0].exists() and not payloads[0].exists()


@pytest.mark.parametrize("answer", [True, None])
def test_quarantine_active_or_unknown_and_referenced_objects_are_never_swept(
    tmp_path: Path, answer: bool | None
) -> None:
    store = LocalQuarantineStore(tmp_path / "quarantine")
    writer = store.create("reservation")
    writer.write(b"payload")
    item = writer.finalize()
    writer.close()
    record = next((tmp_path / "quarantine" / "records").glob("*.json"))
    _old(record)
    assert store.recover_quarantine(lambda _: answer, older_than_seconds=100) == ()
    with item.open() as stream:
        assert stream.read() == b"payload"


def test_mark_referenced_retry_after_manifest_cleanup_is_idempotent(tmp_path: Path) -> None:
    store = LocalMediaObjectStore(tmp_path / "objects")
    staged = store.stage(Normalized(), "media-item", "reservation")
    store.mark_referenced(staged)
    store.mark_referenced(staged)
    with store.open(staged.opaque_object_key) as stream:
        assert stream.read() == b"normalized"


def test_abort_retry_is_idempotent_and_never_deletes_referenced(tmp_path: Path) -> None:
    store = LocalMediaObjectStore(tmp_path / "objects")
    staged = store.stage(Normalized(), "media-item", "reservation")
    store.abort_staged(staged)
    store.abort_staged(staged)
    referenced = store.stage(Normalized(), "referenced", "reservation")
    store.mark_referenced(referenced)
    with pytest.raises(ValueError):
        store.abort_staged(referenced)
    with store.open(referenced.opaque_object_key) as stream:
        assert stream.read() == b"normalized"



def _store_paths(root: Path, key: str, manifest_id: str) -> tuple[Path, Path, Path]:
    base = root / 'objects'
    return (
        base / 'staged' / key,
        base / 'referenced' / key,
        base / 'manifests' / f'{manifest_id}.json',
    )


def _write_recovery_manifest(
    root: Path,
    *,
    key: str,
    manifest_id: str,
    phase: str,
    media_item_id: str = 'media-item',
    reservation_id: str = 'reservation',
) -> Path:
    manifest = root / 'objects' / 'manifests' / f'{manifest_id}.json'
    manifest.write_text(
        json.dumps(
            {
                'schema': 2,
                'manifest_id': manifest_id,
                'opaque_object_key': key,
                'media_item_id': media_item_id,
                'reservation_id': reservation_id,
                'phase': phase,
            }
        ),
        encoding='utf-8',
    )
    _old(manifest)
    return manifest


def _directory_fsync_spy(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    seen: list[Path] = []

    def spy(fd: int) -> None:
        try:
            target = Path(os.readlink(f'/proc/self/fd/{fd}'))
        except OSError:
            return None
        if target.is_dir():
            seen.append(target.resolve())
        return None

    monkeypatch.setattr(object_module.os, 'fsync', spy)
    return seen


def test_mark_referenced_never_overwrites_different_existing_target(tmp_path: Path) -> None:
    store = LocalMediaObjectStore(tmp_path / 'objects')
    staged = store.stage(Normalized(b'source-payload'), 'media-item', 'reservation')
    source, target, manifest = _store_paths(tmp_path, staged.opaque_object_key, staged.manifest_id)
    target.write_bytes(b'existing-referenced')

    with pytest.raises(ValueError, match='conflict'):
        store.mark_referenced(staged)

    assert source.read_bytes() == b'source-payload'
    assert target.read_bytes() == b'existing-referenced'
    assert manifest.exists()


def test_mark_referenced_same_existing_target_is_idempotent_without_replace(
    tmp_path: Path,
) -> None:
    store = LocalMediaObjectStore(tmp_path / 'objects')
    staged = store.stage(Normalized(b'same-payload'), 'media-item', 'reservation')
    source, target, manifest = _store_paths(tmp_path, staged.opaque_object_key, staged.manifest_id)
    target.write_bytes(b'same-payload')
    before = target.stat()

    store.mark_referenced(staged)
    store.mark_referenced(staged)

    assert not source.exists()
    assert not manifest.exists()
    assert target.read_bytes() == b'same-payload'
    assert target.stat().st_ino == before.st_ino


@pytest.mark.parametrize(
    (
        'phase',
        'source_payload',
        'target_payload',
        'db_answer',
        'want_recovered',
        'want_source',
        'want_target',
        'want_manifest',
    ),
    [
        ('MANIFEST_ONLY', None, None, False, True, False, False, False),
        ('MANIFEST_ONLY', b'payload', None, False, True, False, False, False),
        ('MANIFEST_ONLY', b'payload', None, True, False, True, False, True),
        ('MANIFEST_ONLY', b'payload', None, None, False, True, False, True),
        ('MANIFEST_ONLY', b'payload', b'referenced', False, False, True, True, True),
        ('STAGED', b'payload', None, True, True, False, True, False),
        ('STAGED', b'payload', None, False, True, False, False, False),
        ('STAGED', b'payload', None, None, False, True, False, True),
        ('STAGED', None, b'payload', True, True, False, True, False),
        ('STAGED', b'payload', b'referenced', False, False, True, True, True),
    ],
)
def test_recover_staged_manifest_state_matrix_is_db_first(
    tmp_path: Path,
    phase: str,
    source_payload: bytes | None,
    target_payload: bytes | None,
    db_answer: bool | None,
    want_recovered: bool,
    want_source: bool,
    want_target: bool,
    want_manifest: bool,
) -> None:
    store = LocalMediaObjectStore(tmp_path / 'objects')
    key = '1' * 32
    manifest_id = '2' * 32
    source, target, manifest = _store_paths(tmp_path, key, manifest_id)
    _write_recovery_manifest(tmp_path, key=key, manifest_id=manifest_id, phase=phase)
    if source_payload is not None:
        source.write_bytes(source_payload)
    if target_payload is not None:
        target.write_bytes(target_payload)

    recovered = store.recover_staged(lambda _: db_answer, older_than_seconds=100)

    assert recovered == ((manifest_id,) if want_recovered else ())
    assert source.exists() is want_source
    assert target.exists() is want_target
    assert manifest.exists() is want_manifest


def test_recover_staged_preserves_manifest_on_db_exception(tmp_path: Path) -> None:
    store = LocalMediaObjectStore(tmp_path / 'objects')
    key = '3' * 32
    manifest_id = '4' * 32
    source, _, manifest = _store_paths(tmp_path, key, manifest_id)
    _write_recovery_manifest(tmp_path, key=key, manifest_id=manifest_id, phase='MANIFEST_ONLY')
    source.write_bytes(b'payload')

    def broken(_: str) -> bool:
        raise RuntimeError('db unavailable')

    assert store.recover_staged(broken, older_than_seconds=100) == ()
    assert source.exists()
    assert manifest.exists()


def test_cross_directory_mark_fsyncs_source_and_target_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalMediaObjectStore(tmp_path / 'objects')
    staged = store.stage(Normalized(), 'media-item', 'reservation')
    seen = _directory_fsync_spy(monkeypatch)

    store.mark_referenced(staged)

    assert (tmp_path / 'objects' / 'staged').resolve() in seen
    assert (tmp_path / 'objects' / 'referenced').resolve() in seen


def test_object_and_quarantine_deletes_fsync_parent_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    object_store = LocalMediaObjectStore(tmp_path / 'objects')
    staged = object_store.stage(Normalized(), 'media-item', 'reservation')
    object_store.mark_referenced(staged)
    quarantine = LocalQuarantineStore(tmp_path / 'quarantine')
    writer = quarantine.create('reservation')
    writer.write(b'payload')
    item = writer.finalize()
    writer.close()
    seen = _directory_fsync_spy(monkeypatch)

    object_store.delete(staged.opaque_object_key)
    item.delete()

    assert (tmp_path / 'objects' / 'referenced').resolve() in seen
    assert (tmp_path / 'quarantine' / 'payloads').resolve() in seen
    assert (tmp_path / 'quarantine' / 'records').resolve() in seen


def test_quarantine_abort_fsyncs_payload_and_record_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = LocalQuarantineStore(tmp_path / 'quarantine').create('reservation')
    writer.write(b'partial')
    seen = _directory_fsync_spy(monkeypatch)

    writer.abort()

    assert (tmp_path / 'quarantine' / 'payloads').resolve() in seen
    assert (tmp_path / 'quarantine' / 'records').resolve() in seen


def test_mark_referenced_missing_manifest_does_not_mask_source_target_conflict(tmp_path: Path) -> None:
    store = LocalMediaObjectStore(tmp_path / 'objects')
    staged = store.stage(Normalized(b'source-payload'), 'media-item', 'reservation')
    source, target, manifest = _store_paths(tmp_path, staged.opaque_object_key, staged.manifest_id)
    target.write_bytes(b'existing-referenced')
    manifest.unlink()

    with pytest.raises(ValueError, match='conflict'):
        store.mark_referenced(staged)

    assert source.read_bytes() == b'source-payload'
    assert target.read_bytes() == b'existing-referenced'
    assert not manifest.exists()


def _fail_directory_fsync_for(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    failing_directory: Path,
) -> None:
    original_fsync = module.os.fsync
    failing_directory = failing_directory.resolve()

    def fail_on_directory(fd: int) -> None:
        try:
            target = Path(os.readlink(f'/proc/self/fd/{fd}')).resolve()
        except OSError:
            original_fsync(fd)
            return
        if target == failing_directory:
            raise OSError(errno.EIO, 'injected directory fsync failure')
        original_fsync(fd)

    monkeypatch.setattr(module.os, 'fsync', fail_on_directory)


def test_mark_referenced_propagates_target_directory_fsync_failure_before_source_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalMediaObjectStore(tmp_path / 'objects')
    staged = store.stage(Normalized(b'payload'), 'media-item', 'reservation')
    source, target, manifest = _store_paths(tmp_path, staged.opaque_object_key, staged.manifest_id)
    _fail_directory_fsync_for(monkeypatch, object_module, tmp_path / 'objects' / 'referenced')

    with pytest.raises(OSError) as error:
        store.mark_referenced(staged)

    assert error.value.errno == errno.EIO
    assert source.read_bytes() == b'payload'
    assert target.read_bytes() == b'payload'
    assert manifest.exists()


def test_delete_propagates_directory_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalMediaObjectStore(tmp_path / 'objects')
    staged = store.stage(Normalized(), 'media-item', 'reservation')
    store.mark_referenced(staged)
    _fail_directory_fsync_for(monkeypatch, object_module, tmp_path / 'objects' / 'referenced')

    with pytest.raises(OSError) as error:
        store.delete(staged.opaque_object_key)

    assert error.value.errno == errno.EIO


def test_quarantine_delete_propagates_payload_directory_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import focusproof.media_adapters.local_quarantine_store as quarantine_module

    store = LocalQuarantineStore(tmp_path / 'quarantine')
    writer = store.create('reservation')
    writer.write(b'payload')
    item = writer.finalize()
    writer.close()
    _fail_directory_fsync_for(monkeypatch, quarantine_module, tmp_path / 'quarantine' / 'payloads')

    with pytest.raises(OSError) as error:
        item.delete()

    assert error.value.errno == errno.EIO


def test_quarantine_delete_propagates_record_directory_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import focusproof.media_adapters.local_quarantine_store as quarantine_module

    store = LocalQuarantineStore(tmp_path / 'quarantine')
    writer = store.create('reservation')
    writer.write(b'payload')
    item = writer.finalize()
    writer.close()
    _fail_directory_fsync_for(monkeypatch, quarantine_module, tmp_path / 'quarantine' / 'records')

    with pytest.raises(OSError) as error:
        item.delete()

    assert error.value.errno == errno.EIO


def test_recover_staged_durability_error_does_not_report_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalMediaObjectStore(tmp_path / 'objects')
    staged = store.stage(Normalized(), 'media-item', 'reservation')
    source, _, manifest = _store_paths(tmp_path, staged.opaque_object_key, staged.manifest_id)
    _old(manifest)
    _fail_directory_fsync_for(monkeypatch, object_module, tmp_path / 'objects' / 'staged')

    assert store.recover_staged(lambda _: False, older_than_seconds=100) == ()
    assert manifest.exists()
    assert not source.exists()


def test_recover_quarantine_durability_error_does_not_report_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import focusproof.media_adapters.local_quarantine_store as quarantine_module

    store = LocalQuarantineStore(tmp_path / 'quarantine')
    writer = store.create('reservation')
    writer.write(b'payload')
    item = writer.finalize()
    writer.close()
    record = next((tmp_path / 'quarantine' / 'records').glob('*.json'))
    _old(record)
    _fail_directory_fsync_for(monkeypatch, quarantine_module, tmp_path / 'quarantine' / 'payloads')

    assert store.recover_quarantine(lambda _: False, older_than_seconds=100) == ()
    assert record.exists()
    assert not (tmp_path / 'quarantine' / 'payloads' / item.quarantine_id).exists()


def test_directory_fsync_unsupported_errno_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unsupported(fd: int) -> None:
        target = Path(os.readlink(f'/proc/self/fd/{fd}'))
        if target.is_dir():
            raise OSError(errno.EINVAL, 'directory fsync unsupported')

    monkeypatch.setattr(object_module.os, 'fsync', unsupported)
    store = LocalMediaObjectStore(tmp_path / 'objects')
    staged = store.stage(Normalized(), 'media-item', 'reservation')

    store.mark_referenced(staged)

    with store.open(staged.opaque_object_key) as stream:
        assert stream.read() == b'normalized'
