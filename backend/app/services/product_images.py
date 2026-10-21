"""Product image processing: bytes in, two WebP variants out. No I/O, so the API's register
step and the future showcase loader can share it.

- Accepts only real JPEG, PNG or WebP images (decoded with Pillow; any declared content type
  is ignored), at most MAX_BYTES and MAX_PIXELS (decompression-bomb guard).
- Applies the EXIF orientation, converts a non-sRGB embedded ICC profile to sRGB, keeps alpha
  for PNG/WebP with transparency, then re-encodes from pixels only: EXIF (including GPS), XMP
  and ICC metadata never reach the output.
- Writes a 1200 px (WebP quality 85) and a 400 px (quality 82) variant, by the longest side,
  never upscaling.

CPU-bound: call it through process_product_image_async (thread pool, capped concurrency),
never directly on the event loop.
"""

import asyncio
import io
import warnings
import weakref
from dataclasses import dataclass

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 25_000_000
ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
LARGE_SIDE, LARGE_QUALITY = 1200, 85
THUMB_SIDE, THUMB_QUALITY = 400, 82
MAX_CONCURRENT = 2

_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))


class ImageRejected(ValueError):
    """The file isn't an acceptable image. The message is shown to the user."""


@dataclass(frozen=True)
class ProcessedImage:
    large: bytes
    thumb: bytes
    width: int  # of the large variant
    height: int


def _open_checked(data: bytes) -> Image.Image:
    if len(data) > MAX_BYTES:
        raise ImageRejected(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    try:
        with warnings.catch_warnings():
            # Pillow warns past MAX_IMAGE_PIXELS and errors past twice that; both are refusals.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(data))
            if probe.format not in ALLOWED_FORMATS:
                raise ImageRejected("Only JPEG, PNG and WebP images are accepted.")
            width, height = probe.size
            if width * height > MAX_PIXELS:
                raise ImageRejected(
                    f"The image is too large ({width} x {height} pixels); "
                    f"the limit is {MAX_PIXELS // 1_000_000} megapixels."
                )
            probe.verify()  # structural check; the image can't be used after this
            image = Image.open(io.BytesIO(data))
            image.load()  # full decode: truncated or corrupt data fails here
    except ImageRejected:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageRejected("The file is not a valid JPEG, PNG or WebP image.") from None
    except (OSError, SyntaxError, ValueError):
        raise ImageRejected("The file is not a valid JPEG, PNG or WebP image.") from None
    return image


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )


def _to_srgb(image: Image.Image, source_format: str) -> Image.Image:
    """Pixels as RGB or RGBA in sRGB. A non-sRGB embedded ICC profile is converted, not dropped."""
    keep_alpha = source_format in ("PNG", "WEBP") and _has_alpha(image)
    target_mode = "RGBA" if keep_alpha else "RGB"
    icc = image.info.get("icc_profile")

    if icc:
        try:
            source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            is_srgb = "srgb" in (ImageCms.getProfileDescription(source) or "").lower()
        except (ImageCms.PyCMSError, OSError):
            source, is_srgb = None, True  # unreadable profile: treat the pixels as sRGB
        if source is not None and not is_srgb:
            if image.mode not in ("RGB", "RGBA", "CMYK"):
                image = image.convert("RGBA" if keep_alpha else "RGB")
            if image.mode == "CMYK":
                target_mode = "RGB"
            if image.mode == "RGBA" and target_mode == "RGB":
                image = image.convert("RGB")
            return ImageCms.profileToProfile(image, source, _SRGB, outputMode=target_mode)

    return image if image.mode == target_mode else image.convert(target_mode)


def _variant(image: Image.Image, side: int, quality: int) -> tuple[bytes, int, int]:
    width, height = image.size
    scale = min(1.0, side / max(width, height))
    if scale < 1.0:
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    # A fresh encode with no exif/icc/xmp arguments: only pixels are written.
    image.save(out, format="WEBP", quality=quality, method=4)
    return out.getvalue(), image.size[0], image.size[1]


def process_product_image(data: bytes) -> ProcessedImage:
    image = _open_checked(data)
    source_format = image.format or ""  # transposed copies don't keep .format
    image = ImageOps.exif_transpose(image)  # bake in the orientation before EXIF is dropped
    pixels = _to_srgb(image, source_format)
    # Copy the pixels into a new image so no metadata (info dict) can follow.
    clean = Image.new(pixels.mode, pixels.size)
    clean.paste(pixels)
    large, width, height = _variant(clean, LARGE_SIDE, LARGE_QUALITY)
    thumb, _, _ = _variant(clean, THUMB_SIDE, THUMB_QUALITY)
    return ProcessedImage(large=large, thumb=thumb, width=width, height=height)


# One semaphore per event loop (tests run several loops); held weakly so loops aren't kept.
_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


async def process_product_image_async(data: bytes) -> ProcessedImage:
    """process_product_image in the thread pool, at most MAX_CONCURRENT at a time."""
    loop = asyncio.get_running_loop()
    semaphore = _semaphores.setdefault(loop, asyncio.Semaphore(MAX_CONCURRENT))
    async with semaphore:
        return await run_in_threadpool(process_product_image, data)
