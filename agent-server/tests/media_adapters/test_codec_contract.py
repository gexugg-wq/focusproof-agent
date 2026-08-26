from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import textwrap
import time
import zlib

import pytest
from PIL import Image

from focusproof.media_adapters.pillow_image_codec import PillowImageCodecAdapter
from focusproof.media_core.ports import ReadOnlyMediaSource, ValidatedMediaMetadata


class TrackingStream(BytesIO):
    close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()


def source(payload: bytes, stream: BytesIO | None = None) -> ReadOnlyMediaSource:
    return ReadOnlyMediaSource(
        stream or BytesIO(payload), len(payload), sha256(payload).hexdigest()
    )


def metadata_for(payload: bytes) -> ValidatedMediaMetadata:
    return ValidatedMediaMetadata(
        media_type="image/png",
        byte_size=len(payload),
        source_sha256=sha256(payload).hexdigest(),
        attributes={"width": 4, "height": 3, "has_alpha": False},
    )


def image_bytes(format_name: str, *, mode: str = "RGB", **save: object) -> bytes:
    output = BytesIO()
    color = (11, 22, 33, 128) if mode == "RGBA" else (11, 22, 33)
    Image.new(mode, (4, 3), color).save(output, format=format_name, **save)
    return output.getvalue()


def _worker_script(tmp_path: Path, body: str) -> tuple[str, str]:
    script = tmp_path / "worker.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return (sys.executable, str(script))


def _jobs(tmp_path: Path) -> Path:
    root = tmp_path / "jobs"
    root.mkdir(exist_ok=True)
    return root


@pytest.mark.parametrize(
    ("payload", "declared"),
    [
        (image_bytes("PNG"), "image/jpeg"),
        (image_bytes("JPEG"), "image/webp"),
        (image_bytes("WEBP"), "image/png"),
    ],
)
def test_declared_mime_must_match_detected_format(payload: bytes, declared: str) -> None:
    with pytest.raises(ValueError):
        PillowImageCodecAdapter().validate(source(payload), declared)


@pytest.mark.parametrize("format_name", ["PNG", "JPEG"])
def test_terminal_marker_must_be_end_of_file(format_name: str) -> None:
    payload = image_bytes(format_name) + b"trailing"
    with pytest.raises(ValueError):
        PillowImageCodecAdapter().validate(source(payload), None)


def test_webp_riff_declared_length_and_alignment_are_strict() -> None:
    payload = bytearray(image_bytes("WEBP"))
    payload[4:8] = struct.pack("<I", len(payload) - 9)
    with pytest.raises(ValueError):
        PillowImageCodecAdapter().validate(source(bytes(payload)), "image/webp")
    aligned = image_bytes("WEBP") + b"\x00"
    with pytest.raises(ValueError):
        PillowImageCodecAdapter().validate(source(aligned), "image/webp")


@pytest.mark.parametrize(
    "payload",
    [
        image_bytes("GIF"),
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>',
        b"\x00\x00\x00\x18ftypheic" + b"\x00" * 24,
    ],
)
def test_unsupported_formats_are_rejected(payload: bytes) -> None:
    with pytest.raises(ValueError):
        PillowImageCodecAdapter().validate(source(payload), None)


def test_animated_webp_is_rejected() -> None:
    output = BytesIO()
    frames = [Image.new("RGB", (2, 2), color) for color in ("red", "blue")]
    frames[0].save(output, format="WEBP", save_all=True, append_images=frames[1:], duration=20)
    with pytest.raises(ValueError):
        PillowImageCodecAdapter().validate(source(output.getvalue()), "image/webp")


def _png_dimensions(payload: bytes, width: int, height: int) -> bytes:
    data = bytearray(payload)
    data[16:24] = struct.pack(">II", width, height)
    data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]) & 0xFFFFFFFF)
    return bytes(data)


@pytest.mark.parametrize(("width", "height"), [(12_001, 1), (1, 12_001), (10_000, 4_001)])
def test_dimensions_pixels_and_rgba_decode_budget_are_enforced(width: int, height: int) -> None:
    payload = _png_dimensions(image_bytes("PNG"), width, height)
    with pytest.raises(ValueError):
        PillowImageCodecAdapter().validate(source(payload), "image/png")


def test_decompression_bomb_warning_is_rejected() -> None:
    payload = _png_dimensions(image_bytes("PNG"), 10_000, 10_000)
    with pytest.raises(ValueError):
        PillowImageCodecAdapter().validate(source(payload), "image/png")


def test_decompression_bomb_error_is_rejected() -> None:
    payload = _png_dimensions(image_bytes("PNG"), 20_000, 20_000)
    with pytest.raises(ValueError):
        PillowImageCodecAdapter().validate(source(payload), "image/png")


def test_corrupt_decode_is_mapped_to_value_error() -> None:
    payload = bytearray(image_bytes("PNG"))
    idat = payload.find(b"IDAT")
    assert idat > 0
    payload[idat + 4] ^= 0xFF
    with pytest.raises(ValueError):
        PillowImageCodecAdapter().validate(source(bytes(payload)), "image/png")


def test_orientation_is_applied_and_metadata_is_stripped() -> None:
    exif = Image.Exif()
    exif[274] = 6
    payload = image_bytes(
        "JPEG", exif=exif, icc_profile=b"fake-profile", comment=b"private-comment"
    )
    codec = PillowImageCodecAdapter()
    metadata = codec.validate(source(payload), "image/jpeg")
    normalized = codec.normalize(source(payload), metadata)
    try:
        result = normalized.stream.read()
        with Image.open(BytesIO(result)) as decoded:
            assert decoded.size == (3, 4)
            assert decoded.getexif() == {}
            assert "icc_profile" not in decoded.info
            assert "comment" not in decoded.info
    finally:
        normalized.close()


@pytest.mark.parametrize(
    ("format_name", "mode", "expected"),
    [
        ("PNG", "RGBA", "image/png"),
        ("PNG", "RGB", "image/png"),
        ("JPEG", "RGB", "image/jpeg"),
        ("WEBP", "RGB", "image/webp"),
    ],
)
def test_output_format_policy_and_determinism(
    format_name: str, mode: str, expected: str
) -> None:
    payload = image_bytes(format_name, mode=mode)
    codec = PillowImageCodecAdapter()
    metadata = codec.validate(source(payload), expected)
    first = codec.normalize(source(payload), metadata)
    second = codec.normalize(source(payload), metadata)
    try:
        first_bytes = first.stream.read()
        second_bytes = second.stream.read()
        assert first.media_type == second.media_type == expected
        assert first_bytes == second_bytes
        assert first.normalized_sha256 == second.normalized_sha256 == sha256(first_bytes).hexdigest()
        assert first.byte_size == second.byte_size == len(first_bytes)
    finally:
        first.close()
        second.close()


def test_stream_ownership_position_rewind_and_close() -> None:
    payload = image_bytes("PNG")
    stream = TrackingStream(payload)
    codec = PillowImageCodecAdapter()
    metadata = codec.validate(source(payload, stream), "image/png")
    normalized = codec.normalize(source(payload, stream), metadata)
    assert not stream.closed
    assert stream.close_calls == 0
    assert normalized.stream.tell() == 0
    normalized.stream.read(1)
    normalized.rewind()
    assert normalized.stream.tell() == 0



_PNG_IEND_CHUNK = bytes.fromhex('0000000049454e44ae426082')


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack('>I', len(data)) + chunk_type + data + struct.pack('>I', crc)


def _insert_png_chunk_before_iend(payload: bytes, chunk_type: bytes, data: bytes) -> bytes:
    index = payload.rfind(_PNG_IEND_CHUNK)
    assert index != -1
    return payload[:index] + _png_chunk(chunk_type, data) + payload[index:]


def test_png_ancillary_chunk_may_contain_iend_marker_like_bytes() -> None:
    payload = _insert_png_chunk_before_iend(
        image_bytes('PNG'),
        b'vpAg',
        b'ignored metadata ' + _PNG_IEND_CHUNK + b' inside ancillary data',
    )

    metadata = PillowImageCodecAdapter().validate(source(payload), 'image/png')

    assert metadata.media_type == 'image/png'
    assert metadata.attributes['width'] == 4


@pytest.mark.parametrize(
    'payload',
    [
        image_bytes('PNG')[:-len(_PNG_IEND_CHUNK)],
        image_bytes('PNG') + _PNG_IEND_CHUNK,
        image_bytes('PNG')[:-len(_PNG_IEND_CHUNK)] + _png_chunk(b'IEND', b'x'),
        image_bytes('PNG') + b'trailing',
    ],
)
def test_structured_png_iend_rejects_missing_duplicate_nonzero_or_trailing(
    payload: bytes,
) -> None:
    with pytest.raises(ValueError):
        PillowImageCodecAdapter().validate(source(payload), 'image/png')


def test_decoder_runs_in_distinct_process() -> None:
    codec = PillowImageCodecAdapter()
    codec.validate(source(image_bytes("PNG")), "image/png")
    assert codec.last_worker_pid is not None
    assert codec.last_worker_pid != os.getpid()


@pytest.mark.parametrize("payload", [b"corrupt", image_bytes("GIF")])
def test_worker_failure_never_returns_safe_metadata(payload: bytes) -> None:
    with pytest.raises(ValueError):
        PillowImageCodecAdapter().validate(source(payload), None)


def test_decoder_timeout_cleans_worker_and_tempfiles(tmp_path: Path) -> None:
    codec = PillowImageCodecAdapter(timeout_seconds=0.01, worker_delay_seconds=1.0, temp_root=tmp_path)
    with pytest.raises(TimeoutError):
        codec.validate(source(image_bytes("PNG")), "image/png")
    assert list(tmp_path.iterdir()) == []


def test_decoder_startup_failure_cleans_tempfiles(tmp_path: Path) -> None:
    codec = PillowImageCodecAdapter(worker_command=("/missing/decoder",), temp_root=tmp_path)
    with pytest.raises(ValueError, match="start"):
        codec.validate(source(image_bytes("PNG")), "image/png")
    assert list(tmp_path.iterdir()) == []


def test_decoder_ipc_eof_fails_closed_and_cleans_tempfiles(tmp_path: Path) -> None:
    codec = PillowImageCodecAdapter(
        worker_command=(sys.executable, "-I", "-c", "pass"), temp_root=tmp_path
    )
    with pytest.raises(ValueError, match="IPC"):
        codec.validate(source(image_bytes("PNG")), "image/png")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "body",
    [
        """
        import struct, sys
        sys.stdout.buffer.write(struct.pack('>I', 10_000_000))
        sys.stdout.buffer.flush()
        """,
        """
        import sys
        while True:
            sys.stdout.buffer.write(b'x' * 65536)
            sys.stdout.buffer.flush()
        """,
        """
        import sys
        while True:
            sys.stderr.buffer.write(b'e' * 65536)
            sys.stderr.buffer.flush()
        """,
        """
        import struct, sys
        sys.stdout.buffer.write(struct.pack('>I', 100))
        sys.stdout.buffer.write(b'{}')
        sys.stdout.buffer.flush()
        """,
    ],
)
def test_decoder_rejects_malformed_or_oversized_ipc_frames(
    tmp_path: Path,
    body: str,
) -> None:
    codec = PillowImageCodecAdapter(
        worker_command=_worker_script(tmp_path, body),
        timeout_seconds=1.0,
        temp_root=_jobs(tmp_path),
    )
    with pytest.raises(ValueError, match="IPC|stderr|frame"):
        codec.validate(source(image_bytes("PNG")), "image/png")
    assert list((tmp_path / "jobs").iterdir()) == []


class _NoEventSelector:
    def register(self, fileobj: object, events: int, data: object = None) -> None:
        return None

    def select(self, timeout: float | None = None) -> list[tuple[object, object]]:
        return []


class _ExitedProcessWithUnreadStdout:
    def __init__(self, stdout_fd: int, stderr_fd: int) -> None:
        self.stdout = os.fdopen(stdout_fd, "rb", buffering=0)
        self.stderr = os.fdopen(stderr_fd, "rb", buffering=0)

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_decoder_parent_drains_stdout_after_fast_worker_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"ok": True, "result": {"worker_pid": os.getpid() + 1}}).encode()
    out_read, out_write = os.pipe()
    err_read, err_write = os.pipe()
    os.write(out_write, struct.pack(">I", len(payload)) + payload)
    os.close(out_write)
    os.close(err_write)
    process = _ExitedProcessWithUnreadStdout(out_read, err_read)
    monkeypatch.setattr("focusproof.media_adapters.pillow_image_codec.selectors.DefaultSelector", _NoEventSelector)

    try:
        response = PillowImageCodecAdapter()._read_response(process)  # noqa: SLF001
    finally:
        process.stdout.close()
        process.stderr.close()

    assert response == {"ok": True, "result": {"worker_pid": os.getpid() + 1}}


def test_normalized_output_uses_bounded_job_file_not_base64_stdout(tmp_path: Path) -> None:
    payload = image_bytes("PNG")
    jobs = _jobs(tmp_path)
    codec = PillowImageCodecAdapter(temp_root=jobs)
    metadata = codec.validate(source(payload), "image/png")
    normalized = codec.normalize(source(payload), metadata)
    try:
        assert normalized.stream.read()
    finally:
        normalized.close()
    assert list(jobs.iterdir()) == []


@pytest.mark.parametrize(
    "result_fragment",
    [
        '"normalized_path":"../escape","byte_size":1,"normalized_sha256":"' + "0" * 64 + '"',
        '"normalized_path":"output","byte_size":1,"normalized_sha256":"' + "0" * 64 + '"',
    ],
)
def test_decoder_rejects_normalized_output_path_and_digest_spoof(
    tmp_path: Path,
    result_fragment: str,
) -> None:
    body = f"""
    import json, pathlib, struct, sys
    raw = sys.stdin.buffer.read()
    root = pathlib.Path(json.loads(raw[4:])['job_root'])
    (root / 'output').write_bytes(b'not-matching')
    (root / 'output').chmod(0o600)
    response = '{{"ok":true,"result":{{"worker_pid":123456,"media_type":"image/png",{result_fragment}}}}}'.encode()
    sys.stdout.buffer.write(struct.pack('>I', len(response)) + response)
    sys.stdout.buffer.flush()
    """
    codec = PillowImageCodecAdapter(
        worker_command=_worker_script(tmp_path, body),
        temp_root=_jobs(tmp_path),
    )
    payload = image_bytes("PNG")
    with pytest.raises(ValueError, match="decoder"):
        codec.normalize(source(payload), metadata_for(payload))
    assert list((tmp_path / "jobs").iterdir()) == []


def test_decoder_rejects_oversized_normalized_output_file(tmp_path: Path) -> None:
    body = """
    import json, pathlib, struct, sys
    raw = sys.stdin.buffer.read()
    root = pathlib.Path(json.loads(raw[4:])['job_root'])
    payload = b'x' * (1024 * 1024 + 1)
    (root / 'output').write_bytes(payload)
    (root / 'output').chmod(0o600)
    response = json.dumps({
        'ok': True,
        'result': {
            'worker_pid': 123456,
            'media_type': 'image/png',
            'normalized_path': 'output',
            'byte_size': len(payload),
            'normalized_sha256': '0' * 64,
        },
    }).encode()
    sys.stdout.buffer.write(struct.pack('>I', len(response)) + response)
    sys.stdout.buffer.flush()
    """
    payload = image_bytes("PNG")
    codec = PillowImageCodecAdapter(
        worker_command=_worker_script(tmp_path, body),
        max_normalized_bytes=1024 * 1024,
        temp_root=_jobs(tmp_path),
    )
    with pytest.raises(ValueError, match="normalized"):
        codec.normalize(source(payload), metadata_for(payload))
    assert list((tmp_path / "jobs").iterdir()) == []


def test_parent_cancellation_terminates_worker_and_cleans_tempfiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codec = PillowImageCodecAdapter(temp_root=tmp_path, worker_delay_seconds=10.0)

    def cancelled(_: subprocess.Popen[bytes]) -> dict[str, object]:
        raise asyncio.CancelledError

    monkeypatch.setattr(codec, "_read_response", cancelled)
    with pytest.raises(asyncio.CancelledError):
        codec.validate(source(image_bytes("PNG")), "image/png")
    assert codec.last_worker_pid is not None
    gone = subprocess.run(["kill", "-0", str(codec.last_worker_pid)], check=False)
    assert gone.returncode != 0
    assert list(tmp_path.iterdir()) == []


def test_real_parent_sigint_reaps_worker_and_removes_job_root(tmp_path: Path) -> None:
    driver = tmp_path / "driver.py"
    driver.write_text(
        textwrap.dedent(
            f"""
            import pathlib, sys
            sys.path.insert(0, {str(Path.cwd() / "agent-server")!r})
            from tests.media_adapters.test_codec_contract import image_bytes, source
            from focusproof.media_adapters.pillow_image_codec import PillowImageCodecAdapter
            codec = PillowImageCodecAdapter(timeout_seconds=30, worker_delay_seconds=20, temp_root=pathlib.Path({str(tmp_path)!r}))
            try:
                codec.validate(source(image_bytes("PNG")), "image/png")
            except KeyboardInterrupt:
                print("parent-interrupted", flush=True)
                raise
            """
        ),
        encoding="utf-8",
    )
    parent = subprocess.Popen(
        [sys.executable, str(driver)],
        cwd=Path.cwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        child_pid: int | None = None
        while time.monotonic() < deadline:
            ps = subprocess.run(
                ["pgrep", "-P", str(parent.pid)],
                check=False,
                capture_output=True,
                text=True,
            )
            if ps.stdout.strip():
                child_pid = int(ps.stdout.split()[0])
                break
            assert parent.poll() is None
            time.sleep(0.02)
        assert child_pid is not None
        parent.send_signal(signal.SIGINT)
        parent.wait(timeout=5)
        gone = subprocess.run(["kill", "-0", str(child_pid)], check=False)
        assert gone.returncode != 0
        assert list(tmp_path.glob("focusproof-decoder-*")) == []
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
