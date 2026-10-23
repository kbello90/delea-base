"""Expense receipt checks: bytes in, the bytes to store out. No I/O.

- PDF: stored unchanged, but the file must start with %PDF-.
- JPEG, PNG, WebP: the real content must be the declared format (the same check and 25 MP
  guard as product images). The EXIF orientation is applied, then the pixels are re-encoded in
  the SAME format (JPEG quality 90, PNG, WebP quality 90): EXIF (including GPS), XMP and ICC
  metadata never reach the stored file. A non-sRGB colour profile is converted to sRGB first.
- At most MAX_BYTES in, whatever the type.

CPU-bound for images: call process_receipt_async (thread pool, capped concurrency).
"""

import asyncio
import io
import weakref
from dataclasses import dataclass
from enum import StrEnum

from PIL import Image, ImageOps
from starlette.concurrency import run_in_threadpool

from app.services.product_images import ImageRejected, open_image_checked, to_srgb

MAX_BYTES = 10 * 1024 * 1024
JPEG_QUALITY = 90
WEBP_QUALITY = 90
MAX_CONCURRENT = 2


class ReceiptType(StrEnum):
    jpeg = "image/jpeg"
    png = "image/png"
    webp = "image/webp"
    pdf = "application/pdf"


EXTENSIONS = {
    ReceiptType.jpeg: "jpg",
    ReceiptType.png: "png",
    ReceiptType.webp: "webp",
    ReceiptType.pdf: "pdf",
}
_PIL_FORMATS = {ReceiptType.jpeg: "JPEG", ReceiptType.png: "PNG", ReceiptType.webp: "WEBP"}


class ReceiptRejected(ValueError):
    """The file isn't an acceptable receipt. The message is shown to the user."""


@dataclass(frozen=True)
class ProcessedReceipt:
    data: bytes
    content_type: ReceiptType


def _image(data: bytes, declared: ReceiptType) -> bytes:
    try:
        image = open_image_checked(data, MAX_BYTES)
    except ImageRejected as exc:
        raise ReceiptRejected(str(exc)) from None
    source_format = image.format or ""
    if source_format != _PIL_FORMATS[declared]:
        raise ReceiptRejected(
            f"The file's content doesn't match its type: it isn't a {EXTENSIONS[declared].upper()} "
            "image."
        )
    image = ImageOps.exif_transpose(image)  # bake in the orientation before EXIF is dropped
    pixels = to_srgb(image, source_format)
    clean = Image.new(pixels.mode, pixels.size)  # pixels only: no info dict follows
    clean.paste(pixels)
    out = io.BytesIO()
    if declared is ReceiptType.jpeg:
        clean.save(out, format="JPEG", quality=JPEG_QUALITY)
    elif declared is ReceiptType.png:
        clean.save(out, format="PNG", optimize=True)
    else:
        clean.save(out, format="WEBP", quality=WEBP_QUALITY, method=4)
    return out.getvalue()


def process_receipt(data: bytes, declared: ReceiptType) -> ProcessedReceipt:
    if len(data) > MAX_BYTES:
        raise ReceiptRejected(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise ReceiptRejected("The file is empty.")
    if declared is ReceiptType.pdf:
        if not data.startswith(b"%PDF-"):
            raise ReceiptRejected("The file's content doesn't match its type: it isn't a PDF.")
        return ProcessedReceipt(data, ReceiptType.pdf)
    return ProcessedReceipt(_image(data, declared), declared)


_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


async def process_receipt_async(data: bytes, declared: ReceiptType) -> ProcessedReceipt:
    """process_receipt in the thread pool, at most MAX_CONCURRENT at a time."""
    loop = asyncio.get_running_loop()
    semaphore = _semaphores.setdefault(loop, asyncio.Semaphore(MAX_CONCURRENT))
    async with semaphore:
        return await run_in_threadpool(process_receipt, data, declared)
