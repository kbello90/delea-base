-- Card only on Stripe Checkout (TRD §6.14). API version 2026-09-30.endive no longer takes
-- payment_method_types on a Checkout Session: payment methods come from a payment method
-- configuration on the shop's own account. DELEA makes one there that has card on and every
-- other method off, and names it on each session.
--   1. payment_accounts.card_config_id: that configuration (pmc_...), made on the shop's first
--      card checkout. A new connected account (stripe_link_account) starts without one.
--   2. stripe_events outcome 'unsupported': the async_payment_* events of methods that confirm
--      later, which DELEA doesn't take (card only). Recorded, nothing else.
--   3. payments_writer functions: stripe_card_config, stripe_set_card_config,
--      stripe_record_unsupported.

-- ---------------------------------------------------------------------------
-- 1. The shop's card-only configuration
-- ---------------------------------------------------------------------------
alter table public.payment_accounts
  add column card_config_id text check (card_config_id ~ '^pmc_[A-Za-z0-9]{1,64}$');

-- ---------------------------------------------------------------------------
-- 2. The unsupported outcome
-- ---------------------------------------------------------------------------
alter table public.stripe_events drop constraint stripe_events_outcome_check;
alter table public.stripe_events add constraint stripe_events_outcome_check
  check (outcome in ('applied', 'stock_short', 'unchanged', 'ignored', 'no_match', 'live_refused',
                     'unsupported'));

-- ---------------------------------------------------------------------------
-- 3. Functions (payments_writer only)
-- ---------------------------------------------------------------------------
-- The card-only configuration stored for the shop's account in force, or null.
create function public.stripe_card_config(p_tenant_id uuid, p_account_id text)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select a.card_config_id from public.payment_accounts a
   where a.tenant_id = p_tenant_id and a.provider = 'stripe'
     and a.external_account_id = p_account_id and a.disconnected_at is null;
$$;

-- Stores a configuration made at Stripe. p_replaces is the one it replaces (null the first
-- time): compare-and-set, so two checkouts at once keep a single configuration. Returns the
-- configuration in force afterwards (null when the account isn't the shop's any more).
create function public.stripe_set_card_config(
  p_tenant_id uuid, p_account_id text, p_config_id text, p_replaces text)
returns text
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v text;
begin
  update public.payment_accounts a set card_config_id = p_config_id
   where a.tenant_id = p_tenant_id and a.provider = 'stripe'
     and a.external_account_id = p_account_id and a.disconnected_at is null
     and a.card_config_id is not distinct from p_replaces
  returning a.card_config_id into v;
  if v is null then
    v := public.stripe_card_config(p_tenant_id, p_account_id);
  end if;
  return v;
end;
$$;

-- checkout.session.async_payment_succeeded / _failed: only a method that confirms later sends
-- them, and the configuration DELEA makes is card only. If the owner turns such a method on in
-- their Stripe Dashboard, its events are recorded as unsupported and change nothing.
create function public.stripe_record_unsupported(
  p_event_id text, p_event_type text, p_account_id text, p_livemode boolean)
returns text
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_tenant uuid;
begin
  if not public.stripe_event_first_time(p_event_id, p_event_type, p_account_id, p_livemode) then
    return 'duplicate';
  end if;
  if p_livemode then
    return public.stripe_event_outcome(p_event_id, null, 'live_refused');
  end if;
  select a.tenant_id into v_tenant from public.payment_accounts a
   where a.provider = 'stripe' and a.external_account_id = p_account_id;
  return public.stripe_event_outcome(p_event_id, v_tenant, 'unsupported');
end;
$$;

revoke all on function public.stripe_card_config(uuid, text) from public, anon, authenticated, service_role;
revoke all on function public.stripe_set_card_config(uuid, text, text, text) from public, anon, authenticated, service_role;
revoke all on function public.stripe_record_unsupported(text, text, text, boolean) from public, anon, authenticated, service_role;

grant execute on function public.stripe_card_config(uuid, text) to payments_writer;
grant execute on function public.stripe_set_card_config(uuid, text, text, text) to payments_writer;
grant execute on function public.stripe_record_unsupported(text, text, text, boolean) to payments_writer;
