"""Adding a domain to a tenant (manual, admin-only onboarding; infra/scripts/storefront-domain.md).

Runs on an administrative database connection, like provisioning: app users can't write
tenant_domains (RLS gives owner and staff read only). Used by scripts/add_tenant_domain.py.

Domains are stored the way a browser sends them in the Origin header: lowercase, punycode for
non-ASCII names, no trailing dot, no scheme or path. A port is part of the domain only for
local development (`localhost:5174`), which is also the only place `localhost` and
`*.localhost` names are accepted.
"""

import re
from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection

DOMAIN_KINDS = ("storefront", "admin")
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


class DomainRefused(ValueError):
    """The domain is malformed, not allowed for this target, or belongs to someone else."""


def normalize_domain(raw: str, *, local: bool) -> str:
    """The domain as stored, or DomainRefused. `local`: the target is the local stack."""
    text = raw.strip().lower().rstrip(".")
    if not text:
        raise DomainRefused("enter a domain, for example shop.example.com")
    if "://" in text or "/" in text or "@" in text or "?" in text or "#" in text:
        raise DomainRefused("enter the domain only (shop.example.com), without https:// or a path")
    host, _, port = text.partition(":")
    if port:
        if not local:
            raise DomainRefused("a port is only allowed for a local development domain")
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise DomainRefused(f"invalid port {port!r}")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise DomainRefused(f"{raw!r} is not a valid domain name") from exc
    is_localhost = host == "localhost" or host.endswith(".localhost")
    if is_localhost and not local:
        raise DomainRefused("localhost domains are only for the local stack")
    labels = host.split(".")
    if len(host) > 253 or not all(_LABEL.fullmatch(label) for label in labels):
        raise DomainRefused(f"{raw!r} is not a valid domain name")
    if not is_localhost and (len(labels) < 2 or labels[-1].isdigit()):
        raise DomainRefused(f"{raw!r} is not a public domain name (an IP address can't be used)")
    return f"{host}:{port}" if port else host


@dataclass(frozen=True)
class DomainResult:
    domain: str
    created: bool  # False: it was already set for this tenant and kind
    tenant_name: str
    storefront_enabled: bool
    primary: bool = False  # the shop's primary website address (after the run, or would be)
    previous_primary: str | None = None  # set when the primary moved (or would move) to domain


async def add_domain(
    conn: AsyncConnection,
    tenant_id: UUID,
    domain: str,
    kind: str,
    *,
    primary: bool = False,
    dry_run: bool = False,
) -> DomainResult:
    """Map `domain` (already normalized) to the tenant. Refuses an unknown or inactive tenant,
    and a domain that belongs to another tenant or kind.

    A shop's first storefront domain becomes its primary website address by itself (a
    database trigger). `primary=True` makes `domain` the primary, also when it was already
    mapped; the move is one statement, in the same transaction as any insert."""
    if kind not in DOMAIN_KINDS:
        raise DomainRefused(f"kind must be one of {', '.join(DOMAIN_KINDS)}")
    if primary and kind != "storefront":
        raise DomainRefused("only a storefront domain can be the shop's primary website address")
    async with conn.transaction():
        cur = await conn.execute(
            "select t.name, t.is_active, coalesce(s.storefront_enabled, false) "
            "from public.tenants t "
            "left join public.storefront_settings s on s.tenant_id = t.id where t.id = %s "
            "for update of t",
            (tenant_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise DomainRefused(f"no tenant {tenant_id}")
        name, active, enabled = row
        if not active:
            raise DomainRefused(f"tenant {tenant_id} ({name}) is not active")
        cur = await conn.execute(
            "select tenant_id, kind from public.tenant_domains where domain = %s for update",
            (domain,),
        )
        existing = await cur.fetchone()
        if existing is not None and (existing[0] != tenant_id or existing[1] != kind):
            raise DomainRefused(f"{domain} is already used (another tenant or kind)")
        cur = await conn.execute(
            "select domain from public.tenant_domains where tenant_id = %s and is_primary",
            (tenant_id,),
        )
        current = await cur.fetchone()
        current_primary = current[0] if current else None

        created = existing is None
        if created and not dry_run:
            await conn.execute(
                "insert into public.tenant_domains (domain, tenant_id, kind) values (%s, %s, %s)",
                (domain, tenant_id, kind),
            )
        moves = primary and current_primary is not None and current_primary != domain
        if moves and not dry_run:
            # One statement: the one-primary rule is checked at its end.
            await conn.execute(
                "update public.tenant_domains set is_primary = (domain = %(d)s) "
                "where tenant_id = %(t)s and kind = 'storefront' "
                "and is_primary is distinct from (domain = %(d)s)",
                {"d": domain, "t": tenant_id},
            )
        is_primary = kind == "storefront" and (
            current_primary == domain or current_primary is None or primary
        )
    return DomainResult(
        domain,
        created and not dry_run,
        name,
        enabled,
        primary=is_primary,
        previous_primary=current_primary if moves else None,
    )
