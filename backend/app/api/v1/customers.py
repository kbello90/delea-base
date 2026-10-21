"""Customer (CRM) routes.

Read, create, update: owner and staff. Delete: owner only (RLS: owner all; staff select,
insert, update). require_role gives a clear 403 up front, but RLS (user session as
`authenticated`) is the enforcement. Every query also filters on the caller's tenant.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from psycopg.rows import dict_row

from app.api.deps import CurrentClaims, DbConn, require_role
from app.core.errors import ConflictError, NotFoundError
from app.db.sql_helpers import like_pattern, patch_changes, update_statement
from app.models.customer import (
    NULLABLE_FIELDS,
    CustomerCreate,
    CustomerOut,
    CustomerType,
    CustomerUpdate,
    wholesale_rule_errors,
)

router = APIRouter(prefix="/customers", tags=["customers"])

OwnerOrStaff = Depends(require_role("owner", "staff"))
OwnerOnly = Depends(require_role("owner"))

_COLUMNS = (
    "id, tenant_id, customer_type::text as customer_type, first_name, last_name, email, phone, "
    "address_line1, address_line2, city, state, postal_code, country, marketing_opt_in, notes, "
    "company_name, tax_exempt, resale_certificate_number, wholesale_discount_bps, "
    "payment_terms::text as payment_terms, created_at, updated_at"
)
_CASTS = {"customer_type": "public.customer_type", "payment_terms": "public.payment_terms"}


@router.get("", response_model=list[CustomerOut], dependencies=[OwnerOrStaff])
async def list_customers(
    claims: CurrentClaims,
    conn: DbConn,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    customer_type: CustomerType | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CustomerOut]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            select {_COLUMNS}
              from public.customers
             where tenant_id = %(tenant_id)s
               and (%(type)s::text is null or customer_type::text = %(type)s)
               and (%(pattern)s::text is null
                    or first_name ilike %(pattern)s or last_name ilike %(pattern)s
                    or company_name ilike %(pattern)s or email ilike %(pattern)s)
             order by last_name nulls last, first_name, id
             limit %(limit)s offset %(offset)s
            """,
            {
                "tenant_id": claims.tenant_id,
                "type": customer_type.value if customer_type else None,
                "pattern": like_pattern(q) if q else None,
                "limit": limit,
                "offset": offset,
            },
        )
        return [CustomerOut.model_validate(row) for row in await cur.fetchall()]


@router.get("/{customer_id}", response_model=CustomerOut, dependencies=[OwnerOrStaff])
async def get_customer(customer_id: UUID, claims: CurrentClaims, conn: DbConn) -> CustomerOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_COLUMNS} from public.customers where id = %s and tenant_id = %s",
            (customer_id, claims.tenant_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Customer not found")
    return CustomerOut.model_validate(row)


@router.post(
    "",
    response_model=CustomerOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[OwnerOrStaff],
)
async def create_customer(body: CustomerCreate, claims: CurrentClaims, conn: DbConn) -> CustomerOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            insert into public.customers
              (tenant_id, customer_type, first_name, last_name, email, phone, address_line1,
               address_line2, city, state, postal_code, country, marketing_opt_in, notes,
               company_name, tax_exempt, resale_certificate_number, wholesale_discount_bps,
               payment_terms)
            values
              (%(tenant_id)s, %(customer_type)s::public.customer_type, %(first_name)s,
               %(last_name)s, %(email)s, %(phone)s, %(address_line1)s, %(address_line2)s,
               %(city)s, %(state)s, %(postal_code)s, %(country)s, %(marketing_opt_in)s,
               %(notes)s, %(company_name)s, %(tax_exempt)s, %(resale_certificate_number)s,
               %(wholesale_discount_bps)s, %(payment_terms)s::public.payment_terms)
            returning {_COLUMNS}
            """,
            {**body.model_dump(mode="json"), "tenant_id": claims.tenant_id},
        )
        row = await cur.fetchone()
    return CustomerOut.model_validate(row)


@router.patch("/{customer_id}", response_model=CustomerOut, dependencies=[OwnerOrStaff])
async def update_customer(
    customer_id: UUID, body: CustomerUpdate, claims: CurrentClaims, conn: DbConn
) -> CustomerOut:
    changes = patch_changes(body, NULLABLE_FIELDS)
    async with conn.cursor(row_factory=dict_row) as cur:
        # Lock the row so the merged-row check and the update see the same data.
        await cur.execute(
            f"select {_COLUMNS} from public.customers where id = %s and tenant_id = %s for update",
            (customer_id, claims.tenant_id),
        )
        current = await cur.fetchone()
        if current is None:
            raise NotFoundError("Customer not found")
        errors = wholesale_rule_errors({**current, **changes})
        if errors:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "; ".join(errors))

        query = update_statement("customers", changes, CustomerUpdate, _COLUMNS, casts=_CASTS)
        await cur.execute(query, {**changes, "key": customer_id, "tenant_id": claims.tenant_id})
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Customer not found")
    return CustomerOut.model_validate(row)


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[OwnerOnly])
async def delete_customer(customer_id: UUID, claims: CurrentClaims, conn: DbConn) -> Response:
    # Deleting would set customer_id to null on the customer's invoices (FK set null),
    # silently detaching their history, so a customer with invoices is kept.
    # Lock the row first: a concurrent insert of a referencing row must wait for this
    # transaction, so a reference can't appear between the count and the delete.
    cur = await conn.execute(
        "select id from public.customers where id = %s and tenant_id = %s for update",
        (customer_id, claims.tenant_id),
    )
    if await cur.fetchone() is None:
        raise NotFoundError("Customer not found")
    cur = await conn.execute(
        "select count(*) from public.invoices where customer_id = %s and tenant_id = %s",
        (customer_id, claims.tenant_id),
    )
    (references,) = await cur.fetchone()
    if references:
        raise ConflictError(
            f"This customer has {references} invoice(s); deleting would detach them "
            "from their history."
        )
    cur = await conn.execute(
        "delete from public.customers where id = %s and tenant_id = %s returning id",
        (customer_id, claims.tenant_id),
    )
    if await cur.fetchone() is None:
        raise NotFoundError("Customer not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
