from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import errno
import json
import os
from pathlib import Path
import stat
import time
from typing import Any
from uuid import uuid4

from focusproof.media_core.models import PendingCleanReceipt, formal_artifact_id_from_receipt

_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_SCHEMA = 3
_MAX_JSON_RECORD_BYTES = 4096
_EXCLUSIVE_CREATE_FLAG = getattr(os, "O_" + "EXCL")
_DELETION_TARGETS = frozenset(
    {
        "marker",
        "record",
        "payload",
        "part",
        "payload_part",
        "record_part",
        "commit_part",
        "spool_part",
    }
)


@dataclass(frozen=True, slots=True)
class RollbackFailure:
    artifact_id: str
    target: str
    operation: str
    error_type: str


@dataclass(frozen=True, slots=True)
class RecoveryDiagnostic:
    artifact_id: str
    target: str
    operation: str
    error_type: str
    retryable: bool


class QuarantinePublishError(OSError):
    """Raised when publish failed and rollback left a recoverable orphan."""

    def __init__(self, failures: tuple[RollbackFailure, ...]) -> None:
        super().__init__("quarantine publish failed with recoverable rollback orphan")
        self.rollback_failures = failures


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("quarantine timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _safe_root(root: Path) -> Path:
    if root.exists() and root.is_symlink():
        raise ValueError("storage root may not be a symlink")
    root.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
    root.chmod(_DIRECTORY_MODE)
    resolved = root.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != _DIRECTORY_MODE:
        raise ValueError("quarantine directory permission mismatch")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ValueError("quarantine directory owner mismatch")
    return resolved


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve(strict=False)
    if resolved == root or root not in resolved.parents:
        raise ValueError("quarantine path escapes managed root")
    return resolved


def _regular_owner_file(path: Path, root: Path) -> None:
    _inside(path, root)
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ValueError("quarantine artifact must be a regular file")
    if stat.S_IMODE(info.st_mode) != _FILE_MODE:
        raise ValueError("quarantine artifact permission mismatch")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ValueError("quarantine artifact owner mismatch")


def _regular_owner_directory(path: Path, root: Path) -> None:
    _inside(path, root)
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise ValueError("quarantine directory must be an owned directory")
    if stat.S_IMODE(info.st_mode) != _DIRECTORY_MODE:
        raise ValueError("quarantine directory permission mismatch")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ValueError("quarantine directory owner mismatch")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    primary: BaseException | None = None
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            primary = exc
            if exc.errno in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
                return
            raise
    finally:
        try:
            os.close(descriptor)
        except OSError:
            if primary is None:
                raise


def _durable_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    _fsync_directory(path.parent)


def _artifact_id_from_part(path: Path) -> str:
    name = path.name
    if name.startswith(".") and name.endswith(".part"):
        return name[1:-5]
    return path.stem.lstrip(".")


def _diagnostic(
    diagnostics: list[RecoveryDiagnostic],
    *,
    artifact_id: str,
    target: str,
    operation: str,
    exc: BaseException,
    retryable: bool = True,
) -> None:
    if len(diagnostics) >= 128:
        return
    safe_id = artifact_id if _is_hex32(artifact_id) else "invalid"
    diagnostics.append(
        RecoveryDiagnostic(
            artifact_id=safe_id,
            target=target,
            operation=operation,
            error_type=type(exc).__name__,
            retryable=retryable,
        )
    )


def _safe_recovery_unlink(
    path: Path,
    *,
    root: Path,
    artifact_id: str,
    target: str,
    diagnostics: list[RecoveryDiagnostic],
) -> bool:
    try:
        _regular_owner_file(path, root)
    except FileNotFoundError:
        return True
    except Exception as exc:
        _diagnostic(
            diagnostics,
            artifact_id=artifact_id,
            target=target,
            operation="validate",
            exc=exc,
        )
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError as exc:
        _diagnostic(
            diagnostics,
            artifact_id=artifact_id,
            target=target,
            operation="unlink",
            exc=exc,
        )
        return False
    try:
        _fsync_directory(path.parent)
    except OSError as exc:
        _diagnostic(
            diagnostics,
            artifact_id=artifact_id,
            target=target,
            operation="fsync",
            exc=exc,
        )
        return False
    return True


def _rollback_unlink(*paths: Path) -> tuple[RollbackFailure, ...]:
    failures: list[RollbackFailure] = []
    for path in paths:
        artifact_id = path.stem.lstrip(".")
        target = path.parent.name
        removed = False
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
                removed = True
        except OSError as exc:
            failures.append(
                RollbackFailure(
                    artifact_id=artifact_id,
                    target=target,
                    operation="unlink",
                    error_type=type(exc).__name__,
                )
            )
            continue
        if removed:
            try:
                _fsync_directory(path.parent)
            except OSError as exc:
                failures.append(
                    RollbackFailure(
                        artifact_id=artifact_id,
                        target=target,
                        operation="directory_fsync",
                        error_type=type(exc).__name__,
                    )
                )
    return tuple(failures)


def _fsync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _publish_file(temporary: Path, target: Path, root: Path) -> None:
    os.chmod(temporary, _FILE_MODE)
    _fsync_file(temporary)
    os.replace(temporary, target)
    os.chmod(target, _FILE_MODE)
    _regular_owner_file(target, root)
    _fsync_directory(target.parent)


def _write_json_part(path: Path, payload: dict[str, object]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | _EXCLUSIVE_CREATE_FLAG, _FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(payload, output, sort_keys=True, separators=(",", ":"))
        output.flush()
        os.fsync(output.fileno())


def _is_hex32(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def _safe_hash(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _formal_item_id(receipt_id: str, receipt_hash: str) -> str:
    return formal_artifact_id_from_receipt(receipt_id, receipt_hash)


def _read_json_file(path: Path, root: Path) -> dict[str, Any]:
    try:
        _regular_owner_file(path, root)
        if path.stat().st_size > _MAX_JSON_RECORD_BYTES:
            raise ValueError("oversized quarantine record")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("missing quarantine commit marker") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid quarantine record")
    return payload


def _validate_record_payload(
    *,
    item_id: str,
    payload: dict[str, Any],
    payloads: Path,
    records: Path,
    commits: Path,
    root: Path,
    require_commit: bool,
) -> dict[str, Any]:
    expected = {
        "schema",
        "quarantine_id",
        "reservation_id",
        "receipt_id",
        "receipt_hash",
        "created_at",
        "quarantine_expires_at",
        "payload_name",
        "byte_size",
        "sha256",
        "mode",
    }
    if set(payload) != expected or payload.get("schema") != _SCHEMA:
        raise ValueError("invalid quarantine record")
    if payload["quarantine_id"] != item_id or payload["payload_name"] != item_id:
        raise ValueError("invalid quarantine identity")
    if not _is_hex32(item_id):
        raise ValueError("invalid quarantine identity")
    for field in ("reservation_id", "receipt_id"):
        if not isinstance(payload[field], str) or not payload[field]:
            raise ValueError("invalid quarantine record identity")
    if not isinstance(payload["receipt_hash"], str) or not _safe_hash(payload["receipt_hash"]):
        raise ValueError("invalid receipt hash")
    if payload["mode"] != "0600":
        raise ValueError("invalid quarantine mode")
    created_at = _utc(datetime.fromisoformat(str(payload["created_at"])))
    expires_at = _utc(datetime.fromisoformat(str(payload["quarantine_expires_at"])))
    if expires_at <= created_at:
        raise ValueError("invalid quarantine expiry")
    if not isinstance(payload["byte_size"], int) or payload["byte_size"] < 0:
        raise ValueError("invalid quarantine byte size")
    if not isinstance(payload["sha256"], str) or not _safe_hash(payload["sha256"]):
        raise ValueError("invalid quarantine digest")
    record_path = records / f"{item_id}.json"
    payload_path = payloads / item_id
    commit_path = commits / f"{item_id}.commit"
    _regular_owner_file(record_path, root)
    _regular_owner_file(payload_path, root)
    if require_commit:
        marker = _read_json_file(commit_path, root)
        if marker != {
            "schema": 1,
            "quarantine_id": item_id,
            "receipt_hash": payload["receipt_hash"],
        }:
            raise ValueError("invalid quarantine commit marker")
    digest = sha256(payload_path.read_bytes()).hexdigest()
    if payload_path.stat().st_size != payload["byte_size"] or digest != payload["sha256"]:
        raise ValueError("quarantine payload metadata mismatch")
    return payload


class _QuarantineObject:
    def __init__(
        self,
        *,
        path: Path,
        record: Path,
        commit: Path,
        payloads: Path,
        records: Path,
        commits: Path,
        root: Path,
        item_id: str,
        size: int,
        digest: str,
        expires_at: datetime,
        receipt_id: str,
        receipt_hash: str,
    ) -> None:
        self._path = path
        self._record = record
        self._commit = commit
        self._payloads = payloads
        self._records = records
        self._commits = commits
        self._root = root
        self.quarantine_id = item_id
        self.receipt_id = receipt_id
        self.receipt_hash = receipt_hash
        self.byte_size = size
        self.streaming_sha256 = digest
        self.quarantine_expires_at = expires_at

    def is_expired(self, now: datetime | None = None) -> bool:
        return _utc(now or datetime.now(UTC)) >= self.quarantine_expires_at

    @contextmanager
    def open(self) -> Iterator[Any]:
        if self.is_expired():
            raise ValueError("quarantine artifact expired")
        record_payload = _read_json_file(self._record, self._root)
        _validate_record_payload(
            item_id=self.quarantine_id,
            payload=record_payload,
            payloads=self._payloads,
            records=self._records,
            commits=self._commits,
            root=self._root,
            require_commit=True,
        )
        descriptor = os.open(self._path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            with os.fdopen(descriptor, "rb") as stream:
                yield stream
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def delete(self) -> None:
        _durable_unlink(self._commit)
        _durable_unlink(self._path)
        _durable_unlink(self._record)

    def close(self) -> None:
        return None


class _SpoolObject:
    receipt_id = ""
    receipt_hash = ""

    def __init__(self, path: Path, root: Path, item_id: str, size: int, digest: str,
                 expires_at: datetime) -> None:
        self._path = path
        self._root = root
        self.quarantine_id = item_id
        self.byte_size = size
        self.streaming_sha256 = digest
        self.quarantine_expires_at = expires_at
        self._consumed = False

    def is_expired(self, now: datetime | None = None) -> bool:
        return _utc(now or datetime.now(UTC)) >= self.quarantine_expires_at

    @contextmanager
    def open(self) -> Iterator[Any]:
        if self.is_expired():
            raise ValueError("scan spool expired")
        _regular_owner_file(self._path, self._root)
        descriptor = os.open(self._path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            with os.fdopen(descriptor, "rb") as stream:
                yield stream
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def delete(self) -> None:
        if not self._consumed:
            _durable_unlink(self._path)

    def close(self) -> None:
        return None


class _Writer:
    def __init__(
        self,
        directory: Path,
        root: Path,
        reservation_id: str,
        created_at: datetime,
        expires_at: datetime,
        *,
        formal: bool,
        receipt_id: str,
        receipt_hash: str,
        payloads: Path | None = None,
        records: Path | None = None,
        commits: Path | None = None,
    ) -> None:
        self._id = uuid4().hex
        self._reservation_id = reservation_id
        self._directory = directory
        self._root = root
        self._created_at = created_at
        self._expires_at = expires_at
        self._formal = formal
        self._receipt_id = receipt_id
        self._receipt_hash = receipt_hash
        self._payloads = payloads
        self._records = records
        self._commits = commits
        self._temporary = directory / f".{self._id}.part"
        self._final = directory / self._id
        descriptor = os.open(
            self._temporary,
            os.O_WRONLY | os.O_CREAT | _EXCLUSIVE_CREATE_FLAG,
            _FILE_MODE,
        )
        self._stream = os.fdopen(descriptor, "wb")
        self._digest = sha256()
        self._size = 0
        self._finalized = False

    def write(self, chunk: bytes) -> None:
        if self._finalized or self._stream.closed:
            raise ValueError("quarantine writer is closed")
        self._stream.write(chunk)
        self._digest.update(chunk)
        self._size += len(chunk)

    def finalize(self) -> _QuarantineObject | _SpoolObject:
        if self._finalized or self._stream.closed:
            raise ValueError("quarantine writer already finalized")
        try:
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._stream.close()
            result: _QuarantineObject | _SpoolObject
            if self._formal:
                if self._payloads is None or self._records is None or self._commits is None:
                    raise ValueError("formal quarantine directories missing")
                result = _publish_formal(
                    payload_tmp=self._temporary,
                    payloads=self._payloads,
                    records=self._records,
                    commits=self._commits,
                    root=self._root,
                    item_id=self._id,
                    reservation_id=self._reservation_id,
                    receipt_id=self._receipt_id,
                    receipt_hash=self._receipt_hash,
                    created_at=self._created_at,
                    expires_at=self._expires_at,
                    size=self._size,
                    digest=self._digest.hexdigest(),
                )
            else:
                _publish_file(self._temporary, self._final, self._root)
                result = _SpoolObject(
                    self._final, self._root, self._id, self._size,
                    self._digest.hexdigest(), self._expires_at
                )
        except Exception:
            if not self._stream.closed:
                self._stream.close()
            _rollback_unlink(self._temporary, self._final)
            raise
        self._finalized = True
        return result

    def abort(self) -> None:
        if not self._stream.closed:
            self._stream.close()
        _rollback_unlink(self._temporary, self._final)
        directories = [self._directory]
        if self._formal:
            directories.extend(
                directory for directory in (self._records, self._commits)
                if directory is not None
            )
        for directory in directories:
            try:
                _fsync_directory(directory)
            except OSError:
                pass

    def close(self) -> None:
        if not self._stream.closed:
            self._stream.close()


def _publish_formal(
    *,
    payload_tmp: Path,
    payloads: Path,
    records: Path,
    commits: Path,
    root: Path,
    item_id: str,
    reservation_id: str,
    receipt_id: str,
    receipt_hash: str,
    created_at: datetime,
    expires_at: datetime,
    size: int,
    digest: str,
) -> _QuarantineObject:
    payload_final = payloads / item_id
    record_tmp = records / f".{item_id}.part"
    record_final = records / f"{item_id}.json"
    commit_tmp = commits / f".{item_id}.part"
    commit_final = commits / f"{item_id}.commit"
    existing = _load_existing_formal(
        payload_final=payload_final,
        record_final=record_final,
        commit_final=commit_final,
        payloads=payloads,
        records=records,
        commits=commits,
        root=root,
        item_id=item_id,
        reservation_id=reservation_id,
        receipt_id=receipt_id,
        receipt_hash=receipt_hash,
        expires_at=expires_at,
        size=size,
        digest=digest,
    )
    if existing is not None:
        _rollback_unlink(payload_tmp, record_tmp, commit_tmp)
        return existing
    record = {
        "schema": _SCHEMA,
        "quarantine_id": item_id,
        "reservation_id": reservation_id,
        "receipt_id": receipt_id,
        "receipt_hash": receipt_hash,
        "created_at": created_at.isoformat(),
        "quarantine_expires_at": expires_at.isoformat(),
        "payload_name": item_id,
        "byte_size": size,
        "sha256": digest,
        "mode": "0600",
    }
    marker = {"schema": 1, "quarantine_id": item_id, "receipt_hash": receipt_hash}
    try:
        _publish_file(payload_tmp, payload_final, root)
        _write_json_part(record_tmp, record)
        _publish_file(record_tmp, record_final, root)
        _write_json_part(commit_tmp, marker)
        _publish_file(commit_tmp, commit_final, root)
        _validate_record_payload(
            item_id=item_id,
            payload=record,
            payloads=payloads,
            records=records,
            commits=commits,
            root=root,
            require_commit=True,
        )
    except Exception:
        failures = _rollback_unlink(
            commit_tmp,
            commit_final,
            record_tmp,
            record_final,
            payload_tmp,
            payload_final,
        )
        if failures:
            raise QuarantinePublishError(failures) from None
        raise
    return _QuarantineObject(
        path=payload_final,
        record=record_final,
        commit=commit_final,
        payloads=payloads,
        records=records,
        commits=commits,
        root=root,
        item_id=item_id,
        size=size,
        digest=digest,
        expires_at=expires_at,
        receipt_id=receipt_id,
        receipt_hash=receipt_hash,
    )


def _load_existing_formal(
    *,
    payload_final: Path,
    record_final: Path,
    commit_final: Path,
    payloads: Path,
    records: Path,
    commits: Path,
    root: Path,
    item_id: str,
    reservation_id: str,
    receipt_id: str,
    receipt_hash: str,
    expires_at: datetime,
    size: int,
    digest: str,
) -> _QuarantineObject | None:
    existing_paths = (payload_final, record_final, commit_final)
    if not any(path.exists() or path.is_symlink() for path in existing_paths):
        return None
    try:
        payload = _validate_record_payload(
            item_id=item_id,
            payload=_read_json_file(record_final, root),
            payloads=payloads,
            records=records,
            commits=commits,
            root=root,
            require_commit=True,
        )
    except Exception as exc:
        raise ValueError("conflicting formal quarantine artifact") from exc
    stored_expires_at = _utc(datetime.fromisoformat(str(payload["quarantine_expires_at"])))
    if (
        payload["reservation_id"] != reservation_id
        or payload["receipt_id"] != receipt_id
        or payload["receipt_hash"] != receipt_hash
        or payload["sha256"] != digest
        or payload["byte_size"] != size
        or stored_expires_at != expires_at
    ):
        raise ValueError("conflicting formal quarantine artifact")
    return _QuarantineObject(
        path=payload_final,
        record=record_final,
        commit=commit_final,
        payloads=payloads,
        records=records,
        commits=commits,
        root=root,
        item_id=item_id,
        size=size,
        digest=digest,
        expires_at=stored_expires_at,
        receipt_id=receipt_id,
        receipt_hash=receipt_hash,
    )


class LocalQuarantineStore:
    def __init__(self, root: Path, *, ttl_seconds: float = 3600,
                 spool_ttl_seconds: float = 60,
                 clock: Callable[[], datetime] | None = None) -> None:
        if ttl_seconds <= 0 or spool_ttl_seconds <= 0:
            raise ValueError("quarantine TTL must be positive")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl_seconds = ttl_seconds
        self._spool_ttl_seconds = spool_ttl_seconds
        self._root = _safe_root(root)
        self._payloads = _safe_root(self._root / "payloads")
        self._records = _safe_root(self._root / "records")
        self._commits = _safe_root(self._root / "commits")
        self._spool = _safe_root(self._root / "untrusted-scan-spool")
        self._deletion_journal = _safe_root(self._root / "deletion-journal")
        self.last_diagnostics: tuple[RecoveryDiagnostic, ...] = ()

    def create(
        self,
        reservation_id: str,
        *,
        quarantine_expires_at: datetime | None = None,
        receipt_id: str = "test-clean-receipt",
        receipt_hash: str = "0" * 64,
    ) -> _Writer:
        return self._create_formal(
            reservation_id,
            quarantine_expires_at=quarantine_expires_at,
            receipt_id=receipt_id,
            receipt_hash=receipt_hash,
        )

    def create_untrusted_scan_spool(self, reservation_id: str) -> _Writer:
        if not reservation_id or len(reservation_id) > 256:
            raise ValueError("invalid reservation identity")
        created_at = _utc(self._clock())
        expires_at = created_at + timedelta(seconds=self._spool_ttl_seconds)
        return _Writer(
            self._spool, self._root, reservation_id, created_at, expires_at,
            formal=False, receipt_id="", receipt_hash=""
        )

    def promote_clean_spool(
        self,
        spool: object,
        *,
        receipt_id: str,
        receipt_hash: str,
        formal_artifact_id: str,
        quarantine_expires_at: datetime | None = None,
    ) -> _QuarantineObject:
        if not isinstance(spool, _SpoolObject):
            raise ValueError("clean promotion requires a managed scan spool")
        if not receipt_id or not _safe_hash(receipt_hash):
            raise ValueError("clean promotion requires receipt binding")
        if formal_artifact_id != _formal_item_id(receipt_id, receipt_hash):
            raise ValueError("formal artifact id does not match receipt identity")
        promoted = self.find_promoted_clean_receipt(
            receipt_id=receipt_id,
            receipt_hash=receipt_hash,
            artifact_sha256=spool.streaming_sha256,
        )
        if promoted is not None:
            spool._consumed = True
            return promoted
        _regular_owner_file(spool._path, self._root)
        if spool.is_expired(self._clock()):
            raise ValueError("scan spool expired")
        created_at = _utc(self._clock())
        expires_at = _utc(
            quarantine_expires_at or (created_at + timedelta(seconds=self._ttl_seconds))
        )
        if expires_at <= created_at:
            raise ValueError("quarantine expiry must follow creation")
        item_id = formal_artifact_id
        payload_tmp = self._payloads / f".{item_id}.part"
        try:
            if payload_tmp.exists():
                _regular_owner_file(payload_tmp, self._root)
                _durable_unlink(payload_tmp)
            os.link(spool._path, payload_tmp)
            os.chmod(payload_tmp, _FILE_MODE)
            result = _publish_formal(
                payload_tmp=payload_tmp,
                payloads=self._payloads,
                records=self._records,
                commits=self._commits,
                root=self._root,
                item_id=item_id,
                reservation_id="promoted:" + spool.quarantine_id,
                receipt_id=receipt_id,
                receipt_hash=receipt_hash,
                created_at=created_at,
                expires_at=expires_at,
                size=spool.byte_size,
                digest=spool.streaming_sha256,
            )
            spool.delete()
        except Exception:
            failures = _rollback_unlink(payload_tmp)
            if failures:
                raise QuarantinePublishError(failures) from None
            raise
        spool._consumed = True
        return result

    def pending_clean_receipt(
        self,
        spool: object,
        *,
        receipt_id: str,
        attempt_id: str,
        artifact_sha256: str,
        receipt_hash: str,
        created_at: datetime,
    ) -> PendingCleanReceipt:
        if not isinstance(spool, _SpoolObject):
            raise ValueError("pending receipt requires a managed scan spool")
        if spool.streaming_sha256 != artifact_sha256:
            raise ValueError("pending receipt spool digest mismatch")
        created = _utc(created_at)
        quarantine_expires_at = _utc(self._clock()) + timedelta(seconds=self._ttl_seconds)
        return PendingCleanReceipt(
            receipt_id=receipt_id,
            attempt_id=attempt_id,
            artifact_sha256=artifact_sha256,
            receipt_hash=receipt_hash,
            spool_token=spool.quarantine_id,
            spool_byte_size=spool.byte_size,
            spool_sha256=spool.streaming_sha256,
            spool_expires_at=spool.quarantine_expires_at,
            quarantine_expires_at=quarantine_expires_at,
            created_at=created,
        )

    def open_pending_spool(self, pending: PendingCleanReceipt) -> _SpoolObject:
        token = pending.spool_token
        if not _is_hex32(token):
            raise ValueError("invalid scan spool token")
        path = self._spool / token
        _regular_owner_file(path, self._root)
        if _utc(self._clock()) >= _utc(pending.spool_expires_at):
            raise ValueError("scan spool expired")
        size = path.stat().st_size
        digest = sha256(path.read_bytes()).hexdigest()
        if (
            size != pending.spool_byte_size
            or digest != pending.spool_sha256
            or digest != pending.artifact_sha256
        ):
            raise ValueError("scan spool metadata mismatch")
        return _SpoolObject(
            path,
            self._root,
            token,
            pending.spool_byte_size,
            pending.spool_sha256,
            _utc(pending.spool_expires_at),
        )

    def open_formal_clean_receipt(
        self,
        pending: PendingCleanReceipt,
    ) -> _QuarantineObject | None:
        expected_id = _formal_item_id(pending.receipt_id, pending.receipt_hash)
        if (
            pending.formal_artifact_id != expected_id
            or pending.spool_sha256 != pending.artifact_sha256
        ):
            raise ValueError("pending clean receipt metadata mismatch")
        item_id = pending.formal_artifact_id
        existing = _load_existing_formal(
            payload_final=self._payloads / item_id,
            record_final=self._records / f"{item_id}.json",
            commit_final=self._commits / f"{item_id}.commit",
            payloads=self._payloads,
            records=self._records,
            commits=self._commits,
            root=self._root,
            item_id=item_id,
            reservation_id="promoted:" + pending.spool_token,
            receipt_id=pending.receipt_id,
            receipt_hash=pending.receipt_hash,
            expires_at=_utc(pending.quarantine_expires_at),
            size=pending.spool_byte_size,
            digest=pending.artifact_sha256,
        )
        if existing is not None and _utc(self._clock()) >= existing.quarantine_expires_at:
            raise ValueError("quarantine artifact expired")
        return existing

    def find_promoted_clean_receipt(
        self,
        *,
        receipt_id: str,
        receipt_hash: str,
        artifact_sha256: str,
    ) -> _QuarantineObject | None:
        for record_path in self._records.glob("*.json"):
            try:
                item_id, payload = self._load_record(record_path, require_commit=True)
                if (
                    payload["receipt_id"] != receipt_id
                    or payload["receipt_hash"] != receipt_hash
                    or payload["sha256"] != artifact_sha256
                ):
                    continue
                expires_at = _utc(datetime.fromisoformat(str(payload["quarantine_expires_at"])))
                if _utc(self._clock()) >= expires_at:
                    raise ValueError("quarantine artifact expired")
                return _QuarantineObject(
                    path=self._payloads / item_id,
                    record=record_path,
                    commit=self._commits / f"{item_id}.commit",
                    payloads=self._payloads,
                    records=self._records,
                    commits=self._commits,
                    root=self._root,
                    item_id=item_id,
                    size=int(payload["byte_size"]),
                    digest=str(payload["sha256"]),
                    expires_at=expires_at,
                    receipt_id=str(payload["receipt_id"]),
                    receipt_hash=str(payload["receipt_hash"]),
                )
            except Exception:
                continue
        return None

    def discard_pending_clean_receipt(self, pending: PendingCleanReceipt) -> bool:
        diagnostics: list[RecoveryDiagnostic] = []
        complete = True
        token = pending.spool_token
        if _is_hex32(token):
            complete = _safe_recovery_unlink(
                self._spool / token,
                root=self._root,
                artifact_id=token,
                target="part",
                diagnostics=diagnostics,
            ) and complete
        else:
            _diagnostic(
                diagnostics,
                artifact_id=token,
                target="part",
                operation="validate",
                exc=ValueError(),
            )
            complete = False
        item_id = _formal_item_id(pending.receipt_id, pending.receipt_hash)
        for path, target in (
            (self._commits / f"{item_id}.commit", "marker"),
            (self._records / f"{item_id}.json", "record"),
            (self._payloads / item_id, "payload"),
        ):
            complete = _safe_recovery_unlink(
                path,
                root=self._root,
                artifact_id=item_id,
                target=target,
                diagnostics=diagnostics,
            ) and complete
        self.last_diagnostics = tuple(diagnostics)
        return complete

    def _create_formal(
        self,
        reservation_id: str,
        *,
        quarantine_expires_at: datetime | None,
        receipt_id: str,
        receipt_hash: str,
    ) -> _Writer:
        if not reservation_id or len(reservation_id) > 256:
            raise ValueError("invalid reservation identity")
        if not receipt_id or not _safe_hash(receipt_hash):
            raise ValueError("formal quarantine requires receipt binding")
        created_at = _utc(self._clock())
        expires_at = _utc(quarantine_expires_at or
                          (created_at + timedelta(seconds=self._ttl_seconds)))
        if expires_at <= created_at:
            raise ValueError("quarantine expiry must follow creation")
        return _Writer(
            self._payloads, self._root, reservation_id, created_at, expires_at,
            formal=True, receipt_id=receipt_id, receipt_hash=receipt_hash,
            payloads=self._payloads, records=self._records, commits=self._commits,
        )

    def _load_record(self, record_path: Path, *, require_commit: bool) -> tuple[str, dict[str, Any]]:
        item_id = record_path.stem
        payload = _read_json_file(record_path, self._root)
        if int(payload.get("schema", 0)) == 2:
            raise ValueError("legacy quarantine record is not active")
        return item_id, _validate_record_payload(
            item_id=item_id,
            payload=payload,
            payloads=self._payloads,
            records=self._records,
            commits=self._commits,
            root=self._root,
            require_commit=require_commit,
        )

    def remove_expired(self, *, now: datetime | None = None) -> tuple[str, ...]:
        current = _utc(now or self._clock())
        removed: list[str] = []
        diagnostics: list[RecoveryDiagnostic] = []
        for record_path in self._records.glob("*.json"):
            try:
                item_id, payload = self._load_record(record_path, require_commit=True)
                if current < _utc(datetime.fromisoformat(payload["quarantine_expires_at"])):
                    continue
                if self._delete_triplet(item_id, diagnostics=diagnostics):
                    removed.append(item_id)
            except Exception as exc:
                _diagnostic(
                    diagnostics,
                    artifact_id=record_path.stem,
                    target="record",
                    operation="validate",
                    exc=exc,
                )
        self.last_diagnostics = tuple(diagnostics)
        return tuple(removed)

    def recover_quarantine(
        self,
        reservation_active_checker: Callable[[str], bool | None],
        *,
        older_than_seconds: float,
    ) -> tuple[str, ...]:
        cutoff = time.time() - older_than_seconds
        recovered: list[str] = []
        diagnostics: list[RecoveryDiagnostic] = []
        recovered.extend(self._recover_deletion_journals(diagnostics))
        if diagnostics:
            self.last_diagnostics = tuple(diagnostics)
            return ()
        part_directories = (
            (self._payloads, "payload_part"),
            (self._records, "record_part"),
            (self._commits, "commit_part"),
            (self._spool, "spool_part"),
        )
        for directory, target in part_directories:
            recovered.extend(self._recover_old_parts(directory, cutoff, diagnostics, target))
        if diagnostics:
            self.last_diagnostics = tuple(diagnostics)
            return ()
        for commit_path in self._commits.glob("*.commit"):
            try:
                if commit_path.lstat().st_mtime >= cutoff:
                    continue
                item_id = commit_path.stem
                if not _is_hex32(item_id):
                    if _safe_recovery_unlink(
                        commit_path,
                        root=self._root,
                        artifact_id=item_id,
                        target="marker",
                        diagnostics=diagnostics,
                    ):
                        recovered.append("invalid")
                    continue
                try:
                    self._load_record(self._records / f"{item_id}.json", require_commit=True)
                except Exception:
                    if self._delete_triplet(item_id, diagnostics=diagnostics):
                        recovered.append(item_id)
            except Exception as exc:
                _diagnostic(
                    diagnostics,
                    artifact_id=commit_path.stem,
                    target="marker",
                    operation="validate",
                    exc=exc,
                )
                continue
        if diagnostics:
            self.last_diagnostics = tuple(diagnostics)
            return ()
        for record_path in self._records.glob("*.json"):
            try:
                if record_path.lstat().st_mtime >= cutoff:
                    continue
                item_id, payload = self._load_record(record_path, require_commit=False)
                if reservation_active_checker(str(payload["reservation_id"])) is not False:
                    continue
                if self._delete_triplet(item_id, diagnostics=diagnostics):
                    recovered.append(item_id)
            except Exception:
                item_id = record_path.stem
                if _is_hex32(item_id) and self._recovery_unlink(
                    record_path,
                    artifact_id=item_id,
                    target="record",
                    diagnostics=diagnostics,
                ):
                    recovered.append(item_id)
                continue
        if diagnostics:
            self.last_diagnostics = tuple(diagnostics)
            return ()
        for payload_path in self._payloads.iterdir():
            try:
                if payload_path.name.startswith(".") or payload_path.lstat().st_mtime >= cutoff:
                    continue
                item_id = payload_path.name
                if not _is_hex32(item_id):
                    continue
                if (self._records / f"{item_id}.json").exists():
                    continue
                if (self._commits / f"{item_id}.commit").exists():
                    continue
                if self._recovery_unlink(
                    payload_path,
                    artifact_id=item_id,
                    target="payload",
                    diagnostics=diagnostics,
                ):
                    recovered.append(item_id)
            except Exception as exc:
                _diagnostic(
                    diagnostics,
                    artifact_id=payload_path.name,
                    target="payload",
                    operation="validate",
                    exc=exc,
                )
        self.last_diagnostics = tuple(diagnostics)
        return tuple(dict.fromkeys(recovered))

    def _recover_old_parts(
        self,
        directory: Path,
        cutoff: float,
        diagnostics: list[RecoveryDiagnostic],
        target: str,
    ) -> list[str]:
        recovered: list[str] = []
        for candidate in directory.glob("*.part"):
            try:
                if candidate.is_symlink() or candidate.lstat().st_mtime >= cutoff:
                    continue
                item_id = _artifact_id_from_part(candidate)
                if self._recovery_unlink(
                    candidate,
                    artifact_id=item_id,
                    target=target,
                    diagnostics=diagnostics,
                ):
                    recovered.append(item_id)
            except Exception as exc:
                _diagnostic(
                    diagnostics,
                    artifact_id=_artifact_id_from_part(candidate),
                    target=target,
                    operation="validate",
                    exc=exc,
                )
                continue
        return recovered

    def _delete_triplet(
        self,
        item_id: str,
        *,
        diagnostics: list[RecoveryDiagnostic],
    ) -> bool:
        if not self._recovery_unlink(
            self._commits / f"{item_id}.commit",
            artifact_id=item_id,
            target="marker",
            diagnostics=diagnostics,
        ):
            return False
        if not self._recovery_unlink(
            self._payloads / item_id,
            artifact_id=item_id,
            target="payload",
            diagnostics=diagnostics,
        ):
            return False
        return self._recovery_unlink(
            self._records / f"{item_id}.json",
            artifact_id=item_id,
            target="record",
            diagnostics=diagnostics,
        )

    def _journal_path(self, artifact_id: str, target: str) -> Path:
        name = sha256(f"{artifact_id}:{target}".encode("ascii")).hexdigest()[:32]
        return self._deletion_journal / f"{name}.json"

    def _target_path(self, artifact_id: str, target: str, basename: str) -> Path:
        expected: dict[str, tuple[Path, str]] = {
            "marker": (self._commits, f"{artifact_id}.commit"),
            "record": (self._records, f"{artifact_id}.json"),
            "payload": (self._payloads, artifact_id),
            "part": (self._payloads, f".{artifact_id}.part"),
            "payload_part": (self._payloads, f".{artifact_id}.part"),
            "record_part": (self._records, f".{artifact_id}.part"),
            "commit_part": (self._commits, f".{artifact_id}.part"),
            "spool_part": (self._spool, f".{artifact_id}.part"),
        }
        directory, expected_name = expected[target]
        if basename != expected_name:
            raise ValueError("invalid deletion journal basename")
        return directory / basename

    def _write_journal(self, path: Path, payload: dict[str, object]) -> None:
        _regular_owner_directory(self._deletion_journal, self._root)
        temporary = path.with_name(f".{path.stem}.part")
        if temporary.exists():
            _regular_owner_file(temporary, self._root)
            _durable_unlink(temporary)
        _write_json_part(temporary, payload)
        os.replace(temporary, path)
        os.chmod(path, _FILE_MODE)
        _fsync_directory(self._deletion_journal)

    def _recovery_unlink(
        self,
        path: Path,
        *,
        artifact_id: str,
        target: str,
        diagnostics: list[RecoveryDiagnostic],
    ) -> bool:
        if not _is_hex32(artifact_id) or target not in _DELETION_TARGETS:
            _diagnostic(
                diagnostics, artifact_id=artifact_id, target=target,
                operation="validate", exc=ValueError(), retryable=False,
            )
            return False
        journal = self._journal_path(artifact_id, target)
        try:
            _regular_owner_file(path, self._root)
        except FileNotFoundError:
            return True
        except Exception as exc:
            _diagnostic(
                diagnostics, artifact_id=artifact_id, target=target,
                operation="validate", exc=exc,
            )
            return False
        try:
            expected_path = self._target_path(artifact_id, target, path.name)
            if path.resolve(strict=False) != expected_path.resolve(strict=False):
                raise ValueError("deletion target path mismatch")
        except Exception as exc:
            _diagnostic(
                diagnostics, artifact_id=artifact_id, target=target,
                operation="validate", exc=exc, retryable=False,
            )
            return False
        payload: dict[str, object] = {
            "schema": 1,
            "state": "intent",
            "artifact_id": artifact_id,
            "target": target,
            "basename": path.name,
        }
        try:
            if not journal.exists():
                self._write_journal(journal, payload)
            return self._finish_deletion_journal(journal, payload, diagnostics)
        except Exception as exc:
            _diagnostic(
                diagnostics, artifact_id=artifact_id, target=target,
                operation="journal", exc=exc,
            )
            return False

    def _finish_deletion_journal(
        self,
        journal: Path,
        payload: dict[str, object],
        diagnostics: list[RecoveryDiagnostic],
    ) -> bool:
        artifact_id = str(payload["artifact_id"])
        target = str(payload["target"])
        try:
            target_path = self._target_path(artifact_id, target, str(payload["basename"]))
            try:
                _regular_owner_file(target_path, self._root)
            except FileNotFoundError:
                pass
            else:
                try:
                    target_path.unlink()
                except OSError as exc:
                    _diagnostic(
                        diagnostics, artifact_id=artifact_id, target=target,
                        operation="unlink", exc=exc,
                    )
                    return False
            _fsync_directory(target_path.parent)
            if target_path.exists() or target_path.is_symlink():
                raise OSError(errno.EIO, "deletion not confirmed")
            done = dict(payload)
            done["state"] = "done"
            self._write_journal(journal, done)
            return self._cleanup_deletion_journal(
                journal, done, diagnostics
            )
        except Exception as exc:
            _diagnostic(
                diagnostics, artifact_id=artifact_id, target=target,
                operation="fsync" if isinstance(exc, OSError) else "validate", exc=exc,
            )
            return False

    def _cleanup_deletion_journal(
        self,
        journal: Path,
        payload: dict[str, object],
        diagnostics: list[RecoveryDiagnostic],
    ) -> bool:
        artifact_id = str(payload["artifact_id"])
        target = str(payload["target"])
        try:
            _durable_unlink(journal)
            return True
        except OSError as exc:
            if not journal.exists():
                try:
                    self._write_journal(journal, payload)
                except OSError:
                    pass
            _diagnostic(
                diagnostics, artifact_id=artifact_id, target=target,
                operation="journal_cleanup", exc=exc,
            )
            return False

    def _recover_deletion_journals(
        self, diagnostics: list[RecoveryDiagnostic]
    ) -> list[str]:
        recovered: list[str] = []
        try:
            _regular_owner_directory(self._deletion_journal, self._root)
        except Exception as exc:
            _diagnostic(
                diagnostics, artifact_id="invalid", target="journal",
                operation="validate", exc=exc, retryable=True,
            )
            return recovered
        for journal in self._deletion_journal.glob("*.json"):
            try:
                payload = _read_json_file(journal, self._root)
                if (
                    set(payload) != {"schema", "state", "artifact_id", "target", "basename"}
                    or payload["schema"] != 1
                    or payload["state"] not in {"intent", "done"}
                    or not _is_hex32(str(payload["artifact_id"]))
                    or payload["target"] not in _DELETION_TARGETS
                ):
                    raise ValueError("invalid deletion journal")
                artifact_id = str(payload["artifact_id"])
                target = str(payload["target"])
                self._target_path(artifact_id, target, str(payload["basename"]))
                if payload["state"] == "done":
                    if self._cleanup_deletion_journal(journal, payload, diagnostics):
                        recovered.append(artifact_id)
                elif self._finish_deletion_journal(journal, payload, diagnostics):
                    recovered.append(artifact_id)
            except Exception as exc:
                _diagnostic(
                    diagnostics, artifact_id="invalid", target="journal",
                    operation="validate", exc=exc, retryable=False,
                )
        return recovered
