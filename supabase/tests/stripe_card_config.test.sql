begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migration 20261029000400: the shop's card-only payment method configuration and the
-- unsupported outcome for async_payment_* events. Everything is rolled back.

create function pg_temp.set_claims(p_tenant uuid, p_app_role text)
returns void
language plpgsql
as $$
begin
  perform set_config('request.jwt.claims', json_build_object(
    'sub', gen_random_uuid(), 'role', 'authenticated',
    'tenant_id', p_tenant, 'app_role', p_app_role)::text, true);
end;
$$;

-- Runs a query returning one text value as p_role (no claims unless set).
create function pg_temp.as_role(p_role text, p_sql text)
returns text
language plpgsql
as $$
declare
  v text;
begin
  perform set_config('role', p_role, true);
  begin
    execute p_sql into v;
  exception when others then
    perform set_config('role', 'none', true);
    raise;
  end;
  perform set_config('role', 'none', true);
  return v;
end;
$$;

create function pg_temp.writer(p_sql text)
returns text
language sql
as $$ select pg_temp.as_role('payments_writer', p_sql); $$;

-- ---------------------------------------------------------------------------
-- 1. Who may execute the functions
-- ---------------------------------------------------------------------------
select ok(has_function_privilege('payments_writer', 'public.stripe_card_config(uuid, text)', 'execute')
      and has_function_privilege('payments_writer', 'public.stripe_set_card_config(uuid, text, text, text)', 'execute')
      and has_function_privilege('payments_writer', 'public.stripe_record_unsupported(text, text, text, boolean)', 'execute'),
          'payments_writer executes the three functions');
select ok(not has_function_privilege(r, 'public.stripe_set_card_config(uuid, text, text, text)', 'execute')
      and not has_function_privilege(r, 'public.stripe_card_config(uuid, text)', 'execute')
      and not has_function_privilege(r, 'public.stripe_record_unsupported(text, text, text, boolean)', 'execute'),
          r || ' does not') from unnest(array['anon', 'authenticated', 'service_role', 'storefront_writer', 'storefront_reader']) r;

-- ---------------------------------------------------------------------------
-- 2. Storing the configuration
-- ---------------------------------------------------------------------------
select is(pg_temp.writer($q$select public.stripe_link_account('11111111-1111-1111-1111-111111111111', 'acct_ShopA')$q$),
          'acct_ShopA', 'shop A has an account');
select is(pg_temp.writer($q$select public.stripe_card_config('11111111-1111-1111-1111-111111111111', 'acct_ShopA')$q$),
          null, 'no configuration until the first card checkout');
select is(pg_temp.writer($q$select public.stripe_set_card_config('11111111-1111-1111-1111-111111111111', 'acct_ShopA', 'pmc_First', null)$q$),
          'pmc_First', 'the first configuration is stored');
select is(pg_temp.writer($q$select public.stripe_set_card_config('11111111-1111-1111-1111-111111111111', 'acct_ShopA', 'pmc_Racing', null)$q$),
          'pmc_First', 'a second checkout at the same time keeps the stored one');
select is(pg_temp.writer($q$select public.stripe_set_card_config('11111111-1111-1111-1111-111111111111', 'acct_ShopA', 'pmc_Second', 'pmc_Stale')$q$),
          'pmc_First', 'replacing needs the one in force');
select is(pg_temp.writer($q$select public.stripe_set_card_config('11111111-1111-1111-1111-111111111111', 'acct_ShopA', 'pmc_Second', 'pmc_First')$q$),
          'pmc_Second', 'an unusable configuration is replaced');
select is(pg_temp.writer($q$select public.stripe_card_config('22222222-2222-2222-2222-222222222222', 'acct_ShopA')$q$),
          null, 'another shop never reads it');
select is(pg_temp.writer($q$select public.stripe_set_card_config('22222222-2222-2222-2222-222222222222', 'acct_ShopA', 'pmc_Evil', 'pmc_Second')$q$),
          null, 'nor writes it');
select is((select card_config_id from public.payment_accounts where tenant_id = '11111111-1111-1111-1111-111111111111'),
          'pmc_Second', 'shop A keeps its own');
select throws_ok($q$select pg_temp.writer($$select public.stripe_set_card_config('11111111-1111-1111-1111-111111111111', 'acct_ShopA', 'not a pmc', 'pmc_Second')$$)$q$,
                 '23514', null, 'only a pmc_ id');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select throws_ok($q$select pg_temp.as_role('authenticated', $$update public.payment_accounts set card_config_id = 'pmc_Owner' returning 'x'$$)$q$,
                 '42501', null, 'the owner reads the account but cannot write the configuration');

-- A disconnected account, then a new one: the new account starts without a configuration.
select pg_temp.writer($q$select public.stripe_apply_account(null, null, 'acct_ShopA', false, null, null, true)$q$);
select is(pg_temp.writer($q$select public.stripe_card_config('11111111-1111-1111-1111-111111111111', 'acct_ShopA')$q$),
          null, 'a disconnected account has none in force');
select is(pg_temp.writer($q$select public.stripe_link_account('11111111-1111-1111-1111-111111111111', 'acct_ShopANew')$q$),
          'acct_ShopANew', 'a new account');
select is((select card_config_id from public.payment_accounts where tenant_id = '11111111-1111-1111-1111-111111111111'),
          null, 'starts without a configuration');

-- ---------------------------------------------------------------------------
-- 3. async_payment_* events: recorded as unsupported, nothing else
-- ---------------------------------------------------------------------------
select is(pg_temp.writer($q$select public.stripe_record_unsupported('evt_async_ok', 'checkout.session.async_payment_succeeded', 'acct_ShopANew', false)$q$),
          'unsupported', 'async_payment_succeeded is unsupported');
select is((select tenant_id::text || '/' || event_type from public.stripe_events where event_id = 'evt_async_ok'),
          '11111111-1111-1111-1111-111111111111/checkout.session.async_payment_succeeded',
          'recorded against the shop');
select is(pg_temp.writer($q$select public.stripe_record_unsupported('evt_async_ok', 'checkout.session.async_payment_succeeded', 'acct_ShopANew', false)$q$),
          'duplicate', 'once');
select is(pg_temp.writer($q$select public.stripe_record_unsupported('evt_async_no', 'checkout.session.async_payment_failed', 'acct_Nobody', false)$q$),
          'unsupported', 'async_payment_failed too, even for an unknown account');
select is(pg_temp.writer($q$select public.stripe_record_unsupported('evt_async_live', 'checkout.session.async_payment_succeeded', 'acct_ShopANew', true)$q$),
          'live_refused', 'live mode is refused');

select * from finish();
rollback;
