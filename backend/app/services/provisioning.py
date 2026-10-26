"""Tenant provisioning: create a tenant with everything it needs, or delete a demo tenant.

This is the customer-provisioning path (used today by the showcase loader). It runs on an
administrative database connection (no app_role claim, RLS bypassed): app users can't create
tenants, and the tenants_is_demo_guard trigger refuses is_demo from any other role.

provision_tenant writes, in one transaction: tenants, company_settings, brand_config,
storefront_settings (switched off unless the spec says otherwise), any tenant_domains, and the
members (tenant_members). The member users must already exist in
Supabase Auth (see app/core/auth_admin.py); the access token hook reads tenant_members at
their next sign-in.

Local vs hosted: nothing in here differs. The caller chooses the connection (its URL and
credentials) and decides whether a non-local target is allowed.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection, sql

from app.core import storage
from app.models.settings import SUPPORTED_CURRENCIES

APP_ROLES = ("owner", "staff", "cpa")
DOMAIN_KINDS = ("storefront", "admin")
TENANT_BUCKETS = (
    storage.IMAGES_BUCKET,
    storage.UPLOADS_BUCKET,
    storage.RECEIPTS_BUCKET,
    storage.BRAND_BUCKET,
)


class ProvisioningError(ValueError):
    """The spec is invalid or the operation is refused."""


@dataclass(frozen=True)
class Member:
    user_id: str
    app_role: str


@dataclass(frozen=True)
class TenantSpec:
    name: str
    business_name: str
    members: tuple[Member, ...]
    tenant_id: UUID | None = None  # None: the database assigns one
    tier: str = "essential"
    is_demo: bool = False
    currency: str = "USD"
    default_tax_rate_bps: int = 0
    legal_name: str | None = None
    address: str | None = None
    tagline: str | None = None
    primary_color: str | None = None  # None: the database default (burgundy)
    accent_color: str | None = None  # None: the database default (gold)
    domains: tuple[tuple[str, str], ...] = field(default=())  # (domain, kind)
    storefront_enabled: bool = False  # off until the owner's domain and pages are ready
    created_at: datetime | None = None  # None: now()


def validate_spec(spec: TenantSpec) -> None:
    if not spec.name.strip() or not spec.business_name.strip():
        raise ProvisioningError("a tenant needs a name and a business name")
    roles = [m.app_role for m in spec.members]
    if any(role not in APP_ROLES for role in roles):
        raise ProvisioningError(f"member roles must be one of {', '.join(APP_ROLES)}")
    if roles.count("owner") != 1:
        raise ProvisioningError("a tenant needs exactly one owner")
    if len({m.user_id for m in spec.members}) != len(spec.members):
        raise ProvisioningError("a user can hold only one role in a tenant")
    if not 0 <= spec.default_tax_rate_bps <= 10_000:
        raise ProvisioningError("the tax rate must be between 0 and 10000 basis points")
    # The currency is chosen here, once: the database refuses a change after data exists.
    if spec.currency not in SUPPORTED_CURRENCIES:
        raise ProvisioningError(f"the currency must be one of {', '.join(SUPPORTED_CURRENCIES)}")
    if any(kind not in DOMAIN_KINDS for _, kind in spec.domains):
        raise ProvisioningError(f"domain kinds must be one of {', '.join(DOMAIN_KINDS)}")


async def provision_tenant(conn: AsyncConnection, spec: TenantSpec) -> UUID:
    """Create the tenant and its settings, brand and members in one transaction."""
    validate_spec(spec)
    async with conn.transaction():
        cur = await conn.execute(
            "insert into public.tenants (id, name, tier, is_demo, created_at) "
            "values (coalesce(%s, gen_random_uuid()), %s, %s::public.tier, %s, "
            "coalesce(%s, now())) returning id",
            (spec.tenant_id, spec.name, spec.tier, spec.is_demo, spec.created_at),
        )
        (tenant_id,) = await cur.fetchone()
        await conn.execute(
            "insert into public.company_settings "
            "(tenant_id, legal_name, address, currency, default_tax_rate_bps) "
            "values (%s, %s, %s, %s, %s)",
            (tenant_id, spec.legal_name, spec.address, spec.currency, spec.default_tax_rate_bps),
        )
        # Colours left unset are left out, so the column defaults apply.
        brand = {"tenant_id": tenant_id, "business_name": spec.business_name}
        brand["tagline"] = spec.tagline
        for column in ("primary_color", "accent_color"):
            if getattr(spec, column) is not None:
                brand[column] = getattr(spec, column)
        await conn.execute(
            sql.SQL("insert into public.brand_config ({}) values ({})").format(
                sql.SQL(", ").join(map(sql.Identifier, brand)),
                sql.SQL(", ").join(sql.Placeholder() * len(brand)),
            ),
            list(brand.values()),
        )
        await conn.execute(
            "insert into public.storefront_settings (tenant_id, storefront_enabled) "
            "values (%s, %s)",
            (tenant_id, spec.storefront_enabled),
        )
        for domain, kind in spec.domains:
            await conn.execute(
                "insert into public.tenant_domains (domain, tenant_id, kind) values (%s, %s, %s)",
                (domain, tenant_id, kind),
            )
        for member in spec.members:
            await conn.execute(
                "insert into public.tenant_members (user_id, tenant_id, app_role) "
                "values (%s, %s, %s::public.app_role)",
                (member.user_id, tenant_id, member.app_role),
            )
    return tenant_id


async def tenant_storage_paths(conn: AsyncConnection, tenant_id: UUID) -> dict[str, list[str]]:
    """Every Storage object under {tenant_id}/ in the product buckets, by bucket."""
    cur = await conn.execute(
        "select bucket_id, name from storage.objects "
        "where bucket_id = any(%s) and name like %s order by bucket_id, name",
        (list(TENANT_BUCKETS), f"{tenant_id}/%"),
    )
    paths: dict[str, list[str]] = {bucket: [] for bucket in TENANT_BUCKETS}
    for bucket, name in await cur.fetchall():
        paths[bucket].append(name)
    return paths


async def refuse_unless_demo(conn: AsyncConnection, tenant_id: UUID) -> None:
    """Raise unless the tenant is a demo tenant (or doesn't exist)."""
    cur = await conn.execute("select is_demo from public.tenants where id = %s", (tenant_id,))
    row = await cur.fetchone()
    if row is not None and not row[0]:
        raise ProvisioningError(f"tenant {tenant_id} is not a demo tenant; refusing to delete")


async def remove_tenant_storage(conn: AsyncConnection, tenant_id: UUID) -> int:
    """Remove a demo tenant's Storage objects (through the Storage API, which also deletes the
    files). Refuses a tenant that isn't a demo tenant, like delete_demo_tenant. Returns how
    many were removed."""
    await refuse_unless_demo(conn, tenant_id)
    removed = 0
    for bucket, paths in (await tenant_storage_paths(conn, tenant_id)).items():
        for start in range(0, len(paths), 500):
            await storage.remove(bucket, paths[start : start + 500])
        removed += len(paths)
    return removed


async def delete_demo_tenant(conn: AsyncConnection, tenant_id: UUID) -> bool:
    """Delete a demo tenant and, by cascade, all its rows. Its Storage objects are not
    touched (call remove_tenant_storage first). Refuses a tenant that isn't a demo tenant, so
    a wrong id can never remove a real business. Returns False if there was nothing to do."""
    async with conn.transaction():
        await refuse_unless_demo(conn, tenant_id)
        cur = await conn.execute(
            "delete from public.tenants where id = %s and is_demo returning id", (tenant_id,)
        )
        return await cur.fetchone() is not None
