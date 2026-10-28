"""Which tenant a storefront request is for (TRD §1.3, §6.11).

The storefront runs on the tenant's own domain and calls this API cross-origin, so the API's
own Host header is the API's. The tenant comes from the request's `Origin` header instead: the
browser sets it (page script can't), and it is what the CORS check answers. Its host[:port]
is looked up in `storefront.hosts` (kind 'storefront', active tenant, storefront switched on).

Forging Origin outside a browser only selects which tenant's public data is read, the same as
visiting that shop. Anything unknown, inactive or switched off resolves to nothing (a 404).

Development shortcuts (`*.localhost` hosts and plain http:// origins) are refused outside the
local and test environments, so a seeded dev mapping can never serve a deployed storefront.

Lookups are cached in-process for 60 seconds, unknown hosts included: a new or removed domain
takes effect within a minute.
"""

import time
from urllib.parse import urlsplit

from psycopg_pool import AsyncConnectionPool

from app.db.session import storefront_session

CACHE_SECONDS = 60.0
CACHE_MAX_ENTRIES = 1000
_MAX_ORIGIN_LENGTH = 300


def storefront_url(domain: str, dev: bool) -> str:
    """A shop's website from its storefront domain: plain http only in local/test."""
    return f"{'http' if dev else 'https'}://{domain}"


def origin_host(origin: str | None, *, dev: bool) -> str | None:
    """The lowercased host[:port] of an acceptable storefront Origin, or None.

    An Origin is scheme://host[:port] and nothing else. https only, except http in
    development; `localhost` and `*.localhost` only in development.
    """
    if not origin or origin == "null" or len(origin) > _MAX_ORIGIN_LENGTH:
        return None
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" and not (dev and parts.scheme == "http"):
        return None
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        return None
    host = parts.hostname
    if not host:
        return None
    if (host == "localhost" or host.endswith(".localhost")) and not dev:
        return None
    return f"{host}:{port}" if port is not None else host


class HostCache:
    """host -> tenant id (or None for an unknown host), each entry kept CACHE_SECONDS."""

    def __init__(self, ttl: float = CACHE_SECONDS, max_entries: int = CACHE_MAX_ENTRIES) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self._entries: dict[str, tuple[str | None, float]] = {}

    def get(self, host: str) -> tuple[bool, str | None]:
        entry = self._entries.get(host)
        if entry is None or entry[1] <= time.monotonic():
            return False, None
        return True, entry[0]

    def put(self, host: str, tenant_id: str | None) -> None:
        if len(self._entries) >= self.max_entries:
            # Random Origins could otherwise grow this without bound.
            self._entries.clear()
        self._entries[host] = (tenant_id, time.monotonic() + self.ttl)

    def clear(self) -> None:
        self._entries.clear()


host_cache = HostCache()


def cached_tenant(origin: str | None, *, dev: bool) -> str | None:
    """The tenant for this Origin if the cache already knows it; never touches the database."""
    host = origin_host(origin, dev=dev)
    if host is None:
        return None
    return host_cache.get(host)[1]


async def resolve_tenant(pool: AsyncConnectionPool, origin: str | None, *, dev: bool) -> str | None:
    """The tenant whose storefront sent this Origin, or None."""
    host = origin_host(origin, dev=dev)
    if host is None:
        return None
    hit, tenant_id = host_cache.get(host)
    if hit:
        return tenant_id
    async with storefront_session(pool) as conn:
        cur = await conn.execute(
            "select tenant_id::text from storefront.hosts where domain = %s", (host,)
        )
        row = await cur.fetchone()
    tenant_id = row[0] if row else None
    host_cache.put(host, tenant_id)
    return tenant_id
