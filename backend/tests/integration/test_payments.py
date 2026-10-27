"""Payments and deposits through the API (Slice B1): record a deposit, start production, pay
the rest, void, refund a deposit, stock short at payment, the old pay paths' ledger rows, and
two payments racing for the same balance.

Each test makes its own IT- products and customers; their invoices (and payments, which
cascade) are removed afterwards (conftest)."""

import asyncio
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import errors

from app.core.config import Settings
from app.core.errors import map_db_error
from app.core.security import TokenClaims
from app.db.session import apply_user_scope

from .support import TENANT_A, TestUser, assert_access

pytestmark = pytest.mark.integration

INVOICES = "/api/v1/invoices"


def tag() -> str:
    return uuid.uuid4().hex[:6].upper()


def product(db, price=10000, stock=10) -> str:
    return str(
        db.execute(
            "insert into public.products (tenant_id, sku, name, metal, price_cents, stock_qty) "
            "values (%s, %s, %s, 'other', %s, %s) returning id",
            (TENANT_A, f"IT-{tag()}", f"IT ring {tag()}", price, stock),
        ).fetchone()[0]
    )


def customer(db) -> str:
    return str(
        db.execute(
            "insert into public.customers (tenant_id, first_name, last_name) "
            "values (%s, %s, 'Tester') returning id",
            (TENANT_A, f"IT-{tag()}"),
        ).fetchone()[0]
    )


def stock(db, product_id: str) -> int:
    return db.execute(
        "select stock_qty from public.products where id = %s", (product_id,)
    ).fetchone()[0]


def quote(client, user: TestUser, db, p: str | None = None, qty=1, **extra) -> dict:
    """A quote of one IT product at 100.00 + 8% tax = 108.00."""
    p = p or product(db)
    response = client.post(
        INVOICES,
        json={"items": [{"product_id": p, "qty": qty}], "customer_id": customer(db), **extra},
        headers=user.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def pay(client, user: TestUser, invoice_id: str, cents: int, method="cash", code=201, **extra):
    response = client.post(
        f"{INVOICES}/{invoice_id}/payments",
        json={"amount_cents": cents, "method": method, **extra},
        headers=user.headers,
    )
    assert response.status_code == code, response.text
    return response.json()


def post(client, user: TestUser, path: str, code=200) -> dict:
    response = client.post(path, headers=user.headers)
    assert response.status_code == code, response.text
    return response.json()


def ledger(invoice: dict) -> list[tuple]:
    return [(p["kind"], p["method"], p["status"], p["amount_cents"]) for p in invoice["payments"]]


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
def test_role_matrix(client: TestClient, users: dict[str, TestUser], db) -> None:
    owner = users["owner_a"]
    inv = quote(client, owner, db)
    url = f"{INVOICES}/{inv['id']}"

    assert_access(
        client,
        users,
        "GET",
        f"{url}/payments",
        {"owner_a": 200, "staff_a": 200, "cpa_a": 200, "owner_b": 404},
    )
    assert_access(
        client,
        users,
        "POST",
        f"{url}/payments",
        {"owner_a": 201, "staff_a": 201, "owner_b": 404},
        json={"amount_cents": 100, "method": "cash"},
    )
    assert_access(
        client,
        users,
        "POST",
        f"{url}/start-production",
        {"owner_a": 200, "staff_a": 409, "owner_b": 404},
    )
    deposit = client.get(url, headers=owner.headers).json()["payments"][0]["id"]
    assert_access(
        client, users, "POST", f"{url}/payments/{deposit}/void", {"owner_a": 200, "owner_b": 404}
    )
    assert_access(
        client, users, "POST", f"{url}/settle", {"owner_a": 409, "staff_a": 409, "owner_b": 404}
    )


def test_the_cpa_reads_the_ledger(client: TestClient, users: dict[str, TestUser], db) -> None:
    inv = quote(client, users["owner_a"], db)
    pay(client, users["staff_a"], inv["id"], 2500, "bank_transfer", note="Deposit")
    seen = client.get(f"{INVOICES}/{inv['id']}", headers=users["cpa_a"].headers).json()
    assert seen["items"] is None  # still no line items for the CPA
    assert ledger(seen) == [("payment", "bank_transfer", "succeeded", 2500)]
    assert seen["payments"][0]["note"] == "Deposit"
    listed = client.get(f"{INVOICES}/{inv['id']}/payments", headers=users["cpa_a"].headers)
    assert [p["amount_cents"] for p in listed.json()] == [2500]


# ---------------------------------------------------------------------------
# A deposit, production, then the rest
# ---------------------------------------------------------------------------
def test_deposit_then_production_then_the_rest(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    owner, staff = users["owner_a"], users["staff_a"]
    p = product(db, stock=3)
    inv = quote(client, owner, db, p)
    url = f"{INVOICES}/{inv['id']}"
    assert (inv["amount_paid_cents"], inv["balance_cents"]) == (0, 10800)

    held = pay(client, staff, inv["id"], 3000)
    assert held["order_status"] == "quote"
    assert (held["amount_paid_cents"], held["balance_cents"]) == (3000, 7800)
    assert held["invoice_number"] is None and held["paid_at"] is None
    assert stock(db, p) == 3  # a deposit takes no stock

    started = post(client, staff, f"{url}/start-production")
    assert started["production_started_at"] is not None and started["order_status"] == "quote"
    post(client, staff, f"{url}/start-production", code=409)  # once

    # Locked: the items, a cancel, a delete.
    edit = client.put(
        f"{url}/items", json={"items": [{"product_id": p, "qty": 2}]}, headers=owner.headers
    )
    assert edit.status_code == 409 and edit.json()["detail"].startswith("Production has started")
    cancel = client.post(f"{url}/cancel", headers=owner.headers)
    assert cancel.status_code == 409
    assert cancel.json()["detail"] == "This quote holds a deposit. Refund it before cancelling."
    assert client.delete(url, headers=owner.headers).status_code == 409

    too_much = pay(client, staff, inv["id"], 7801, code=422)
    assert too_much["detail"] == "That is more than the 78.00 still due."

    done = pay(client, staff, inv["id"], 7800, "card")
    assert done["order_status"] == "in_production"
    assert (done["amount_paid_cents"], done["balance_cents"], done["stock_short"]) == (
        10800,
        0,
        False,
    )
    assert done["invoice_number"].startswith("INV-") and done["paid_at"] is not None
    assert done["payment_method"] == "other"  # cash and card
    assert ledger(done) == [
        ("payment", "cash", "succeeded", 3000),
        ("payment", "card", "succeeded", 7800),
    ]
    assert stock(db, p) == 2  # once, when it was paid in full

    pay(client, staff, inv["id"], 1, code=409)  # nothing more is due
    assert stock(db, p) == 2


def test_paid_in_full_without_production_is_paid(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    inv = quote(client, users["owner_a"], db)
    pay(client, users["staff_a"], inv["id"], 800)
    done = pay(client, users["staff_a"], inv["id"], 10000)
    assert (done["order_status"], done["payment_method"]) == ("paid", "cash")


def test_stock_short_at_payment_keeps_the_payment(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    owner = users["owner_a"]
    p = product(db, stock=0)
    inv = quote(client, owner, db, p)
    url = f"{INVOICES}/{inv['id']}"

    short = pay(client, owner, inv["id"], 10800, "bank_transfer")
    assert short["order_status"] == "quote" and short["stock_short"] is True
    assert (short["amount_paid_cents"], short["balance_cents"]) == (10800, 0)

    refused = client.post(f"{url}/settle", headers=owner.headers)
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("Not enough stock for SKU IT-")
    db.execute("update public.products set stock_qty = 1 where id = %s", (p,))
    done = post(client, owner, f"{url}/settle")
    assert (done["order_status"], done["stock_short"], done["payment_method"]) == (
        "paid",
        False,
        "bank_transfer",
    )
    assert stock(db, p) == 0


# ---------------------------------------------------------------------------
# Corrections: void, refund a deposit, then cancel
# ---------------------------------------------------------------------------
def test_void_and_refund_a_deposit_then_cancel(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    owner = users["owner_a"]
    inv = quote(client, owner, db)
    url = f"{INVOICES}/{inv['id']}"

    mistake = pay(client, owner, inv["id"], 500)
    voided = post(client, owner, f"{url}/payments/{mistake['payments'][0]['id']}/void")
    assert ledger(voided) == [("payment", "cash", "voided", 500)]
    assert voided["payments"][0]["voided_at"] is not None
    assert voided["amount_paid_cents"] == 0
    post(client, owner, f"{url}/payments/{mistake['payments'][0]['id']}/void", code=409)

    held = pay(client, owner, inv["id"], 2000, "bank_transfer")
    refunded = post(client, owner, f"{url}/refund")
    assert refunded["order_status"] == "quote" and refunded["refunded_at"] is None
    assert refunded["amount_paid_cents"] == 0
    assert ledger(refunded)[-1] == ("refund", "bank_transfer", "succeeded", 2000)
    assert held["balance_cents"] == 8800
    cancelled = post(client, owner, f"{url}/cancel")
    assert cancelled["cancelled_at"] is not None
    pay(client, owner, inv["id"], 100, code=409)


def test_payments_on_a_paid_invoice_cannot_be_voided(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    owner = users["owner_a"]
    inv = quote(client, owner, db)
    done = pay(client, owner, inv["id"], 10800)
    response = client.post(
        f"{INVOICES}/{inv['id']}/payments/{done['payments'][0]['id']}/void", headers=owner.headers
    )
    assert response.status_code == 409
    assert response.json()["detail"].endswith("Refund it instead.")


# ---------------------------------------------------------------------------
# The old pay paths now leave a ledger row
# ---------------------------------------------------------------------------
def test_pay_now_records_one_payment_for_the_total(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    p = product(db)
    response = client.post(
        INVOICES,
        json={
            "items": [{"product_id": p, "qty": 1}],
            "pay_now": True,
            "payment_method": "card",
            "customer_id": customer(db),
        },
        headers=users["staff_a"].headers,
    )
    assert response.status_code == 201, response.text
    assert ledger(response.json()) == [("payment", "card", "succeeded", 10800)]


def test_pay_now_short_of_stock_keeps_no_payment(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    p = product(db, stock=0)
    name = f"IT-{tag()}"
    cid = db.execute(
        "insert into public.customers (tenant_id, first_name) values (%s, %s) returning id",
        (TENANT_A, name),
    ).fetchone()[0]
    response = client.post(
        INVOICES,
        json={
            "items": [{"product_id": p, "qty": 1}],
            "pay_now": True,
            "payment_method": "cash",
            "customer_id": str(cid),
        },
        headers=users["owner_a"].headers,
    )
    assert response.status_code == 409
    assert db.execute(
        "select count(*) from public.payments p join public.invoices i on i.id = p.invoice_id "
        "where i.customer_id = %s",
        (cid,),
    ).fetchone() == (0,)


def test_a_status_move_out_of_quote_records_only_the_balance(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    owner = users["owner_a"]
    inv = quote(client, owner, db)
    pay(client, owner, inv["id"], 3000)
    post(client, owner, f"{INVOICES}/{inv['id']}/start-production")
    response = client.post(
        f"{INVOICES}/{inv['id']}/status",
        json={"status": "paid", "payment_method": "bank_transfer"},
        headers=owner.headers,
    )
    assert response.status_code == 200, response.text
    moved = response.json()
    assert moved["order_status"] == "in_production"  # production had started
    assert ledger(moved) == [
        ("payment", "cash", "succeeded", 3000),
        ("payment", "bank_transfer", "succeeded", 7800),
    ]


def test_refunding_a_paid_invoice_records_the_refund(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    owner = users["owner_a"]
    inv = quote(client, owner, db)
    pay(client, owner, inv["id"], 10800, "card")
    refunded = post(client, owner, f"{INVOICES}/{inv['id']}/refund")
    assert refunded["refunded_at"] is not None and refunded["amount_paid_cents"] == 0
    assert ledger(refunded)[-1] == ("refund", "card", "succeeded", 10800)


def test_a_card_payment_taken_online_is_refunded_in_stripe(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    owner = users["owner_a"]
    inv = quote(client, owner, db)
    db.execute(
        "insert into public.payments (tenant_id, invoice_id, kind, provider, method, amount_cents, "
        "currency, external_id) values (%s, %s, 'payment', 'stripe', 'card', 10800, 'USD', %s)",
        (TENANT_A, inv["id"], f"pi_test_{tag()}"),
    )
    response = client.post(f"{INVOICES}/{inv['id']}/refund", headers=owner.headers)
    assert response.status_code == 409
    assert response.json()["detail"] == "This invoice was paid by card online. Refund it in Stripe."


def test_correcting_the_method_corrects_its_single_payment(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    owner = users["owner_a"]
    inv = quote(client, owner, db)
    pay(client, owner, inv["id"], 10800, "cash")
    response = client.patch(
        f"{INVOICES}/{inv['id']}", json={"payment_method": "card"}, headers=owner.headers
    )
    assert response.status_code == 200, response.text
    assert (response.json()["payment_method"], ledger(response.json())[0][1]) == ("card", "card")


# ---------------------------------------------------------------------------
# Two payments racing for the same balance
# ---------------------------------------------------------------------------
async def _wait_until_blocked(settings: Settings, pid: int) -> None:
    async with await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True) as w:
        for _ in range(200):
            cur = await w.execute(
                "select wait_event_type from pg_stat_activity where pid = %s", (pid,)
            )
            row = await cur.fetchone()
            if row and row[0] == "Lock":
                return
            await asyncio.sleep(0.05)
    raise AssertionError("the second payment never waited on the invoice lock")


@pytest.mark.anyio
async def test_two_payments_cannot_overpay(
    client: TestClient, users: dict[str, TestUser], settings: Settings, db
) -> None:
    inv = quote(client, users["owner_a"], db)
    insert = (
        "insert into public.payments (tenant_id, invoice_id, kind, method, amount_cents, currency) "
        "values (%s, %s, 'payment', 'cash', 10800, 'USD')"
    )

    def as_member(name: str) -> TokenClaims:
        return TokenClaims(
            sub=users[name].user_id,
            tenant_id=TENANT_A,
            app_role="owner" if name == "owner_a" else "staff",
        )

    async with (
        await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True) as c1,
        await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True) as c2,
    ):
        second: dict = {}

        async def second_payment() -> None:
            try:
                async with c2.transaction():
                    await apply_user_scope(c2, as_member("staff_a"))
                    await c2.execute(insert, (TENANT_A, inv["id"]))
                second["ok"] = True
            except psycopg.Error as exc:
                second["error"] = exc

        async with c1.transaction():
            await apply_user_scope(c1, as_member("owner_a"))
            await c1.execute(insert, (TENANT_A, inv["id"]))
            task = asyncio.create_task(second_payment())
            await _wait_until_blocked(settings, c2.info.backend_pid)
        await asyncio.wait_for(task, timeout=10)

    assert isinstance(second.get("error"), errors.CheckViolation)
    mapped = map_db_error(second["error"])
    assert (mapped.status_code, mapped.detail) == (422, "That is more than the 0.00 USD still due.")
    assert db.execute("select public.invoice_amount_paid(%s)", (inv["id"],)).fetchone() == (10800,)
