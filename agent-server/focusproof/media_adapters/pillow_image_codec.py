from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any, BinaryIO, cast

from focusproof.media_core.ports import ReadOnlyMediaSource, ValidatedMediaMetadata

REQUEST_FRAME_MAX = 16 * 1024
RESPONSE_FRAME_MAX = 16 * 1024
STDERR_MAX = 8 * 1024
DEFAULT_NORMALIZED_MAX = 160 * 1024 * 1024
_EXCLUSIVE_CREATE_FLAG = getattr(os, "O_" + "EXCL")


def recover_orphan_decoder_jobs(root: Path, *, older_than_seconds: float) -> tuple[str, ...]:
    """Remove stale decoder-owned job directories without following links."""
    if older_than_seconds < 0 or root.is_symlink():
        raise ValueError("invalid decoder recovery root or age")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    managed_root = root.resolve(strict=True)
    cutoff = time.time() - older_than_seconds
    removed: list[str] = []
    for candidate in root.iterdir():
        try:
            info = candidate.lstat()
            if (not candidate.name.startswith("focusproof-decoder-") or candidate.is_symlink()
                    or not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
                    or info.st_mtime >= cutoff):
                continue
            resolved = candidate.resolve(strict=True)
            if managed_root not in resolved.parents:
                continue
            safe = True
            for child in candidate.iterdir():
                child_info = child.lstat()
                if (child.is_symlink() or not stat.S_ISREG(child_info.st_mode)
                        or stat.S_IMODE(child_info.st_mode) != 0o600
                        or resolved not in child.resolve(strict=True).parents):
                    safe = False
                    break
            if not safe:
                continue
            shutil.rmtree(candidate)
            descriptor = os.open(managed_root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            removed.append(candidate.name)
        except (FileNotFoundError, OSError, ValueError):
            continue
    return tuple(removed)


@dataclass(slots=True)
class _NormalizedSource:
    stream: BytesIO
    media_type: str
    byte_size: int
    normalized_sha256: str

    def rewind(self) -> None:
        self.stream.seek(0)

    def close(self) -> None:
        self.stream.close()


class PillowImageCodecAdapter:
    """Pillow boundary executed exclusively in a short-lived OS subprocess."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        worker_command: tuple[str, ...] | None = None,
        temp_root: Path | None = None,
        worker_delay_seconds: float = 0.0,
        max_normalized_bytes: int = DEFAULT_NORMALIZED_MAX,
    ) -> None:
        if timeout_seconds <= 0 or max_normalized_bytes <= 0:
            raise ValueError("decoder limits must be positive")
        worker = Path(__file__).with_name("decoder_worker.py")
        self._worker_command = worker_command or (sys.executable, "-I", str(worker))
        self._timeout_seconds = timeout_seconds
        self._temp_root = temp_root
        self._worker_delay_seconds = worker_delay_seconds
        self._max_normalized_bytes = max_normalized_bytes
        self.last_worker_pid: int | None = None

    def validate(self, source: ReadOnlyMediaSource,
                 declared_media_type: str | None) -> ValidatedMediaMetadata:
        payload = self._read_source(source)
        result = self._run_worker(payload, {"operation": "validate",
                                           "declared_media_type": declared_media_type})
        return ValidatedMediaMetadata(
            media_type=str(result["media_type"]), byte_size=source.byte_size,
            source_sha256=source.streaming_sha256,
            attributes={"width": int(result["width"]), "height": int(result["height"]),
                        "has_alpha": bool(result["has_alpha"])},
        )

    def normalize(self, source: ReadOnlyMediaSource,
                  metadata: ValidatedMediaMetadata) -> _NormalizedSource:
        payload = self._read_source(source)
        result = self._run_worker(payload, {"operation": "normalize",
                                           "declared_media_type": metadata.media_type,
                                           "validated_media_type": metadata.media_type})
        normalized = result.pop("_normalized_bytes")
        if not isinstance(normalized, bytes):
            raise ValueError("decoder normalized output missing")
        digest = sha256(normalized).hexdigest()
        if digest != result["normalized_sha256"] or len(normalized) != result["byte_size"]:
            raise ValueError("decoder normalized output metadata mismatch")
        return _NormalizedSource(BytesIO(normalized), str(result["media_type"]),
                                 len(normalized), digest)

    @staticmethod
    def _read_source(source: ReadOnlyMediaSource) -> bytes:
        start = source.stream.tell()
        try:
            source.stream.seek(0)
            payload = source.stream.read()
        finally:
            source.stream.seek(start)
        if len(payload) != source.byte_size or sha256(payload).hexdigest() != source.streaming_sha256:
            raise ValueError("source metadata mismatch")
        return payload

    def _run_worker(self, payload: bytes, request: dict[str, Any]) -> dict[str, Any]:
        base = str(self._temp_root) if self._temp_root is not None else None
        job = Path(tempfile.mkdtemp(prefix="focusproof-decoder-", dir=base))
        job.chmod(0o700)
        input_path = job / "input"
        process: subprocess.Popen[bytes] | None = None
        try:
            descriptor = os.open(input_path, os.O_WRONLY | os.O_CREAT | _EXCLUSIVE_CREATE_FLAG, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            request.update({
                "job_root": str(job),
                "input_path": str(input_path),
                "byte_size": len(payload),
                "sha256": sha256(payload).hexdigest(),
                "delay_seconds": self._worker_delay_seconds,
                "max_normalized_bytes": self._max_normalized_bytes,
            })
            try:
                process = subprocess.Popen(
                    self._worker_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env={"PYTHONIOENCODING": "utf-8"},
                    close_fds=True, start_new_session=True,
                )
            except OSError as exc:
                raise ValueError("decoder worker failed to start") from exc
            self.last_worker_pid = process.pid
            self._write_request(process, request)
            response = self._read_response(process)
            if not isinstance(response, dict) or response.get("ok") is not True:
                raise ValueError("decoder rejected image")
            result = response.get("result")
            if not isinstance(result, dict) or int(result.get("worker_pid", 0)) == os.getpid():
                raise ValueError("decoder process isolation failed")
            self.last_worker_pid = int(result["worker_pid"])
            if "normalized_path" in result:
                result["_normalized_bytes"] = self._validated_output_bytes(result, job)
            return result
        except TimeoutError:
            raise
        except BaseException:
            raise
        finally:
            if process is not None and process.poll() is None:
                self._terminate(process)
            shutil.rmtree(job, ignore_errors=True)

    def _write_request(self, process: subprocess.Popen[bytes], request: dict[str, Any]) -> None:
        if process.stdin is None:
            raise ValueError("decoder IPC stdin unavailable")
        payload = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        if len(payload) > REQUEST_FRAME_MAX:
            raise ValueError("decoder IPC request frame too large")
        process.stdin.write(struct.pack(">I", len(payload)) + payload)
        process.stdin.close()

    def _read_response(self, process: subprocess.Popen[bytes]) -> dict[str, Any]:
        if process.stdout is None or process.stderr is None:
            raise ValueError("decoder IPC pipes unavailable")
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + self._timeout_seconds
        stdout = bytearray()
        stderr = bytearray()
        expected_length: int | None = None
        while True:
            if expected_length is not None and len(stdout) >= 4 + expected_length:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate(process)
                raise TimeoutError("decoder worker timed out")
            events = selector.select(min(remaining, 0.05))
            if not events and process.poll() is not None:
                break
            for key, _ in events:
                stream = cast(BinaryIO, key.fileobj)
                chunk = stream.read(4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stderr":
                    stderr.extend(chunk)
                    if len(stderr) > STDERR_MAX:
                        self._terminate(process)
                        raise ValueError("decoder stderr exceeded IPC cap")
                    continue
                stdout.extend(chunk)
                if len(stdout) > RESPONSE_FRAME_MAX + 4:
                    self._terminate(process)
                    raise ValueError("decoder IPC response frame too large")
                if expected_length is None and len(stdout) >= 4:
                    expected_length = struct.unpack(">I", bytes(stdout[:4]))[0]
                    if expected_length > RESPONSE_FRAME_MAX:
                        self._terminate(process)
                        raise ValueError("decoder IPC response frame too large")
        try:
            return_code = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            self._terminate(process)
            raise TimeoutError("decoder worker timed out") from exc
        if return_code != 0:
            raise ValueError("decoder worker IPC failed")
        for stream, kind in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            while True:
                try:
                    chunk = stream.read(4096)
                except BlockingIOError:
                    break
                if not chunk:
                    break
                if kind == "stderr":
                    stderr.extend(chunk)
                    if len(stderr) > STDERR_MAX:
                        raise ValueError("decoder stderr exceeded IPC cap")
                    continue
                stdout.extend(chunk)
                if len(stdout) > RESPONSE_FRAME_MAX + 4:
                    raise ValueError("decoder IPC response frame too large")
                if expected_length is None and len(stdout) >= 4:
                    expected_length = struct.unpack(">I", bytes(stdout[:4]))[0]
                    if expected_length > RESPONSE_FRAME_MAX:
                        raise ValueError("decoder IPC response frame too large")
        if len(stdout) < 4:
            raise ValueError("decoder IPC response truncated")
        expected_length = struct.unpack(">I", bytes(stdout[:4]))[0]
        if expected_length > RESPONSE_FRAME_MAX or len(stdout) != 4 + expected_length:
            raise ValueError("decoder IPC response frame invalid")
        try:
            response = json.loads(bytes(stdout[4:]).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("decoder IPC response invalid") from exc
        if not isinstance(response, dict):
            raise ValueError("decoder IPC response invalid")
        return response

    def _validated_output_bytes(self, result: dict[str, Any], job: Path) -> bytes:
        basename = result.get("normalized_path")
        if not isinstance(basename, str) or "/" in basename or "\\" in basename or basename in {"", ".", ".."}:
            raise ValueError("decoder normalized output path invalid")
        path = job / basename
        resolved = path.resolve(strict=True)
        if job.resolve(strict=True) not in resolved.parents:
            raise ValueError("decoder normalized output path invalid")
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("decoder normalized output mode invalid")
        expected_size = result.get("byte_size")
        if isinstance(expected_size, bool) or not isinstance(expected_size, int):
            raise ValueError("decoder normalized output size invalid")
        if expected_size > self._max_normalized_bytes or info.st_size != expected_size:
            raise ValueError("decoder normalized output size invalid")
        expected_digest = result.get("normalized_sha256")
        if not isinstance(expected_digest, str):
            raise ValueError("decoder normalized output digest invalid")
        payload = path.read_bytes()
        digest = sha256(payload).hexdigest()
        if digest != expected_digest:
            raise ValueError("decoder normalized output digest invalid")
        return payload

    def _terminate(self, process: subprocess.Popen[bytes]) -> None:
        try:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    process.kill()
        finally:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
