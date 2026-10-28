"""Brand config, company/tax info, the brand logo and the storefront settings (TRD §6.9).

company_settings: owner only (it holds the tax ID and the Stripe account reference). The
currency is read-only here: it is set at provisioning and the database refuses a change once
the tenant has data. A default tax-rate change applies to new quotes only: every invoice keeps
the rate it was created with (invoices.tax_rate_bps), and replacing a quote's lines reuses it.
brand_config: read by owner and staff, written by the owner (RLS: owner all, staff select).
storefront_settings: owner only here (everything in it is public on the storefront: the
on/off switch, the contact email and the pages).
currency: read by owner, staff and cpa through current_tenant_currency() (SECURITY DEFINER),
so staff and cpa learn the currency without reading company_settings.
require_role gives a clear 403 up front, but RLS (user session as `authenticated`) is the
enforcement. Every query also filters on the caller's tenant.

Logo upload (owner only), the product-image pipeline:
1. POST /settings/brand/logo/uploads signs an upload URL in the private product-uploads
   bucket at {tenant_id}/brand/{upload_id}.{ext}; the tenant comes from the verified token.
2. POST /settings/brand/logo rebuilds that path from the token, checks and processes the file
   (services.brand_logo: real JPEG/PNG/WebP, 2 MB, 25 MP, no metadata, one WebP of at most
   512 px), writes {tenant_id}/logo-{uuid}.webp to the public brand-assets bucket and records
   it; the raw upload is always deleted, and a replaced logo is deleted after the commit.
The database stores the path only; the URL comes from BRAND_ASSETS_BASE_URL.
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.api.deps import CurrentClaims, DbConn, get_pool, require_role
from app.core import storage
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.security import TokenClaims
from app.core.storefront_hosts import storefront_url
from app.db.session import user_session
from app.db.sql_helpers import patch_changes, update_statement
from app.models.settings import (
    BRAND_NULLABLE,
    COMPANY_NULLABLE,
    LOGO_EXTENSIONS,
    STOREFRONT_NULLABLE,
    BrandConfigOut,
    BrandConfigUpdate,
    CompanySettingsOut,
    CompanySettingsUpdate,
    CurrencyOut,
    LogoRegister,
    LogoType,
    LogoUploadRequest,
    LogoUploadTicket,
    StorefrontSettingsOut,
    StorefrontSettingsUpdate,
    WebsiteOut,
    language_rule_errors,
    storefront_rule_errors,
)
from app.services.brand_logo import MAX_BYTES, ImageRejected, process_logo_async
from app.services.currency import fetch_currency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

OwnerOnly = Depends(require_role("owner"))
OwnerOrStaff = Depends(require_role("owner", "staff"))
AnyMember = Depends(require_role("owner", "staff", "cpa"))

_COMPANY_COLUMNS = (
    "tenant_id, legal_name, ein, address, currency::text as currency, default_language, "
    "enabled_languages, default_tax_rate_bps, next_quote_number, next_invoice_number, "
    "updated_at"
)
_BRAND_COLUMNS = (
    "tenant_id, business_name, tagline, primary_color, accent_color, logo_path, hero_title, "
    "hero_subtitle, hero_eyebrow, collection_title, font_preset, corner_style, mark_style, "
    "hero_cta_label, hero_cta_url, updated_at"
)
_STOREFRONT_COLUMNS = (
    "storefront_enabled, contact_email, return_policy, privacy_policy, terms, "
    "custom_orders_info, ship_enabled, ship_flat_cents, free_ship_over_cents, tax_shipping, "
    "pickup_enabled, pickup_note, bank_transfer_enabled, pay_on_pickup_enabled, "
    "manual_payment_instructions, updated_at"
)


def brand_out(row: dict[str, Any]) -> BrandConfigOut:
    path = row.pop("logo_path")
    url = f"{get_settings().brand_assets_public_base}/{path}" if path else None
    return BrandConfigOut.model_validate({**row, "logo_url": url})


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
    """Only the fields sent change. A new default tax rate is read when the next quote is
    created; nothing here touches existing invoices."""
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


async def _brand(conn: AsyncConnection, claims: TokenClaims) -> BrandConfigOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_BRAND_COLUMNS} from public.brand_config where tenant_id = %s",
            (claims.tenant_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Brand config not found")
    return brand_out(row)


@router.get("/brand", response_model=BrandConfigOut, dependencies=[OwnerOrStaff])
async def get_brand_config(claims: CurrentClaims, conn: DbConn) -> BrandConfigOut:
    return await _brand(conn, claims)


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
    return brand_out(row)


@router.get("/storefront", response_model=StorefrontSettingsOut, dependencies=[OwnerOnly])
async def get_storefront_settings(claims: CurrentClaims, conn: DbConn) -> StorefrontSettingsOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_STOREFRONT_COLUMNS} from public.storefront_settings where tenant_id = %s",
            (claims.tenant_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Storefront settings not found")
    return StorefrontSettingsOut.model_validate(row)


@router.get("/website", response_model=WebsiteOut, dependencies=[OwnerOnly])
async def get_website(claims: CurrentClaims, conn: DbConn) -> WebsiteOut:
    """The shop's own website address (its first storefront domain, alphabetically: the one
    customer emails link to) and whether the storefront is switched on."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select (select d.domain from public.tenant_domains d "
            "where d.tenant_id = %(t)s and d.kind = 'storefront' order by d.domain limit 1) "
            "as domain, coalesce((select s.storefront_enabled from public.storefront_settings s "
            "where s.tenant_id = %(t)s), false) as enabled",
            {"t": claims.tenant_id},
        )
        row = await cur.fetchone()
    assert row is not None
    dev = get_settings().is_dev_environment
    return WebsiteOut(
        url=storefront_url(row["domain"], dev) if row["domain"] else None,
        enabled=row["enabled"],
    )


@router.patch("/storefront", response_model=StorefrontSettingsOut, dependencies=[OwnerOnly])
async def update_storefront_settings(
    body: StorefrontSettingsUpdate, claims: CurrentClaims, conn: DbConn
) -> StorefrontSettingsOut:
    """Everything here is public. Switching the storefront off makes every one of the
    tenant's storefront domains answer "Store not found" (within a minute: lookups are
    cached)."""
    changes = patch_changes(body, STOREFRONT_NULLABLE)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select pickup_enabled, pay_on_pickup_enabled from public.storefront_settings "
            "where tenant_id = %s for update",
            (claims.tenant_id,),
        )
        current = await cur.fetchone()
    if current is None:
        raise NotFoundError("Storefront settings not found")
    errors = storefront_rule_errors({**current, **changes})
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "; ".join(errors))
    query = update_statement(
        "storefront_settings",
        changes,
        StorefrontSettingsUpdate,
        _STOREFRONT_COLUMNS,
        key_column="tenant_id",
    )
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            query, {**changes, "key": claims.tenant_id, "tenant_id": claims.tenant_id}
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Storefront settings not found")
    return StorefrontSettingsOut.model_validate(row)


@router.get("/currency", response_model=CurrencyOut, dependencies=[AnyMember])
async def get_currency(conn: DbConn) -> CurrencyOut:
    return CurrencyOut(currency=await fetch_currency(conn))


# ---------------------------------------------------------------------------
# Logo
# ---------------------------------------------------------------------------
def raw_logo_path(claims: TokenClaims, upload_id: uuid.UUID, kind: LogoType) -> str:
    """Where a raw logo upload waits. The tenant comes from the verified token only."""
    return f"{claims.tenant_id}/brand/{upload_id}.{LOGO_EXTENSIONS[kind]}"


def _storage_unavailable() -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, "Image storage is temporarily unavailable"
    )


async def _remove_quietly(bucket: str, paths: list[str]) -> None:
    try:
        await storage.remove(bucket, paths)
    except Exception:
        logger.exception("Could not remove %d object(s) from %s", len(paths), bucket)


async def remove_logo_files(paths: list[str]) -> None:
    """Background task (after the transaction committed). A failure leaves an orphan for the
    Milestone B orphan sweep (TRD §6.6)."""
    await _remove_quietly(storage.BRAND_BUCKET, paths)


async def _lock_logo(conn: AsyncConnection, claims: TokenClaims) -> str | None:
    cur = await conn.execute(
        "select logo_path from public.brand_config where tenant_id = %s for update",
        (claims.tenant_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Brand config not found")
    return row[0]


@router.post(
    "/brand/logo/uploads",
    response_model=LogoUploadTicket,
    status_code=status.HTTP_201_CREATED,
    dependencies=[OwnerOnly],
)
async def create_logo_upload(
    body: LogoUploadRequest, claims: CurrentClaims, conn: DbConn
) -> LogoUploadTicket:
    await _lock_logo(conn, claims)  # the caller's own brand row, in its RLS session
    upload_id = uuid.uuid4()
    try:
        signed = await storage.create_signed_upload(
            storage.UPLOADS_BUCKET, raw_logo_path(claims, upload_id, body.content_type)
        )
    except storage.StorageError as exc:
        raise _storage_unavailable() from exc
    return LogoUploadTicket(upload_id=upload_id, upload_url=signed.url, max_bytes=MAX_BYTES)


@router.post("/brand/logo", response_model=BrandConfigOut, dependencies=[OwnerOnly])
async def register_logo(
    body: LogoRegister,
    claims: CurrentClaims,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    background: BackgroundTasks,
) -> BrandConfigOut:
    raw = raw_logo_path(claims, body.upload_id, body.content_type)
    # Short sessions only: no transaction or row lock is held while the file is processed.
    try:
        async with user_session(pool, claims) as conn:
            await _lock_logo(conn, claims)
        try:
            data = await storage.download(storage.UPLOADS_BUCKET, raw, MAX_BYTES)
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
            logo = await process_logo_async(data)
        except ImageRejected as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    finally:
        # The raw upload (which may carry GPS metadata) never outlives this request.
        await _remove_quietly(storage.UPLOADS_BUCKET, [raw])

    path = f"{claims.tenant_id}/logo-{uuid.uuid4()}.webp"
    try:
        await storage.upload(storage.BRAND_BUCKET, path, logo.data, "image/webp")
    except storage.StorageError as exc:
        raise _storage_unavailable() from exc
    try:
        async with user_session(pool, claims) as conn:
            previous = await _lock_logo(conn, claims)
            await conn.execute(
                "update public.brand_config set logo_path = %s where tenant_id = %s",
                (path, claims.tenant_id),
            )
            brand = await _brand(conn, claims)
    except BaseException:
        # Nothing was recorded: the stored file would be an orphan.
        await _remove_quietly(storage.BRAND_BUCKET, [path])
        raise
    if previous:
        background.add_task(remove_logo_files, [previous])
    return brand


@router.delete("/brand/logo", response_model=BrandConfigOut, dependencies=[OwnerOnly])
async def remove_logo(
    claims: CurrentClaims, conn: DbConn, background: BackgroundTasks
) -> BrandConfigOut:
    previous = await _lock_logo(conn, claims)
    if previous is None:
        raise NotFoundError("There is no logo to remove")
    await conn.execute(
        "update public.brand_config set logo_path = null where tenant_id = %s",
        (claims.tenant_id,),
    )
    background.add_task(remove_logo_files, [previous])
    return await _brand(conn, claims)
