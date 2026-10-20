"""Raw materials routes.

Reads: owner and staff. Writes: owner only (RLS: owner all, staff select). require_role gives a
clear 403 up front, but RLS (user session as `authenticated`) is the enforcement. Every query
also filters on the caller's tenant from the verified token.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from psycopg.rows import dict_row

from app.api.deps import CurrentClaims, DbConn, require_role
from app.core.errors import NotFoundError
from app.db.sql_helpers import like_pattern, patch_changes, update_statement
from app.models.material import NULLABLE_FIELDS, MaterialCreate, MaterialOut, MaterialUpdate

router = APIRouter(prefix="/materials", tags=["materials"])

ReadRoles = Depends(require_role("owner", "staff"))
OwnerOnly = Depends(require_role("owner"))

# "Low" is strictly below the threshold: the same rule as ai.materials_status and the
# material_low trigger.
_COLUMNS = (
    "id, tenant_id, name, unit, unit_cost_cents, qty_on_hand, reorder_threshold, "
    "qty_on_hand < reorder_threshold as is_low, created_at, updated_at"
)


@router.get("", response_model=list[MaterialOut], dependencies=[ReadRoles])
async def list_materials(
    claims: CurrentClaims,
    conn: DbConn,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    low_stock: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MaterialOut]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            select {_COLUMNS}
              from public.materials
             where tenant_id = %(tenant_id)s
               and (%(pattern)s::text is null or name ilike %(pattern)s)
               and (%(low)s::boolean is null
                    or (qty_on_hand < reorder_threshold) = %(low)s)
             order by name, id
             limit %(limit)s offset %(offset)s
            """,
            {
                "tenant_id": claims.tenant_id,
                "pattern": like_pattern(q) if q else None,
                "low": low_stock,
                "limit": limit,
                "offset": offset,
            },
        )
        return [MaterialOut.model_validate(row) for row in await cur.fetchall()]


@router.get("/{material_id}", response_model=MaterialOut, dependencies=[ReadRoles])
async def get_material(material_id: UUID, claims: CurrentClaims, conn: DbConn) -> MaterialOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_COLUMNS} from public.materials where id = %s and tenant_id = %s",
            (material_id, claims.tenant_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Material not found")
    return MaterialOut.model_validate(row)


@router.post(
    "", response_model=MaterialOut, status_code=status.HTTP_201_CREATED, dependencies=[OwnerOnly]
)
async def create_material(body: MaterialCreate, claims: CurrentClaims, conn: DbConn) -> MaterialOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            insert into public.materials
              (tenant_id, name, unit, unit_cost_cents, qty_on_hand, reorder_threshold)
            values
              (%(tenant_id)s, %(name)s, %(unit)s, %(unit_cost_cents)s, %(qty_on_hand)s,
               %(reorder_threshold)s)
            returning {_COLUMNS}
            """,
            {**body.model_dump(), "tenant_id": claims.tenant_id},
        )
        row = await cur.fetchone()
    return MaterialOut.model_validate(row)


@router.patch("/{material_id}", response_model=MaterialOut, dependencies=[OwnerOnly])
async def update_material(
    material_id: UUID, body: MaterialUpdate, claims: CurrentClaims, conn: DbConn
) -> MaterialOut:
    patch_changes(body, NULLABLE_FIELDS)  # 422 for an empty body or a null required field
    changes = body.model_dump(exclude_unset=True)  # keep Decimal values exact
    query = update_statement("materials", changes, MaterialUpdate, _COLUMNS)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, {**changes, "key": material_id, "tenant_id": claims.tenant_id})
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Material not found")
    return MaterialOut.model_validate(row)


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[OwnerOnly])
async def delete_material(material_id: UUID, claims: CurrentClaims, conn: DbConn) -> Response:
    cur = await conn.execute(
        "delete from public.materials where id = %s and tenant_id = %s returning id",
        (material_id, claims.tenant_id),
    )
    if await cur.fetchone() is None:
        raise NotFoundError("Material not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
