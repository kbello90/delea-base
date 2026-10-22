-- Invoices slice (TRD §3.1):
--   1. payment_method: how a sale was paid. Null on quotes (check), set when an invoice is
--      paid. Nullable because invoices paid before this migration have none. Staff can't
--      change it once set; the owner can correct it.
--   2. Closed invoices: a cancelled or refunded invoice keeps its status, a cancelled one
--      can't be refunded and a refunded one can't be cancelled. Every caller, like the
--      forward-only status rule.
--   3. Plain-language messages for a backward status move and for insufficient stock.

-- ---------------------------------------------------------------------------
-- 1. Payment method
-- ---------------------------------------------------------------------------
create type public.payment_method as enum ('cash', 'card', 'bank_transfer', 'other');

alter table public.invoices add column payment_method public.payment_method;
alter table public.invoices add constraint invoices_payment_method_only_when_paid
  check (order_status <> 'quote' or payment_method is null);

-- The staff financial guard, now also covering a payment method that is already set.
create or replace function public.invoices_staff_financial_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (select auth.jwt() ->> 'app_role') = 'staff'
     and old.order_status <> 'quote'
     and (new.subtotal_cents is distinct from old.subtotal_cents
          or new.tax_rate_bps is distinct from old.tax_rate_bps
          or new.tax_cents is distinct from old.tax_cents
          or new.total_cents is distinct from old.total_cents) then
    raise exception 'staff cannot change amounts on an invoice past quote'
      using errcode = 'insufficient_privilege';
  end if;
  if (select auth.jwt() ->> 'app_role') = 'staff'
     and old.payment_method is not null
     and new.payment_method is distinct from old.payment_method then
    raise exception 'staff cannot change the payment method once it is set'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- 2. Cancelled and refunded invoices
-- ---------------------------------------------------------------------------
create function public.invoices_closed_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.order_status is distinct from old.order_status then
    if old.cancelled_at is not null then
      raise exception 'This invoice is cancelled, so its status can''t change.';  -- P0001 -> 409
    end if;
    if old.refunded_at is not null then
      raise exception 'This invoice is refunded, so its status can''t change.';
    end if;
  end if;
  if old.refunded_at is null and new.refunded_at is not null and new.cancelled_at is not null then
    raise exception 'A cancelled invoice can''t be refunded.';
  end if;
  if old.cancelled_at is null and new.cancelled_at is not null and new.refunded_at is not null then
    raise exception 'A refunded invoice can''t be cancelled.';
  end if;
  return new;
end;
$$;

-- Fires first among the update guards (BEFORE triggers run in name order), so a move on a
-- cancelled invoice says why rather than reporting the direction.
create trigger invoices_08_closed_guard
  before update on public.invoices
  for each row execute function public.invoices_closed_guard();

revoke all on function public.invoices_closed_guard() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. Plain-language messages
-- ---------------------------------------------------------------------------
create or replace function public.invoices_enforce_forward_status()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.order_status < old.order_status then
    raise exception 'This invoice can''t move back to %.', replace(new.order_status::text, '_', ' ')
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

-- Same as migration 12 apart from the message.
create or replace function public.invoices_decrement_stock()
returns trigger
language plpgsql
security definer  -- staff may advance an invoice but can't update products
set search_path = ''
as $$
declare
  r record;
begin
  -- Lock this invoice's products, in id order (no deadlocks), before reading stock: two
  -- simultaneous sales of the same product serialize here, and the second one sees the
  -- first one's decrement.
  perform 1
     from public.products p
    where p.tenant_id = new.tenant_id
      and p.id in (select li.product_id
                     from public.invoice_line_items li
                    where li.invoice_id = new.id
                      and li.tenant_id = new.tenant_id
                      and li.product_id is not null)
    order by p.id
      for update;

  -- One check and one decrement per product, summing qty across that product's lines.
  -- Lines without a product (e.g. a custom piece) are skipped.
  for r in
    select p.id, p.sku, p.stock_qty, n.needed
      from (select li.product_id, sum(li.qty)::bigint as needed
              from public.invoice_line_items li
             where li.invoice_id = new.id
               and li.tenant_id = new.tenant_id
               and li.product_id is not null
             group by li.product_id) n
      join public.products p on p.id = n.product_id and p.tenant_id = new.tenant_id
     order by p.sku
  loop
    if r.stock_qty < r.needed then
      raise exception 'Not enough stock for SKU %: % available, % needed.',
        r.sku, r.stock_qty, r.needed;  -- P0001 -> 409 with this message
    end if;
    update public.products
       set stock_qty = stock_qty - r.needed::integer
     where id = r.id and tenant_id = new.tenant_id;
  end loop;
  return null;
end;
$$;
