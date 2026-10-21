"""Product image routes (owner only, like every product write).

Upload flow:
1. POST /products/{id}/images/uploads: the backend signs an upload URL in the private
   product-uploads bucket. The path is {tenant_id}/{product_id}/{upload_id}.{ext}, with the
   tenant from the verified token and the product checked in the caller's RLS session. The
   browser PUTs the file to that URL; no client can write to the buckets any other way.
2. POST /products/{id}/images: the register step. The raw path is rebuilt from the token
   (never taken from the request), the object is downloaded and processed
   (services.product_images, thread pool), the variants are written to the public
   product-images bucket, the row is inserted, and the raw upload is always deleted. If the
   insert fails, the variants are deleted again.

The database stores object paths only; URLs come from PRODUCT_IMAGES_BASE_URL.
"""

import logging
import uuid
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import CurrentClaims, DbConn, get_pool, require_role
from app.api.v1.products import _SELECT, image_out, product_out, remove_image_files
from app.core import storage
from app.core.errors import ConflictError, NotFoundError
from app.core.security import TokenClaims
from app.db.session import user_session
from app.models.product import ProductImageOut, ProductOut
from app.services.product_images import (
    MAX_BYTES,
    ImageRejected,
    process_product_image_async,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/products", tags=["product images"])

OwnerOnly = Depends(require_role("owner"))

MAX_IMAGES = 10  # also enforced by the product_images_limit trigger


class ImageType(StrEnum):
    jpeg = "image/jpeg"
    png = "image/png"
    webp = "image/webp"


_EXTENSIONS = {ImageType.jpeg: "jpg", ImageType.png: "png", ImageType.webp: "webp"}


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a tenant_id or path in the body is a 422

    content_type: ImageType


class UploadTicket(BaseModel):
    upload_id: UUID
    upload_url: str  # PUT the file here with the same Content-Type
    max_bytes: int


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: UUID
    content_type: ImageType


class ImageOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_ids: Annotated[list[UUID], Field(min_length=1, max_length=MAX_IMAGES)]


def raw_upload_path(claims: TokenClaims, product_id: UUID, upload_id: UUID, kind: ImageType) -> str:
    """Where a raw upload lives. The tenant comes from the verified token only."""
    return f"{claims.tenant_id}/{product_id}/{upload_id}.{_EXTENSIONS[kind]}"


async def _lock_product(conn: AsyncConnection, claims: TokenClaims, product_id: UUID) -> int:
    """Locks the product row (404 if it isn't the caller's) and returns its image count."""
    cur = await conn.execute(
        "select id from public.products where id = %s and tenant_id = %s for update",
        (product_id, claims.tenant_id),
    )
    if await cur.fetchone() is None:
        raise NotFoundError("Product not found")
    cur = await conn.execute(
        "select count(*) from public.product_images where product_id = %s and tenant_id = %s",
        (product_id, claims.tenant_id),
    )
    (count,) = await cur.fetchone()
    return count


def _ensure_room(count: int) -> None:
    if count >= MAX_IMAGES:
        raise ConflictError(f"A product can have at most {MAX_IMAGES} images.")


async def _product(conn: AsyncConnection, claims: TokenClaims, product_id: UUID) -> ProductOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"{_SELECT} where p.id = %s and p.tenant_id = %s", (product_id, claims.tenant_id)
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Product not found")
    return product_out(row)


def _storage_unavailable() -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, "Image storage is temporarily unavailable"
    )


async def _remove_quietly(bucket: str, paths: list[str]) -> None:
    try:
        await storage.remove(bucket, paths)
    except Exception:
        logger.exception("Could not remove %d object(s) from %s", len(paths), bucket)


# ---------------------------------------------------------------------------
# Upload and register
# ---------------------------------------------------------------------------
@router.post(
    "/{product_id}/images/uploads",
    response_model=UploadTicket,
    status_code=status.HTTP_201_CREATED,
    dependencies=[OwnerOnly],
)
async def create_upload(
    product_id: UUID, body: UploadRequest, claims: CurrentClaims, conn: DbConn
) -> UploadTicket:
    _ensure_room(await _lock_product(conn, claims, product_id))
    upload_id = uuid.uuid4()
    path = raw_upload_path(claims, product_id, upload_id, body.content_type)
    try:
        signed = await storage.create_signed_upload(storage.UPLOADS_BUCKET, path)
    except storage.StorageError as exc:
        raise _storage_unavailable() from exc
    return UploadTicket(upload_id=upload_id, upload_url=signed.url, max_bytes=MAX_BYTES)


async def _insert_image(
    conn: AsyncConnection,
    claims: TokenClaims,
    product_id: UUID,
    image_id: UUID,
    large_path: str,
    thumb_path: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    count = await _lock_product(conn, claims, product_id)
    _ensure_room(count)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            insert into public.product_images
              (id, tenant_id, product_id, position, is_primary, large_path, thumb_path,
               width, height)
            select %(id)s, %(tenant_id)s, %(product_id)s,
                   coalesce(max(position) + 1, 0), count(*) = 0,
                   %(large)s, %(thumb)s, %(width)s, %(height)s
              from public.product_images
             where product_id = %(product_id)s and tenant_id = %(tenant_id)s
            returning id, position, is_primary, large_path, thumb_path, width, height
            """,
            {
                "id": image_id,
                "tenant_id": claims.tenant_id,
                "product_id": product_id,
                "large": large_path,
                "thumb": thumb_path,
                "width": width,
                "height": height,
            },
        )
        row = await cur.fetchone()
    assert row is not None
    return row


@router.post(
    "/{product_id}/images",
    response_model=ProductImageOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[OwnerOnly],
)
async def register_image(
    product_id: UUID,
    body: RegisterRequest,
    claims: CurrentClaims,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> ProductImageOut:
    raw_path = raw_upload_path(claims, product_id, body.upload_id, body.content_type)
    # Short sessions only: no transaction or row lock is held while the file is processed.
    try:
        async with user_session(pool, claims) as conn:
            _ensure_room(await _lock_product(conn, claims, product_id))
        try:
            data = await storage.download(storage.UPLOADS_BUCKET, raw_path, MAX_BYTES)
        except storage.TooLarge:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.",
            ) from None
        except storage.StorageError as exc:
            raise _storage_unavailable() from exc
        if data is None:
            raise NotFoundError("Upload not found. Upload the file first, then register it.")
        try:
            processed = await process_product_image_async(data)
        except ImageRejected as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    finally:
        # The raw upload (which may carry GPS metadata) never outlives this request.
        await _remove_quietly(storage.UPLOADS_BUCKET, [raw_path])

    image_id = uuid.uuid4()
    prefix = f"{claims.tenant_id}/{product_id}/{image_id}"
    large_path, thumb_path = f"{prefix}-1200.webp", f"{prefix}-400.webp"
    uploaded: list[str] = []
    try:
        for path, data in ((large_path, processed.large), (thumb_path, processed.thumb)):
            try:
                await storage.upload(storage.IMAGES_BUCKET, path, data, "image/webp")
            except storage.StorageError as exc:
                raise _storage_unavailable() from exc
            uploaded.append(path)
        async with user_session(pool, claims) as conn:
            row = await _insert_image(
                conn,
                claims,
                product_id,
                image_id,
                large_path,
                thumb_path,
                processed.width,
                processed.height,
            )
    except BaseException:
        # Nothing was recorded: the stored variants would be orphans.
        await _remove_quietly(storage.IMAGES_BUCKET, uploaded)
        raise
    return image_out(row)


# ---------------------------------------------------------------------------
# Primary, order, delete
# ---------------------------------------------------------------------------
@router.post(
    "/{product_id}/images/{image_id}/primary",
    response_model=ProductOut,
    dependencies=[OwnerOnly],
)
async def set_primary_image(
    product_id: UUID, image_id: UUID, claims: CurrentClaims, conn: DbConn
) -> ProductOut:
    await _lock_product(conn, claims, product_id)
    cur = await conn.execute(
        "select 1 from public.product_images where id = %s and product_id = %s and tenant_id = %s",
        (image_id, product_id, claims.tenant_id),
    )
    if await cur.fetchone() is None:
        raise NotFoundError("Image not found")
    # Clear the old primary first: the one-primary index is checked per statement.
    await conn.execute(
        "update public.product_images set is_primary = false "
        "where product_id = %s and tenant_id = %s and is_primary and id <> %s",
        (product_id, claims.tenant_id, image_id),
    )
    await conn.execute(
        "update public.product_images set is_primary = true where id = %s and tenant_id = %s",
        (image_id, claims.tenant_id),
    )
    return await _product(conn, claims, product_id)


@router.put("/{product_id}/images/order", response_model=ProductOut, dependencies=[OwnerOnly])
async def reorder_images(
    product_id: UUID, body: ImageOrder, claims: CurrentClaims, conn: DbConn
) -> ProductOut:
    await _lock_product(conn, claims, product_id)
    cur = await conn.execute(
        "select id from public.product_images where product_id = %s and tenant_id = %s",
        (product_id, claims.tenant_id),
    )
    current = {row[0] for row in await cur.fetchall()}
    if len(set(body.image_ids)) != len(body.image_ids) or set(body.image_ids) != current:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "image_ids must list each of this product's images exactly once",
        )
    # Positions are unique per product but deferred, so the new order can be written at once.
    await conn.execute(
        """
        update public.product_images i
           set position = o.ord - 1
          from unnest(%s::uuid[]) with ordinality as o (id, ord)
         where i.id = o.id and i.product_id = %s and i.tenant_id = %s
        """,
        (body.image_ids, product_id, claims.tenant_id),
    )
    return await _product(conn, claims, product_id)


@router.delete(
    "/{product_id}/images/{image_id}", response_model=ProductOut, dependencies=[OwnerOnly]
)
async def delete_image(
    product_id: UUID,
    image_id: UUID,
    claims: CurrentClaims,
    conn: DbConn,
    background: BackgroundTasks,
) -> ProductOut:
    await _lock_product(conn, claims, product_id)
    cur = await conn.execute(
        "delete from public.product_images where id = %s and product_id = %s and tenant_id = %s "
        "returning large_path, thumb_path, is_primary",
        (image_id, product_id, claims.tenant_id),
    )
    deleted = await cur.fetchone()
    if deleted is None:
        raise NotFoundError("Image not found")
    large_path, thumb_path, was_primary = deleted
    # Close the gap in positions, and promote the first remaining image if needed.
    await conn.execute(
        """
        update public.product_images i
           set position = r.rn - 1,
               is_primary = case when %(promote)s then r.rn = 1 else i.is_primary end
          from (select id, row_number() over (order by position) as rn
                  from public.product_images
                 where product_id = %(product_id)s and tenant_id = %(tenant_id)s) r
         where i.id = r.id
        """,
        {"promote": was_primary, "product_id": product_id, "tenant_id": claims.tenant_id},
    )
    background.add_task(remove_image_files, [large_path, thumb_path])
    return await _product(conn, claims, product_id)
