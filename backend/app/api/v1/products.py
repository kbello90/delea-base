"""Product catalog routes.

Reads: owner and staff. Writes: owner only. require_role gives a clear 403 up front, but RLS
(user session as `authenticated`) is the enforcement. Every query also filters on the
caller's tenant from the verified token; tenant_id is never taken from the request.

Each product carries its images (ordered); see product_images.py for the image endpoints.
"""

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from psycopg.rows import dict_row

from app.api.deps import CurrentClaims, DbConn, require_role
from app.core import storage
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.db.sql_helpers import like_pattern, patch_changes, update_statement
from app.models.product import (
    NULLABLE_FIELDS,
    MetalType,
    ProductCreate,
    ProductImageOut,
    ProductOut,
    ProductUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/products", tags=["products"])

ReadRoles = Depends(require_role("owner", "staff"))
OwnerOnly = Depends(require_role("owner"))

_COLUMNS = (
    "id, tenant_id, sku, name, description, metal::text as metal, price_cents, "
    "sale_price_cents, sale_ends_at, stock_qty, low_stock_threshold, icon, "
    "is_published, sort_order, created_at, updated_at"
)


def images_column(products_ref: str) -> str:
    """A json array of the product's images, ordered by position (RLS applies to it too)."""
    return f"""coalesce((
        select json_agg(json_build_object(
                 'id', i.id, 'position', i.position, 'is_primary', i.is_primary,
                 'large_path', i.large_path, 'thumb_path', i.thumb_path,
                 'width', i.width, 'height', i.height) order by i.position)
          from public.product_images i
         where i.product_id = {products_ref}.id and i.tenant_id = {products_ref}.tenant_id
      ), '[]'::json) as images"""


def image_out(image: dict[str, Any]) -> ProductImageOut:
    base = get_settings().product_images_public_base
    return ProductImageOut(
        id=image["id"],
        position=image["position"],
        is_primary=image["is_primary"],
        url=f"{base}/{image['large_path']}",
        thumb_url=f"{base}/{image['thumb_path']}",
        width=image["width"],
        height=image["height"],
    )


def product_out(row: dict[str, Any]) -> ProductOut:
    images = [image_out(image) for image in row.pop("images", None) or []]
    return ProductOut.model_validate({**row, "images": images})


_SELECT = f"select {_COLUMNS}, {images_column('p')} from public.products p"


@router.get("", response_model=list[ProductOut], dependencies=[ReadRoles])
async def list_products(
    claims: CurrentClaims,
    conn: DbConn,
    is_published: bool | None = None,
    metal: MetalType | None = None,
    low_stock: Annotated[
        bool | None, Query(description="true: stock below the low-stock threshold")
    ] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProductOut]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            {_SELECT}
             where p.tenant_id = %(tenant_id)s
               and (%(is_published)s::boolean is null or p.is_published = %(is_published)s)
               and (%(metal)s::text is null or p.metal = %(metal)s::public.metal_type)
               and (%(low)s::boolean is null
                    or (p.stock_qty < p.low_stock_threshold) = %(low)s)
               and (%(pattern)s::text is null
                    or p.name ilike %(pattern)s or p.sku ilike %(pattern)s)
             order by p.sort_order, p.name, p.id
             limit %(limit)s offset %(offset)s
            """,
            {
                "tenant_id": claims.tenant_id,
                "is_published": is_published,
                "metal": metal.value if metal else None,
                "low": low_stock,
                "pattern": like_pattern(q) if q else None,
                "limit": limit,
                "offset": offset,
            },
        )
        return [product_out(row) for row in await cur.fetchall()]


@router.get("/{product_id}", response_model=ProductOut, dependencies=[ReadRoles])
async def get_product(product_id: UUID, claims: CurrentClaims, conn: DbConn) -> ProductOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"{_SELECT} where p.id = %s and p.tenant_id = %s", (product_id, claims.tenant_id)
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Product not found")
    return product_out(row)


@router.post(
    "",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[OwnerOnly],
)
async def create_product(body: ProductCreate, claims: CurrentClaims, conn: DbConn) -> ProductOut:
    values = body.model_dump(mode="json")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            insert into public.products
              (tenant_id, sku, name, description, metal, price_cents, sale_price_cents,
               sale_ends_at, stock_qty, low_stock_threshold, icon, is_published, sort_order)
            values
              (%(tenant_id)s, %(sku)s, %(name)s, %(description)s,
               %(metal)s::public.metal_type, %(price_cents)s, %(sale_price_cents)s,
               %(sale_ends_at)s, %(stock_qty)s, %(low_stock_threshold)s,
               %(icon)s, %(is_published)s, %(sort_order)s)
            returning {_COLUMNS}
            """,
            {**values, "tenant_id": claims.tenant_id},
        )
        row = await cur.fetchone()
    return product_out(row)  # a new product has no images


@router.patch("/{product_id}", response_model=ProductOut, dependencies=[OwnerOnly])
async def update_product(
    product_id: UUID, body: ProductUpdate, claims: CurrentClaims, conn: DbConn
) -> ProductOut:
    changes = patch_changes(body, NULLABLE_FIELDS)
    query = update_statement(
        "products",
        changes,
        ProductUpdate,
        f"{_COLUMNS}, {images_column('products')}",
        casts={"metal": "public.metal_type"},
    )
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, {**changes, "key": product_id, "tenant_id": claims.tenant_id})
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Product not found")
    return product_out(row)


async def remove_image_files(paths: list[str]) -> None:
    """Background task (after the transaction committed): delete image files; log failures."""
    try:
        await storage.remove(storage.IMAGES_BUCKET, paths)
    except Exception:
        logger.exception("Could not remove %d product image file(s)", len(paths))


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[OwnerOnly])
async def delete_product(
    product_id: UUID, claims: CurrentClaims, conn: DbConn, background: BackgroundTasks
) -> Response:
    # Lock the row first: a concurrent insert of a referencing row must wait for this
    # transaction, so a reference can't appear between the count and the delete.
    cur = await conn.execute(
        "select id from public.products where id = %s and tenant_id = %s for update",
        (product_id, claims.tenant_id),
    )
    if await cur.fetchone() is None:
        raise NotFoundError("Product not found")
    cur = await conn.execute(
        "select count(*) from public.invoice_line_items where product_id = %s and tenant_id = %s",
        (product_id, claims.tenant_id),
    )
    (references,) = await cur.fetchone()
    if references:
        raise ConflictError(
            f"This product appears on {references} invoice line(s). "
            "Unpublish it instead (set is_published to false)."
        )
    cur = await conn.execute(
        "select large_path, thumb_path from public.product_images "
        "where product_id = %s and tenant_id = %s",
        (product_id, claims.tenant_id),
    )
    paths = [path for row in await cur.fetchall() for path in row]
    cur = await conn.execute(
        "delete from public.products where id = %s and tenant_id = %s returning id",
        (product_id, claims.tenant_id),
    )
    if await cur.fetchone() is None:
        raise NotFoundError("Product not found")
    # The image rows cascade with the product; the files go once the delete has committed
    # (the DbConn transaction commits before the response, and tasks run after it).
    if paths:
        background.add_task(remove_image_files, paths)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
