"""Product catalog routes.

Reads: owner and staff. Writes: owner only. require_role gives a clear 403 up front, but RLS
(user session as `authenticated`) is the enforcement. Every query also filters on the
caller's tenant from the verified token; tenant_id is never taken from the request.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from psycopg import sql
from psycopg.rows import dict_row

from app.api.deps import CurrentClaims, DbConn, require_role
from app.core.errors import NotFoundError
from app.models.product import NULLABLE_FIELDS, ProductCreate, ProductOut, ProductUpdate

router = APIRouter(prefix="/products", tags=["products"])

ReadRoles = Depends(require_role("owner", "staff"))
OwnerOnly = Depends(require_role("owner"))

_COLUMNS = (
    "id, tenant_id, sku, name, description, metal::text as metal, price_cents, "
    "sale_price_cents, sale_ends_at, stock_qty, low_stock_threshold, image_urls, icon, "
    "is_published, sort_order, created_at, updated_at"
)


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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
                "pattern": _like_pattern(q) if q else None,
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
    changes = body.model_dump(mode="json", exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No fields to update")
    null_not_allowed = sorted(
        k for k, v in changes.items() if v is None and k not in NULLABLE_FIELDS
    )
    if null_not_allowed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"These fields cannot be null: {', '.join(null_not_allowed)}",
        )

    # Column names come from the model's own fields, never from the request keys directly.
    assignments = [
        sql.SQL("{} = {}").format(
            sql.Identifier(field),
            sql.SQL("{}::public.metal_type").format(sql.Placeholder(field))
            if field == "metal"
            else sql.Placeholder(field),
        )
        for field in changes
        if field in ProductUpdate.model_fields
    ]
    query = sql.SQL(
        "update public.products set {assignments} "
        "where id = %(id)s and tenant_id = %(tenant_id)s returning " + _COLUMNS
    ).format(assignments=sql.SQL(", ").join(assignments))

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, {**changes, "id": product_id, "tenant_id": claims.tenant_id})
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Product not found")
    return ProductOut.model_validate(row)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[OwnerOnly])
async def delete_product(product_id: UUID, claims: CurrentClaims, conn: DbConn) -> Response:
    cur = await conn.execute(
        "delete from public.products where id = %s and tenant_id = %s returning id",
        (product_id, claims.tenant_id),
    )
    if await cur.fetchone() is None:
        raise NotFoundError("Product not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
