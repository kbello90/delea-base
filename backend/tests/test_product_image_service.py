"""Unit tests for process_product_image (no network, no database)."""

import asyncio
import io
import struct
import threading
import time

import pytest
from PIL import ExifTags, Image, ImageCms

from app.services import product_images
from app.services.product_images import (
    MAX_BYTES,
    ImageRejected,
    process_product_image,
    process_product_image_async,
)


def encode(image: Image.Image, fmt: str, **params: object) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **params)
    return out.getvalue()


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


# ---------------------------------------------------------------------------
# A minimal ICC v2 RGB profile, built here so the test needs no binary fixture.
# ---------------------------------------------------------------------------
def _s15(value: float) -> bytes:
    return struct.pack(">i", round(value * 65536))


def _xyz(x: float, y: float, z: float) -> bytes:
    return b"XYZ " + b"\0" * 4 + _s15(x) + _s15(y) + _s15(z)


def _desc(text: str) -> bytes:
    ascii_ = text.encode("ascii") + b"\0"
    return (
        b"desc"
        + b"\0" * 4
        + struct.pack(">I", len(ascii_))
        + ascii_
        + struct.pack(">II", 0, 0)
        + struct.pack(">HB", 0, 0)
        + b"\0" * 67
    )


def icc_profile(description: str, red, green, blue) -> bytes:
    """Matrix/TRC RGB profile with linear tone curves and the given D50 primaries."""
    curve = b"curv" + b"\0" * 4 + struct.pack(">I", 0)  # identity
    tags = [
        (b"desc", _desc(description)),
        (b"wtpt", _xyz(0.9642, 1.0, 0.8249)),
        (b"rXYZ", _xyz(*red)),
        (b"gXYZ", _xyz(*green)),
        (b"bXYZ", _xyz(*blue)),
        (b"rTRC", curve),
        (b"gTRC", curve),
        (b"bTRC", curve),
    ]
    offset = 128 + 4 + 12 * len(tags)
    table, body = b"", b""
    for signature, data in tags:
        data += b"\0" * (-len(data) % 4)
        table += signature + struct.pack(">II", offset + len(body), len(data))
        body += data
    size = offset + len(body)
    header = (
        struct.pack(">I", size)
        + b"\0" * 4
        + bytes([2, 0x10, 0, 0])
        + b"mntrRGB XYZ "
        + b"\0" * 12
        + b"acsp"
        + b"\0" * 24
        + struct.pack(">I", 0)
        + _xyz(0.9642, 1.0, 0.8249)[8:]
        + b"\0" * 48
    )
    assert len(header) == 128
    return header + struct.pack(">I", len(tags)) + table + body


SRGB_RED = (0.4361, 0.2225, 0.0139)
SRGB_GREEN = (0.3851, 0.7169, 0.0971)
SRGB_BLUE = (0.1431, 0.0606, 0.7141)


# ---------------------------------------------------------------------------
# Accepted images and variants
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_variants_are_webp_1200_and_400_by_longest_side(fmt: str) -> None:
    result = process_product_image(encode(Image.new("RGB", (3000, 2000), (180, 40, 60)), fmt))
    large, thumb = decode(result.large), decode(result.thumb)
    assert (large.format, thumb.format) == ("WEBP", "WEBP")
    assert large.size == (1200, 800) and (result.width, result.height) == (1200, 800)
    assert thumb.size == (400, 267)


def test_portrait_images_are_limited_by_height() -> None:
    result = process_product_image(encode(Image.new("RGB", (900, 2400), "white"), "PNG"))
    assert decode(result.large).size == (450, 1200)
    assert decode(result.thumb).size == (150, 400)


def test_small_images_are_never_upscaled() -> None:
    result = process_product_image(encode(Image.new("RGB", (300, 200), "white"), "PNG"))
    assert decode(result.large).size == (300, 200)
    assert decode(result.thumb).size == (300, 200)


def test_qualities_are_85_for_large_and_82_for_thumb() -> None:
    source = Image.new("RGB", (300, 200))
    source.putdata([(x % 256, (x * 7) % 256, (x * 13) % 256) for x in range(300 * 200)])
    result = process_product_image(encode(source, "PNG"))
    # libwebp is deterministic: same pixels and settings give the same bytes.
    assert result.large == encode(source, "WEBP", quality=85, method=4)
    assert result.thumb == encode(source, "WEBP", quality=82, method=4)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
def test_exif_and_gps_are_stripped() -> None:
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "TestCam"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (40.0, 44.0, 54.0)
    gps[ExifTags.GPS.GPSLongitudeRef] = "W"
    gps[ExifTags.GPS.GPSLongitude] = (73.0, 59.0, 9.0)
    source = encode(Image.new("RGB", (640, 480), "white"), "JPEG", exif=exif)
    assert decode(source).getexif().get_ifd(ExifTags.IFD.GPSInfo)  # the input really has GPS

    result = process_product_image(source)
    for variant in (result.large, result.thumb):
        image = decode(variant)
        assert not image.getexif()
        assert "exif" not in image.info and "icc_profile" not in image.info
        assert "xmp" not in image.info
        assert b"TestCam" not in variant and b"Exif" not in variant


def test_exif_orientation_is_applied_before_it_is_dropped() -> None:
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6  # rotate 90° clockwise to display
    result = process_product_image(encode(Image.new("RGB", (400, 200), "white"), "JPEG", exif=exif))
    assert decode(result.large).size == (200, 400)


# ---------------------------------------------------------------------------
# Colour: non-sRGB profiles are converted to sRGB
# ---------------------------------------------------------------------------
def test_a_non_srgb_profile_is_converted_to_srgb() -> None:
    # Red and green primaries swapped: a pure "red" pixel in this profile is sRGB green.
    swapped = icc_profile("Swapped primaries (test)", SRGB_GREEN, SRGB_RED, SRGB_BLUE)
    source = encode(Image.new("RGB", (64, 64), (255, 0, 0)), "PNG", icc_profile=swapped)
    assert decode(source).info["icc_profile"] == swapped

    r, g, b = decode(process_product_image(source).large).convert("RGB").getpixel((32, 32))
    assert g > 200 and r < 60 and b < 60, (r, g, b)


def test_an_srgb_profile_leaves_the_pixels_alone() -> None:
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    source = encode(Image.new("RGB", (64, 64), (200, 30, 40)), "PNG", icc_profile=srgb)
    r, g, b = decode(process_product_image(source).large).convert("RGB").getpixel((32, 32))
    assert abs(r - 200) <= 3 and abs(g - 30) <= 3 and abs(b - 40) <= 3, (r, g, b)


def test_conversion_keeps_alpha() -> None:
    swapped = icc_profile("Swapped primaries (test)", SRGB_GREEN, SRGB_RED, SRGB_BLUE)
    image = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
    image.paste((0, 0, 0, 0), (0, 0, 32, 64))
    result = decode(process_product_image(encode(image, "PNG", icc_profile=swapped)).large)
    assert result.mode == "RGBA"
    assert result.getpixel((5, 5))[3] == 0
    r, g, b, a = result.getpixel((50, 32))
    assert a == 255 and g > 200 and r < 60


# ---------------------------------------------------------------------------
# Transparency
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_transparency_is_kept_for_png_and_webp(fmt: str) -> None:
    image = Image.new("RGBA", (200, 100), (10, 120, 200, 255))
    image.paste((0, 0, 0, 0), (0, 0, 100, 100))  # left half fully transparent
    params = {"lossless": True} if fmt == "WEBP" else {}
    result = decode(process_product_image(encode(image, fmt, **params)).large)
    assert result.mode == "RGBA"
    assert result.getpixel((10, 50))[3] == 0
    assert result.getpixel((190, 50))[3] == 255


def test_palette_png_transparency_becomes_alpha() -> None:
    image = Image.new("P", (100, 100), 0)
    image.putpalette([255, 255, 255, 200, 0, 0] + [0] * 762)
    image.paste(1, (50, 0, 100, 100))
    result = decode(process_product_image(encode(image, "PNG", transparency=0)).large)
    assert result.mode == "RGBA"
    assert result.getpixel((10, 10))[3] == 0
    assert result.getpixel((80, 10))[3] == 255


def test_opaque_images_have_no_alpha() -> None:
    result = decode(
        process_product_image(encode(Image.new("RGB", (50, 50), "white"), "JPEG")).large
    )
    assert result.mode == "RGB"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "data",
    [
        b"<html><body>not an image</body></html>",
        b"PK\x03\x04" + b"\0" * 200,  # a zip
        b"",
        encode(Image.new("RGB", (40, 40)), "JPEG")[:200],  # truncated JPEG
    ],
    ids=["html", "zip", "empty", "truncated-jpeg"],
)
def test_non_images_are_rejected(data: bytes) -> None:
    with pytest.raises(ImageRejected, match="not a valid JPEG, PNG or WebP image"):
        process_product_image(data)


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_other_image_formats_are_rejected(fmt: str) -> None:
    with pytest.raises(ImageRejected, match="Only JPEG, PNG and WebP"):
        process_product_image(encode(Image.new("RGB", (40, 40)), fmt))


def test_files_over_5_mb_are_rejected_before_decoding() -> None:
    with pytest.raises(ImageRejected, match="larger than 5 MB"):
        process_product_image(b"\xff\xd8" + b"\0" * MAX_BYTES)


@pytest.mark.parametrize("size", [(5001, 5000), (12000, 12000)], ids=["just-over", "bomb"])
def test_images_over_25_megapixels_are_rejected(size: tuple[int, int]) -> None:
    # A solid colour compresses to a few KB: small file, huge pixel count.
    data = encode(Image.new("L", size, 255), "PNG")
    assert len(data) < MAX_BYTES
    with pytest.raises(ImageRejected, match="25 megapixels|not a valid"):
        process_product_image(data)


# ---------------------------------------------------------------------------
# Thread pool and concurrency cap
# ---------------------------------------------------------------------------
def test_async_runs_off_the_event_loop_with_at_most_two_at_a_time(monkeypatch) -> None:
    active = 0
    peak = 0
    threads: set[int] = set()
    lock = threading.Lock()

    def slow(data: bytes) -> str:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        threads.add(threading.get_ident())
        time.sleep(0.05)
        with lock:
            active -= 1
        return "done"

    monkeypatch.setattr(product_images, "process_product_image", slow)

    async def run() -> list[str]:
        return await asyncio.gather(*(process_product_image_async(b"x") for _ in range(6)))

    loop_thread = threading.get_ident()
    assert asyncio.run(run()) == ["done"] * 6
    assert peak == 2
    assert loop_thread not in threads
