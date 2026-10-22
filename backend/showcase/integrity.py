"""Post-load integrity checks for one tenant. They run inside the load transaction, after the
backdating step (which bypasses triggers, foreign keys included), and the load is rolled back
if any fails.

1. Every foreign key and CHECK constraint on public tables, re-validated for the tenant's
   rows (read from pg_constraint, so a new constraint is covered without changing this).
2. Document counters at max + 1, numbers unique and in date order, numbers only on
   documents that left quote.
3. Each invoice: line_total = unit price x qty, subtotal = sum of lines, tax = subtotal x rate
   rounded half up, total = subtotal + tax; refunds only on paid invoices; timestamps in order
   and not in the future.
4. Stock equals the expected values (the YAML's), product images as listed, one primary.
5. No automation event left unprocessed (a future worker must find nothing to send).
"""

from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection, sql

from app.services.pricing import round_half_up_bps


class IntegrityError(RuntimeError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("showcase integrity checks failed:\n  - " + "\n  - ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Expected:
    stock_by_sku: dict[str, int]
    images_by_sku: dict[str, int]


async def _rows(conn: AsyncConnection, query, params=()) -> list[tuple]:
    cur = await conn.execute(query, params)
    return await cur.fetchall()


async def _has_tenant_id(conn: AsyncConnection, table: str) -> bool:
    rows = await _rows(
        conn,
        "select 1 from information_schema.columns "
        "where table_schema = 'public' and table_name = %s and column_name = 'tenant_id'",
        (table,),
    )
    return bool(rows)


def _scope(table: str, has_tenant_id: bool) -> sql.Composable:
    if table == "tenants":
        return sql.SQL("c.id = %(tenant)s")
    return sql.SQL("c.tenant_id = %(tenant)s") if has_tenant_id else sql.SQL("true")


async def check_foreign_keys(conn: AsyncConnection, tenant_id: UUID) -> list[str]:
    fks = await _rows(
        conn,
        """
        select con.conname, child.relname, pn.nspname, parent.relname,
               array_agg(ca.attname order by k.ord), array_agg(pa.attname order by k.ord)
          from pg_constraint con
          join pg_class child on child.oid = con.conrelid
          join pg_namespace cn on cn.oid = child.relnamespace and cn.nspname = 'public'
          join pg_class parent on parent.oid = con.confrelid
          join pg_namespace pn on pn.oid = parent.relnamespace
          cross join lateral unnest(con.conkey, con.confkey) with ordinality as k(ck, pk, ord)
          join pg_attribute ca on ca.attrelid = con.conrelid and ca.attnum = k.ck
          join pg_attribute pa on pa.attrelid = con.confrelid and pa.attnum = k.pk
         where con.contype = 'f'
         group by con.conname, child.relname, pn.nspname, parent.relname
         order by child.relname, con.conname
        """,
    )
    problems = []
    for name, child, parent_schema, parent, child_cols, parent_cols in fks:
        # MATCH SIMPLE: a row with any null key column is not checked.
        not_null = sql.SQL(" and ").join(
            sql.SQL("c.{} is not null").format(sql.Identifier(col)) for col in child_cols
        )
        join = sql.SQL(" and ").join(
            sql.SQL("p.{} = c.{}").format(sql.Identifier(pc), sql.Identifier(cc))
            for cc, pc in zip(child_cols, parent_cols, strict=True)
        )
        query = sql.SQL(
            "select count(*) from public.{child} c where {scope} and {not_null} "
            "and not exists (select 1 from {parent} p where {join})"
        ).format(
            child=sql.Identifier(child),
            scope=_scope(child, await _has_tenant_id(conn, child)),
            not_null=not_null,
            parent=sql.Identifier(parent_schema, parent),
            join=join,
        )
        ((orphans,),) = await _rows(conn, query, {"tenant": tenant_id})
        if orphans:
            problems.append(f"{orphans} {child} row(s) break foreign key {name} (-> {parent})")
    return problems


async def check_constraints(conn: AsyncConnection, tenant_id: UUID) -> list[str]:
    checks = await _rows(
        conn,
        """
        select con.conname, rel.relname, pg_get_constraintdef(con.oid)
          from pg_constraint con
          join pg_class rel on rel.oid = con.conrelid
          join pg_namespace n on n.oid = rel.relnamespace and n.nspname = 'public'
         where con.contype = 'c'
         order by rel.relname, con.conname
        """,
    )
    problems = []
    for name, table, definition in checks:
        expression = definition.removeprefix("CHECK ").removesuffix(" NOT VALID")
        # A CHECK passes unless the expression is false (null passes).
        query = sql.SQL(
            "select count(*) from public.{t} c where {scope} and ({e}) is false"
        ).format(
            t=sql.Identifier(table),
            scope=_scope(table, await _has_tenant_id(conn, table)),
            e=sql.SQL(expression),
        )
        ((failing,),) = await _rows(conn, query, {"tenant": tenant_id})
        if failing:
            problems.append(f"{failing} {table} row(s) fail check constraint {name}")
    return problems


async def check_documents(conn: AsyncConnection, tenant_id: UUID) -> list[str]:
    problems = []
    rows = await _rows(
        conn,
        "select id, order_status::text, quote_number, invoice_number, subtotal_cents, "
        "tax_rate_bps, tax_cents, total_cents, created_at, paid_at, refunded_at, cancelled_at, "
        "now() from public.invoices where tenant_id = %s",
        (tenant_id,),
    )
    lines = await _rows(
        conn,
        "select invoice_id, qty, unit_price_cents, line_total_cents "
        "from public.invoice_line_items where tenant_id = %s",
        (tenant_id,),
    )
    by_invoice: dict[UUID, list[tuple]] = {}
    for invoice_id, qty, unit, total in lines:
        by_invoice.setdefault(invoice_id, []).append((qty, unit, total))
        if unit * qty != total:
            problems.append(f"invoice {invoice_id}: a line total isn't unit price x qty")

    for (
        iid,
        status,
        qno,
        ino,
        sub,
        rate,
        tax,
        total,
        created,
        paid,
        refunded,
        cancelled,
        now,
    ) in rows:
        items = by_invoice.get(iid, [])
        if not items:
            problems.append(f"invoice {iid}: has no lines")
        if sub != sum(t for _, _, t in items):
            problems.append(f"invoice {iid}: subtotal {sub} isn't the sum of its lines")
        if tax != round_half_up_bps(sub, rate):
            problems.append(f"invoice {iid}: tax {tax} isn't subtotal x {rate} bps")
        if total != sub + tax:
            problems.append(f"invoice {iid}: total {total} isn't subtotal + tax")
        if qno is None:
            problems.append(f"invoice {iid}: no quote number")
        left_quote = status != "quote"
        if left_quote != (paid is not None) or left_quote != (ino is not None):
            problems.append(f"invoice {iid}: status {status} doesn't match paid_at/invoice_number")
        if refunded is not None and paid is None:
            problems.append(f"invoice {iid}: refunded but never paid")
        stamps = [t for t in (created, paid) if t is not None]
        if stamps != sorted(stamps):
            problems.append(f"invoice {iid}: paid before it was created")
        for label, at in (("refunded", refunded), ("cancelled", cancelled)):
            if at is not None and paid is not None and at < paid:
                problems.append(f"invoice {iid}: {label} before it was paid")
        if any(t is not None and t > now for t in (created, paid, refunded, cancelled)):
            problems.append(f"invoice {iid}: a timestamp is in the future")

    ((next_quote, next_invoice),) = await _rows(
        conn,
        "select next_quote_number, next_invoice_number from public.company_settings "
        "where tenant_id = %s",
        (tenant_id,),
    )
    # (date, number) pairs: quotes by created_at, invoices by paid_at.
    quotes = sorted((r[8], r[2]) for r in rows if r[2] is not None)
    invoices = sorted((r[9], r[3]) for r in rows if r[3] is not None)
    for kind, numbered, next_value in (
        ("quote", quotes, next_quote),
        ("invoice", invoices, next_invoice),
    ):
        sequence = [int(number.rsplit("-", 1)[1]) for _, number in numbered]
        if sequence != list(range(1, len(sequence) + 1)):
            problems.append(f"{kind} numbers aren't 1..{len(sequence)} in date order")
        if any(number.split("-")[1] != f"{at:%Y}" for at, number in numbered):
            problems.append(f"a {kind} number's year doesn't match its date")
        if next_value != len(sequence) + 1:
            problems.append(f"next_{kind}_number is {next_value}, expected {len(sequence) + 1}")
    return problems


async def check_catalog(conn: AsyncConnection, tenant_id: UUID, expected: Expected) -> list[str]:
    problems = []
    stock = dict(
        await _rows(
            conn, "select sku, stock_qty from public.products where tenant_id = %s", (tenant_id,)
        )
    )
    if stock != expected.stock_by_sku:
        diff = {
            sku: (stock.get(sku), want)
            for sku, want in expected.stock_by_sku.items()
            if stock.get(sku) != want
        }
        extra = sorted(set(stock) - set(expected.stock_by_sku))
        problems.append(f"stock differs from products.yaml (got, want): {diff} extra: {extra}")
    images = await _rows(
        conn,
        "select p.sku, count(i.id), count(i.id) filter (where i.is_primary), "
        "bool_and(i.position <> 0 or i.is_primary) "
        "from public.products p left join public.product_images i on i.product_id = p.id "
        "where p.tenant_id = %s group by p.sku",
        (tenant_id,),
    )
    for sku, count, primaries, first_is_primary in images:
        if count != expected.images_by_sku.get(sku):
            problems.append(f"{sku}: {count} images, expected {expected.images_by_sku.get(sku)}")
        if count and (primaries != 1 or not first_is_primary):
            problems.append(f"{sku}: the first image must be the only primary")
    return problems


async def check_events(conn: AsyncConnection, tenant_id: UUID) -> list[str]:
    ((open_events,),) = await _rows(
        conn,
        "select count(*) from public.automation_events "
        "where tenant_id = %s and processed_at is null",
        (tenant_id,),
    )
    ((rules,),) = await _rows(
        conn, "select count(*) from public.automation_rules where tenant_id = %s", (tenant_id,)
    )
    problems = []
    if open_events:
        problems.append(f"{open_events} automation event(s) left unprocessed")
    if rules:
        problems.append(f"{rules} automation rule(s) exist for the demo tenant")
    return problems


async def run_all(conn: AsyncConnection, tenant_id: UUID, expected: Expected) -> None:
    problems = []
    problems += await check_foreign_keys(conn, tenant_id)
    problems += await check_constraints(conn, tenant_id)
    problems += await check_documents(conn, tenant_id)
    problems += await check_catalog(conn, tenant_id, expected)
    problems += await check_events(conn, tenant_id)
    if problems:
        raise IntegrityError(problems)
