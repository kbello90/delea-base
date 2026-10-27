"""The showcase loader end to end against the local stack, with generated photos and its own
test tenant (never the real showcase): the dashboard equals sums computed independently from
the loaded rows, a second run gives the same result, the integrity checks catch what they
must, and a member sees only the showcase. Tenants A and B are guarded by the session-wide
seed fingerprint."""

import io
import json
import uuid
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core import storage
from app.core.auth_admin import AuthAdmin
from app.services.provisioning import (
    ProvisioningError,
    delete_demo_tenant,
    remove_tenant_storage,
)
from showcase import integrity
from showcase.load import ShowcaseConfig, load_showcase

from .support import PASSWORD, TENANT_A, run_async, sign_in

pytestmark = pytest.mark.integration

CATALOG = """
products:
  - sku: DL-IT-SALE-01
    folder: sale
    name: IT Sale Necklace
    description: Test product on sale.
    metal: other
    price: "38.00"
    sale_price: "32.00"
    stock: 20
    low_stock_threshold: 5
    published: true
    images: [front.png, "worn photo.jpg", alpha.png]
  - sku: DL-IT-LOW-01
    folder: low
    name: IT Low Necklace
    description: Test product that ends low.
    metal: other
    price: "45.00"
    sale_price: null
    stock: 2
    low_stock_threshold: 4
    published: true
    images: [one.jpg]
  - sku: DL-IT-STUD-01
    folder: stud
    name: IT Stud Earrings
    description: Test earrings.
    metal: other
    price: "28.00"
    sale_price: null
    stock: 15
    low_stock_threshold: 3
    published: true
    images: [a.png, b.png]
  - sku: DL-IT-DRAFT-01
    folder: draft
    name: IT Draft Box
    description: Test draft product.
    metal: other
    price: "24.00"
    sale_price: null
    stock: 6
    low_stock_threshold: 2
    published: false
    images: [box.png]
"""


def _photo(path: Path, color, alpha: bool = False) -> None:
    mode = "RGBA" if alpha else "RGB"
    image = Image.new(mode, (900, 700), (*color, 255) if alpha else color)
    if alpha:
        image.paste((0, 0, 0, 0), (0, 0, 300, 700))
    image.save(path, quality=90) if path.suffix == ".jpg" else image.save(path)


@pytest.fixture(scope="module")
def photos(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("photos")
    files = {
        "sale": [
            ("front.png", (200, 120, 60), False),
            ("worn photo.jpg", (90, 60, 40), False),
            ("alpha.png", (212, 175, 55), True),
        ],
        "low": [("one.jpg", (30, 120, 90), False)],
        "stud": [("a.png", (20, 40, 160), False), ("b.png", (160, 20, 60), False)],
        "draft": [("box.png", (230, 190, 200), False)],
    }
    for folder, images in files.items():
        (root / folder).mkdir()
        for name, color, alpha in images:
            _photo(root / folder / name, color, alpha)
    (root / "products.yaml").write_text(CATALOG, encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def showcase(settings, photos) -> dict:
    run = uuid.uuid4().hex[:8]
    config = ShowcaseConfig(
        database_url=settings.database_url,
        settings=settings,
        catalog_path=photos / "products.yaml",
        photos_dir=photos,
        members=tuple((f"it-showcase-{run}-{r}@test.local", r) for r in ("owner", "staff", "cpa")),
        password=PASSWORD,
        now=datetime.now(UTC).replace(minute=0, second=0, microsecond=0),
        tenant_id=uuid.uuid4(),
        tenant_name="IT Showcase",
        storefront_domain=f"it-showcase-{uuid.uuid4().hex[:8]}.localhost:5174",
    )
    report = run_async(load_showcase(config))
    yield {"config": config, "report": report, "id": str(config.tenant_id)}
    run_async(_teardown(config))


async def _teardown(config: ShowcaseConfig) -> None:
    async with await psycopg.AsyncConnection.connect(config.database_url, autocommit=True) as c:
        await remove_tenant_storage(c, config.tenant_id)
        await delete_demo_tenant(c, config.tenant_id)
    admin = AuthAdmin(config.settings.supabase_url, config.settings.supabase_service_role_key)
    for email, _ in config.members:
        user_id = await admin.find_user_id(email)
        if user_id:
            await admin.delete_user(user_id)


def _token(settings, showcase, role: str) -> dict[str, str]:
    email = next(e for e, r in showcase["config"].members if r == role)
    token = sign_in(settings.supabase_url, settings.supabase_anon_key, email, PASSWORD)
    return {"Authorization": f"Bearer {token}"}


# Columns that legitimately differ between two runs (database clock, random tokens/ids).
_VOLATILE = {"updated_at", "public_token"}
_VOLATILE_BY_TABLE = {"tenant_members": {"created_at"}, "automation_events": {"id"}}


def _snapshot(db, tenant_id: str) -> dict[str, list[str]]:
    tables = [
        r[0]
        for r in db.execute(
            "select table_name from information_schema.columns where table_schema = 'public' "
            "and column_name = 'tenant_id' group by table_name order by table_name"
        ).fetchall()
    ]
    snapshot = {}
    for table in [*tables, "tenants"]:
        column = "id" if table == "tenants" else "tenant_id"
        drop = sorted(_VOLATILE | _VOLATILE_BY_TABLE.get(table, set()))
        rows = db.execute(
            f"select (to_jsonb(t) - %s::text[])::text from public.{table} t where {column} = %s",
            (drop, tenant_id),
        ).fetchall()
        snapshot[table] = sorted(r[0] for r in rows)
    snapshot["storage"] = sorted(
        f"{r[0]}:{r[1]}:{r[2]}"
        for r in db.execute(
            "select bucket_id, name, metadata->>'size' from storage.objects where name like %s",
            (f"{tenant_id}/%",),
        ).fetchall()
    )
    return snapshot


# ---------------------------------------------------------------------------
# What was loaded
# ---------------------------------------------------------------------------
def test_the_tenant_is_a_demo_tenant_with_its_members(showcase, db) -> None:
    tid = showcase["id"]
    assert db.execute(
        "select name, is_demo from public.tenants where id = %s", (tid,)
    ).fetchone() == (
        "IT Showcase",
        True,
    )
    assert db.execute(
        "select default_tax_rate_bps from public.company_settings where tenant_id = %s", (tid,)
    ).fetchone() == (800,)
    assert db.execute(
        "select business_name from public.brand_config where tenant_id = %s", (tid,)
    ).fetchone() == ("DELEA Jewelry",)
    # Its local storefront is on.
    assert db.execute(
        "select d.domain, d.kind, s.storefront_enabled from public.tenant_domains d "
        "join public.storefront_settings s using (tenant_id) where d.tenant_id = %s",
        (tid,),
    ).fetchall() == [(showcase["config"].storefront_domain, "storefront", True)]
    roles = Counter(
        r[0]
        for r in db.execute(
            "select app_role::text from public.tenant_members where tenant_id = %s", (tid,)
        ).fetchall()
    )
    assert roles == {"owner": 1, "staff": 1, "cpa": 1}


def test_stock_ends_at_the_yaml_values_and_images_are_in_order(showcase, db, settings) -> None:
    tid = showcase["id"]
    stock = dict(
        db.execute("select sku, stock_qty from public.products where tenant_id = %s", (tid,))
    )
    assert stock == {
        "DL-IT-SALE-01": 20,
        "DL-IT-LOW-01": 2,
        "DL-IT-STUD-01": 15,
        "DL-IT-DRAFT-01": 6,
    }
    sold = db.execute(
        "select count(*) from public.invoice_line_items li join public.products p "
        "on p.id = li.product_id where p.sku = 'DL-IT-DRAFT-01'"
    ).fetchone()[0]
    assert sold == 0  # unpublished products are never sold
    images = db.execute(
        "select i.position, i.is_primary, i.large_path, i.thumb_path, i.width, i.height "
        "from public.product_images i join public.products p on p.id = i.product_id "
        "where p.sku = 'DL-IT-SALE-01' order by i.position"
    ).fetchall()
    assert [(pos, primary) for pos, primary, *_ in images] == [(0, True), (1, False), (2, False)]
    assert all(
        large.startswith(f"{tid}/") and large.endswith("-1200.webp") for _, _, large, *_ in images
    )
    # The variants are really in Storage and are WebP (the alpha image kept its transparency).
    data = run_async(storage.download(storage.IMAGES_BUCKET, images[2][2], 5_000_000))
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.format == "WEBP" and decoded.mode == "RGBA"
        assert decoded.getpixel((5, 5))[3] == 0


def test_nothing_can_send_mail(showcase, db) -> None:
    tid = showcase["id"]
    assert db.execute(
        "select count(*) from public.automation_events "
        "where tenant_id = %s and processed_at is null",
        (tid,),
    ).fetchone() == (0,)
    assert (
        db.execute(
            "select count(*) from public.automation_events where tenant_id = %s", (tid,)
        ).fetchone()[0]
        > 0
    )  # the triggers did run
    assert db.execute(
        "select count(*) from public.automation_rules where tenant_id = %s", (tid,)
    ).fetchone() == (0,)
    emails = [
        r[0] for r in db.execute("select email from public.customers where tenant_id = %s", (tid,))
    ]
    assert emails and all(e.endswith("example.com") for e in emails)
    assert db.execute(
        "select count(*) from public.customers where tenant_id = %s and marketing_opt_in", (tid,)
    ).fetchone() == (0,)


# ---------------------------------------------------------------------------
# The dashboard equals sums computed independently from the loaded rows
# ---------------------------------------------------------------------------
def _independent_summary(db, tid: str) -> dict:
    now = db.execute("select now()").fetchone()[0].astimezone(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    year_start = datetime(now.year, 1, 1, tzinfo=UTC)
    today = now.date()
    invoices = db.execute(
        "select order_status::text, subtotal_cents, tax_cents, paid_at, refunded_at, cancelled_at "
        "from public.invoices where tenant_id = %s",
        (tid,),
    ).fetchall()
    expenses = db.execute(
        "select expense_date, amount_cents, is_tax_deductible from public.expenses "
        "where tenant_id = %s",
        (tid,),
    ).fetchall()
    counted = [i for i in invoices if i[3] and not i[4] and not i[5]]

    def period(since: datetime, since_day: date) -> dict:
        revenue = sum(i[1] for i in counted if i[3] >= since)
        tax = sum(i[2] for i in counted if i[3] >= since)
        spent = [e for e in expenses if since_day <= e[0] <= today]
        total = sum(e[1] for e in spent)
        return {
            "revenue_cents": revenue,
            "tax_reserve_cents": tax,
            "expenses_cents": total,
            "deductible_expenses_cents": sum(e[1] for e in spent if e[2]),
            "net_profit_cents": revenue - total,
        }

    statuses = Counter(i[0] for i in invoices if not i[4] and not i[5])
    products = db.execute(
        "select stock_qty, low_stock_threshold from public.products where tenant_id = %s", (tid,)
    ).fetchall()
    materials = db.execute(
        "select qty_on_hand, reorder_threshold from public.materials where tenant_id = %s", (tid,)
    ).fetchall()

    months = []
    for back in range(5, -1, -1):
        y, m = now.year, now.month - back
        while m < 1:
            y, m = y - 1, m + 12
        start = datetime(y, m, 1, tzinfo=UTC)
        end = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=UTC)
        months.append(
            {
                "month_start": start.date().isoformat(),
                "revenue_cents": sum(i[1] for i in counted if start <= i[3] < end),
                "expenses_cents": sum(
                    e[1] for e in expenses if start.date() <= e[0] < end.date() and e[0] <= today
                ),
            }
        )
    return {
        "mtd": period(month_start, month_start.date()),
        "ytd": period(year_start, year_start.date()),
        "low_stock_products": sum(1 for s, t in products if s < t),
        "low_stock_materials": sum(1 for q, t in materials if q < t),
        "invoice_counts": {
            **{
                s: statuses.get(s, 0)
                for s in ("quote", "paid", "in_production", "shipped", "delivered")
            },
            "refunded": sum(1 for i in invoices if i[4] and not i[5]),
            "cancelled": sum(1 for i in invoices if i[5]),
        },
        "months": months,
    }


def test_the_dashboard_equals_independent_sums(showcase, db, settings, client: TestClient) -> None:
    owner = _token(settings, showcase, "owner")
    summary = client.get("/api/v1/dashboard/summary", headers=owner).json()
    monthly = client.get("/api/v1/dashboard/monthly", headers=owner).json()
    want = _independent_summary(db, showcase["id"])
    got = {
        "mtd": summary["mtd"],
        "ytd": summary["ytd"],
        "low_stock_products": summary["low_stock_products"],
        "low_stock_materials": summary["low_stock_materials"],
        "invoice_counts": summary["invoice_counts"],
        "months": [
            {k: m[k] for k in ("month_start", "revenue_cents", "expenses_cents")}
            for m in monthly["months"]
        ],
    }
    assert got == want
    # And the history is non-trivial: revenue in at least five of the six months.
    assert sum(1 for m in want["months"] if m["revenue_cents"] > 0) >= 5
    assert want["low_stock_products"] == 1 and want["low_stock_materials"] == 1
    assert want["invoice_counts"]["refunded"] == 1 and want["invoice_counts"]["cancelled"] == 1


# ---------------------------------------------------------------------------
# Members see only the showcase
# ---------------------------------------------------------------------------
def test_members_see_only_the_showcase(showcase, settings, client: TestClient) -> None:
    owner, staff, cpa = (_token(settings, showcase, r) for r in ("owner", "staff", "cpa"))
    skus = {p["sku"] for p in client.get("/api/v1/products?limit=100", headers=owner).json()}
    assert skus == {"DL-IT-SALE-01", "DL-IT-LOW-01", "DL-IT-STUD-01", "DL-IT-DRAFT-01"}
    assert (
        client.get(
            f"/api/v1/products/{'a1000000-0000-0000-0000-000000000001'}", headers=owner
        ).status_code
        == 404
    )
    assert {
        p["sku"] for p in client.get("/api/v1/products?limit=100", headers=staff).json()
    } == skus
    assert client.get("/api/v1/products", headers=cpa).status_code == 403
    invoices = client.get("/api/v1/invoices?limit=100", headers=cpa).json()
    assert invoices and all(i["tenant_id"] == showcase["id"] for i in invoices)


@pytest.mark.parametrize("role", ["owner", "staff", "cpa"])
def test_members_see_no_tenant_a_or_b_rows_through_rls(showcase, db, role: str) -> None:
    """The database layer on its own (the API also filters by the caller's tenant)."""
    email = next(e for e, r in showcase["config"].members if r == role)
    user_id = db.execute("select id from auth.users where email = %s", (email,)).fetchone()[0]
    claims = {"sub": str(user_id), "role": "authenticated", "tenant_id": showcase["id"]}
    claims["app_role"] = role
    with db.transaction():
        db.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
        db.execute("set local role authenticated")
        for table in (
            "products",
            "product_images",
            "materials",
            "customers",
            "invoices",
            "invoice_line_items",
            "expenses",
            "tenants",
        ):
            column = "id" if table == "tenants" else "tenant_id"
            (seen,) = db.execute(
                f"select count(*) from public.{table} where {column} <> %s", (showcase["id"],)
            ).fetchone()
            assert seen == 0, f"showcase {role} sees {seen} {table} rows of other tenants"
        db.execute("reset role")


# ---------------------------------------------------------------------------
# The integrity checks catch what they must
# ---------------------------------------------------------------------------
async def _check_with(settings, tid: str, sabotage: str | list[str], check) -> list[str]:
    """Apply `sabotage` (one statement or several) inside a transaction, run `check`, roll
    back."""
    statements = [sabotage] if isinstance(sabotage, str) else sabotage
    async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
        try:
            for statement in statements:
                await conn.execute(statement, {"t": tid} if "%(t)s" in statement else None)
            return await check(conn, uuid.UUID(tid))
        finally:
            await conn.rollback()


def test_the_checks_pass_on_the_loaded_tenant(showcase, settings) -> None:
    expected = integrity.Expected(
        stock_by_sku={
            "DL-IT-SALE-01": 20,
            "DL-IT-LOW-01": 2,
            "DL-IT-STUD-01": 15,
            "DL-IT-DRAFT-01": 6,
        },
        images_by_sku={
            "DL-IT-SALE-01": 3,
            "DL-IT-LOW-01": 1,
            "DL-IT-STUD-01": 2,
            "DL-IT-DRAFT-01": 1,
        },
    )

    async def run(conn, tid):
        await integrity.run_all(conn, tid, expected)
        return []

    assert run_async(_check_with(settings, showcase["id"], "select %(t)s", run)) == []


def test_a_broken_invoice_tax_is_caught(showcase, settings) -> None:
    # The database refuses a total that isn't subtotal + tax + shipping (migration
    # 20261027000200), so the break moves tax and total together: the tax is then wrong.
    problems = run_async(
        _check_with(
            settings,
            showcase["id"],
            "update public.invoices set tax_cents = tax_cents + 1, total_cents = total_cents + 1 "
            "where id = "
            "(select id from public.invoices where tenant_id = %(t)s order by created_at limit 1)",
            integrity.check_documents,
        )
    )
    assert len(problems) == 1 and "isn't subtotal x" in problems[0]


def test_a_paid_invoice_without_its_payment_is_caught(showcase, settings) -> None:
    problems = run_async(
        _check_with(
            settings,
            showcase["id"],
            "update public.payments set status = 'voided', voided_at = now() where id = "
            "(select p.id from public.payments p join public.invoices i on i.id = p.invoice_id "
            " where p.tenant_id = %(t)s and i.refunded_at is null and p.kind = 'payment' "
            " order by p.received_at limit 1)",
            integrity.check_payments,
        )
    )
    assert problems == ["1 invoice(s) don't match their payments"]


def test_a_line_that_no_longer_matches_the_subtotal_is_caught(showcase, settings) -> None:
    problems = run_async(
        _check_with(
            settings,
            showcase["id"],
            "update public.invoice_line_items set qty = qty + 1, "
            "line_total_cents = unit_price_cents * (qty + 1) where id = "
            "(select id from public.invoice_line_items where tenant_id = %(t)s limit 1)",
            integrity.check_documents,
        )
    )
    assert any("isn't the sum of its lines" in p for p in problems)


def test_an_orphan_left_by_the_replica_step_is_caught(showcase, settings) -> None:
    problems = run_async(
        _check_with(
            settings,
            showcase["id"],
            [
                "set local session_replication_role = replica",
                "update public.invoice_line_items set invoice_id = gen_random_uuid() where id = "
                "(select id from public.invoice_line_items where tenant_id = %(t)s limit 1)",
            ],
            integrity.check_foreign_keys,
        )
    )
    assert any("invoice_line_items" in p and "foreign key" in p for p in problems)


def test_a_broken_check_constraint_is_caught(showcase, settings) -> None:
    problems = run_async(
        _check_with(
            settings,
            showcase["id"],
            [
                "alter table public.customers "
                "drop constraint customers_retail_has_no_wholesale_fields",
                "update public.customers set wholesale_discount_bps = 500 where id = (select id "
                "from public.customers where tenant_id = %(t)s and customer_type = 'retail' "
                "limit 1)",
                "alter table public.customers add constraint "
                "customers_retail_has_no_wholesale_fields check (customer_type = 'wholesale' or "
                "(company_name is null and not tax_exempt and wholesale_discount_bps = 0 "
                "and payment_terms = 'due_on_receipt')) not valid",
            ],
            integrity.check_constraints,
        )
    )
    assert any("customers_retail_has_no_wholesale_fields" in p for p in problems)


def test_a_counter_out_of_step_is_caught(showcase, settings) -> None:
    problems = run_async(
        _check_with(
            settings,
            showcase["id"],
            "update public.company_settings set next_invoice_number = next_invoice_number + 1 "
            "where tenant_id = %(t)s",
            integrity.check_documents,
        )
    )
    assert any("next_invoice_number" in p for p in problems)


# ---------------------------------------------------------------------------
# A second run gives the same result; a failing run leaves nothing behind
# ---------------------------------------------------------------------------
def test_a_second_run_gives_the_same_result(showcase, db) -> None:
    before = _snapshot(db, showcase["id"])
    report = run_async(load_showcase(showcase["config"]))
    after = _snapshot(db, showcase["id"])
    assert report.removed_objects == len(before["storage"])
    assert {k: len(v) for k, v in after.items()} == {k: len(v) for k, v in before.items()}
    assert after == before


def test_the_loader_never_touches_a_tenant_that_is_not_a_demo(showcase, settings, db) -> None:
    config = showcase["config"]
    hostile = ShowcaseConfig(**{**config.__dict__, "tenant_id": uuid.UUID(TENANT_A)})
    with pytest.raises(ProvisioningError, match="not a demo tenant"):
        run_async(load_showcase(hostile))
    assert (
        db.execute(
            "select count(*) from public.products where tenant_id = %s", (TENANT_A,)
        ).fetchone()[0]
        > 0
    )


def test_a_failed_integrity_check_rolls_everything_back(showcase, settings, db) -> None:
    """Runs last: a load whose expectations can't be met leaves no rows and no objects (the
    old showcase is removed first, by design). The fixture's teardown copes with that."""
    tid = showcase["id"]

    def wrong_stock(expected):
        return integrity.Expected(
            {**expected.stock_by_sku, "DL-IT-LOW-01": 99}, expected.images_by_sku
        )

    with pytest.raises(integrity.IntegrityError, match="stock differs"):
        run_async(load_showcase(showcase["config"], expected_hook=wrong_stock))
    assert db.execute("select count(*) from public.tenants where id = %s", (tid,)).fetchone() == (
        0,
    )
    assert db.execute(
        "select count(*) from storage.objects where name like %s", (f"{tid}/%",)
    ).fetchone() == (0,)
    # The users stay (they are reused); no membership is left for them.
    assert db.execute(
        "select count(*) from public.tenant_members where tenant_id = %s", (tid,)
    ).fetchone() == (0,)
