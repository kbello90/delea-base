"""Quote pricing: pure functions over integer cents. No database, no floats.

Money rule (TRD "As built: backend"):

- Rounding is half-up, in integer arithmetic: round_half_up(a * bps / 10000) is
  (a * bps + 5000) // 10000 for non-negative a.
- Unit price:
    * the sale price while a sale is active (sale_price_cents set and sale_ends_at in the
      future; a sale with no sale_ends_at is open-ended and active), otherwise the regular
      price_cents;
    * for a wholesale customer, the LOWER of that value and
      round_half_up(price_cents * (10000 - discount_bps) / 10000). Discounts never stack: the
      wholesale discount applies to the regular price, never on top of a sale price.
- line_total = unit_price * qty; subtotal = sum of line totals.
- Tax is computed once on the subtotal: tax = round_half_up(subtotal * rate_bps / 10000).
- Tax rate: 0 for a tax-exempt customer; otherwise an owner-supplied override if given, else
  the tenant's default_tax_rate_bps. The rate used is stored on the invoice as a snapshot. An
  override on a tax-exempt customer is an error.
- total = subtotal + tax.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

BPS_DENOMINATOR = 10_000
BIGINT_MAX = 2**63 - 1


class PricingError(ValueError):
    """The inputs can't be priced (bad quantity, rate, or an override on an exempt customer)."""


def round_half_up_bps(amount_cents: int, bps: int) -> int:
    """amount * bps / 10000, rounded half-up. Both arguments must be non-negative."""
    if amount_cents < 0 or bps < 0:
        raise PricingError("amount and basis points must be non-negative")
    return (amount_cents * bps + BPS_DENOMINATOR // 2) // BPS_DENOMINATOR


def _check_bps(bps: int, what: str) -> None:
    if not 0 <= bps <= BPS_DENOMINATOR:
        raise PricingError(f"{what} must be between 0 and {BPS_DENOMINATOR} basis points")


@dataclass(frozen=True)
class ProductPrice:
    product_id: UUID
    name: str
    price_cents: int
    sale_price_cents: int | None = None
    sale_ends_at: datetime | None = None


@dataclass(frozen=True)
class CustomerPricing:
    is_wholesale: bool = False
    wholesale_discount_bps: int = 0
    tax_exempt: bool = False


@dataclass(frozen=True)
class LineRequest:
    product: ProductPrice
    qty: int


@dataclass(frozen=True)
class PricedLine:
    product_id: UUID
    description: str
    qty: int
    unit_price_cents: int
    line_total_cents: int


@dataclass(frozen=True)
class PricedQuote:
    lines: tuple[PricedLine, ...]
    subtotal_cents: int
    tax_rate_bps: int
    tax_cents: int
    total_cents: int


def sale_is_active(product: ProductPrice, now: datetime) -> bool:
    if product.sale_price_cents is None:
        return False
    return product.sale_ends_at is None or product.sale_ends_at > now


def unit_price_cents(product: ProductPrice, customer: CustomerPricing, now: datetime) -> int:
    if product.price_cents < 0 or (product.sale_price_cents or 0) < 0:
        raise PricingError("prices must be non-negative")
    active = product.sale_price_cents if sale_is_active(product, now) else product.price_cents
    assert active is not None
    if not customer.is_wholesale:
        return active
    _check_bps(customer.wholesale_discount_bps, "wholesale discount")
    wholesale = round_half_up_bps(
        product.price_cents, BPS_DENOMINATOR - customer.wholesale_discount_bps
    )
    return min(active, wholesale)


def tax_rate_bps(customer: CustomerPricing, default_rate_bps: int, override_bps: int | None) -> int:
    if customer.tax_exempt:
        if override_bps is not None:
            raise PricingError("a tax-exempt customer can't have a tax rate override")
        return 0
    rate = default_rate_bps if override_bps is None else override_bps
    _check_bps(rate, "tax rate")
    return rate


def price_quote(
    lines: Sequence[LineRequest],
    customer: CustomerPricing,
    default_tax_rate_bps: int,
    now: datetime,
    tax_rate_override_bps: int | None = None,
) -> PricedQuote:
    if not lines:
        raise PricingError("a quote needs at least one line")
    priced = []
    for line in lines:
        if line.qty <= 0:
            raise PricingError("qty must be positive")
        unit = unit_price_cents(line.product, customer, now)
        priced.append(
            PricedLine(
                product_id=line.product.product_id,
                description=line.product.name,
                qty=line.qty,
                unit_price_cents=unit,
                line_total_cents=unit * line.qty,
            )
        )
    subtotal = sum(p.line_total_cents for p in priced)
    rate = tax_rate_bps(customer, default_tax_rate_bps, tax_rate_override_bps)
    tax = round_half_up_bps(subtotal, rate)
    total = subtotal + tax
    if total > BIGINT_MAX:
        raise PricingError("quote total is too large")
    return PricedQuote(tuple(priced), subtotal, rate, tax, total)
