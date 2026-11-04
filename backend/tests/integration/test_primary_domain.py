"""A shop's primary website address (hosted-demo plan step 2, migration 20261103000200).

Exactly one storefront domain per shop is primary while it has any; the first one becomes
primary by itself; add_tenant_domain.py --primary moves it; removing, unsetting or turning
the primary into an admin domain is refused while other storefront domains exist. Customer
emails, the preview and the Website page link to the primary (never the first domain
alphabetically), and a shop without a domain gets no link at all. Throwaway tenants only."""

import uuid

import psycopg
import pytest

from app.core.config import get_settings
from app.services.email_templates import NO_WEBSITE
from app.worker import Worker

from . import test_email as d1
from .test_email import SETTINGS_URL, Recorder, place
from .test_tenant_domains_helpers import script

shop = d1.shop
pool = d1.pool
_quiet_queue = d1._quiet_queue

pytestmark = pytest.mark.integration

REFUSED = "is this shop's primary website address. Make another storefront domain primary first"
WEBSITE_URL = "/api/v1/settings/website"


def _primary(db: psycopg.Connection, tenant_id: str) -> list[str]:
    return [
        r[0]
        for r in db.execute(
            "select domain from public.tenant_domains where tenant_id = %s and is_primary",
            (tenant_id,),
        ).fetchall()
    ]


def _add(db: psycopg.Connection, tenant_id: str, domain: str, kind: str = "storefront") -> None:
    db.execute(
        "insert into public.tenant_domains (domain, tenant_id, kind) values (%s, %s, %s)",
        (domain, tenant_id, kind),
    )


def test_the_first_storefront_domain_is_primary_and_the_script_moves_it(
    make_tenant, db, capsys
) -> None:
    tenant = make_tenant()
    tag = uuid.uuid4().hex[:8]
    first, second, third = (f"{p}-{tag}.localhost:5174" for p in ("m", "a", "z"))

    assert script.main(["--tenant", tenant.id, "--domain", first]) == 0
    assert f"{first} is the shop's primary website address." in capsys.readouterr().out
    assert script.main(["--tenant", tenant.id, "--domain", second]) == 0  # sorts first
    assert "primary" not in capsys.readouterr().out
    assert (
        script.main(
            ["--tenant", tenant.id, "--domain", f"adm-{tag}.localhost:5173", "--kind", "admin"]
        )
        == 0
    )
    assert _primary(db, tenant.id) == [first]

    # A dry run says what would happen and writes nothing.
    assert script.main(["--tenant", tenant.id, "--domain", second, "--primary", "--dry-run"]) == 0
    assert f"{second} would be the shop's primary website address (was {first})" in (
        capsys.readouterr().out
    )
    assert _primary(db, tenant.id) == [first]

    # An existing domain becomes primary; running it again changes nothing.
    assert script.main(["--tenant", tenant.id, "--domain", second, "--primary"]) == 0
    out = capsys.readouterr().out
    assert f"{second} is the shop's primary website address (was {first})" in out
    assert _primary(db, tenant.id) == [second]
    assert script.main(["--tenant", tenant.id, "--domain", second, "--primary"]) == 0
    assert "nothing changed" in capsys.readouterr().out
    assert _primary(db, tenant.id) == [second]

    # A new domain can be added as the primary in one go.
    assert script.main(["--tenant", tenant.id, "--domain", third, "--primary"]) == 0
    out = capsys.readouterr().out
    assert f"Added {third}" in out and f"(was {second})" in out
    assert _primary(db, tenant.id) == [third]

    # Only a storefront domain can be primary.
    assert (
        script.main(
            [
                "--tenant",
                tenant.id,
                "--domain",
                f"adm-{tag}.localhost:5173",
                "--kind",
                "admin",
                "--primary",
            ]
        )
        == 2
    )
    assert "only a storefront domain" in capsys.readouterr().err
    assert _primary(db, tenant.id) == [third]


def test_the_primary_stays_while_other_storefront_domains_exist(make_tenant, db) -> None:
    tenant = make_tenant()
    tag = uuid.uuid4().hex[:8]
    primary, other = f"p-{tag}.localhost:5174", f"o-{tag}.localhost:5174"
    _add(db, tenant.id, primary)
    _add(db, tenant.id, other)
    assert _primary(db, tenant.id) == [primary]

    for statement in (
        "delete from public.tenant_domains where domain = %s",
        "update public.tenant_domains set kind = 'admin' where domain = %s",
        "update public.tenant_domains set is_primary = false where domain = %s",
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match=f"{primary} {REFUSED}"):
            db.execute(statement, (primary,))
    assert _primary(db, tenant.id) == [primary]

    # Never two primaries, by an update or an insert.
    with pytest.raises(psycopg.errors.ExclusionViolation):
        db.execute("update public.tenant_domains set is_primary = true where domain = %s", (other,))
    with pytest.raises(psycopg.errors.ExclusionViolation):
        db.execute(
            "insert into public.tenant_domains (domain, tenant_id, kind, is_primary) "
            "values (%s, %s, 'storefront', true)",
            (f"n-{tag}.localhost:5174", tenant.id),
        )
    # An admin domain is never primary.
    with pytest.raises(psycopg.errors.CheckViolation, match="tenant_domains_primary_is_storefront"):
        db.execute(
            "insert into public.tenant_domains (domain, tenant_id, kind, is_primary) "
            "values (%s, %s, 'admin', true)",
            (f"adm-{tag}.localhost:5173", tenant.id),
        )

    # Moved in one statement, then the old primary can go.
    db.execute(
        "update public.tenant_domains set is_primary = (domain = %s) "
        "where tenant_id = %s and kind = 'storefront'",
        (other, tenant.id),
    )
    assert _primary(db, tenant.id) == [other]
    db.execute("delete from public.tenant_domains where domain = %s", (primary,))

    # The last storefront domain may go (the shop then has no website address), or become an
    # admin domain; the next storefront domain is primary again by itself.
    db.execute("update public.tenant_domains set kind = 'admin' where domain = %s", (other,))
    assert _primary(db, tenant.id) == []
    _add(db, tenant.id, primary)
    assert _primary(db, tenant.id) == [primary]
    db.execute("delete from public.tenant_domains where domain = %s", (primary,))
    assert _primary(db, tenant.id) == []


def test_deleting_a_shop_takes_all_its_domains(make_tenant, db) -> None:
    tenant = make_tenant(is_demo=True)
    tag = uuid.uuid4().hex[:8]
    _add(db, tenant.id, f"p-{tag}.localhost:5174")
    _add(db, tenant.id, f"o-{tag}.localhost:5174")
    db.execute("delete from public.tenants where id = %s and is_demo", (tenant.id,))
    assert db.execute(
        "select count(*) from public.tenant_domains where tenant_id = %s", (tenant.id,)
    ).fetchone() == (0,)


def test_the_website_page_shows_the_primary_not_the_first_alphabetically(
    client, make_tenant, db
) -> None:
    tenant = make_tenant()
    tag = uuid.uuid4().hex[:8]
    _add(db, tenant.id, f"m-{tag}.localhost:5174")
    _add(db, tenant.id, f"a-{tag}.localhost:5174")
    out = client.get(WEBSITE_URL, headers=tenant.headers("owner")).json()
    assert out["url"] == f"http://m-{tag}.localhost:5174"
    assert (
        script.main(["--tenant", tenant.id, "--domain", f"z-{tag}.localhost:5174", "--primary"])
        == 0
    )
    out = client.get(WEBSITE_URL, headers=tenant.headers("owner")).json()
    assert out["url"] == f"http://z-{tag}.localhost:5174"


@pytest.mark.anyio
async def test_customer_emails_link_to_the_primary(client, shop, db, pool) -> None:
    """The order is placed on the shop's first domain; the primary then moves to one that
    sorts after it. Every link in the email points at the primary."""
    tenant = shop["tenant"]
    primary = f"z-{uuid.uuid4().hex[:8]}.localhost:5174"
    order = place(client, shop)
    # What add_tenant_domain.py --primary runs (the script can't run inside this event loop).
    _add(db, tenant.id, primary)
    db.execute(
        "update public.tenant_domains set is_primary = (domain = %s) "
        "where tenant_id = %s and kind = 'storefront'",
        (primary, tenant.id),
    )
    recorder = Recorder()
    await Worker(pool, recorder, get_settings()).run_once()

    (received,) = [e for e in recorder.sent if e.recipient == "sarah@example.com"]
    tracking = f"http://{primary}/orders/{order['public_token']}"
    assert f'href="{tracking}"' in received.html
    assert f"Track your order: {tracking}" in received.text
    assert shop["origin"] not in received.text + received.html

    preview = client.post(
        f"{SETTINGS_URL}/preview", json={"template": "shipped"}, headers=tenant.headers("owner")
    ).json()
    assert f"http://{primary}/orders/sample" in preview["text"]
    assert shop["origin"] not in preview["text"] + preview["html"]


def test_a_shop_without_a_website_address_gets_no_link(client, make_tenant, db) -> None:
    tenant = make_tenant()
    owner = tenant.headers("owner")
    assert client.get(WEBSITE_URL, headers=owner).json()["url"] is None
    for template in (
        "order_received",
        "payment_received",
        "deposit_received",
        "in_production",
        "shipped",
    ):
        out = client.post(f"{SETTINGS_URL}/preview", json={"template": template}, headers=owner)
        assert out.status_code == 200, out.text
        body = out.json()
        assert NO_WEBSITE in body["text"], template
        assert "/orders/" not in body["text"] + body["html"], template
        assert "shop.example" not in body["text"] + body["html"], template
        assert "Pay now:" not in body["text"] and "Track your order:" not in body["text"]


@pytest.mark.anyio
async def test_a_customer_email_without_a_website_address_is_kept_as_not_sent(
    client, shop, db, pool
) -> None:
    """The order is placed, then the shop's only storefront domain is removed (allowed: it is
    the last one). The shopper's email is not sent but kept in the outbox as suppressed with
    the reason "no website address", without any link; the owner's alert still goes out."""
    tenant = shop["tenant"]
    order = place(client, shop)
    db.execute(
        "delete from public.tenant_domains where tenant_id = %s and kind = 'storefront'",
        (tenant.id,),
    )
    recorder = Recorder()
    await Worker(pool, recorder, get_settings()).run_once()

    assert [e for e in recorder.sent if e.recipient == "sarah@example.com"] == []
    assert any(e.recipient == shop["owner"] for e in recorder.sent)  # the owner's alert
    rows = db.execute(
        "select template, status, last_error, html_body, text_body from public.email_messages "
        "where tenant_id = %s and kind = 'customer'",
        (tenant.id,),
    ).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [
        ("order_received", "suppressed", "no website address")
    ]
    html, text = rows[0][3], rows[0][4]
    assert order["order_ref"] in text
    assert "/orders/" not in html + text and "shop.example" not in html + text
