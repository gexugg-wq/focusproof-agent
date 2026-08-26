from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from focusproof.media_core.models import StagedMediaObject
from focusproof.media_core.ports import NormalizedMediaSource


_SCHEMA = 2
_PHASES = {"MANIFEST_ONLY", "STAGED"}
_CHUNK_SIZE = 1024 * 1024


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    primary: BaseException | None = None
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            primary = exc
            # Some platforms/filesystems explicitly do not support directory fsync.
            # Durability failures such as EIO/EACCES/ENOENT must remain visible.
            if exc.errno in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
                return
            raise
    finally:
        try:
            os.close(descriptor)
        except OSError:
            if primary is None:
                raise


def _durable_publish(temporary: Path, target: Path) -> None:
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    _fsync_directory(target.parent)


def _durable_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    _fsync_directory(path.parent)


def _path_present(path: Path) -> bool:
    return path.is_symlink() or path.exists()


def _is_regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _same_file_bytes(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_stream, right.open("rb") as right_stream:
        while True:
            left_chunk = left_stream.read(_CHUNK_SIZE)
            right_chunk = right_stream.read(_CHUNK_SIZE)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


class LocalMediaObjectStore:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise ValueError("storage root may not be a symlink")
        self._root = root.resolve(strict=True)
        self._staged = self._directory("staged")
        self._referenced = self._directory("referenced")
        self._manifests = self._directory("manifests")

    def _directory(self, name: str) -> Path:
        path = self._root / name
        path.mkdir(mode=0o700, exist_ok=True)
        if path.is_symlink():
            raise ValueError("storage directory may not be a symlink")
        return path.resolve(strict=True)

    @staticmethod
    def _validate_key(key: str) -> str:
        if len(key) != 32 or any(character not in "0123456789abcdef" for character in key):
            raise ValueError("invalid opaque object key")
        return key

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        with path.open("x", encoding="utf-8") as output:
            json.dump(payload, output, sort_keys=True, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())

    def stage(
        self,
        normalized: NormalizedMediaSource,
        media_item_id: str,
        reservation_id: str,
    ) -> StagedMediaObject:
        if not media_item_id or not reservation_id:
            raise ValueError("media and reservation identities are required")
        key = uuid4().hex
        manifest_id = uuid4().hex
        manifest_path = self._manifests / f"{manifest_id}.json"
        manifest_tmp = self._manifests / f".{manifest_id}.part"
        object_path = self._staged / key
        object_tmp = self._staged / f".{key}.part"
        manifest: dict[str, object] = {
            "schema": _SCHEMA,
            "manifest_id": manifest_id,
            "opaque_object_key": key,
            "media_item_id": media_item_id,
            "reservation_id": reservation_id,
            "phase": "MANIFEST_ONLY",
        }
        try:
            self._write_json(manifest_tmp, manifest)
            _durable_publish(manifest_tmp, manifest_path)
            with object_tmp.open("xb") as output:
                while chunk := normalized.stream.read(_CHUNK_SIZE):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            _durable_publish(object_tmp, object_path)
            manifest["phase"] = "STAGED"
            self._write_json(manifest_tmp, manifest)
            _durable_publish(manifest_tmp, manifest_path)
        except Exception:
            _durable_unlink(object_tmp)
            _durable_unlink(manifest_tmp)
            if _path_present(object_path) and not manifest_path.exists():
                _durable_unlink(object_path)
            raise
        return StagedMediaObject(media_item_id, reservation_id, key, manifest_id)

    def mark_referenced(self, staged: StagedMediaObject) -> None:
        source = self._staged / self._validate_key(staged.opaque_object_key)
        target = self._referenced / staged.opaque_object_key
        manifest_path = self._manifests / f"{self._validate_key(staged.manifest_id)}.json"
        if not manifest_path.exists():
            if _path_present(source):
                if not _is_regular_file(source):
                    raise ValueError("staged object has no binding manifest")
                if _path_present(target):
                    if not _is_regular_file(target) or not _same_file_bytes(source, target):
                        raise ValueError("referenced media object conflict")
                    _durable_unlink(source)
                    return
                raise ValueError("staged object has no binding manifest")
            if _is_regular_file(target):
                return
            if _path_present(target):
                raise ValueError("referenced media object is invalid")
            raise FileNotFoundError("media object is absent")
        self._assert_manifest(staged, phase="STAGED")
        if _path_present(source):
            if not _is_regular_file(source):
                raise ValueError("invalid staged media object")
            if _path_present(target):
                if not _is_regular_file(target):
                    raise ValueError("invalid referenced media object")
                if not _same_file_bytes(source, target):
                    raise ValueError("referenced media object conflict")
                _durable_unlink(source)
            else:
                try:
                    os.link(source, target)
                except FileExistsError:
                    if not _is_regular_file(target) or not _same_file_bytes(source, target):
                        raise ValueError("referenced media object conflict") from None
                    _durable_unlink(source)
                else:
                    _fsync_directory(self._referenced)
                    _durable_unlink(source)
        elif _is_regular_file(target):
            pass
        elif _path_present(target):
            raise ValueError("invalid referenced media object")
        else:
            raise FileNotFoundError("staged media object is absent")
        _durable_unlink(manifest_path)

    def abort_staged(self, staged: StagedMediaObject) -> None:
        path = self._staged / self._validate_key(staged.opaque_object_key)
        target = self._referenced / staged.opaque_object_key
        manifest_path = self._manifests / f"{self._validate_key(staged.manifest_id)}.json"
        if not manifest_path.exists():
            if _path_present(path) or _path_present(target):
                raise ValueError("media object has no binding manifest")
            return
        self._assert_manifest(staged, phase="STAGED")
        if _path_present(target):
            raise ValueError("referenced media object cannot be aborted")
        if _path_present(path) and not _is_regular_file(path):
            raise ValueError("invalid staged media object")
        _durable_unlink(path)
        _durable_unlink(manifest_path)

    @contextmanager
    def open(self, opaque_object_key: str) -> Iterator[Any]:
        key = self._validate_key(opaque_object_key)
        path = self._referenced / key
        if path.is_symlink():
            raise ValueError("symlink media object rejected")
        with path.open("rb") as stream:
            yield stream

    def delete(self, opaque_object_key: str) -> None:
        key = self._validate_key(opaque_object_key)
        path = self._referenced / key
        if path.is_symlink():
            raise ValueError("symlink media object rejected")
        if path.exists() and not path.is_file():
            raise ValueError("invalid media object")
        _durable_unlink(path)

    def _assert_manifest(self, staged: StagedMediaObject, *, phase: str) -> Path:
        manifest_id = self._validate_key(staged.manifest_id)
        path = self._manifests / f"{manifest_id}.json"
        record = self._read_record(path)
        expected = {
            "schema": _SCHEMA,
            "manifest_id": manifest_id,
            "opaque_object_key": staged.opaque_object_key,
            "media_item_id": staged.media_item_id,
            "reservation_id": staged.reservation_id,
            "phase": phase,
        }
        if record != expected:
            raise ValueError("staged manifest binding mismatch")
        return path

    def _read_record(self, path: Path) -> dict[str, object] | None:
        try:
            if path.is_symlink() or not path.is_file():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "schema", "manifest_id", "opaque_object_key", "media_item_id",
                "reservation_id", "phase",
            }:
                return None
            manifest_id = payload.get("manifest_id")
            key = payload.get("opaque_object_key")
            if not isinstance(manifest_id, str) or path.stem != manifest_id:
                return None
            if not isinstance(key, str):
                return None
            self._validate_key(manifest_id)
            self._validate_key(key)
            if payload.get("schema") != _SCHEMA or payload.get("phase") not in _PHASES:
                return None
            if not isinstance(payload.get("media_item_id"), str) or not payload["media_item_id"]:
                return None
            if not isinstance(payload.get("reservation_id"), str) or not payload["reservation_id"]:
                return None
            return payload
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def recover_staged(
        self,
        reference_checker: Callable[[str], bool | None],
        *,
        older_than_seconds: float,
    ) -> tuple[str, ...]:
        cutoff = time.time() - older_than_seconds
        recovered: list[str] = []
        for manifest_path in self._manifests.glob("*.json"):
            try:
                if manifest_path.stat().st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            record = self._read_record(manifest_path)
            if record is None:
                continue
            try:
                referenced = reference_checker(str(record["media_item_id"]))
            except Exception:
                continue
            object_path = self._staged / str(record["opaque_object_key"])
            target_path = self._referenced / str(record["opaque_object_key"])
            if _path_present(object_path) and not _is_regular_file(object_path):
                continue
            if _path_present(target_path) and not _is_regular_file(target_path):
                continue
            staged = StagedMediaObject(
                str(record["media_item_id"]), str(record["reservation_id"]),
                str(record["opaque_object_key"]), str(record["manifest_id"]),
            )
            phase = str(record["phase"])
            try:
                if referenced is True:
                    if phase != "STAGED":
                        continue
                    if not _path_present(object_path) and not _path_present(target_path):
                        continue
                    self.mark_referenced(staged)
                elif referenced is False:
                    if _path_present(target_path):
                        continue
                    if phase == "MANIFEST_ONLY":
                        _durable_unlink(object_path)
                        _durable_unlink(manifest_path)
                    else:
                        self.abort_staged(staged)
                else:
                    continue
            except (OSError, ValueError, FileNotFoundError):
                continue
            recovered.append(str(record["manifest_id"]))
        return tuple(recovered)
