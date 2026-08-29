from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import time
from typing import IO, Protocol, cast

from focusproof.speech_core.errors import AudioValidationError, SpeechErrorCode
from focusproof.speech_core.models import (
    MAX_AUDIO_BYTES,
    MAX_AUDIO_DURATION_MS,
    AudioFacts,
    AudioFormat,
)

_MAX_OUTPUT_BYTES = 64 * 1024
_INSPECTION_TIMEOUT_SECONDS = 2.0
_SANDBOX_AUDIO_PATH = "/input/audio"
_PopenFactory = Callable[..., subprocess.Popen[bytes]]


class CommandRunner(Protocol):
    async def run(
        self,
        argv: Sequence[str],
        *,
        deadline: float,
        output_limit: int,
    ) -> bytes: ...


class SandboxedCommandRunner:
    def __init__(self, *, popen: _PopenFactory | None = None) -> None:
        self._popen = popen or cast(_PopenFactory, subprocess.Popen)

    async def run(
        self,
        argv: Sequence[str],
        *,
        deadline: float,
        output_limit: int,
    ) -> bytes:
        return await asyncio.to_thread(
            self._run_sync,
            tuple(argv),
            deadline=deadline,
            output_limit=output_limit,
        )

    def _run_sync(
        self,
        argv: Sequence[str],
        *,
        deadline: float,
        output_limit: int,
    ) -> bytes:
        if output_limit <= 0:
            raise ValueError("sandbox output limit must be positive")
        if deadline <= time.monotonic():
            raise TimeoutError("sandbox command timed out")
        try:
            process = self._popen(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env={},
                close_fds=True,
                start_new_session=True,
            )
        except OSError:
            raise RuntimeError("sandbox command unavailable") from None
        stdout = process.stdout
        stderr = process.stderr
        if stdout is None or stderr is None:
            self._terminate(process)
            raise RuntimeError("sandbox command pipes unavailable")
        try:
            return self._collect_output(
                process,
                stdout,
                stderr,
                deadline=deadline,
                output_limit=output_limit,
            )
        finally:
            stdout.close()
            stderr.close()

    def _collect_output(
        self,
        process: subprocess.Popen[bytes],
        stdout: IO[bytes],
        stderr: IO[bytes],
        *,
        deadline: float,
        output_limit: int,
    ) -> bytes:
        output = bytearray()
        total_bytes = 0
        with selectors.DefaultSelector() as selector:
            for stream, name in ((stdout, "stdout"), (stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate(process)
                    raise TimeoutError("sandbox command timed out")
                events = selector.select(min(remaining, 0.05))
                if not events:
                    continue
                for key, _mask in events:
                    stream = cast(IO[bytes], key.fileobj)
                    read_size = min(8192, output_limit + 1 - total_bytes)
                    chunk = os.read(stream.fileno(), max(1, read_size))
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    total_bytes += len(chunk)
                    if total_bytes > output_limit:
                        self._terminate(process)
                        raise RuntimeError("sandbox output limit exceeded")
                    if key.data == "stdout":
                        output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._terminate(process)
            raise TimeoutError("sandbox command timed out")
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            self._terminate(process)
            raise TimeoutError("sandbox command timed out") from None
        if returncode != 0:
            raise RuntimeError("sandbox command failed")
        return bytes(output)

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=0.2)


class MediainfoAudioInspector:
    def __init__(
        self,
        *,
        mediainfo_path: Path | None = None,
        bubblewrap_path: Path | None = None,
        prlimit_path: Path | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self._mediainfo_path = mediainfo_path or Path("/usr/bin/mediainfo")
        self._bubblewrap_path = bubblewrap_path or Path("/usr/bin/bwrap")
        self._prlimit_path = prlimit_path or Path("/usr/bin/prlimit")
        self._runner = command_runner or SandboxedCommandRunner()

    @classmethod
    def prerequisites_available(
        cls,
        *,
        mediainfo_path: Path = Path("/usr/bin/mediainfo"),
        bubblewrap_path: Path = Path("/usr/bin/bwrap"),
        prlimit_path: Path = Path("/usr/bin/prlimit"),
    ) -> bool:
        return all(
            path.is_file() and os.access(path, os.X_OK)
            for path in (mediainfo_path, bubblewrap_path, prlimit_path)
        )

    async def inspect(
        self,
        path: Path,
        *,
        declared_media_type: str | None,
        deadline: float,
    ) -> AudioFacts:
        try:
            byte_size = self._validate_source(path)
            command_deadline = min(
                deadline,
                time.monotonic() + _INSPECTION_TIMEOUT_SECONDS,
            )
            if command_deadline <= time.monotonic():
                raise TimeoutError("audio inspection timed out")
            output = await self._runner.run(
                self._command(path),
                deadline=command_deadline,
                output_limit=_MAX_OUTPUT_BYTES,
            )
            return self._parse(
                output,
                path=path,
                byte_size=byte_size,
                declared_media_type=declared_media_type,
            )
        except AudioValidationError:
            raise
        except TimeoutError:
            raise AudioValidationError(SpeechErrorCode.TRANSCRIPTION_TIMEOUT) from None
        except Exception:
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO) from None

    @staticmethod
    def _validate_source(path: Path) -> int:
        if not path.is_absolute() or path.is_symlink():
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO)
        try:
            source_stat = path.stat()
        except OSError:
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO) from None
        if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size <= 0:
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO)
        if source_stat.st_size > MAX_AUDIO_BYTES:
            raise AudioValidationError(SpeechErrorCode.AUDIO_TOO_LARGE)
        return source_stat.st_size

    def _command(self, path: Path) -> tuple[str, ...]:
        argv: list[str] = [
            str(self._bubblewrap_path),
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--unshare-net",
            "--clearenv",
            "--tmpfs",
            "/",
        ]
        for runtime_root in (Path("/usr"), Path("/lib"), Path("/lib64")):
            if runtime_root.exists():
                argv.extend(("--ro-bind", str(runtime_root), str(runtime_root)))
        argv.extend(
            (
                "--tmpfs",
                "/tmp",
                "--dir",
                "/input",
                "--ro-bind",
                str(path),
                _SANDBOX_AUDIO_PATH,
                "--chdir",
                "/input",
                "--setenv",
                "PATH",
                "/usr/bin:/bin",
                "--",
                str(self._prlimit_path),
                "--as=134217728",
                "--cpu=1",
                "--nofile=32:32",
                "--",
                str(self._mediainfo_path),
                "--Output=JSON",
                _SANDBOX_AUDIO_PATH,
            )
        )
        return tuple(argv)

    def _parse(
        self,
        output: bytes,
        *,
        path: Path,
        byte_size: int,
        declared_media_type: str | None,
    ) -> AudioFacts:
        try:
            document = json.loads(output.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO) from None
        if not isinstance(document, dict):
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO)
        media = document.get("media")
        if not isinstance(media, dict):
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO)
        tracks = media.get("track")
        if not isinstance(tracks, list) or not all(isinstance(track, dict) for track in tracks):
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO)
        general_tracks = [track for track in tracks if track.get("@type") == "General"]
        audio_tracks = [track for track in tracks if track.get("@type") == "Audio"]
        if len(general_tracks) != 1 or len(audio_tracks) != 1 or len(tracks) != 2:
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO)
        general = general_tracks[0]
        audio = audio_tracks[0]
        stored_size = self._integer(general.get("FileSize"))
        if stored_size != byte_size:
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO)
        duration_ms = self._duration_ms(audio.get("Duration", general.get("Duration")))
        audio_format, media_type, codec = self._classify(general, audio)
        expected_suffix = {
            AudioFormat.WEBM_OPUS: ".webm",
            AudioFormat.WAV_PCM: ".wav",
            AudioFormat.MP3: ".mp3",
        }[audio_format]
        if path.suffix.casefold() != expected_suffix or declared_media_type != media_type:
            raise AudioValidationError(SpeechErrorCode.UNSUPPORTED_AUDIO_FORMAT)
        return AudioFacts(
            audio_format=audio_format,
            media_type=media_type,
            codec=codec,
            byte_size=byte_size,
            duration_ms=duration_ms,
        )

    @staticmethod
    def _integer(value: object) -> int:
        if isinstance(value, bool):
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO)
        try:
            parsed = int(cast(str | int, value))
        except (TypeError, ValueError):
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO) from None
        if str(parsed) != str(value):
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO)
        return parsed

    @staticmethod
    def _duration_ms(value: object) -> int:
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO)
        try:
            seconds = Decimal(str(value))
        except InvalidOperation:
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO) from None
        if not seconds.is_finite() or seconds <= 0:
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO)
        milliseconds = seconds * 1000
        if milliseconds > MAX_AUDIO_DURATION_MS:
            raise AudioValidationError(SpeechErrorCode.AUDIO_TOO_LONG)
        return int(milliseconds.to_integral_value(rounding=ROUND_HALF_UP))

    @staticmethod
    def _classify(
        general: dict[object, object],
        audio: dict[object, object],
    ) -> tuple[AudioFormat, str, str]:
        container = general.get("Format")
        codec = audio.get("Format")
        profile = audio.get("Format_Profile")
        if not isinstance(container, str) or not isinstance(codec, str):
            raise AudioValidationError(SpeechErrorCode.INVALID_AUDIO)
        if container.casefold() == "webm" and codec.casefold() == "opus":
            return AudioFormat.WEBM_OPUS, "audio/webm;codecs=opus", "Opus"
        if container.casefold() == "wave" and codec.casefold() == "pcm":
            return AudioFormat.WAV_PCM, "audio/wav", "PCM"
        if (
            container.casefold() == "mpeg audio"
            and codec.casefold() == "mpeg audio"
            and isinstance(profile, str)
            and "layer 3" in profile.casefold()
        ):
            return AudioFormat.MP3, "audio/mpeg", "MPEG Audio Layer 3"
        raise AudioValidationError(SpeechErrorCode.UNSUPPORTED_AUDIO_FORMAT)
