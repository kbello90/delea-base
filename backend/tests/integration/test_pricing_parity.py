"""Parity: the storefront's pricing (storefront_price_cart, in SQL) and the admin's
(app/services/pricing.py) give identical subtotal, tax and total for the same cart.

pricing.py has no shipping (admin invoices don't ship), so the expected storefront amounts are
pricing.py's subtotal plus the storefront shipping rule, with tax from pricing.py's own
rounding (round_half_up_bps) on the subtotal, or on subtotal + shipping when the shop taxes
shipping. Covered: half-cent rounding, sale prices (active and ended), the free-shipping
threshold (below, at, above), tax on shipping on and off, pickup, delivery not chosen, and a
few hundred seeded random carts. Runs on a throwaway shop.
"""

import random
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.services.pricing import (
    CustomerPricing,
    LineRequest,
    ProductPrice,
    price_quote,
    round_half_up_bps,
)

from .test_public_checkout import open_shop

pytestmark = pytest.mark.integration

# (name, price, sale price, sale ends in days; None: no end, negative: ended)
PRODUCTS = [
    ("Four", 4, None, None),
    ("Eight", 8, None, None),
    ("Forty", 40, None, None),
    ("Hundred", 100, None, None),
    ("Odd", 12345, None, None),
    ("Necklace", 9800, None, None),
    ("On sale", 10000, 7499, 10),
    ("Sale ended", 10000, 7499, -1),
    ("Sale no end", 5555, 3333, None),
]
# Each of these rates turns some cart above into an exact half cent of tax:
# 4 x 12.5%, 8 x 6.25%, 40 x 8.75%, 100 x 5.5% = .5 of a cent.
RATES = [0, 550, 625, 800, 875, 1025, 1250, 10000]


@pytest.fixture
def shop(make_tenant, db: psycopg.Connection) -> dict:
    tenant = make_tenant()
    open_shop(db, tenant.id, f"it-{uuid.uuid4().hex[:8]}.localhost:5174")
    now = datetime.now(UTC)
    ids = {}
    for name, price, sale, ends in PRODUCTS:
        ends_at = None if ends is None else now + timedelta(days=ends)
        ids[name] = str(
            db.execute(
                "insert into public.products (tenant_id, sku, name, metal, price_cents, "
                "sale_price_cents, sale_ends_at, stock_qty, is_published) "
                "values (%s, %s, %s, 'other', %s, %s, %s, 10000, true) returning id",
                (tenant.id, f"IT-{uuid.uuid4().hex[:10]}", name, price, sale, ends_at),
            ).fetchone()[0]
        )
    return {"tenant_id": tenant.id, "ids": ids}


def configure(db, shop: dict, *, rate: int, flat: int, free_over: int | None, tax_shipping: bool):
    db.execute(
        "update public.company_settings set default_tax_rate_bps = %s where tenant_id = %s",
        (rate, shop["tenant_id"]),
    )
    db.execute(
        "update public.storefront_settings set ship_flat_cents = %s, free_ship_over_cents = %s, "
        "tax_shipping = %s where tenant_id = %s",
        (flat, free_over, tax_shipping, shop["tenant_id"]),
    )


def storefront_price(db, shop: dict, cart: list[tuple[str, int]], fulfillment: str | None) -> dict:
    items = [{"product_id": shop["ids"][name], "qty": qty} for name, qty in cart]
    with db.transaction():
        db.execute("set local role storefront_writer")
        result = db.execute(
            "select public.storefront_price_cart(%s, %s, %s)",
            (shop["tenant_id"], Jsonb(items), fulfillment),
        ).fetchone()[0]
    assert result["ok"] and not result["has_problems"], result
    return result


def admin_price(
    db, cart: list[tuple[str, int]], *, rate: int, flat: int, free_over, tax_shipping, fulfillment
) -> dict:
    """pricing.py's answer, plus the storefront's shipping rule."""
    now = db.execute("select now()").fetchone()[0]
    by_name = {name: (price, sale, ends) for name, price, sale, ends in PRODUCTS}
    lines = []
    for name, qty in cart:
        price, sale, ends = by_name[name]
        ends_at = None if ends is None else now + timedelta(days=ends)
        product = ProductPrice(uuid.uuid4(), name, price, sale, ends_at)
        lines.append(LineRequest(product, qty))
    quote = price_quote(lines, CustomerPricing(), default_tax_rate_bps=rate, now=now)

    if fulfillment == "ship":
        shipping = 0 if free_over is not None and quote.subtotal_cents >= free_over else flat
    elif fulfillment == "pickup":
        shipping = 0
    else:
        shipping = None
    if tax_shipping and shipping:
        tax = round_half_up_bps(quote.subtotal_cents + shipping, rate)
    else:
        tax = quote.tax_cents
    return {
        "subtotal_cents": quote.subtotal_cents,
        "shipping_cents": shipping,
        "tax_cents": tax,
        "total_cents": quote.subtotal_cents + (shipping or 0) + tax,
    }


def check(
    db, shop, cart, *, rate, flat=600, free_over=15000, tax_shipping=False, fulfillment="ship"
):
    configure(db, shop, rate=rate, flat=flat, free_over=free_over, tax_shipping=tax_shipping)
    got = storefront_price(db, shop, cart, fulfillment)
    want = admin_price(
        db, cart, rate=rate, flat=flat, free_over=free_over, tax_shipping=tax_shipping,
        fulfillment=fulfillment,
    )  # fmt: skip
    assert {k: got[k] for k in want} == want, (
        cart,
        rate,
        flat,
        free_over,
        tax_shipping,
        fulfillment,
    )
    return got


# ---------------------------------------------------------------------------
# Named edge cases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("cart", "rate", "tax"),
    [
        ([("Four", 1)], 1250, 1),  # 0.5 cent -> 1
        ([("Eight", 1)], 625, 1),  # 0.5 -> 1
        ([("Forty", 1)], 875, 4),  # 3.5 -> 4
        ([("Hundred", 1)], 550, 6),  # 5.5 -> 6
        ([("Hundred", 3)], 550, 17),  # 16.5 -> 17
        ([("Odd", 1)], 875, 1080),  # 1080.1875 -> 1080
        ([("Odd", 1)], 1025, 1265),  # 1265.3625 -> 1265
        ([("Four", 1)], 0, 0),
        ([("Necklace", 1)], 10000, 9800),
    ],
)
def test_half_cent_and_rate_edges(db, shop, cart, rate: int, tax: int) -> None:
    got = check(db, shop, cart, rate=rate, fulfillment="pickup")
    assert got["tax_cents"] == tax


def test_sale_prices_active_ended_and_open_ended(db, shop) -> None:
    got = check(db, shop, [("On sale", 1), ("Sale ended", 1), ("Sale no end", 2)], rate=800)
    assert got["subtotal_cents"] == 7499 + 10000 + 2 * 3333


@pytest.mark.parametrize(
    ("cart", "shipping"),
    [
        ([("Necklace", 1)], 600),  # 9800: below 25000
        ([("Necklace", 1), ("Odd", 2)], 0),  # 9800 + 24690 = 34490: over 25000, free
    ],
)
def test_free_shipping_threshold_below_and_over(db, shop, cart, shipping: int) -> None:
    got = check(db, shop, cart, rate=800, free_over=25000)
    assert got["shipping_cents"] == shipping


def test_free_shipping_exactly_at_the_threshold(db, shop) -> None:
    # 9800 + 100 x 2 = 10000: exactly at a threshold of 10000 is free.
    got = check(db, shop, [("Necklace", 1), ("Hundred", 2)], rate=800, free_over=10000)
    assert got["shipping_cents"] == 0
    below = check(db, shop, [("Necklace", 1), ("Hundred", 1)], rate=800, free_over=10000)
    assert below["shipping_cents"] == 600


@pytest.mark.parametrize("tax_shipping", [False, True])
@pytest.mark.parametrize("flat", [0, 595, 600])
def test_tax_on_shipping_on_and_off(db, shop, tax_shipping: bool, flat: int) -> None:
    got = check(
        db, shop, [("Forty", 1)], rate=875, flat=flat, free_over=None, tax_shipping=tax_shipping
    )
    expected_base = 40 + (flat if tax_shipping else 0)
    assert got["tax_cents"] == round_half_up_bps(expected_base, 875)


@pytest.mark.parametrize("fulfillment", ["pickup", None])
def test_pickup_and_not_chosen_have_no_shipping(db, shop, fulfillment) -> None:
    got = check(db, shop, [("Odd", 2)], rate=625, tax_shipping=True, fulfillment=fulfillment)
    assert got["shipping_cents"] == (0 if fulfillment == "pickup" else None)


# ---------------------------------------------------------------------------
# Seeded random carts
# ---------------------------------------------------------------------------
def test_random_carts_agree(db, shop) -> None:
    rng = random.Random(20261027)
    names = [p[0] for p in PRODUCTS]
    for _ in range(300):
        cart = [(rng.choice(names), rng.randint(1, 99)) for _ in range(rng.randint(1, 6))]
        check(
            db,
            shop,
            cart,
            rate=rng.choice(RATES + [rng.randint(0, 10000)]),
            flat=rng.choice([0, 1, 595, 600, 1250]),
            free_over=rng.choice([None, 0, 5000, 15000, 100000]),
            tax_shipping=rng.random() < 0.5,
            fulfillment=rng.choice(["ship", "ship", "pickup", None]),
        )
