-- Slice B1: deposits (TRD §6.13). A deposit is money held for the customer, not revenue: the
-- invoice stays a quote until it is paid in full.
--   Rule 1  invoices_09_paid_in_full_guard: an invoice leaves quote only when its net payments
--           cover its total. Every caller, the service role included.
--   Rule 2  production_started_at: set once, on a quote that holds a payment and isn't
--           cancelled; never changed or cleared after that.
--   Rule 3  invoice_settle(): when the balance reaches zero the quote becomes paid, or
--           in production when production had started. If stock is short the payment stays
--           and the invoice stays a quote (the admin shows it).
--   Rule 5  a quote holding payments can't be cancelled or deleted, its total can't drop below
--           what has been paid, and its lines lock once production has started.
-- (Rule 4, "customer deposits held", is a dashboard figure.)

alter table public.invoices add column production_started_at timestamptz;

-- ---------------------------------------------------------------------------
-- Rule 1
-- ---------------------------------------------------------------------------
create function public.invoices_paid_in_full_guard()
returns trigger
language plpgsql
security definer  -- the sum must see every payment, whatever the caller's RLS shows
set search_path = ''
as $$
declare
  v_paid bigint;
  v_currency text;
begin
  if old.order_status = 'quote' and new.order_status > 'quote' then
    v_paid := public.invoice_amount_paid(old.id);
    if v_paid < new.total_cents then
      select cs.currency into v_currency
        from public.company_settings cs where cs.tenant_id = new.tenant_id;
      raise exception 'This invoice isn''t paid in full: % of % received.',
        public.money_text(v_paid, v_currency), public.money_text(new.total_cents, v_currency);
    end if;
    -- Production is already under way: paid in full means in production, not just paid.
    if old.production_started_at is not null and new.order_status = 'paid' then
      new.order_status := 'in_production';
    end if;
  end if;
  return new;
end;
$$;

-- After the closed guard (08), before the forward-only rule (10) and numbering (30).
create trigger invoices_09_paid_in_full_guard
  before update on public.invoices
  for each row execute function public.invoices_paid_in_full_guard();

-- ---------------------------------------------------------------------------
-- Rules 2 and 5 (invoice side)
-- ---------------------------------------------------------------------------
create function public.invoices_deposit_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_paid bigint;
  v_currency text;
begin
  if old.production_started_at is not null
     and new.production_started_at is distinct from old.production_started_at then
    raise exception 'Production has started; that can''t be undone.' using errcode = 'check_violation';
  end if;

  v_paid := public.invoice_amount_paid(old.id);

  if old.production_started_at is null and new.production_started_at is not null then
    if old.order_status <> 'quote' or old.cancelled_at is not null or v_paid <= 0 then
      raise exception 'Production can be started only on an open quote that holds a deposit.';
    end if;
    if (select auth.jwt() ->> 'app_role') is not null then
      new.production_started_at := now();
    end if;
  end if;

  if old.order_status = 'quote' and old.cancelled_at is null and new.cancelled_at is not null
     and v_paid > 0 then
    raise exception 'This quote holds a deposit. Refund it before cancelling.';
  end if;

  if new.total_cents is distinct from old.total_cents and new.total_cents < v_paid then
    select cs.currency into v_currency
      from public.company_settings cs where cs.tenant_id = new.tenant_id;
    raise exception 'The total can''t be less than the % already paid.',
      public.money_text(v_paid, v_currency);
  end if;
  return new;
end;
$$;

create trigger invoices_25_deposit_guard
  before update on public.invoices
  for each row execute function public.invoices_deposit_guard();

-- Same as migration 20261020001300, and a quote with any payment row (voided ones included:
-- they are part of its history) can't be deleted either. Cancel it instead.
create or replace function public.invoices_delete_quote_only_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if (select auth.jwt() ->> 'app_role') is not null then
    if old.order_status <> 'quote' then
      raise exception 'only quotes can be deleted; cancel or refund this invoice instead'
        using errcode = 'insufficient_privilege';
    end if;
    if exists (select 1 from public.payments p where p.invoice_id = old.id) then
      raise exception 'This quote has payments recorded, so it can''t be deleted. Cancel it instead.';
    end if;
  end if;
  return old;
end;
$$;

-- Same as migration 20261020001200, and lines also lock once production has started.
create or replace function public.invoice_line_items_quote_only_guard()
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
  if exists (select 1 from public.invoices i
              where i.id = any (parent_ids) and i.production_started_at is not null) then
    raise exception 'Production has started, so the items can''t change.'
      using errcode = 'insufficient_privilege';
  end if;
  return coalesce(new, old);
end;
$$;

revoke all on function public.invoices_paid_in_full_guard() from public, anon, authenticated;
revoke all on function public.invoices_deposit_guard() from public, anon, authenticated;
revoke all on function public.invoices_delete_quote_only_guard() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- Rule 3
-- ---------------------------------------------------------------------------
-- Completes a quote whose balance has reached zero. Returns what happened:
--   'completed'    it is now paid (or in production)
--   'stock_short'  paid in full, but not enough stock: it stays a quote, the payments stay
--   'balance_due'  not paid in full yet
--   'unchanged'    not an open quote
-- SECURITY INVOKER: the caller's RLS and the invoice triggers apply as for any status move.
-- The stock check runs in a subtransaction (the BEGIN ... EXCEPTION block), so a shortage
-- undoes only the status move.
create function public.invoice_settle(p_invoice_id uuid)
returns text
language plpgsql
set search_path = ''
as $$
declare
  inv record;
  v_method public.payment_method;
begin
  if (select auth.jwt() ->> 'app_role') not in ('owner', 'staff') then
    raise exception 'only the owner or staff can settle an invoice'
      using errcode = 'insufficient_privilege';
  end if;
  select i.order_status, i.total_cents, i.cancelled_at, i.refunded_at
    into inv
    from public.invoices i
   where i.id = p_invoice_id
     for update;
  if not found then
    raise exception 'Invoice not found' using errcode = 'no_data_found';
  end if;
  if inv.order_status <> 'quote' or inv.cancelled_at is not null or inv.refunded_at is not null then
    return 'unchanged';
  end if;
  if public.invoice_amount_paid(p_invoice_id) < inv.total_cents then
    return 'balance_due';
  end if;

  -- The invoice's method: the one all its payments used, otherwise 'other'.
  select case when count(distinct p.method) = 1 then min(p.method::text)::public.payment_method
              else 'other'::public.payment_method end
    into v_method
    from public.payments p
   where p.invoice_id = p_invoice_id and p.kind = 'payment' and p.status = 'succeeded';

  begin
    -- 'paid' becomes 'in_production' when production had started (invoices_09).
    update public.invoices
       set order_status = 'paid',
           payment_method = coalesce(payment_method, v_method, 'other')
     where id = p_invoice_id;
    return 'completed';
  exception when raise_exception then
    if sqlerrm like 'Not enough stock%' then
      return 'stock_short';
    end if;
    raise;
  end;
end;
$$;

revoke all on function public.invoice_settle(uuid) from public, anon, authenticated;
grant execute on function public.invoice_settle(uuid) to authenticated;
