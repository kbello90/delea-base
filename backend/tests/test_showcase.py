"""Showcase loader, without a database: the catalog, the safety rules, the generated history,
and guards that keep photos and machine paths out of the repository."""

import re
import subprocess
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.pricing import CustomerPricing, LineRequest, ProductPrice, price_quote
from showcase.catalog import CatalogError, load_catalog, money_to_cents
from showcase.config import (
    DEFAULT_CATALOG,
    LOCAL_DEV_PASSWORD,
    ConfigRefused,
    build_config,
    is_local_url,
)
from showcase.history import MONTHS, TAX_RATE_BPS, SaleProduct, generate, stable_id

REPO = Path(__file__).resolve().parents[2]
TENANT = uuid.UUID("5ca1ab1e-0000-4000-8000-de1ea5000001")
LOCAL = dict(
    supabase_url="http://127.0.0.1:55321",
    supabase_service_role_key="local-key",
    database_url="postgresql://postgres:postgres@127.0.0.1:55322/postgres",
)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
def test_the_committed_products_yaml_is_valid() -> None:
    entries = load_catalog(DEFAULT_CATALOG)
    assert len(entries) == 10
    assert all(e.metal == "other" for e in entries)
    drafts = {e.sku for e in entries if not e.published}
    assert drafts == {"DL-AC-BOX-01", "DL-AC-TRAVEL-01"}
    assert all(1 <= len(e.images) <= 10 for e in entries)


@pytest.mark.parametrize(
    ("text", "cents"), [("38.00", 3800), ("38", 3800), ("38.5", 3850), ("0.05", 5)]
)
def test_money_to_cents(text: str, cents: int) -> None:
    assert money_to_cents(text) == cents


@pytest.mark.parametrize("value", [38.1, "38.123", "-1", "", None, "1,000"])
def test_money_to_cents_refuses(value) -> None:
    with pytest.raises(ValueError):
        money_to_cents(value)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "products.yaml"
    path.write_text(body, encoding="utf-8")
    return path


GOOD = """
  - sku: DL-NK-TEST-01
    folder: one
    name: Test Necklace
    description: A test.
    metal: other
    price: "40.00"
    sale_price: null
    stock: 5
    low_stock_threshold: 2
    published: true
    images: [a.png]
"""


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (("DL-NK-TEST-01", "dl-bad"), "sku must look like"),
        (('price: "40.00"', "price: 40.10"), "quoted amount"),
        (("sale_price: null", 'sale_price: "45.00"'), "sale_price must be lower"),
        (("metal: other", "metal: brass"), "metal must be one of"),
        (
            ("images: [a.png]", "images: [" + ", ".join(f"{i}.png" for i in range(11)) + "]"),
            "1 to 10 files",
        ),
        (("images: [a.png]", "images: [a.png, a.png]"), "listed twice"),
        (("images: [a.png]", "images: [a.gif]"), "must be .jpg"),
        (("stock: 5", "stock: -1"), "stock must be between"),
    ],
)
def test_catalog_problems_are_reported(tmp_path: Path, change, problem: str) -> None:
    path = _write(tmp_path, "products:" + GOOD.replace(*change))
    with pytest.raises(CatalogError, match=re.escape(problem)):
        load_catalog(path)


def test_a_duplicate_sku_is_reported(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="used twice"):
        load_catalog(_write(tmp_path, "products:" + GOOD + GOOD))


# ---------------------------------------------------------------------------
# Safety rules
# ---------------------------------------------------------------------------
def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**LOCAL, **overrides})


ENV = {"SHOWCASE_DIR": "C:/photos"}


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://127.0.0.1:55321", True),
        ("http://localhost:54321", True),
        ("postgresql://postgres:postgres@127.0.0.1:55322/postgres", True),
        ("https://abcd.supabase.co", False),
        ("postgresql://postgres.abcd:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres", False),
        ("http://127.0.0.1.evil.example:80", False),
        ("not a url", False),
    ],
)
def test_is_local_url(url: str, local: bool) -> None:
    assert is_local_url(url) is local


def test_local_defaults() -> None:
    config = build_config(ENV, _settings(), allow_remote=False)
    assert config.database_url == LOCAL["database_url"]
    assert config.password == LOCAL_DEV_PASSWORD
    assert [role for _, role in config.members] == ["owner", "staff", "cpa"]
    assert all(email.endswith("@showcase.delea.test") for email, _ in config.members)


def test_the_emails_are_configurable() -> None:
    env = {**ENV, "SHOWCASE_OWNER_EMAIL": "me@shop.test"}
    config = build_config(env, _settings(), allow_remote=False)
    assert config.members[0] == ("me@shop.test", "owner")


def test_a_remote_storage_url_is_refused_without_the_flag() -> None:
    with pytest.raises(ConfigRefused, match="--allow-remote"):
        build_config(ENV, _settings(supabase_url="https://abcd.supabase.co"), allow_remote=False)


def test_a_remote_database_url_is_refused_without_the_flag() -> None:
    env = {**ENV, "SHOWCASE_DATABASE_URL": "postgresql://u:p@db.abcd.supabase.co:5432/postgres"}
    with pytest.raises(ConfigRefused, match="--allow-remote"):
        build_config(env, _settings(), allow_remote=False)


def test_remote_needs_its_own_database_url_and_a_password() -> None:
    remote = _settings(
        supabase_url="https://abcd.supabase.co",
        database_url="postgresql://app:p@db.abcd.supabase.co:5432/postgres",
    )
    with pytest.raises(ConfigRefused, match="SHOWCASE_DATABASE_URL"):
        build_config(ENV, remote, allow_remote=True)
    env = {**ENV, "SHOWCASE_DATABASE_URL": "postgresql://admin:p@db.abcd.supabase.co:5432/postgres"}
    with pytest.raises(ConfigRefused, match="SHOWCASE_PASSWORD"):
        build_config(env, remote, allow_remote=True)
    config = build_config({**env, "SHOWCASE_PASSWORD": "from-the-env"}, remote, allow_remote=True)
    assert config.database_url == env["SHOWCASE_DATABASE_URL"]
    assert config.password == "from-the-env"


def test_showcase_dir_is_required() -> None:
    with pytest.raises(ConfigRefused, match="SHOWCASE_DIR"):
        build_config({}, _settings(), allow_remote=False)


# ---------------------------------------------------------------------------
# Generated history
# ---------------------------------------------------------------------------
def _products() -> list[SaleProduct]:
    return [
        SaleProduct(
            stable_id(TENANT, "product", e.sku),
            e.sku,
            e.name,
            e.price_cents,
            e.sale_price_cents,
            e.published,
        )
        for e in load_catalog(DEFAULT_CATALOG)
    ]


RUN_DATES = [
    datetime(2026, 10, 22, 14, tzinfo=UTC),
    datetime(2027, 1, 1, 2, tzinfo=UTC),  # just after a year boundary
    datetime(2026, 3, 31, 23, tzinfo=UTC),
]


@pytest.mark.parametrize("now", RUN_DATES)
def test_the_history_is_deterministic(now: datetime) -> None:
    a, b = generate(TENANT, _products(), now), generate(TENANT, _products(), now)
    assert (a.invoices, a.customers, a.expenses, a.materials) == (
        b.invoices,
        b.customers,
        b.expenses,
        b.materials,
    )


@pytest.mark.parametrize("now", RUN_DATES)
def test_the_history_looks_like_a_working_shop(now: datetime) -> None:
    h = generate(TENANT, _products(), now)
    statuses = Counter(inv.status for inv in h.invoices)
    assert statuses["quote"] == 3
    assert sum(1 for inv in h.invoices if inv.refunded_at) == 1
    assert sum(1 for inv in h.invoices if inv.cancelled_at) == 1
    assert statuses["delivered"] > len(h.invoices) / 2

    # Retail revenue rises month over month across the full months.
    retail = Counter()
    for inv in h.invoices:
        if inv.paid_at and inv.is_retail and not inv.refunded_at and not inv.cancelled_at:
            retail[(inv.paid_at.year, inv.paid_at.month)] += inv.quote.subtotal_cents
    full_months = sorted(retail)[: MONTHS - 1]
    values = [retail[m] for m in full_months]
    assert values == sorted(values), values

    # Nothing in the future; everything inside the window.
    limit = now - timedelta(hours=1)
    for inv in h.invoices:
        stamps = [inv.created_at, *(t for _, t in inv.steps), inv.refunded_at, inv.cancelled_at]
        assert all(h.window_start <= t <= limit for t in stamps if t), inv.id
    assert all(e.expense_date <= now.date() for e in h.expenses)


def test_customers_are_fictional_and_never_emailed() -> None:
    h = generate(TENANT, _products(), RUN_DATES[0])
    assert len(h.customers) == 14
    wholesale = [c for c in h.customers if c.customer_type == "wholesale"]
    assert len(wholesale) == 3
    assert [c.company_name for c in wholesale if c.tax_exempt] == ["Harbor Lane Gifts"]
    assert all(re.search(r"@([a-z]+\.)?example\.com$", c.email) for c in h.customers)
    assert all(c.phone.startswith("555-01") for c in h.customers)


def test_every_invoice_is_priced_by_the_api_rules() -> None:
    products = {p.product_id: p for p in _products()}
    h = generate(TENANT, list(products.values()), RUN_DATES[0])
    for inv in h.invoices:
        pricing = inv.customer.pricing() if inv.customer else CustomerPricing()
        expected = price_quote(
            [
                LineRequest(
                    ProductPrice(
                        line.product_id,
                        line.description,
                        products[line.product_id].price_cents,
                        products[line.product_id].sale_price_cents,
                    ),
                    line.qty,
                )
                for line in inv.quote.lines
            ],
            pricing,
            TAX_RATE_BPS,
            inv.created_at,
        )
        assert inv.quote == expected
        if inv.customer and inv.customer.tax_exempt:
            assert inv.quote.tax_cents == 0


def test_unpublished_products_are_never_sold() -> None:
    products = _products()
    drafts = {p.product_id for p in products if not p.published}
    h = generate(TENANT, products, RUN_DATES[0])
    sold = {line.product_id for inv in h.invoices for line in inv.quote.lines}
    assert drafts and not sold & drafts


# ---------------------------------------------------------------------------
# Repository guards: no photos, no machine paths, no showcase code in the app
# ---------------------------------------------------------------------------
def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return out.splitlines()


def test_only_the_yaml_and_readme_are_tracked_under_showcase() -> None:
    tracked = [f for f in _tracked() if f.startswith("showcase/")]
    assert set(tracked) <= {"showcase/products.yaml", "showcase/README.md"}, tracked


def test_no_tracked_file_contains_a_path_from_a_users_machine() -> None:
    pattern = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]|OneDrive[\\/]|/home/[a-z]+/|/Users/[a-z]+/")
    offenders = []
    for name in _tracked():
        path = REPO / name
        if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff2", ".pdf"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pattern.search(text):
            offenders.append(name)
    assert offenders == []


def test_the_app_never_imports_the_showcase_package() -> None:
    app_dir = REPO / "backend" / "app"
    offenders = [
        str(p.relative_to(REPO))
        for p in app_dir.rglob("*.py")
        if re.search(r"^\s*(from|import)\s+showcase\b", p.read_text(encoding="utf-8"), re.M)
    ]
    assert offenders == []
