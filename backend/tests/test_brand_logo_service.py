"""Unit tests for process_logo (no network, no database)."""

import io

import pytest
from PIL import ExifTags, Image

from app.services.brand_logo import MAX_BYTES, ImageRejected, process_logo


def encode(image: Image.Image, fmt: str, **params: object) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **params)
    return out.getvalue()


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_one_webp_of_at_most_512_px_without_metadata(fmt: str) -> None:
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "TestCam"
    exif.get_ifd(ExifTags.IFD.GPSInfo)[ExifTags.GPS.GPSLatitudeRef] = "N"
    logo = process_logo(encode(Image.new("RGB", (1000, 400), "white"), fmt, exif=exif))
    image = decode(logo.data)
    assert image.format == "WEBP" and image.size == (512, 205) == (logo.width, logo.height)
    assert not image.getexif() and "icc_profile" not in image.info


def test_transparency_is_kept_and_small_logos_are_not_upscaled() -> None:
    logo = process_logo(encode(Image.new("RGBA", (120, 80), (0, 0, 0, 0)), "PNG"))
    image = decode(logo.data)
    assert image.size == (120, 80) and image.mode == "RGBA" and image.getpixel((0, 0))[3] == 0


def test_the_exif_orientation_is_applied() -> None:
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    logo = process_logo(encode(Image.new("RGB", (400, 200), "white"), "JPEG", exif=exif))
    assert (logo.width, logo.height) == (200, 400)


@pytest.mark.parametrize(
    "data",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"MZ\x90\x00 renamed",
        b"%PDF-1.7",
        encode(Image.new("RGB", (10, 10)), "GIF"),
    ],
)
def test_svg_and_other_files_are_refused(data: bytes) -> None:
    with pytest.raises(ImageRejected):
        process_logo(data)


def test_files_over_2_mb_are_refused_before_decoding() -> None:
    with pytest.raises(ImageRejected, match="larger than 2 MB"):
        process_logo(b"\x89PNG" + b"\0" * MAX_BYTES)
