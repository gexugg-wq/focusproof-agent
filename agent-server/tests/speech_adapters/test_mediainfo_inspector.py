from __future__ import annotations

import base64
import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path
import sys
import time
from typing import Sequence

import pytest

from focusproof.speech_adapters.mediainfo_inspector import (
    MediainfoAudioInspector,
    SandboxedCommandRunner,
)
from focusproof.speech_core.errors import AudioValidationError, SpeechErrorCode
from focusproof.speech_core.models import AudioFormat


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class RecordingRunner:
    output: bytes
    error: BaseException | None = None
    calls: list[tuple[tuple[str, ...], float, int]] = field(default_factory=list)

    async def run(
        self,
        argv: Sequence[str],
        *,
        deadline: float,
        output_limit: int,
    ) -> bytes:
        self.calls.append((tuple(argv), deadline, output_limit))
        if self.error is not None:
            raise self.error
        return self.output


def _inspector(runner: RecordingRunner) -> MediainfoAudioInspector:
    return MediainfoAudioInspector(
        mediainfo_path=Path("/usr/bin/mediainfo"),
        bubblewrap_path=Path("/usr/bin/bwrap"),
        prlimit_path=Path("/usr/bin/prlimit"),
        command_runner=runner,
    )


def _metadata(
    *,
    container: str,
    codec: str,
    size: int = 16,
    duration: str = "1.250",
    profile: str | None = None,
    audio_tracks: int = 1,
) -> bytes:
    general = {
        "@type": "General",
        "Format": container,
        "FileSize": str(size),
        "Duration": duration,
    }
    audio: dict[str, str] = {
        "@type": "Audio",
        "Format": codec,
        "Duration": duration,
    }
    if profile is not None:
        audio["Format_Profile"] = profile
    return json.dumps(
        {"media": {"track": [general, *[dict(audio) for _ in range(audio_tracks)]]}}
    ).encode()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("suffix", "declared", "container", "codec", "profile", "expected"),
    [
        (
            ".webm",
            "audio/webm;codecs=opus",
            "WebM",
            "Opus",
            None,
            AudioFormat.WEBM_OPUS,
        ),
        (".wav", "audio/wav", "Wave", "PCM", None, AudioFormat.WAV_PCM),
        (
            ".mp3",
            "audio/mpeg",
            "MPEG Audio",
            "MPEG Audio",
            "Layer 3",
            AudioFormat.MP3,
        ),
    ],
)
async def test_supported_formats_are_accepted_from_bounded_metadata(
    tmp_path: Path,
    suffix: str,
    declared: str,
    container: str,
    codec: str,
    profile: str | None,
    expected: AudioFormat,
) -> None:
    path = tmp_path / f"input{suffix}"
    path.write_bytes(b"x" * 16)
    runner = RecordingRunner(_metadata(container=container, codec=codec, profile=profile))

    facts = await _inspector(runner).inspect(
        path,
        declared_media_type=declared,
        deadline=time.monotonic() + 30,
    )

    assert facts.audio_format is expected
    assert facts.byte_size == 16
    assert facts.duration_ms == 1_250


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("suffix", "declared"),
    [
        (".mp3", "audio/webm;codecs=opus"),
        (".wav", "audio/mpeg"),
        (".bin", "audio/wav"),
    ],
)
async def test_extension_and_declared_mime_must_match_detected_format(
    tmp_path: Path,
    suffix: str,
    declared: str,
) -> None:
    path = tmp_path / f"mismatch{suffix}"
    path.write_bytes(b"x" * 16)
    runner = RecordingRunner(_metadata(container="Wave", codec="PCM"))

    with pytest.raises(AudioValidationError) as caught:
        await _inspector(runner).inspect(
            path,
            declared_media_type=declared,
            deadline=time.monotonic() + 30,
        )

    assert caught.value.code is SpeechErrorCode.UNSUPPORTED_AUDIO_FORMAT


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (_metadata(container="WebM", codec="Opus", audio_tracks=2), SpeechErrorCode.INVALID_AUDIO),
        (_metadata(container="Wave", codec="PCM", duration="0"), SpeechErrorCode.INVALID_AUDIO),
        (
            _metadata(container="Wave", codec="PCM", duration="120.001"),
            SpeechErrorCode.AUDIO_TOO_LONG,
        ),
        (b"{truncated", SpeechErrorCode.INVALID_AUDIO),
        (json.dumps({"media": {"track": []}}).encode(), SpeechErrorCode.INVALID_AUDIO),
    ],
)
async def test_malformed_multitrack_and_duration_boundaries_fail_closed(
    tmp_path: Path,
    output: bytes,
    expected: SpeechErrorCode,
) -> None:
    path = tmp_path / "input.wav"
    path.write_bytes(b"x" * 16)

    with pytest.raises(AudioValidationError) as caught:
        await _inspector(RecordingRunner(output)).inspect(
            path,
            declared_media_type="audio/wav",
            deadline=time.monotonic() + 30,
        )

    assert caught.value.code is expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("host path /private/audio.wav"), SpeechErrorCode.TRANSCRIPTION_TIMEOUT),
        (OSError("host path /private/audio.wav"), SpeechErrorCode.INVALID_AUDIO),
        (RuntimeError("raw output and /private/audio.wav"), SpeechErrorCode.INVALID_AUDIO),
    ],
)
async def test_runner_failures_are_typed_and_redacted(
    tmp_path: Path,
    error: BaseException,
    expected: SpeechErrorCode,
) -> None:
    path = tmp_path / "secret-name.wav"
    path.write_bytes(b"x" * 16)

    with pytest.raises(AudioValidationError) as caught:
        await _inspector(RecordingRunner(b"", error=error)).inspect(
            path,
            declared_media_type="audio/wav",
            deadline=time.monotonic() + 30,
        )

    assert caught.value.code is expected
    assert str(path) not in str(caught.value)
    assert "raw output" not in str(caught.value)


@pytest.mark.anyio
async def test_command_is_networkless_read_only_bounded_and_uses_one_input(
    tmp_path: Path,
) -> None:
    path = tmp_path / "input.webm"
    path.write_bytes(b"x" * 16)
    runner = RecordingRunner(_metadata(container="WebM", codec="Opus"))
    caller_deadline = time.monotonic() + 30

    await _inspector(runner).inspect(
        path,
        declared_media_type="audio/webm;codecs=opus",
        deadline=caller_deadline,
    )

    argv, command_deadline, output_limit = runner.calls[0]
    assert argv[0] == "/usr/bin/bwrap"
    assert "--unshare-net" in argv
    assert "--clearenv" in argv
    assert ("--tmpfs", "/") in set(zip(argv, argv[1:]))
    assert ("--tmpfs", "/tmp") in set(zip(argv, argv[1:]))
    source_index = argv.index(str(path))
    assert argv[source_index - 1] == "--ro-bind"
    assert argv[source_index + 1] == "/input/audio"
    assert argv.count(str(path)) == 1
    assert "--as=134217728" in argv
    assert "--cpu=1" in argv
    assert "--nofile=32:32" in argv
    assert "--Output=JSON" in argv
    assert argv[-1] == "/input/audio"
    assert output_limit == 64 * 1024
    assert command_deadline <= caller_deadline
    assert command_deadline <= time.monotonic() + 2


@pytest.mark.anyio
async def test_process_runner_never_uses_shell_or_inherited_environment() -> None:
    observed: dict[str, object] = {}

    def unavailable(argv: Sequence[str], **kwargs: object) -> object:
        observed["argv"] = tuple(argv)
        observed.update(kwargs)
        raise OSError("unavailable")

    runner = SandboxedCommandRunner(popen=unavailable)
    with pytest.raises(RuntimeError):
        await runner.run(
            ("/usr/bin/false",),
            deadline=time.monotonic() + 1,
            output_limit=64 * 1024,
        )

    assert observed["shell"] is False
    assert observed["env"] == {}
    assert observed["start_new_session"] is True


@pytest.mark.anyio
async def test_process_runner_enforces_the_output_ceiling() -> None:
    runner = SandboxedCommandRunner()

    with pytest.raises(RuntimeError):
        await runner.run(
            (
                sys.executable,
                "-c",
                "import os; os.write(1, b'x' * 65537)",
            ),
            deadline=time.monotonic() + 2,
            output_limit=64 * 1024,
        )


@pytest.mark.anyio
async def test_process_runner_terminates_the_group_at_deadline() -> None:
    runner = SandboxedCommandRunner()
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        await runner.run(
            (sys.executable, "-c", "import time; time.sleep(10)"),
            deadline=time.monotonic() + 0.05,
            output_limit=64 * 1024,
        )

    assert time.monotonic() - started < 1


@pytest.mark.parametrize(
    ("fixture_name", "magic", "exact_size"),
    [
        ("valid.wav.gz.b64", b"RIFF", None),
        ("zero.wav.gz.b64", b"RIFF", None),
        ("valid.mp3.gz.b64", b"ID3", None),
        ("too-long.mp3.gz.b64", b"ID3", None),
        ("valid.webm.gz.b64", b"\x1aE\xdf\xa3", None),
        ("multitrack.webm.gz.b64", b"\x1aE\xdf\xa3", None),
        ("truncated.webm.gz.b64", b"\x1aE\xdf\xa3", 64),
    ],
)
def test_synthetic_fixture_sources_decode_to_expected_container_headers(
    fixture_name: str,
    magic: bytes,
    exact_size: int | None,
) -> None:
    fixture_root = Path(__file__).resolve().parents[1] / "fixtures" / "audio"
    encoded = (fixture_root / fixture_name).read_bytes()

    payload = gzip.decompress(base64.b64decode(encoded, validate=False))

    assert payload.startswith(magic)
    assert len(payload) > len(magic)
    if exact_size is not None:
        assert len(payload) == exact_size
