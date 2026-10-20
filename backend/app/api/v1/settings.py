"""Brand config, company/tax info, and CPA invites (TRD §3.4).

company_settings: owner only (it holds the EIN and the Stripe account reference).
brand_config: read by owner and staff, written by the owner (RLS: owner all, staff select).
require_role gives a clear 403 up front, but RLS (user session as `authenticated`) is the
enforcement. Every query also filters on the caller's tenant. CPA invites come later.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg.rows import dict_row

from app.api.deps import CurrentClaims, DbConn, require_role
from app.core.errors import NotFoundError
from app.db.sql_helpers import patch_changes, update_statement
from app.models.settings import (
    BRAND_NULLABLE,
    COMPANY_NULLABLE,
    BrandConfigOut,
    BrandConfigUpdate,
    CompanySettingsOut,
    CompanySettingsUpdate,
    language_rule_errors,
)

router = APIRouter(prefix="/settings", tags=["settings"])

OwnerOnly = Depends(require_role("owner"))
OwnerOrStaff = Depends(require_role("owner", "staff"))

_COMPANY_COLUMNS = (
    "tenant_id, legal_name, ein, address, currency::text as currency, default_language, "
    "enabled_languages, default_tax_rate_bps, stripe_account_id, next_quote_number, "
    "next_invoice_number, updated_at"
)
_BRAND_COLUMNS = (
    "tenant_id, business_name, tagline, primary_color, accent_color, logo_url, hero_title, "
    "hero_subtitle, updated_at"
)


@router.get("/company", response_model=CompanySettingsOut, dependencies=[OwnerOnly])
async def get_company_settings(claims: CurrentClaims, conn: DbConn) -> CompanySettingsOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_COMPANY_COLUMNS} from public.company_settings where tenant_id = %s",
            (claims.tenant_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Company settings not found")
    return CompanySettingsOut.model_validate(row)


@router.patch("/company", response_model=CompanySettingsOut, dependencies=[OwnerOnly])
async def update_company_settings(
    body: CompanySettingsUpdate, claims: CurrentClaims, conn: DbConn
) -> CompanySettingsOut:
    changes = patch_changes(body, COMPANY_NULLABLE)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select default_language, enabled_languages from public.company_settings "
            "where tenant_id = %s for update",
            (claims.tenant_id,),
        )
        current = await cur.fetchone()
        if current is None:
            raise NotFoundError("Company settings not found")
        errors = language_rule_errors({**current, **changes})
        if errors:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "; ".join(errors))

        query = update_statement(
            "company_settings",
            changes,
            CompanySettingsUpdate,
            _COMPANY_COLUMNS,
            key_column="tenant_id",
        )
        await cur.execute(
            query, {**changes, "key": claims.tenant_id, "tenant_id": claims.tenant_id}
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Company settings not found")
    return CompanySettingsOut.model_validate(row)


@router.get("/brand", response_model=BrandConfigOut, dependencies=[OwnerOrStaff])
async def get_brand_config(claims: CurrentClaims, conn: DbConn) -> BrandConfigOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_BRAND_COLUMNS} from public.brand_config where tenant_id = %s",
            (claims.tenant_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Brand config not found")
    return BrandConfigOut.model_validate(row)


@router.patch("/brand", response_model=BrandConfigOut, dependencies=[OwnerOnly])
async def update_brand_config(
    body: BrandConfigUpdate, claims: CurrentClaims, conn: DbConn
) -> BrandConfigOut:
    changes = patch_changes(body, BRAND_NULLABLE)
    query = update_statement(
        "brand_config", changes, BrandConfigUpdate, _BRAND_COLUMNS, key_column="tenant_id"
    )
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            query, {**changes, "key": claims.tenant_id, "tenant_id": claims.tenant_id}
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Brand config not found")
    return BrandConfigOut.model_validate(row)
