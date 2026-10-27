-- Slice B2: the Stripe webhook and Connect onboarding write through a narrow role (decision K4).
--
-- payments_writer is a role with no table grants. It may execute these SECURITY DEFINER
-- functions only, each pinned to search_path = '' and checking the shop itself:
--   stripe_apply_session  checkout.session.completed / .expired
--   stripe_apply_refund   refund.created / .updated / .failed
--   stripe_apply_account  account.updated / account.application.deauthorized, and the
--                         onboarding return (no event id)
--   stripe_record_event   any other event: recorded as ignored
--   stripe_link_account   a shop's new connected account (onboarding)
--   stripe_close_session  a session expired by DELEA before a manual payment
-- Idempotency: every event function first inserts its event id into stripe_events with
-- ON CONFLICT DO NOTHING, in the same transaction as its effects. A replay finds the row and
-- changes nothing; an effect that fails rolls the event row back too, so Stripe's retry
-- applies it cleanly. Live-mode events are refused in Slice B (test mode only; D16).

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'payments_writer') then
    create role payments_writer nologin noinherit;
  end if;
end
$$;
-- The API's login switches into it for webhooks and onboarding; the least-privilege login
-- gets the same grant (TRD §6.6).
grant payments_writer to postgres;

-- Records an event once. False when it was already recorded (a replay).
create function public.stripe_event_first_time(
  p_event_id text, p_event_type text, p_account_id text, p_livemode boolean)
returns boolean
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  n integer;
begin
  if p_event_id !~ '^evt_[A-Za-z0-9_]{1,250}$' then
    raise exception 'invalid event id' using errcode = 'invalid_parameter_value';
  end if;
  insert into public.stripe_events (event_id, event_type, account_id, livemode)
  values (p_event_id, p_event_type, p_account_id, p_livemode)
  on conflict (event_id) do nothing;
  get diagnostics n = row_count;
  return n = 1;
end;
$$;

create function public.stripe_event_outcome(p_event_id text, p_tenant_id uuid, p_outcome text)
returns text
language sql
volatile
security definer
set search_path = ''
as $$
  update public.stripe_events set tenant_id = coalesce(p_tenant_id, tenant_id), outcome = p_outcome
   where event_id = p_event_id
  returning outcome;
$$;

-- ---------------------------------------------------------------------------
-- checkout.session.completed / checkout.session.expired
-- ---------------------------------------------------------------------------
-- p_tenant_id and p_payment_id come from the session's metadata; they are trusted only when
-- the payment row, its session, the connected account, the amount and the currency all match.
-- Returns: duplicate | applied | stock_short | unchanged | ignored | no_match | live_refused
create function public.stripe_apply_session(
  p_event_id text, p_event_type text, p_account_id text, p_livemode boolean,
  p_tenant_id uuid, p_payment_id uuid, p_session_id text, p_payment_intent text,
  p_payment_status text, p_amount_cents bigint, p_currency text)
returns text
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  pay record;
  settled text;
begin
  if not public.stripe_event_first_time(p_event_id, p_event_type, p_account_id, p_livemode) then
    return 'duplicate';
  end if;
  if p_livemode then
    return public.stripe_event_outcome(p_event_id, null, 'live_refused');
  end if;

  select p.id, p.tenant_id, p.invoice_id, p.status
    into pay
    from public.payments p
    join public.payment_accounts a
      on a.tenant_id = p.tenant_id and a.provider = 'stripe' and a.external_account_id = p_account_id
   where p.id = p_payment_id and p.tenant_id = p_tenant_id
     and p.provider = 'stripe' and p.kind = 'payment' and p.external_ref = p_session_id
     and p.amount_cents = p_amount_cents and lower(p.currency) = lower(p_currency)
     for update of p;
  if not found then
    return public.stripe_event_outcome(p_event_id, null, 'no_match');
  end if;

  if p_event_type = 'checkout.session.completed' then
    if p_payment_status is distinct from 'paid' then
      return public.stripe_event_outcome(p_event_id, pay.tenant_id, 'ignored');
    end if;
    if pay.status <> 'pending' then
      return public.stripe_event_outcome(p_event_id, pay.tenant_id, 'unchanged');
    end if;
    update public.payments
       set status = 'succeeded', external_id = p_payment_intent, received_at = now()
     where id = pay.id;
    -- Paid in full: paid, or in production. Stock short: the payment stays, the quote stays.
    settled := public.invoice_settle(pay.invoice_id);
    return public.stripe_event_outcome(
      p_event_id, pay.tenant_id, case when settled = 'stock_short' then 'stock_short' else 'applied' end);
  elsif p_event_type = 'checkout.session.expired' then
    if pay.status <> 'pending' then
      return public.stripe_event_outcome(p_event_id, pay.tenant_id, 'unchanged');
    end if;
    update public.payments set status = 'expired' where id = pay.id;
    return public.stripe_event_outcome(p_event_id, pay.tenant_id, 'applied');
  end if;
  return public.stripe_event_outcome(p_event_id, pay.tenant_id, 'ignored');
end;
$$;

-- ---------------------------------------------------------------------------
-- refund.created / refund.updated / refund.failed
-- ---------------------------------------------------------------------------
-- One refund row per Stripe refund id. Only a succeeded refund counts. When everything
-- received has been refunded, a paid invoice is marked refunded (it never restocks).
create function public.stripe_apply_refund(
  p_event_id text, p_event_type text, p_account_id text, p_livemode boolean,
  p_refund_id text, p_payment_intent text, p_amount_cents bigint, p_currency text,
  p_stripe_status text)
returns text
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  paid_row record;
  refund_row record;
  v_status text := case p_stripe_status
                     when 'succeeded' then 'succeeded'
                     when 'failed' then 'failed'
                     when 'canceled' then 'failed'
                     else 'pending' end;
  inv record;
begin
  if not public.stripe_event_first_time(p_event_id, p_event_type, p_account_id, p_livemode) then
    return 'duplicate';
  end if;
  if p_livemode then
    return public.stripe_event_outcome(p_event_id, null, 'live_refused');
  end if;
  if p_refund_id !~ '^(re|pyr)_[A-Za-z0-9_]{1,250}$' or p_amount_cents is null or p_amount_cents <= 0 then
    return public.stripe_event_outcome(p_event_id, null, 'no_match');
  end if;

  select p.id, p.tenant_id, p.invoice_id
    into paid_row
    from public.payments p
    join public.payment_accounts a
      on a.tenant_id = p.tenant_id and a.provider = 'stripe' and a.external_account_id = p_account_id
   where p.provider = 'stripe' and p.kind = 'payment' and p.status = 'succeeded'
     and p.external_id = p_payment_intent and lower(p.currency) = lower(p_currency);
  if not found then
    return public.stripe_event_outcome(p_event_id, null, 'no_match');
  end if;
  -- Serialize with anything else touching this invoice's money.
  select i.id, i.paid_at, i.refunded_at, i.cancelled_at
    into inv
    from public.invoices i where i.id = paid_row.invoice_id for update;

  select r.id, r.status, r.amount_cents into refund_row
    from public.payments r
   where r.provider = 'stripe' and r.external_id = p_refund_id
     for update;
  if not found then
    if v_status = 'succeeded' and p_amount_cents > public.invoice_amount_paid(inv.id) then
      return public.stripe_event_outcome(p_event_id, paid_row.tenant_id, 'no_match');
    end if;
    insert into public.payments (tenant_id, invoice_id, kind, provider, method, status,
                                 amount_cents, currency, external_id, refund_of)
    values (paid_row.tenant_id, inv.id, 'refund', 'stripe', 'card', v_status, p_amount_cents,
            upper(p_currency), p_refund_id, paid_row.id);
  elsif refund_row.amount_cents <> p_amount_cents then
    return public.stripe_event_outcome(p_event_id, paid_row.tenant_id, 'no_match');
  elsif refund_row.status = v_status
        or refund_row.status in ('failed', 'voided', 'expired')
        or (refund_row.status = 'succeeded' and v_status <> 'failed') then
    -- Events can arrive out of order: a state never moves back.
    return public.stripe_event_outcome(p_event_id, paid_row.tenant_id, 'unchanged');
  else
    update public.payments set status = v_status where id = refund_row.id;
  end if;

  if inv.paid_at is not null and inv.refunded_at is null and inv.cancelled_at is null
     and public.invoice_amount_paid(inv.id) <= 0 then
    update public.invoices set refunded_at = now() where id = inv.id;
  end if;
  return public.stripe_event_outcome(p_event_id, paid_row.tenant_id, 'applied');
end;
$$;

-- ---------------------------------------------------------------------------
-- Accounts
-- ---------------------------------------------------------------------------
-- p_event_id null: the owner came back from onboarding and DELEA asked Stripe itself.
create function public.stripe_apply_account(
  p_event_id text, p_event_type text, p_account_id text, p_livemode boolean,
  p_charges_enabled boolean, p_details_submitted boolean, p_deauthorized boolean)
returns text
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_tenant uuid;
begin
  if p_event_id is not null
     and not public.stripe_event_first_time(p_event_id, p_event_type, p_account_id, p_livemode) then
    return 'duplicate';
  end if;
  if p_livemode then
    return coalesce(public.stripe_event_outcome(p_event_id, null, 'live_refused'), 'live_refused');
  end if;
  update public.payment_accounts
     set charges_enabled = case when p_deauthorized then false else coalesce(p_charges_enabled, charges_enabled) end,
         details_submitted = coalesce(p_details_submitted, details_submitted),
         disconnected_at = case when p_deauthorized then coalesce(disconnected_at, now()) else disconnected_at end
   where provider = 'stripe' and external_account_id = p_account_id
  returning tenant_id into v_tenant;
  if v_tenant is null then
    return coalesce(public.stripe_event_outcome(p_event_id, null, 'no_match'), 'no_match');
  end if;
  return coalesce(public.stripe_event_outcome(p_event_id, v_tenant, 'applied'), 'applied');
end;
$$;

create function public.stripe_record_event(
  p_event_id text, p_event_type text, p_account_id text, p_livemode boolean)
returns text
language plpgsql
volatile
security definer
set search_path = ''
as $$
begin
  if not public.stripe_event_first_time(p_event_id, p_event_type, p_account_id, p_livemode) then
    return 'duplicate';
  end if;
  return public.stripe_event_outcome(p_event_id, null,
                                     case when p_livemode then 'live_refused' else 'ignored' end);
end;
$$;

-- A shop's connected account, made by DELEA for onboarding. One per shop; a shop whose account
-- was disconnected may link a new one. Returns the account id in force for the shop.
create function public.stripe_link_account(p_tenant_id uuid, p_account_id text)
returns text
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  current_row record;
begin
  if not exists (select 1 from public.tenants t where t.id = p_tenant_id and t.is_active) then
    raise exception 'shop not found' using errcode = 'no_data_found';
  end if;
  select a.external_account_id, a.disconnected_at into current_row
    from public.payment_accounts a
   where a.tenant_id = p_tenant_id and a.provider = 'stripe'
     for update;
  if found and current_row.disconnected_at is null then
    return current_row.external_account_id;
  end if;
  delete from public.payment_accounts where tenant_id = p_tenant_id and provider = 'stripe';
  insert into public.payment_accounts (tenant_id, provider, external_account_id)
  values (p_tenant_id, 'stripe', p_account_id);
  return p_account_id;
end;
$$;

-- A pending session DELEA expired at Stripe (before recording a manual payment).
create function public.stripe_close_session(p_tenant_id uuid, p_session_id text)
returns void
language sql
volatile
security definer
set search_path = ''
as $$
  update public.payments set status = 'expired'
   where tenant_id = p_tenant_id and provider = 'stripe' and external_ref = p_session_id
     and status = 'pending';
$$;

-- ---------------------------------------------------------------------------
-- Who may execute them
-- ---------------------------------------------------------------------------
revoke all on function public.stripe_event_first_time(text, text, text, boolean) from public, anon, authenticated, service_role;
revoke all on function public.stripe_event_outcome(text, uuid, text) from public, anon, authenticated, service_role;
revoke all on function public.stripe_apply_session(text, text, text, boolean, uuid, uuid, text, text, text, bigint, text) from public, anon, authenticated, service_role;
revoke all on function public.stripe_apply_refund(text, text, text, boolean, text, text, bigint, text, text) from public, anon, authenticated, service_role;
revoke all on function public.stripe_apply_account(text, text, text, boolean, boolean, boolean, boolean) from public, anon, authenticated, service_role;
revoke all on function public.stripe_record_event(text, text, text, boolean) from public, anon, authenticated, service_role;
revoke all on function public.stripe_link_account(uuid, text) from public, anon, authenticated, service_role;
revoke all on function public.stripe_close_session(uuid, text) from public, anon, authenticated, service_role;

grant execute on function public.stripe_apply_session(text, text, text, boolean, uuid, uuid, text, text, text, bigint, text) to payments_writer;
grant execute on function public.stripe_apply_refund(text, text, text, boolean, text, text, bigint, text, text) to payments_writer;
grant execute on function public.stripe_apply_account(text, text, text, boolean, boolean, boolean, boolean) to payments_writer;
grant execute on function public.stripe_record_event(text, text, text, boolean) to payments_writer;
grant execute on function public.stripe_link_account(uuid, text) to payments_writer;
grant execute on function public.stripe_close_session(uuid, text) to payments_writer;
