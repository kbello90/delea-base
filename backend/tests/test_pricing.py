"""Unit tests for app/services/pricing.py (pure functions, no database)."""

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.services.pricing import (
    CustomerPricing,
    LineRequest,
    PricingError,
    ProductPrice,
    price_quote,
    round_half_up_bps,
    sale_is_active,
    tax_rate_bps,
    unit_price_cents,
)

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
RETAIL = CustomerPricing()


def product(price: int, sale: int | None = None, ends: datetime | None = None) -> ProductPrice:
    return ProductPrice(uuid.uuid4(), "Ring", price, sale, ends)


def wholesale(bps: int, exempt: bool = False) -> CustomerPricing:
    return CustomerPricing(is_wholesale=True, wholesale_discount_bps=bps, tax_exempt=exempt)


# --- rounding ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("amount", "bps", "expected"),
    [
        (1, 5000, 1),  # 0.5 -> 1 (half up)
        (1, 4999, 0),  # 0.4999 -> 0
        (1, 5001, 1),
        (3, 5000, 2),  # 1.5 -> 2
        (5, 5000, 3),  # 2.5 -> 3 (not banker's 2)
        (9800, 800, 784),  # exact
        (17500, 888, 1554),  # 1554.0
        (12345, 875, 1080),  # 1080.1875 -> 1080
        (12346, 875, 1080),  # 1080.275 -> 1080
        (12360, 875, 1082),  # 1081.5 -> 1082
        (0, 800, 0),
        (12345, 0, 0),
        (12345, 10000, 12345),
    ],
)
def test_round_half_up(amount: int, bps: int, expected: int) -> None:
    assert round_half_up_bps(amount, bps) == expected


def test_round_half_up_matches_decimal_reference() -> None:
    rng = random.Random(20261020)
    for _ in range(5000):
        amount, bps = rng.randint(0, 10**9), rng.randint(0, 10000)
        reference = (Decimal(amount) * bps / 10000).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        assert round_half_up_bps(amount, bps) == int(reference)


def test_round_half_up_rejects_negatives() -> None:
    with pytest.raises(PricingError):
        round_half_up_bps(-1, 800)


# --- sale price -------------------------------------------------------------------------
def test_sale_active_rules() -> None:
    assert sale_is_active(product(1000, 800, NOW + timedelta(seconds=1)), NOW)
    assert sale_is_active(product(1000, 800, None), NOW)  # open-ended sale
    assert not sale_is_active(product(1000, 800, NOW), NOW)  # ends exactly now: over
    assert not sale_is_active(product(1000, 800, NOW - timedelta(days=1)), NOW)
    assert not sale_is_active(product(1000, None, NOW + timedelta(days=1)), NOW)


def test_retail_unit_price_uses_active_sale_only() -> None:
    assert unit_price_cents(product(1000, 800, NOW + timedelta(days=1)), RETAIL, NOW) == 800
    assert unit_price_cents(product(1000, 800, NOW - timedelta(days=1)), RETAIL, NOW) == 1000
    assert unit_price_cents(product(1000), RETAIL, NOW) == 1000


# --- wholesale: lower of (active price, discounted regular price); never stacked ---------
def test_wholesale_discount_applies_to_regular_price() -> None:
    assert unit_price_cents(product(10000), wholesale(1500), NOW) == 8500


def test_wholesale_takes_sale_when_sale_is_lower() -> None:
    # sale 7000 < discounted regular 8500 -> 7000 (NOT 7000 * 0.85 = 5950)
    assert unit_price_cents(product(10000, 7000), wholesale(1500), NOW) == 7000


def test_wholesale_takes_discount_when_discount_is_lower() -> None:
    # sale 9000 > discounted regular 8500 -> 8500 (NOT 9000 * 0.85 = 7650)
    assert unit_price_cents(product(10000, 9000), wholesale(1500), NOW) == 8500


def test_wholesale_ignores_expired_sale() -> None:
    expired = product(10000, 5000, NOW - timedelta(days=1))
    assert unit_price_cents(expired, wholesale(1500), NOW) == 8500


def test_wholesale_discount_rounds_half_up() -> None:
    # 999 * 0.85 = 849.15 -> 849; 1001 * 0.5 = 500.5 -> 501
    assert unit_price_cents(product(999), wholesale(1500), NOW) == 849
    assert unit_price_cents(product(1001), wholesale(5000), NOW) == 501


def test_retail_customer_never_gets_the_wholesale_discount() -> None:
    not_wholesale = CustomerPricing(is_wholesale=False, wholesale_discount_bps=5000)
    assert unit_price_cents(product(10000), not_wholesale, NOW) == 10000


@pytest.mark.parametrize("bps", [-1, 10001])
def test_wholesale_discount_bounds(bps: int) -> None:
    with pytest.raises(PricingError):
        unit_price_cents(product(10000), wholesale(bps), NOW)


# --- tax --------------------------------------------------------------------------------
def test_tax_is_computed_once_on_the_subtotal() -> None:
    # Two 1-cent lines at 50%: per line would be round(0.5)+round(0.5) = 2;
    # once on the subtotal it is round(1.0) = 1.
    lines = [LineRequest(product(1), 1), LineRequest(product(1), 1)]
    quote = price_quote(lines, RETAIL, default_tax_rate_bps=5000, now=NOW)
    assert (quote.subtotal_cents, quote.tax_cents, quote.total_cents) == (2, 1, 3)


def test_tax_exempt_customer_pays_zero_tax() -> None:
    quote = price_quote([LineRequest(product(10000), 2)], wholesale(1000, exempt=True), 800, NOW)
    assert (quote.tax_rate_bps, quote.tax_cents) == (0, 0)
    assert quote.total_cents == quote.subtotal_cents == 18000


def test_override_on_tax_exempt_customer_is_an_error() -> None:
    with pytest.raises(PricingError, match="tax-exempt"):
        tax_rate_bps(CustomerPricing(tax_exempt=True), 800, override_bps=500)


def test_owner_override_is_used_and_snapshotted() -> None:
    quote = price_quote([LineRequest(product(10000), 1)], RETAIL, 800, NOW, tax_rate_override_bps=0)
    assert (quote.tax_rate_bps, quote.tax_cents) == (0, 0)
    quote = price_quote(
        [LineRequest(product(10000), 1)], RETAIL, 800, NOW, tax_rate_override_bps=625
    )
    assert (quote.tax_rate_bps, quote.tax_cents) == (625, 625)


@pytest.mark.parametrize("rate", [-1, 10001])
def test_tax_rate_bounds(rate: int) -> None:
    with pytest.raises(PricingError):
        tax_rate_bps(RETAIL, rate, None)


# --- whole quotes -----------------------------------------------------------------------
def test_demo_invoices_price_like_the_seed() -> None:
    necklace = price_quote([LineRequest(product(9800), 1)], RETAIL, 800, NOW)
    assert (necklace.subtotal_cents, necklace.tax_cents, necklace.total_cents) == (9800, 784, 10584)


def test_lines_carry_snapshots() -> None:
    p = ProductPrice(uuid.uuid4(), "Celestial Charm", 9500)
    quote = price_quote([LineRequest(p, 3)], RETAIL, 800, NOW)
    (line,) = quote.lines
    assert (line.product_id, line.description, line.qty) == (p.product_id, "Celestial Charm", 3)
    assert (line.unit_price_cents, line.line_total_cents) == (9500, 28500)


def test_totals_are_always_consistent() -> None:
    rng = random.Random(42)
    for _ in range(2000):
        customer = rng.choice(
            [RETAIL, wholesale(rng.randint(0, 10000)), wholesale(rng.randint(0, 10000), True)]
        )
        lines = []
        for _ in range(rng.randint(1, 6)):
            price = rng.randint(0, 500_000)
            sale = rng.choice([None, rng.randint(0, 500_000)])
            ends = rng.choice([None, NOW - timedelta(days=1), NOW + timedelta(days=1)])
            lines.append(LineRequest(product(price, sale, ends), rng.randint(1, 50)))
        rate = rng.randint(0, 10000)
        q = price_quote(lines, customer, rate, NOW)

        assert q.subtotal_cents == sum(line.line_total_cents for line in q.lines)
        assert all(line.line_total_cents == line.unit_price_cents * line.qty for line in q.lines)
        assert q.tax_cents == round_half_up_bps(q.subtotal_cents, q.tax_rate_bps)
        assert q.total_cents == q.subtotal_cents + q.tax_cents
        assert q.tax_rate_bps == (0 if customer.tax_exempt else rate)
        for line, req in zip(q.lines, lines, strict=True):
            regular = req.product.price_cents
            active = req.product.sale_price_cents if sale_is_active(req.product, NOW) else regular
            if customer.is_wholesale:
                discounted = round_half_up_bps(regular, 10000 - customer.wholesale_discount_bps)
                assert line.unit_price_cents == min(active, discounted)
            else:
                assert line.unit_price_cents == active


@pytest.mark.parametrize("qty", [0, -1])
def test_non_positive_qty_is_rejected(qty: int) -> None:
    with pytest.raises(PricingError):
        price_quote([LineRequest(product(100), qty)], RETAIL, 800, NOW)


def test_empty_quote_and_overflow_are_rejected() -> None:
    with pytest.raises(PricingError):
        price_quote([], RETAIL, 800, NOW)
    with pytest.raises(PricingError, match="too large"):
        price_quote([LineRequest(product(2**62), 2)], RETAIL, 800, NOW)
