"""Supabase Storage over HTTP with the service-role key (product images, expense receipts,
brand logos).

The key comes from Settings (backend/.env locally; an App Service setting from a @secure()
Bicep parameter when hosted). It is used only for Storage HTTP calls from the backend: no SQL
runs with it, so this is not a database service session, and it never reaches the browser.
Every database read and write for images still runs in the caller's RLS user session.
"""

from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.core.config import get_settings

UPLOADS_BUCKET = "product-uploads"
IMAGES_BUCKET = "product-images"
RECEIPTS_BUCKET = "expense-receipts"  # private: read only through short-lived signed URLs
BRAND_BUCKET = "brand-assets"  # public: processed logos, written only by the backend


class StorageError(Exception):
    """Storage answered with an unexpected status."""


@dataclass(frozen=True)
class SignedUpload:
    url: str  # absolute; the browser PUTs the file here, no other credentials needed
    path: str


class TooLarge(Exception):
    """The object is bigger than the caller's limit."""


def _base() -> str:
    return f"{get_settings().supabase_url.rstrip('/')}/storage/v1"


def _headers() -> dict[str, str]:
    key = get_settings().supabase_service_role_key
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def _object(bucket: str, path: str) -> str:
    return f"{bucket}/{quote(path)}"


async def create_signed_upload(bucket: str, path: str) -> SignedUpload:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{_base()}/object/upload/sign/{_object(bucket, path)}", headers=_headers()
        )
    if r.status_code != 200:
        raise StorageError(f"sign upload failed: {r.status_code}")
    return SignedUpload(url=f"{_base()}{r.json()['url']}", path=path)


async def create_signed_url(bucket: str, path: str, expires_in: int) -> str:
    """An absolute URL that reads the object for `expires_in` seconds, with no other
    credentials. The object must exist (404 from Storage raises StorageError)."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{_base()}/object/sign/{_object(bucket, path)}",
            headers=_headers(),
            json={"expiresIn": expires_in},
        )
    if r.status_code != 200:
        raise StorageError(f"sign url failed: {r.status_code}")
    return f"{_base()}{r.json()['signedURL']}"


async def download(bucket: str, path: str, max_bytes: int) -> bytes | None:
    """The object's bytes, or None if it doesn't exist. Raises TooLarge past max_bytes."""
    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream(
            "GET", f"{_base()}/object/authenticated/{_object(bucket, path)}", headers=_headers()
        ) as r:
            if r.status_code in (400, 404):
                return None
            if r.status_code != 200:
                raise StorageError(f"download failed: {r.status_code}")
            data = bytearray()
            async for chunk in r.aiter_bytes():
                data += chunk
                if len(data) > max_bytes:
                    raise TooLarge
            return bytes(data)


async def upload(bucket: str, path: str, data: bytes, content_type: str) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{_base()}/object/{_object(bucket, path)}",
            headers={**_headers(), "Content-Type": content_type, "x-upsert": "false"},
            content=data,
        )
    if r.status_code != 200:
        raise StorageError(f"upload failed: {r.status_code}")


async def remove(bucket: str, paths: list[str]) -> None:
    if not paths:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.request(
            "DELETE", f"{_base()}/object/{bucket}", headers=_headers(), json={"prefixes": paths}
        )
    if r.status_code != 200:
        raise StorageError(f"remove failed: {r.status_code}")
