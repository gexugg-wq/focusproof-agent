from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import struct
import sys
import time
import warnings

from PIL import Image, ImageOps

FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
MAX_PIXELS = 40_000_000
MAX_AXIS = 12_000
MAX_DECODED = 160 * 1024 * 1024
REQUEST_FRAME_MAX = 16 * 1024
RESPONSE_FRAME_MAX = 16 * 1024
_EXCLUSIVE_CREATE_FLAG = getattr(os, "O_" + "EXCL")


def _read_exact(size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sys.stdin.buffer.read(size - len(chunks))
        if not chunk:
            raise ValueError("truncated request frame")
        chunks.extend(chunk)
    return bytes(chunks)


def read_request() -> dict[str, object]:
    header = _read_exact(4)
    length = struct.unpack(">I", header)[0]
    if length > REQUEST_FRAME_MAX:
        raise ValueError("request frame too large")
    payload = _read_exact(length)
    request = json.loads(payload.decode("utf-8"))
    if not isinstance(request, dict):
        raise ValueError("request frame invalid")
    return request


def write_response(response: dict[str, object]) -> None:
    payload = json.dumps(response, sort_keys=True, separators=(",", ":")).encode()
    if len(payload) > RESPONSE_FRAME_MAX:
        raise ValueError("response frame too large")
    sys.stdout.buffer.write(struct.pack(">I", len(payload)) + payload)
    sys.stdout.buffer.flush()


def _private_path(value: object, root: Path) -> Path:
    path = Path(str(value))
    resolved = path.resolve(strict=True)
    if root not in resolved.parents:
        raise ValueError("decoder path escapes job root")
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("decoder input is not a private regular file")
    return resolved


def _dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0 or width > MAX_AXIS or height > MAX_AXIS:
        raise ValueError("image dimensions exceed limit")
    pixels = width * height
    if pixels > MAX_PIXELS or pixels * 4 > MAX_DECODED:
        raise ValueError("image decode size exceeds limit")


def _terminal(payload: bytes, media_type: str) -> None:
    if media_type == "image/png":
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("invalid PNG signature")
        offset, seen = 8, False
        while offset < len(payload):
            if len(payload) - offset < 12:
                raise ValueError("invalid PNG chunk structure")
            length = struct.unpack(">I", payload[offset:offset + 4])[0]
            kind = payload[offset + 4:offset + 8]
            end = offset + 12 + length
            if end > len(payload):
                raise ValueError("invalid PNG chunk length")
            if kind == b"IEND":
                if seen or length != 0 or end != len(payload):
                    raise ValueError("invalid PNG terminal structure")
                seen = True
            offset = end
        if not seen:
            raise ValueError("invalid PNG terminal structure")
    elif media_type == "image/jpeg":
        if not payload.endswith(b"\xff\xd9") or payload.rfind(b"\xff\xd9") != len(payload) - 2:
            raise ValueError("invalid JPEG terminal structure")
    elif (len(payload) < 12 or payload[:4] != b"RIFF" or payload[8:12] != b"WEBP"
          or struct.unpack("<I", payload[4:8])[0] + 8 != len(payload)):
        raise ValueError("invalid WebP container")


def inspect(payload: bytes) -> tuple[str, int, int, bool]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(BytesIO(payload)) as header:
                if header.format not in FORMATS:
                    raise ValueError("unsupported image format")
                width, height = header.size
                _dimensions(width, height)
                media_type = FORMATS[header.format]
                if int(getattr(header, "n_frames", 1)) != 1 or bool(getattr(header, "is_animated", False)):
                    raise ValueError("animated or multi-frame images are unsupported")
                alpha = header.mode in {"RGBA", "LA"} or (header.mode == "P" and "transparency" in header.info)
                header.verify()
            _terminal(payload, media_type)
            with Image.open(BytesIO(payload)) as decoded:
                decoded.load()
                if int(getattr(decoded, "n_frames", 1)) != 1:
                    raise ValueError("multi-frame images are unsupported")
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("image exceeds decode safety limits") from exc
        except (OSError, SyntaxError) as exc:
            raise ValueError("invalid image") from exc
    return media_type, width, height, alpha


def _write_private_output(root: Path, payload: bytes) -> tuple[str, int, str]:
    if len(payload) > MAX_DECODED:
        raise ValueError("normalized image exceeds output limit")
    output = root / "normalized"
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | _EXCLUSIVE_CREATE_FLAG, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return output.name, len(payload), sha256(payload).hexdigest()


def run(request: dict[str, object]) -> dict[str, object]:
    root = Path(str(request["job_root"])).resolve(strict=True)
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ValueError("decoder job root permission mismatch")
    payload = _private_path(request["input_path"], root).read_bytes()
    delay_value = request.get("delay_seconds", 0.0)
    byte_size = request.get("byte_size")
    max_normalized = request.get("max_normalized_bytes", MAX_DECODED)
    if isinstance(delay_value, bool) or not isinstance(delay_value, (int, float)):
        raise ValueError("decoder delay is invalid")
    if isinstance(byte_size, bool) or not isinstance(byte_size, int):
        raise ValueError("decoder byte size is invalid")
    if isinstance(max_normalized, bool) or not isinstance(max_normalized, int) or max_normalized <= 0:
        raise ValueError("decoder output limit is invalid")
    if delay_value:
        time.sleep(float(delay_value))
    if len(payload) != byte_size or sha256(payload).hexdigest() != request["sha256"]:
        raise ValueError("source metadata mismatch")
    media_type, width, height, alpha = inspect(payload)
    declared = request.get("declared_media_type")
    if declared is not None and str(declared).lower() != media_type:
        raise ValueError("declared media type does not match image")
    result: dict[str, object] = {"media_type": media_type, "width": width, "height": height,
                                "has_alpha": alpha, "worker_pid": os.getpid()}
    if request["operation"] == "normalize":
        expected = str(request["validated_media_type"])
        if expected != media_type:
            raise ValueError("validated image metadata does not match source")
        with Image.open(BytesIO(payload)) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened)
            alpha = image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info)
            image = image.convert("RGBA" if alpha else "RGB")
            image.info.clear()
            output = BytesIO()
            if alpha:
                out_type = "image/png"
                image.save(output, format="PNG", optimize=False, compress_level=9)
            elif expected == "image/jpeg":
                out_type = "image/jpeg"
                image.save(output, format="JPEG", quality=90, subsampling=0, optimize=False, progressive=False)
            elif expected == "image/webp":
                out_type = "image/webp"
                image.save(output, format="WEBP", quality=90, method=6, lossless=False)
            else:
                out_type = "image/png"
                image.save(output, format="PNG", optimize=False, compress_level=9)
        normalized = output.getvalue()
        if len(normalized) > max_normalized:
            raise ValueError("normalized image exceeds output limit")
        name, size, digest = _write_private_output(root, normalized)
        result.update({
            "media_type": out_type,
            "normalized_path": name,
            "normalized_sha256": digest,
            "byte_size": size,
        })
    return result


def main() -> int:
    try:
        request = read_request()
        os.environ.clear()
        response = {"ok": True, "result": run(request)}
    except BaseException as exc:
        response = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
    try:
        write_response(response)
    except BaseException:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
