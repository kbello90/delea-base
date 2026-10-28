"""The admin's Website page reads GET /settings/website (Website slice): the shop's own
storefront address and whether it is switched on. Owner only. Shops are throwaway tenants."""

import uuid

import psycopg
import pytest

from .test_public_checkout import open_shop

pytestmark = pytest.mark.integration

URL = "/api/v1/settings/website"


def test_each_owner_gets_their_own_shop_first_domain_alphabetically(
    client, make_tenant, db: psycopg.Connection
) -> None:
    mine = make_tenant(roles=("owner", "staff", "cpa"))
    other = make_tenant()
    tag = uuid.uuid4().hex[:8]
    open_shop(db, mine.id, f"b-{tag}.localhost:5174")
    db.execute(
        "insert into public.tenant_domains (domain, tenant_id, kind) values (%s, %s, 'storefront')",
        (f"a-{tag}.localhost:5174", mine.id),
    )
    open_shop(db, other.id, f"0-{tag}.localhost:5174")  # sorts first of all three

    out = client.get(URL, headers=mine.headers("owner"))
    assert out.status_code == 200, out.text
    assert out.json() == {"url": f"http://a-{tag}.localhost:5174", "enabled": True}
    theirs = client.get(URL, headers=other.headers("owner")).json()
    assert theirs["url"] == f"http://0-{tag}.localhost:5174"

    for role in ("staff", "cpa"):
        assert client.get(URL, headers=mine.headers(role)).status_code == 403, role


def test_a_shop_without_a_domain_or_switched_off(client, make_tenant, db) -> None:
    tenant = make_tenant()
    assert client.get(URL, headers=tenant.headers("owner")).json() == {
        "url": None,
        "enabled": False,
    }
    open_shop(db, tenant.id, f"w-{uuid.uuid4().hex[:8]}.localhost:5174")
    db.execute(
        "update public.storefront_settings set storefront_enabled = false where tenant_id = %s",
        (tenant.id,),
    )
    out = client.get(URL, headers=tenant.headers("owner")).json()
    assert out["url"] is not None and out["enabled"] is False
