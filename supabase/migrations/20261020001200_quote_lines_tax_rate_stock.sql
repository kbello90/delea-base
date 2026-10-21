-- Unblocks the invoice endpoints (Schema §9):
--   1. current_tax_rate_bps(): owner/staff read their tenant's tax rate without seeing
--      company_settings (EIN, Stripe account).
--   2. Line items are writable only while the invoice is a quote, for every caller with an
--      app_role; only the service role is exempt. Policies AND a trigger.
--   3. Invoices are created as quotes by app users (service role exempt).
--   4. Stock is decremented once, when an invoice leaves 'quote'. No restock in v1.

-- ---------------------------------------------------------------------------
-- 1. Tax rate for the caller's tenant
-- ---------------------------------------------------------------------------
create function public.current_tax_rate_bps()
returns integer
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  -- Claims come from the caller's verified JWT (request.jwt.claims), read with auth.jwt(),
  -- the same way the RLS policies read them. SECURITY DEFINER doesn't change these.
  v_app_role text := (select auth.jwt() ->> 'app_role');
  v_tenant_id uuid := nullif((select auth.jwt() ->> 'tenant_id'), '')::uuid;
  v_rate integer;
begin
  if v_app_role is null or v_app_role not in ('owner', 'staff') then
    raise exception 'only owner or staff can read the tax rate'
      using errcode = 'insufficient_privilege';
  end if;
  if v_tenant_id is null then
    raise exception 'no tenant in the token' using errcode = 'insufficient_privilege';
  end if;

  select cs.default_tax_rate_bps into v_rate
    from public.company_settings cs
   where cs.tenant_id = v_tenant_id;
  if not found then
    raise exception 'company settings are missing for this tenant';  -- P0001
  end if;
  return v_rate;
end;
$$;

-- EXECUTE for authenticated only. cpa is also `authenticated` at the Postgres level, so the
-- body rejects cpa (and any caller without owner/staff) with 42501.
revoke all on function public.current_tax_rate_bps() from public, anon, authenticated, service_role;
grant execute on function public.current_tax_rate_bps() to authenticated;

-- ---------------------------------------------------------------------------
-- 2. Line items: quote-only writes (policies + trigger)
-- ---------------------------------------------------------------------------
-- Replace the owner's FOR ALL policy and the staff insert/update policies.
-- invoice_line_items_staff_select is kept as is.
drop policy invoice_line_items_owner_all on public.invoice_line_items;
drop policy invoice_line_items_staff_insert on public.invoice_line_items;
drop policy invoice_line_items_staff_update on public.invoice_line_items;

create policy invoice_line_items_owner_select on public.invoice_line_items
  for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner');

-- Owner and staff may write a line only while its invoice (same tenant) is a quote.
create policy invoice_line_items_quote_insert on public.invoice_line_items
  for insert to authenticated
  with check (
    tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
    and (select auth.jwt() ->> 'app_role') in ('owner', 'staff')
    and exists (select 1 from public.invoices i
                 where i.id = invoice_line_items.invoice_id
                   and i.tenant_id = invoice_line_items.tenant_id
                   and i.order_status = 'quote'));

create policy invoice_line_items_quote_update on public.invoice_line_items
  for update to authenticated
  using (
    tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
    and (select auth.jwt() ->> 'app_role') in ('owner', 'staff')
    and exists (select 1 from public.invoices i
                 where i.id = invoice_line_items.invoice_id
                   and i.tenant_id = invoice_line_items.tenant_id
                   and i.order_status = 'quote'))
  with check (
    tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
    and (select auth.jwt() ->> 'app_role') in ('owner', 'staff')
    and exists (select 1 from public.invoices i
                 where i.id = invoice_line_items.invoice_id
                   and i.tenant_id = invoice_line_items.tenant_id
                   and i.order_status = 'quote'));

create policy invoice_line_items_quote_delete on public.invoice_line_items
  for delete to authenticated
  using (
    tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
    and (select auth.jwt() ->> 'app_role') in ('owner', 'staff')
    and exists (select 1 from public.invoices i
                 where i.id = invoice_line_items.invoice_id
                   and i.tenant_id = invoice_line_items.tenant_id
                   and i.order_status = 'quote'));

-- The trigger replaces the staff-only guard and applies to every caller with an app_role.
-- SECURITY DEFINER so it checks the parent's status whatever the caller can see.
drop trigger invoice_line_items_20_staff_guard on public.invoice_line_items;
drop function public.invoice_line_items_staff_guard();

create function public.invoice_line_items_quote_only_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  parent_ids uuid[];
begin
  -- Service role, migrations and seed carry no app_role claim: exempt.
  if (select auth.jwt() ->> 'app_role') is null then
    return coalesce(new, old);
  end if;

  parent_ids := case tg_op
    when 'INSERT' then array[new.invoice_id]
    when 'UPDATE' then array[old.invoice_id, new.invoice_id]
    else array[old.invoice_id]
  end;

  if exists (select 1 from public.invoices i
              where i.id = any (parent_ids) and i.order_status <> 'quote') then
    raise exception 'line items can only change while the invoice is a quote'
      using errcode = 'insufficient_privilege';
  end if;
  return coalesce(new, old);
end;
$$;

create trigger invoice_line_items_20_quote_only_guard
  before insert or update or delete on public.invoice_line_items
  for each row execute function public.invoice_line_items_quote_only_guard();

-- ---------------------------------------------------------------------------
-- 3. App users create invoices as quotes
-- ---------------------------------------------------------------------------
create function public.invoices_insert_as_quote_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (select auth.jwt() ->> 'app_role') is not null and new.order_status <> 'quote' then
    raise exception
      'invoices must be created as a quote (got %); add the line items, then move the status forward',
      new.order_status
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

-- Fires before numbering (invoices_30_numbering): BEFORE triggers run in name order.
create trigger invoices_05_insert_as_quote
  before insert on public.invoices
  for each row execute function public.invoices_insert_as_quote_guard();

-- ---------------------------------------------------------------------------
-- 4. Stock: decrement once when the invoice leaves 'quote'
-- ---------------------------------------------------------------------------
create function public.invoices_decrement_stock()
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
      raise exception 'insufficient stock for SKU %: % available, % requested',
        r.sku, r.stock_qty, r.needed;  -- P0001 -> 409 with this message
    end if;
    update public.products
       set stock_qty = stock_qty - r.needed::integer
     where id = r.id and tenant_id = new.tenant_id;
  end loop;
  return null;
end;
$$;

create trigger invoices_decrement_stock
  after update of order_status on public.invoices
  for each row
  when (old.order_status = 'quote' and new.order_status > 'quote')
  execute function public.invoices_decrement_stock();

-- Trigger functions need no EXECUTE grants (Postgres doesn't check EXECUTE when a trigger
-- fires). Stated explicitly; migration 09's default privileges already do this.
revoke all on function public.invoice_line_items_quote_only_guard() from public, anon, authenticated;
revoke all on function public.invoices_insert_as_quote_guard() from public, anon, authenticated;
revoke all on function public.invoices_decrement_stock() from public, anon, authenticated;
