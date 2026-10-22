"""A believable six-month history for the showcase, generated without a database.

Deterministic: the same seed, products and `now` give exactly the same history, ids included
(uuid5 under the tenant id). Dates are relative to `now`, so a run on another day looks the
same, shifted. Every price goes through app.services.pricing, the API's own rules.

Shape: invoices from the first day of the month five months back up to `now`. Retail revenue
rises each month; about 40% of retail sales are walk-in sales (pop-up markets, no customer
record), the rest come from eleven retail customers who arrive over the months. Three
wholesale orders (one tax-exempt customer) come on top in later months. Older orders are
delivered, recent ones in progress; three open quotes, one refund, one cancellation.
Unpublished products are never sold.
"""

import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.services.invoices import customer_display_name, due_date_for
from app.services.pricing import (
    CustomerPricing,
    LineRequest,
    PricedQuote,
    ProductPrice,
    price_quote,
)

SEED = 1904
TAX_RATE_BPS = 800
MONTHS = 6  # the dashboard trend: five full months back plus the current one
# Retail revenue (subtotal) per month, oldest first, before scaling the current month.
MONTHLY_RETAIL_CENTS = (60_000, 72_000, 86_000, 100_000, 118_000, 140_000)
WALK_IN_SHARE = 0.4
SALES_WEIGHTS = {  # by SKU; anything else sells at weight 1
    "DL-NK-HEART-01": 6,
    "DL-ER-STUD-01": 5,
    "DL-NK-HEART-INIT-01": 4,
    "DL-NK-FLOWER-01": 3,
    "DL-NK-SCRIPT-01": 3,
    "DL-NK-ZODIAC-01": 3,
    "DL-NK-BEZEL-01": 3,
    "DL-NK-LAYER-01": 2,
}


def stable_id(tenant_id: uuid.UUID, kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(tenant_id, f"{kind}:{key}")


@dataclass(frozen=True)
class SaleProduct:
    product_id: uuid.UUID
    sku: str
    name: str
    price_cents: int
    sale_price_cents: int | None
    published: bool


@dataclass(frozen=True)
class Customer:
    id: uuid.UUID
    customer_type: str
    first_name: str
    last_name: str
    email: str
    phone: str
    address_line1: str
    city: str
    state: str
    postal_code: str
    company_name: str | None = None
    tax_exempt: bool = False
    resale_certificate_number: str | None = None
    wholesale_discount_bps: int = 0
    payment_terms: str = "due_on_receipt"
    created_at: datetime | None = None  # set from the first order

    def as_row(self) -> dict:
        return {
            "customer_type": self.customer_type,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "company_name": self.company_name,
            "payment_terms": self.payment_terms,
        }

    def pricing(self) -> CustomerPricing:
        return CustomerPricing(
            is_wholesale=self.customer_type == "wholesale",
            wholesale_discount_bps=self.wholesale_discount_bps,
            tax_exempt=self.tax_exempt,
        )


@dataclass(frozen=True)
class Material:
    id: uuid.UUID
    name: str
    unit: str
    unit_cost_cents: int
    qty_on_hand: Decimal
    reorder_threshold: Decimal
    created_at: datetime


@dataclass(frozen=True)
class Expense:
    id: uuid.UUID
    expense_date: date
    description: str
    category: str
    amount_cents: int
    is_tax_deductible: bool


@dataclass(frozen=True)
class Invoice:
    id: uuid.UUID
    customer: Customer | None  # None: a walk-in sale
    created_at: datetime
    quote: PricedQuote
    due_date: date | None
    # Status changes after creation, oldest first: ("paid", t), ("in_production", t), ...
    steps: tuple[tuple[str, datetime], ...]
    refunded_at: datetime | None = None
    cancelled_at: datetime | None = None

    @property
    def customer_name(self) -> str:
        return customer_display_name(self.customer.as_row() if self.customer else None)

    @property
    def is_retail(self) -> bool:
        return self.customer is None or self.customer.customer_type == "retail"

    @property
    def paid_at(self) -> datetime | None:
        return self.steps[0][1] if self.steps else None

    @property
    def status(self) -> str:
        return self.steps[-1][0] if self.steps else "quote"

    @property
    def payment_method(self) -> str | None:
        """Wholesale pays by bank transfer; retail mostly by card, about one sale in four in
        cash. Derived from the id, so it draws nothing from the random sequence."""
        if not self.steps:
            return None
        if not self.is_retail:
            return "bank_transfer"
        return "cash" if self.id.int % 4 == 0 else "card"


@dataclass
class History:
    tenant_created_at: datetime
    window_start: datetime
    customers: list[Customer]
    materials: list[Material]
    invoices: list[Invoice]  # oldest first
    expenses: list[Expense]
    units_sold: dict[uuid.UUID, int] = field(default_factory=dict)  # left quote, by product


# ---------------------------------------------------------------------------
# Fixed cast (fictional people and shops; example.com addresses never receive mail)
# ---------------------------------------------------------------------------
_RETAIL = (
    ("Ava", "Thompson", "Austin", "TX", "78704"),
    ("Mia", "Rodriguez", "San Antonio", "TX", "78209"),
    ("Chloe", "Nguyen", "Houston", "TX", "77006"),
    ("Sofia", "Patel", "Dallas", "TX", "75206"),
    ("Emma", "Johnson", "Denver", "CO", "80206"),
    ("Isabella", "Garcia", "Phoenix", "AZ", "85004"),
    ("Grace", "Kim", "Seattle", "WA", "98103"),
    ("Lily", "Carter", "Nashville", "TN", "37203"),
    ("Zoe", "Bennett", "Portland", "OR", "97214"),
    ("Hannah", "Brooks", "Charlotte", "NC", "28203"),
    ("Olivia", "Reyes", "Miami", "FL", "33133"),
)
_WHOLESALE = (
    # company, contact first, last, city, state, zip, discount bps, terms, tax exempt
    (
        "Willow & Thread Boutique",
        "Rachel",
        "Moore",
        "Fort Worth",
        "TX",
        "76107",
        3000,
        "net_30",
        False,
    ),
    ("Harbor Lane Gifts", "Daniel", "Price", "Savannah", "GA", "31401", 2500, "net_15", True),
    ("Maple Row Mercantile", "Laura", "Simmons", "Asheville", "NC", "28801", 2000, "net_30", False),
)
_STREETS = ("Maple Avenue", "Cedar Lane", "Oak Street", "Willow Drive", "Elm Court", "Birch Road")


def _customers(tenant_id: uuid.UUID, rng: random.Random) -> list[Customer]:
    result = []
    for i, (first, last, city, state, postal) in enumerate(_RETAIL):
        result.append(
            Customer(
                id=stable_id(tenant_id, "customer", f"{first} {last}"),
                customer_type="retail",
                first_name=first,
                last_name=last,
                email=f"{first.lower()}.{last.lower()}@example.com",
                phone=f"555-01{i + 10:02d}",
                address_line1=f"{rng.randint(100, 989)} {rng.choice(_STREETS)}",
                city=city,
                state=state,
                postal_code=postal,
            )
        )
    for i, (company, first, last, city, state, postal, bps, terms, exempt) in enumerate(_WHOLESALE):
        slug = company.lower().replace("& ", "").replace(" ", "")
        result.append(
            Customer(
                id=stable_id(tenant_id, "customer", company),
                customer_type="wholesale",
                first_name=first,
                last_name=last,
                email=f"orders@{slug}.example.com",
                phone=f"555-01{i + 40:02d}",
                address_line1=f"{rng.randint(100, 989)} Main Street",
                city=city,
                state=state,
                postal_code=postal,
                company_name=company,
                tax_exempt=exempt,
                resale_certificate_number="RC-EXAMPLE-0427" if exempt else None,
                wholesale_discount_bps=bps,
                payment_terms=terms,
            )
        )
    return result


def _month_starts(now: datetime) -> list[datetime]:
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    starts = [first]
    for _ in range(MONTHS - 1):
        starts.insert(0, (starts[0] - timedelta(days=1)).replace(day=1))
    return starts


def _moment(rng: random.Random, start: datetime, end: datetime) -> datetime:
    """A random business-hours moment (13:00-23:00 UTC) in [start, end)."""
    days = max(1, (end - start).days)
    day = start + timedelta(days=rng.randrange(days))
    return day.replace(hour=rng.randint(13, 22), minute=rng.randrange(60), second=0)


def _timeline(
    paid_at: datetime, rng: random.Random, limit: datetime
) -> tuple[tuple[str, datetime], ...]:
    """paid -> in_production -> shipped -> delivered, keeping only the steps already reached."""
    steps = [("paid", paid_at)]
    at = paid_at + timedelta(hours=rng.randint(18, 30))
    steps.append(("in_production", at))
    at += timedelta(days=rng.randint(1, 3))
    steps.append(("shipped", at))
    at += timedelta(days=rng.randint(3, 6))
    steps.append(("delivered", at))
    return tuple(s for s in steps if s[1] <= limit)


def _priced(lines, customer: Customer | None, at: datetime) -> PricedQuote:
    requests = [
        LineRequest(ProductPrice(p.product_id, p.name, p.price_cents, p.sale_price_cents), qty)
        for p, qty in lines
    ]
    pricing = customer.pricing() if customer else CustomerPricing()
    return price_quote(requests, pricing, TAX_RATE_BPS, at)


def generate(
    tenant_id: uuid.UUID,
    products: list[SaleProduct],
    now: datetime,
    seed: int = SEED,
) -> History:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware (UTC)")
    now = now.astimezone(UTC)
    rng = random.Random(seed)
    limit = now - timedelta(hours=1)  # nothing happens in the last hour
    starts = _month_starts(now)
    window_start = starts[0]
    tenant_created_at = window_start - timedelta(days=21)

    customers = _customers(tenant_id, rng)
    retail = [c for c in customers if c.customer_type == "retail"]
    wholesale = {c.company_name: c for c in customers if c.customer_type == "wholesale"}
    sellable = [p for p in products if p.published]
    if not sellable:
        raise ValueError("at least one product must be published to generate sales")
    weights = [SALES_WEIGHTS.get(p.sku, 1) for p in sellable]

    def pick_lines(max_lines: int, max_qty: int) -> list[tuple[SaleProduct, int]]:
        count = rng.choice([1] * 5 + [2] * 4 + [3] * 1) if max_lines > 1 else 1
        chosen: dict[uuid.UUID, tuple[SaleProduct, int]] = {}
        for _ in range(count):
            product = rng.choices(sellable, weights)[0]
            qty = rng.choice([1] * 8 + [2] * 2) if max_qty <= 2 else rng.randint(4, max_qty)
            chosen.setdefault(product.product_id, (product, qty))
        return list(chosen.values())

    # Retail orders, month by month, until each month's target is reached.
    orders: list[tuple[datetime, Customer | None, list[tuple[SaleProduct, int]]]] = []
    newcomers = rng.sample(retail, len(retail))  # customers arrive over the months
    regulars: list[Customer] = []
    for m, start in enumerate(starts):
        end = starts[m + 1] if m + 1 < MONTHS else limit
        if end <= start:
            continue
        target = MONTHLY_RETAIL_CENTS[m]
        if m == MONTHS - 1:
            month_length = ((start + timedelta(days=32)).replace(day=1) - start).total_seconds()
            target = int(target * min(1.0, (end - start).total_seconds() / month_length))
        total, misses = 0, 0
        while total < target and misses < 50:  # misses: a too-short window near `now`
            at = _moment(rng, start, end)
            if at > limit or at >= end:
                misses += 1
                continue
            if rng.random() < WALK_IN_SHARE:
                customer = None
            elif newcomers and (not regulars or rng.random() < 0.3):
                customer = newcomers.pop()
                regulars.append(customer)
            else:
                customer = rng.choice(regulars)
            lines = pick_lines(3, 2)
            orders.append((at, customer, lines))
            total += _priced(lines, customer, at).subtotal_cents

    # Wholesale orders on top (months 2, 3 and 4 of the window).
    wholesale_months = (
        (2, "Willow & Thread Boutique"),
        (3, "Harbor Lane Gifts"),
        (4, "Maple Row Mercantile"),
    )
    for m, company in wholesale_months:
        start = starts[m]
        at = _moment(rng, start + timedelta(days=5), start + timedelta(days=20))
        lines = [(p, rng.randint(4, 8)) for p in rng.sample(sellable, k=min(3, len(sellable)))]
        orders.append((at, wholesale[company], lines))

    orders.sort(key=lambda o: o[0])
    invoices: list[Invoice] = []
    for i, (at, customer, lines) in enumerate(orders):
        created_at = at - timedelta(minutes=rng.randint(3, 40))
        quote = _priced(lines, customer, at)
        invoices.append(
            Invoice(
                id=stable_id(tenant_id, "invoice", str(i)),
                customer=customer,
                created_at=created_at,
                quote=quote,
                due_date=due_date_for(customer and customer.payment_terms, at.date()),
                steps=_timeline(at, rng, limit),
            )
        )

    # One refund: a delivered retail order from two to three months ago.
    refund_window = (now - timedelta(days=92), now - timedelta(days=60))
    for idx, inv in enumerate(invoices):
        if (
            inv.is_retail
            and inv.status == "delivered"
            and refund_window[0] <= inv.paid_at <= refund_window[1]
        ):
            invoices[idx] = _replace(inv, refunded_at=inv.steps[-1][1] + timedelta(days=5))
            break
    # One cancellation: a retail order about five weeks ago, cancelled the next day while
    # still "paid" (it never moves on).
    for idx, inv in enumerate(invoices):
        if (
            inv.is_retail
            and inv.refunded_at is None
            and now - timedelta(days=40) <= inv.paid_at <= now - timedelta(days=30)
        ):
            invoices[idx] = _replace(
                inv, steps=inv.steps[:1], cancelled_at=inv.paid_at + timedelta(hours=20)
            )
            break

    # Three open quotes in the last three weeks (two retail, one wholesale).
    quote_customers = [rng.choice(retail), rng.choice(retail), wholesale["Maple Row Mercantile"]]
    for q, customer in enumerate(quote_customers):
        at = _moment(rng, now - timedelta(days=20), limit)
        at = min(at, limit)
        is_wholesale = customer.customer_type == "wholesale"
        lines = (
            [(p, rng.randint(6, 10)) for p in rng.sample(sellable, k=min(2, len(sellable)))]
            if is_wholesale
            else pick_lines(2, 2)
        )
        invoices.append(
            Invoice(
                id=stable_id(tenant_id, "quote", str(q)),
                customer=customer,
                created_at=at,
                quote=_priced(lines, customer, at),
                due_date=due_date_for(customer.payment_terms, at.date()),
                steps=(),
            )
        )
    invoices.sort(key=lambda inv: inv.created_at)

    # Customers exist from just before their first order (or the window start).
    first_order: dict[uuid.UUID, datetime] = {}
    for inv in invoices:
        if inv.customer:
            first_order.setdefault(inv.customer.id, inv.created_at)
    customers = [
        _replace(c, created_at=first_order.get(c.id, window_start) - timedelta(hours=2))
        for c in customers
    ]
    by_id = {c.id: c for c in customers}
    invoices = [
        _replace(inv, customer=by_id[inv.customer.id]) if inv.customer else inv for inv in invoices
    ]

    units_sold: dict[uuid.UUID, int] = {}
    for inv in invoices:
        if inv.steps:  # left quote: stock was decremented (no restock on refund or cancel)
            for line in inv.quote.lines:
                units_sold[line.product_id] = units_sold.get(line.product_id, 0) + line.qty

    return History(
        tenant_created_at=tenant_created_at,
        window_start=window_start,
        customers=customers,
        materials=_materials(tenant_id, tenant_created_at),
        invoices=invoices,
        expenses=_expenses(tenant_id, rng, starts, now, invoices),
        units_sold=units_sold,
    )


def _replace(obj, **changes):
    from dataclasses import replace

    return replace(obj, **changes)


def _materials(tenant_id: uuid.UUID, created: datetime) -> list[Material]:
    rows = (
        # name, unit, unit cost (cents), on hand, reorder threshold
        ("Gold-tone cable chain", "m", 120, "42.50", "15.00"),
        ("Jump rings", "pcs", 4, "850.00", "300.00"),
        ("Lobster clasps", "pcs", 9, "410.00", "150.00"),
        ("Polishing cloths", "pcs", 35, "120.00", "40.00"),
        ("Gift boxes", "pcs", 110, "18.00", "25.00"),  # below the threshold: low stock
        ("Padded mailers", "pcs", 28, "160.00", "50.00"),
    )
    return [
        Material(
            id=stable_id(tenant_id, "material", name),
            name=name,
            unit=unit,
            unit_cost_cents=cost,
            qty_on_hand=Decimal(qty),
            reorder_threshold=Decimal(threshold),
            created_at=created + timedelta(days=1, hours=i),
        )
        for i, (name, unit, cost, qty, threshold) in enumerate(rows)
    ]


def _expenses(
    tenant_id: uuid.UUID,
    rng: random.Random,
    starts: list[datetime],
    now: datetime,
    invoices: list[Invoice],
) -> list[Expense]:
    today = now.date()
    result: list[Expense] = []

    def add(day: date, description: str, category: str, cents: int, deductible: bool = True):
        if day <= today:
            key = f"{day.isoformat()}:{description}"
            result.append(
                Expense(
                    stable_id(tenant_id, "expense", key),
                    day,
                    description,
                    category,
                    cents,
                    deductible,
                )
            )

    for m, start in enumerate(starts):
        month = start.date()
        revenue = sum(
            inv.quote.subtotal_cents
            for inv in invoices
            if inv.paid_at
            and inv.paid_at.year == month.year
            and inv.paid_at.month == month.month
            and inv.refunded_at is None
            and inv.cancelled_at is None
        )
        add(
            month.replace(day=3),
            "Chain and findings restock",
            "materials",
            rng.randint(18_000, 32_000),
        )
        if m % 2 == 0:
            add(
                month.replace(day=6),
                "Gift boxes and polishing cloths",
                "materials",
                rng.randint(9_000, 14_000),
            )
        add(month.replace(day=10), "Social media ads", "marketing", rng.randint(5_000, 12_000))
        add(month.replace(day=15), "Shipping labels and mailers", "shipping", 4_000 + revenue // 25)
        add(month.replace(day=20), "Personal share of phone bill", "phone", 2_500, False)
        # Last month's platform and payment fees are billed on the 1st.
        next_month = (start + timedelta(days=32)).replace(day=1).date()
        add(
            next_month,
            "Online shop platform and payment fees",
            "platform_fees",
            (revenue * 65 + 500) // 1000,
        )
    add(starts[2].date().replace(day=12), "Coffee with a supplier", "meals", 2_400, False)
    return sorted(result, key=lambda e: (e.expense_date, e.description))
