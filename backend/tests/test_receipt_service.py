"""Unit tests for process_receipt (no network, no database)."""

import io

import pytest
from PIL import ExifTags, Image

from app.services.receipts import (
    MAX_BYTES,
    ReceiptRejected,
    ReceiptType,
    process_receipt,
)

FORMATS = {ReceiptType.jpeg: "JPEG", ReceiptType.png: "PNG", ReceiptType.webp: "WEBP"}


def encode(image: Image.Image, fmt: str, **params: object) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **params)
    return out.getvalue()


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def gps_exif(orientation: int | None = None) -> Image.Exif:
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "TestCam"
    if orientation:
        exif[ExifTags.Base.Orientation] = orientation
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (40.0, 44.0, 54.0)
    return exif


@pytest.mark.parametrize("kind", list(FORMATS))
def test_images_keep_their_format_and_lose_all_metadata(kind: ReceiptType) -> None:
    fmt = FORMATS[kind]
    source = encode(Image.new("RGB", (640, 480), "white"), fmt, exif=gps_exif())
    assert decode(source).getexif().get_ifd(ExifTags.IFD.GPSInfo)  # the input really has GPS
    result = process_receipt(source, kind)
    assert result.content_type is kind
    image = decode(result.data)
    assert image.format == fmt  # no conversion to WebP
    assert image.size == (640, 480)  # no resizing
    assert not image.getexif()
    assert "exif" not in image.info and "icc_profile" not in image.info and "xmp" not in image.info


def test_jpeg_is_reencoded_at_quality_90() -> None:
    source = encode(Image.effect_noise((400, 300), 60).convert("RGB"), "JPEG", quality=50)
    result = process_receipt(source, ReceiptType.jpeg)
    expected = encode(decode(source).convert("RGB"), "JPEG", quality=90)
    assert result.data == expected


def test_exif_orientation_is_applied_before_it_is_dropped() -> None:
    source = encode(Image.new("RGB", (400, 200), "white"), "JPEG", exif=gps_exif(orientation=6))
    image = decode(process_receipt(source, ReceiptType.jpeg).data)
    assert image.size == (200, 400)  # rotated into place
    assert not image.getexif()


def test_png_transparency_is_kept() -> None:
    source = encode(Image.new("RGBA", (50, 50), (255, 0, 0, 0)), "PNG")
    image = decode(process_receipt(source, ReceiptType.png).data)
    assert image.mode == "RGBA" and image.getpixel((0, 0))[3] == 0


@pytest.mark.parametrize(
    ("declared", "actual"),
    [
        (ReceiptType.jpeg, "PNG"),
        (ReceiptType.png, "JPEG"),
        (ReceiptType.webp, "JPEG"),
    ],
)
def test_content_must_match_the_declared_image_type(declared: ReceiptType, actual: str) -> None:
    source = encode(Image.new("RGB", (40, 40), "white"), actual)
    with pytest.raises(ReceiptRejected, match="doesn't match its type"):
        process_receipt(source, declared)


@pytest.mark.parametrize(
    "data",
    [
        b"MZ\x90\x00 this is a renamed program",
        b"<html><script>alert(1)</script></html>",
        b"\x89PNG\r\n\x1a\n truncated",
    ],
)
@pytest.mark.parametrize("declared", list(ReceiptType))
def test_files_that_are_not_what_they_claim_are_refused(data: bytes, declared: ReceiptType) -> None:
    with pytest.raises(ReceiptRejected):
        process_receipt(data, declared)


def test_a_pdf_is_stored_unchanged() -> None:
    pdf = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
    result = process_receipt(pdf, ReceiptType.pdf)
    assert result.data == pdf and result.content_type is ReceiptType.pdf


@pytest.mark.parametrize("data", [b" %PDF-1.7", b"PDF-1.7", b"%PDf-1.7", b"<html>%PDF-"])
def test_a_pdf_must_start_with_the_pdf_marker(data: bytes) -> None:
    with pytest.raises(ReceiptRejected, match="isn't a PDF"):
        process_receipt(data, ReceiptType.pdf)


def test_an_image_is_not_accepted_as_a_pdf() -> None:
    with pytest.raises(ReceiptRejected, match="isn't a PDF"):
        process_receipt(encode(Image.new("RGB", (10, 10)), "JPEG"), ReceiptType.pdf)


def test_files_over_10_mb_are_refused_before_decoding() -> None:
    for declared in ReceiptType:
        with pytest.raises(ReceiptRejected, match="larger than 10 MB"):
            process_receipt(b"%PDF-" + b"\0" * MAX_BYTES, declared)


def test_empty_files_are_refused() -> None:
    with pytest.raises(ReceiptRejected, match="empty"):
        process_receipt(b"", ReceiptType.pdf)


def test_the_25_megapixel_guard_applies() -> None:
    # 5100 x 5000 = 25.5 MP, a tiny PNG of one colour.
    source = encode(Image.new("1", (5100, 5000)), "PNG", optimize=True)
    assert len(source) < MAX_BYTES
    with pytest.raises(ReceiptRejected, match="megapixels"):
        process_receipt(source, ReceiptType.png)
