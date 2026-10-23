"""Brand logo processing: bytes in, one WebP out. No I/O.

The same checks as product images (real JPEG, PNG or WebP content, 25 MP guard), with a 2 MB
input limit. The EXIF orientation is applied, a non-sRGB profile is converted, transparency
is kept, and the pixels are re-encoded with no metadata: one WebP of at most LOGO_SIDE px on
the longest side (never upscaled). SVG is never accepted: it can carry script.

CPU-bound: call process_logo_async (thread pool, capped concurrency).
"""

import asyncio
import io
import weakref
from dataclasses import dataclass

from PIL import Image, ImageOps
from starlette.concurrency import run_in_threadpool

from app.services.product_images import ImageRejected, open_image_checked, to_srgb

MAX_BYTES = 2 * 1024 * 1024
LOGO_SIDE = 512
LOGO_QUALITY = 90
MAX_CONCURRENT = 2


@dataclass(frozen=True)
class ProcessedLogo:
    data: bytes
    width: int
    height: int


def process_logo(data: bytes) -> ProcessedLogo:
    """Raises ImageRejected (message shown to the user) for anything that isn't acceptable."""
    image = open_image_checked(data, MAX_BYTES)
    source_format = image.format or ""
    image = ImageOps.exif_transpose(image)  # bake in the orientation before EXIF is dropped
    pixels = to_srgb(image, source_format)
    clean = Image.new(pixels.mode, pixels.size)  # pixels only: no info dict follows
    clean.paste(pixels)
    width, height = clean.size
    scale = min(1.0, LOGO_SIDE / max(width, height))
    if scale < 1.0:
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        clean = clean.resize(size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    clean.save(out, format="WEBP", quality=LOGO_QUALITY, method=4)
    return ProcessedLogo(out.getvalue(), clean.size[0], clean.size[1])


_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


async def process_logo_async(data: bytes) -> ProcessedLogo:
    loop = asyncio.get_running_loop()
    semaphore = _semaphores.setdefault(loop, asyncio.Semaphore(MAX_CONCURRENT))
    async with semaphore:
        return await run_in_threadpool(process_logo, data)


__all__ = ["MAX_BYTES", "ImageRejected", "ProcessedLogo", "process_logo", "process_logo_async"]
