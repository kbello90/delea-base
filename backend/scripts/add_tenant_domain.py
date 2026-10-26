"""Add a storefront (or admin) domain to a tenant. See infra/scripts/storefront-domain.md.

    python backend/scripts/add_tenant_domain.py --tenant <uuid> --domain shop.example.com
        [--kind storefront] [--dry-run] [--allow-remote]

Local by default: a non-local database is refused unless --allow-remote is given, and then
ADMIN_DATABASE_URL (an administrative connection, never the API's own login) is required.
The domain is normalized (lowercase, punycode, no scheme or path); ports and localhost names
are accepted only on the local stack. A domain that belongs to another tenant is refused.
The API picks up a new domain within a minute (lookups are cached for 60 seconds).
"""

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from psycopg import AsyncConnection  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services.tenant_domains import (  # noqa: E402
    DOMAIN_KINDS,
    DomainRefused,
    add_domain,
    normalize_domain,
)
from showcase.config import is_local_url  # noqa: E402


def database_url(env, settings, *, allow_remote: bool) -> tuple[str, bool]:
    """(url, is_local). Remote needs --allow-remote and its own ADMIN_DATABASE_URL."""
    admin_url = env.get("ADMIN_DATABASE_URL", "").strip()
    url = admin_url or settings.database_url
    if not url:
        raise DomainRefused("set ADMIN_DATABASE_URL (or DATABASE_URL locally)")
    if is_local_url(url):
        return url, True
    if not allow_remote:
        raise DomainRefused("refusing a non-local database; pass --allow-remote for a hosted one")
    if not admin_url:
        raise DomainRefused(
            "on a non-local target, set ADMIN_DATABASE_URL to an administrative connection "
            "(the API's DATABASE_URL is never used there)"
        )
    return url, False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant", required=True, help="the tenant's id (uuid)")
    parser.add_argument("--domain", required=True, help="for example shop.example.com")
    parser.add_argument("--kind", choices=DOMAIN_KINDS, default="storefront")
    parser.add_argument("--allow-remote", action="store_true", help="allow a hosted database")
    parser.add_argument("--dry-run", action="store_true", help="check everything; write nothing")
    args = parser.parse_args(argv)

    try:
        tenant_id = uuid.UUID(args.tenant)
    except ValueError:
        print(f"--tenant must be a uuid, not {args.tenant!r}", file=sys.stderr)
        return 2
    try:
        url, local = database_url(os.environ, get_settings(), allow_remote=args.allow_remote)
        domain = normalize_domain(args.domain, local=local)
        result = _run(_add(url, tenant_id, domain, args.kind, args.dry_run))
    except DomainRefused as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"OK (dry run): {domain} -> {result.tenant_name} ({args.kind}); nothing was written")
    elif result.created:
        print(f"Added {domain} -> {result.tenant_name} ({args.kind}). Live within a minute.")
    else:
        print(f"{domain} is already set for {result.tenant_name} ({args.kind}); nothing changed.")
    if args.kind == "storefront" and not result.storefront_enabled:
        print(
            "Note: this store is switched off. The owner turns it on in Settings > Storefront "
            '("Store is online"); until then the domain answers "Store not found".'
        )
    return 0


async def _add(url: str, tenant_id: uuid.UUID, domain: str, kind: str, dry_run: bool):
    async with await AsyncConnection.connect(url, autocommit=True) as conn:
        return await add_domain(conn, tenant_id, domain, kind, dry_run=dry_run)


def _run(coro):
    # psycopg's async mode needs a selector event loop (Windows defaults to Proactor).
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coro)


if __name__ == "__main__":
    sys.exit(main())
