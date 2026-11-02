-- Stripe's times (Slice B2 follow-up, TRD §6.14).
--
-- A card payment is received when Stripe completed it, not when DELEA heard about it: the
-- webhook can arrive late (a retry, a resend after the forwarder dropped). So:
--   payments.received_at  card payment: the checkout.session.completed event's created time;
--                         card refund: the refund's created time.
--   invoices.paid_at      when a card payment completes the invoice: that payment's time
--                         (the latest succeeded payment, so a manual balance after a card
--                         deposit still dates the invoice by the balance). Otherwise now().
--   invoices.refunded_at  refunded through Stripe: the time of the last refund.
-- Each Stripe time is capped at now() (clock skew) and falls back to now() when missing.
-- Every Stripe refund is then on or after its invoice's paid_at, which the dashboard's
-- refund shares require (received_at >= paid_at); with the arrival time as paid_at, a refund
-- made in Stripe before a late completion event would have been left out of revenue.
-- Manual payments are unchanged: app users' received_at is still now() (no backdating).

-- ---------------------------------------------------------------------------
-- invoices.paid_at follows a card payment's time
-- ---------------------------------------------------------------------------
create or replace function public.invoices_assign_numbers()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    new.quote_number := public.next_document_number(new.tenant_id, 'quote', now());
    new.invoice_number := null;
    new.paid_at := null;
    if new.order_status > 'quote' then
      new.paid_at := now();
      new.invoice_number := public.next_document_number(new.tenant_id, 'invoice', new.paid_at);
    end if;
  else
    -- Numbers and paid_at are immutable once assigned.
    new.quote_number := old.quote_number;
    new.invoice_number := old.invoice_number;
    new.paid_at := old.paid_at;
    if old.order_status = 'quote' and new.order_status > 'quote' then
      -- Paid by card: when the last payment was received (Stripe's time), not now.
      select least(max(p.received_at), now())
        into new.paid_at
        from public.payments p
       where p.invoice_id = new.id and p.tenant_id = new.tenant_id
         and p.kind = 'payment' and p.status = 'succeeded'
      having bool_or(p.provider = 'stripe');
      new.paid_at := coalesce(new.paid_at, now());
      new.invoice_number := public.next_document_number(new.tenant_id, 'invoice', new.paid_at);
    end if;
  end if;
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- The webhook functions take Stripe's time
-- ---------------------------------------------------------------------------
drop function public.stripe_apply_session(text, text, text, boolean, uuid, uuid, text, text, text, bigint, text);
drop function public.stripe_apply_refund(text, text, text, boolean, text, text, bigint, text, text);

-- p_completed_at: the event's created time (when Stripe completed the session).
create function public.stripe_apply_session(
  p_event_id text, p_event_type text, p_account_id text, p_livemode boolean,
  p_tenant_id uuid, p_payment_id uuid, p_session_id text, p_payment_intent text,
  p_payment_status text, p_amount_cents bigint, p_currency text, p_completed_at timestamptz)
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
       set status = 'succeeded', external_id = p_payment_intent,
           received_at = least(coalesce(p_completed_at, now()), now())
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

-- p_created_at: the refund's created time. A refund row keeps it through later status events.
create function public.stripe_apply_refund(
  p_event_id text, p_event_type text, p_account_id text, p_livemode boolean,
  p_refund_id text, p_payment_intent text, p_amount_cents bigint, p_currency text,
  p_stripe_status text, p_created_at timestamptz)
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
                                 amount_cents, currency, external_id, refund_of, received_at)
    values (paid_row.tenant_id, inv.id, 'refund', 'stripe', 'card', v_status, p_amount_cents,
            upper(p_currency), p_refund_id, paid_row.id,
            least(coalesce(p_created_at, now()), now()));
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
    update public.invoices
       set refunded_at = coalesce(
             (select max(r.received_at) from public.payments r
               where r.invoice_id = inv.id and r.kind = 'refund' and r.status = 'succeeded'),
             now())
     where id = inv.id;
  end if;
  return public.stripe_event_outcome(p_event_id, paid_row.tenant_id, 'applied');
end;
$$;

revoke all on function public.stripe_apply_session(text, text, text, boolean, uuid, uuid, text, text, text, bigint, text, timestamptz) from public, anon, authenticated, service_role;
revoke all on function public.stripe_apply_refund(text, text, text, boolean, text, text, bigint, text, text, timestamptz) from public, anon, authenticated, service_role;
grant execute on function public.stripe_apply_session(text, text, text, boolean, uuid, uuid, text, text, text, bigint, text, timestamptz) to payments_writer;
grant execute on function public.stripe_apply_refund(text, text, text, boolean, text, text, bigint, text, text, timestamptz) to payments_writer;
