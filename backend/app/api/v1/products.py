"""Product catalog routes.

Reads: owner and staff. Writes: owner only. require_role gives a clear 403 up front, but RLS
(user session as `authenticated`) is the enforcement. Every query also filters on the
caller's tenant from the verified token; tenant_id is never taken from the request.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from psycopg.rows import dict_row

from app.api.deps import CurrentClaims, DbConn, require_role
from app.core.errors import ConflictError, NotFoundError
from app.db.sql_helpers import like_pattern, patch_changes, update_statement
from app.models.product import NULLABLE_FIELDS, ProductCreate, ProductOut, ProductUpdate

router = APIRouter(prefix="/products", tags=["products"])

ReadRoles = Depends(require_role("owner", "staff"))
OwnerOnly = Depends(require_role("owner"))

_COLUMNS = (
    "id, tenant_id, sku, name, description, metal::text as metal, price_cents, "
    "sale_price_cents, sale_ends_at, stock_qty, low_stock_threshold, image_urls, icon, "
    "is_published, sort_order, created_at, updated_at"
)


@router.get("", response_model=list[ProductOut], dependencies=[ReadRoles])
async def list_products(
    claims: CurrentClaims,
    conn: DbConn,
    is_published: bool | None = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProductOut]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            select {_COLUMNS}
              from public.products
             where tenant_id = %(tenant_id)s
               and (%(is_published)s::boolean is null or is_published = %(is_published)s)
               and (%(pattern)s::text is null
                    or name ilike %(pattern)s or sku ilike %(pattern)s)
             order by sort_order, name, id
             limit %(limit)s offset %(offset)s
            """,
            {
                "tenant_id": claims.tenant_id,
                "is_published": is_published,
                "pattern": like_pattern(q) if q else None,
                "limit": limit,
                "offset": offset,
            },
        )
        return [ProductOut.model_validate(row) for row in await cur.fetchall()]


@router.get("/{product_id}", response_model=ProductOut, dependencies=[ReadRoles])
async def get_product(product_id: UUID, claims: CurrentClaims, conn: DbConn) -> ProductOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_COLUMNS} from public.products where id = %s and tenant_id = %s",
            (product_id, claims.tenant_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Product not found")
    return ProductOut.model_validate(row)


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
               sale_ends_at, stock_qty, low_stock_threshold, image_urls, icon,
               is_published, sort_order)
            values
              (%(tenant_id)s, %(sku)s, %(name)s, %(description)s,
               %(metal)s::public.metal_type, %(price_cents)s, %(sale_price_cents)s,
               %(sale_ends_at)s, %(stock_qty)s, %(low_stock_threshold)s, %(image_urls)s,
               %(icon)s, %(is_published)s, %(sort_order)s)
            returning {_COLUMNS}
            """,
            {**values, "tenant_id": claims.tenant_id},
        )
        row = await cur.fetchone()
    return ProductOut.model_validate(row)


@router.patch("/{product_id}", response_model=ProductOut, dependencies=[OwnerOnly])
async def update_product(
    product_id: UUID, body: ProductUpdate, claims: CurrentClaims, conn: DbConn
) -> ProductOut:
    changes = patch_changes(body, NULLABLE_FIELDS)
    query = update_statement(
        "products", changes, ProductUpdate, _COLUMNS, casts={"metal": "public.metal_type"}
    )
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, {**changes, "key": product_id, "tenant_id": claims.tenant_id})
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Product not found")
    return ProductOut.model_validate(row)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[OwnerOnly])
async def delete_product(product_id: UUID, claims: CurrentClaims, conn: DbConn) -> Response:
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
        "delete from public.products where id = %s and tenant_id = %s returning id",
        (product_id, claims.tenant_id),
    )
    if await cur.fetchone() is None:
        raise NotFoundError("Product not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
