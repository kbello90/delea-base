-- Slice B1: the payments ledger (TRD §6.13).
--   1. payments: one row per money movement on an invoice: a payment taken or a refund given.
--      Manual rows (cash, card on the shop's own terminal, bank transfer, other) are recorded
--      by the owner or staff; Stripe rows arrive in Slice B2 and are written by the service.
--   2. What app users may do: owner and staff record payments, the owner records refunds and
--      voids a mistaken row while the invoice is still a quote, the CPA reads (D15). Nobody
--      deletes. Amounts, invoice and kind never change after insert, for every caller.
--   3. invoice_amount_paid() and invoice_balance(): computed on read, never stored.

-- ---------------------------------------------------------------------------
-- 1. Table
-- ---------------------------------------------------------------------------
create table public.payments (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  invoice_id uuid not null,
  kind text not null check (kind in ('payment', 'refund')),
  provider text not null default 'manual' check (provider in ('manual', 'stripe')),
  method public.payment_method not null,
  status text not null default 'succeeded'
    check (status in ('pending', 'succeeded', 'failed', 'expired', 'voided')),
  amount_cents bigint not null check (amount_cents > 0 and amount_cents <= 100000000000),
  currency char(3) not null,
  -- Stripe: the PaymentIntent (payment) or refund id, and the Checkout Session id. Slice B2.
  external_id text,
  external_ref text,
  -- A refund may name the payment it returns.
  refund_of uuid,
  received_at timestamptz not null default now(),
  recorded_by uuid references auth.users (id) on delete set null,
  note text,
  voided_at timestamptz,
  voided_by uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, invoice_id) references public.invoices (tenant_id, id) on delete cascade,
  foreign key (tenant_id, refund_of) references public.payments (tenant_id, id),
  constraint payments_external_id_unique unique (provider, external_id),
  constraint payments_external_ref_unique unique (provider, external_ref),
  constraint payments_note_length check (note is null or char_length(note) between 1 and 500),
  constraint payments_refund_of_refunds_only check (refund_of is null or kind = 'refund'),
  constraint payments_manual_has_no_stripe_ids
    check (provider <> 'manual' or (external_id is null and external_ref is null)),
  constraint payments_voided_fields check ((status = 'voided') = (voided_at is not null))
);
create index payments_invoice on public.payments (tenant_id, invoice_id);
create index payments_received on public.payments (tenant_id, received_at) where status = 'succeeded';

-- ---------------------------------------------------------------------------
-- 3. Paid and balance (declared before the triggers that use them)
-- ---------------------------------------------------------------------------
-- For messages: 1250, 'USD' -> '12.50 USD'.
create function public.money_text(p_cents bigint, p_currency text)
returns text
language sql
immutable
set search_path = ''
as $$
  select to_char(p_cents / 100.0, 'FM999999999990.00') || ' ' || p_currency;
$$;
revoke all on function public.money_text(bigint, text) from public, anon, authenticated;
grant execute on function public.money_text(bigint, text) to authenticated;

-- Net money received: succeeded payments minus succeeded refunds. Voided, pending, failed and
-- expired rows count for nothing. SECURITY INVOKER: callers see only what RLS lets them see.
create function public.invoice_amount_paid(p_invoice_id uuid)
returns bigint
language sql
stable
set search_path = ''
as $$
  select coalesce(sum(case when p.kind = 'payment' then p.amount_cents else -p.amount_cents end), 0)::bigint
    from public.payments p
   where p.invoice_id = p_invoice_id
     and p.status = 'succeeded';
$$;

create function public.invoice_balance(p_invoice_id uuid)
returns bigint
language sql
stable
set search_path = ''
as $$
  select i.total_cents - public.invoice_amount_paid(i.id)
    from public.invoices i
   where i.id = p_invoice_id;
$$;

revoke all on function public.invoice_amount_paid(uuid) from public, anon, authenticated;
revoke all on function public.invoice_balance(uuid) from public, anon, authenticated;
grant execute on function public.invoice_amount_paid(uuid) to authenticated;
grant execute on function public.invoice_balance(uuid) to authenticated;

-- ---------------------------------------------------------------------------
-- 2. Rules
-- ---------------------------------------------------------------------------
-- Inserts, for every caller. SECURITY DEFINER so the sums see every row of the invoice, not
-- only what the caller's RLS shows (a staff member's view is the same today, but the rule
-- must not depend on it).
create function public.payments_insert_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role text := (select auth.jwt() ->> 'app_role');
  inv record;
  v_paid bigint;
begin
  -- Lock the invoice: two payments recorded at once serialize here, so neither can push the
  -- total received past what is owed.
  select i.order_status, i.total_cents, i.cancelled_at, i.refunded_at
    into inv
    from public.invoices i
   where i.id = new.invoice_id and i.tenant_id = new.tenant_id
     for update;
  if not found then
    raise exception 'invoice not found' using errcode = 'foreign_key_violation';
  end if;

  -- The shop's currency, whatever the caller sent.
  select cs.currency into new.currency
    from public.company_settings cs where cs.tenant_id = new.tenant_id;

  if v_role is not null then
    -- App users record what has happened: no pending rows, no backdating, their own name.
    if new.provider <> 'manual' then
      raise exception 'only the service records Stripe payments' using errcode = 'insufficient_privilege';
    end if;
    new.status := 'succeeded';
    new.received_at := now();
    new.recorded_by := (select auth.uid());
    new.voided_at := null;
    new.voided_by := null;
  end if;

  if new.status = 'succeeded' and inv.cancelled_at is not null then
    raise exception 'This invoice is cancelled, so it can''t take payments or refunds.';
  end if;

  v_paid := public.invoice_amount_paid(new.invoice_id);
  if new.kind = 'payment' then
    if new.status = 'succeeded' and new.provider = 'manual' then
      if inv.refunded_at is not null then
        raise exception 'This invoice is refunded, so it can''t take payments.';
      end if;
      if v_role is not null and inv.order_status <> 'quote' then
        raise exception 'This invoice is already paid.';
      end if;
      if new.amount_cents > inv.total_cents - v_paid then
        raise exception 'That is more than the % still due.',
          public.money_text(inv.total_cents - v_paid, new.currency)
          using errcode = 'check_violation';
      end if;
    end if;
  else
    if v_role is not null and v_role <> 'owner' then
      raise exception 'only the owner can record a refund' using errcode = 'insufficient_privilege';
    end if;
    if new.status = 'succeeded' and new.amount_cents > v_paid then
      raise exception 'That is more than the % received.',
        public.money_text(v_paid, new.currency)
        using errcode = 'check_violation';
    end if;
    if new.refund_of is not null and not exists (
         select 1 from public.payments p
          where p.id = new.refund_of and p.tenant_id = new.tenant_id
            and p.invoice_id = new.invoice_id and p.kind = 'payment') then
      raise exception 'a refund can only name a payment on the same invoice'
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end;
$$;

create trigger payments_10_insert_guard
  before insert on public.payments
  for each row execute function public.payments_insert_guard();

-- Updates. Amount, invoice, tenant, kind, provider, currency, Stripe ids and who recorded it
-- never change, for every caller. App users: the owner may void a manual row while its invoice
-- is still a quote, and may correct a manual row's method. The service (Slice B2) moves
-- Stripe rows forward: pending -> succeeded / failed / expired, never back.
create function public.payments_update_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role text := (select auth.jwt() ->> 'app_role');
  v_status public.order_status;
begin
  if new.tenant_id is distinct from old.tenant_id
     or new.invoice_id is distinct from old.invoice_id
     or new.kind is distinct from old.kind
     or new.provider is distinct from old.provider
     or new.amount_cents is distinct from old.amount_cents
     or new.currency is distinct from old.currency
     or new.external_ref is distinct from old.external_ref
     or new.refund_of is distinct from old.refund_of
     or new.recorded_by is distinct from old.recorded_by
     or new.created_at is distinct from old.created_at
     or (old.external_id is not null and new.external_id is distinct from old.external_id) then
    raise exception 'a recorded payment can''t be changed; void it and record it again'
      using errcode = 'insufficient_privilege';
  end if;

  if new.status is distinct from old.status then
    if old.status in ('voided', 'failed', 'expired')
       or (old.status = 'succeeded' and new.status <> 'voided')
       or new.status = 'pending' then
      raise exception 'a payment can''t move from % to %', old.status, new.status
        using errcode = 'check_violation';
    end if;
  end if;

  if v_role is not null then
    if v_role <> 'owner' then
      raise exception 'only the owner can change a recorded payment'
        using errcode = 'insufficient_privilege';
    end if;
    if old.provider <> 'manual' then
      raise exception 'Card payments are managed in Stripe.' using errcode = 'insufficient_privilege';
    end if;
    if new.received_at is distinct from old.received_at
       or new.note is distinct from old.note
       or new.external_id is distinct from old.external_id
       or (new.voided_at is distinct from old.voided_at and new.status = old.status)
       or (new.voided_by is distinct from old.voided_by and new.status = old.status) then
      raise exception 'only the payment method can be corrected, or the payment voided'
        using errcode = 'insufficient_privilege';
    end if;
    if new.status = 'voided' and old.status <> 'voided' then
      select i.order_status into v_status
        from public.invoices i where i.id = old.invoice_id and i.tenant_id = old.tenant_id
         for update;
      if v_status <> 'quote' then
        raise exception 'This invoice is paid, so its payments can''t be voided. Refund it instead.';
      end if;
      new.voided_at := now();
      new.voided_by := (select auth.uid());
    end if;
  end if;

  -- Voiding a payment must not leave more refunded than received.
  if new.status = 'voided' and old.status = 'succeeded' and old.kind = 'payment'
     and public.invoice_amount_paid(old.invoice_id) - old.amount_cents < 0 then
    raise exception 'Void the refund first: this payment has been refunded.';
  end if;
  return new;
end;
$$;

create trigger payments_10_update_guard
  before update on public.payments
  for each row execute function public.payments_update_guard();

revoke all on function public.payments_insert_guard() from public, anon, authenticated;
revoke all on function public.payments_update_guard() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
alter table public.payments enable row level security;

create policy payments_owner_select on public.payments for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner');
create policy payments_staff_select on public.payments for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');
create policy payments_cpa_select on public.payments for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'cpa');

-- Owner and staff record manual payments; refunds are the owner's (the trigger says so too).
create policy payments_owner_insert on public.payments for insert to authenticated
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner'
              and provider = 'manual');
create policy payments_staff_insert on public.payments for insert to authenticated
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'staff'
              and provider = 'manual' and kind = 'payment');

create policy payments_owner_update on public.payments for update to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner'
         and provider = 'manual')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner'
              and provider = 'manual');

revoke all on public.payments from anon, authenticated;
grant select, insert, update on public.payments to authenticated;
