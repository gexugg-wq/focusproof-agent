from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from focusproof.media_adapters.local_media_object_store import LocalMediaObjectStore
from focusproof.media_adapters.local_quarantine_store import LocalQuarantineStore
from focusproof.media_adapters.media_janitor import MediaJanitor
from focusproof.media_adapters.pillow_image_codec import PillowImageCodecAdapter
from focusproof.media_core.ports import ReadOnlyMediaSource


def _image_bytes(format_name: str, *, mode: str = "RGB") -> bytes:
    output = BytesIO()
    color = (20, 40, 60, 128) if mode == "RGBA" else (20, 40, 60)
    Image.new(mode, (4, 3), color).save(output, format=format_name)
    return output.getvalue()


def _source(payload: bytes) -> ReadOnlyMediaSource:
    return ReadOnlyMediaSource(BytesIO(payload), len(payload), sha256(payload).hexdigest())


@pytest.mark.parametrize(
    ("format_name", "declared", "expected"),
    [
        ("PNG", "image/png", "image/png"),
        ("JPEG", "image/jpeg", "image/jpeg"),
        ("WEBP", "image/webp", "image/webp"),
    ],
)
def test_codec_accepts_supported_still_images(
    format_name: str, declared: str, expected: str
) -> None:
    codec = PillowImageCodecAdapter()
    payload = _image_bytes(format_name)
    metadata = codec.validate(_source(payload), declared)
    assert metadata.media_type == expected
    assert metadata.attributes == {"width": 4, "height": 3, "has_alpha": False}
    normalized = codec.normalize(_source(payload), metadata)
    try:
        assert normalized.media_type == expected
        assert normalized.stream.tell() == 0
        assert normalized.byte_size > 0
    finally:
        normalized.close()


def test_codec_rejects_trailing_bytes_and_unsupported_gif() -> None:
    codec = PillowImageCodecAdapter()
    with pytest.raises(ValueError):
        codec.validate(_source(_image_bytes("PNG") + b"trailing"), "image/png")
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format="GIF")
    with pytest.raises(ValueError):
        codec.validate(_source(output.getvalue()), "image/gif")


def test_quarantine_and_object_store_bind_ids_and_keep_roots_separate(tmp_path: Path) -> None:
    quarantine = LocalQuarantineStore(tmp_path / "quarantine")
    writer = quarantine.create("reservation")
    writer.write(b"abc")
    item = writer.finalize()
    writer.close()
    assert item.byte_size == 3
    assert "reservation" not in item.quarantine_id

    store = LocalMediaObjectStore(tmp_path / "objects")
    payload = _image_bytes("PNG")
    codec = PillowImageCodecAdapter()
    metadata = codec.validate(_source(payload), "image/png")
    normalized = codec.normalize(_source(payload), metadata)
    staged = store.stage(normalized, "media-item", "reservation")
    normalized.close()
    assert staged.media_item_id == "media-item"
    assert staged.reservation_id == "reservation"
    store.mark_referenced(staged)
    with store.open(staged.opaque_object_key) as stream:
        assert stream.read() != b""


def test_janitor_fails_closed_when_database_check_is_unavailable(tmp_path: Path) -> None:
    quarantine = LocalQuarantineStore(tmp_path / "quarantine")
    objects = LocalMediaObjectStore(tmp_path / "objects")
    payload = _image_bytes("PNG")
    codec = PillowImageCodecAdapter()
    metadata = codec.validate(_source(payload), "image/png")
    normalized = codec.normalize(_source(payload), metadata)
    staged = objects.stage(normalized, "media-item", "reservation")
    normalized.close()

    def unavailable(_: str) -> bool:
        raise RuntimeError("database unavailable")

    janitor = MediaJanitor(
        quarantine_store=quarantine,
        object_store=objects,
        reference_checker=unavailable,
        reservation_active_checker=lambda _: True,
    )
    assert janitor.sweep(older_than_seconds=0) == ((), ())
    assert (tmp_path / "objects" / "staged" / staged.opaque_object_key).exists()
