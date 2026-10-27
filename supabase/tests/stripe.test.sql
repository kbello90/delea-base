begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migrations 20261028000500-0700: payment_accounts, card attempts, and the webhook functions
-- run as payments_writer. Everything is rolled back.

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

-- checkout.session.<kind> for the pending row of an invoice, with optional overrides.
create function pg_temp.session_event(
  p_event text, p_kind text, p_payment uuid, p_account text default 'acct_ShopA',
  p_amount bigint default null, p_live boolean default false, p_status text default 'paid')
returns text
language plpgsql
as $$
declare
  pay record;
begin
  select tenant_id, external_ref, amount_cents into pay from public.payments where id = p_payment;
  return pg_temp.writer(format(
    'select public.stripe_apply_session(%L, %L, %L, %L, %L, %L, %L, %L, %L, %s, %L)',
    p_event, 'checkout.session.' || p_kind, p_account, p_live, pay.tenant_id, p_payment,
    pay.external_ref, 'pi_' || replace(p_payment::text, '-', ''), p_status,
    coalesce(p_amount, pay.amount_cents), 'usd'));
end;
$$;

create function pg_temp.refund_event(
  p_event text, p_kind text, p_refund text, p_payment uuid, p_amount bigint, p_status text,
  p_account text default 'acct_ShopA')
returns text
language sql
as $$
  select pg_temp.writer(format(
    'select public.stripe_apply_refund(%L, %L, %L, false, %L, %L, %s, %L, %L)',
    p_event, 'refund.' || p_kind, p_account, p_refund,
    'pi_' || replace(p_payment::text, '-', ''), p_amount, 'usd', p_status));
$$;

-- A storefront order in tenant A paying by card: p_qty of PAY-CARD (1000 + 8% tax).
create function pg_temp.order_(p_id uuid, p_qty integer default 1)
returns void
language plpgsql
as $$
begin
  insert into public.invoices (id, tenant_id, customer_name, source, subtotal_cents, tax_rate_bps,
                               tax_cents, total_cents, fulfillment, payment_choice, checkout_key,
                               checkout_fingerprint, order_ref, public_token)
  values (p_id, '11111111-1111-1111-1111-111111111111', 'Card shopper', 'storefront',
          1000 * p_qty, 800, 80 * p_qty, 1080 * p_qty, 'pickup', 'card', gen_random_uuid(),
          repeat('a', 64), 'W-' || upper(substr(md5(p_id::text), 1, 8)) , p_id);
  insert into public.invoice_line_items
    (invoice_id, tenant_id, product_id, description, qty, unit_price_cents, line_total_cents)
  values (p_id, '11111111-1111-1111-1111-111111111111', 'e4000000-0000-0000-0000-000000000001',
          'Card ring', p_qty, 1000, 1000 * p_qty);
end;
$$;

-- Starts a card payment for an order and attaches a session; returns the payment id.
create function pg_temp.start(p_order uuid, p_session text)
returns uuid
language plpgsql
as $$
declare
  started jsonb;
begin
  started := pg_temp.as_role('storefront_writer', format(
    'select public.storefront_card_payment_start(%L, %L)::text',
    '11111111-1111-1111-1111-111111111111', p_order))::jsonb;
  perform pg_temp.as_role('storefront_writer', format(
    'select public.storefront_card_payment_attach(%L, %L, %L, %L, %L)::text',
    '11111111-1111-1111-1111-111111111111', started ->> 'payment_id', p_session,
    'https://checkout.stripe.com/c/pay/' || p_session, now() + interval '1 hour'));
  return (started ->> 'payment_id')::uuid;
end;
$$;

create function pg_temp.status(p_id uuid)
returns text
language sql
as $$ select order_status::text from public.invoices where id = p_id; $$;

-- The writer roles run as an administrative login in tests, as the API's does.
grant storefront_writer, payments_writer to postgres;
update public.storefront_settings set storefront_enabled = true
 where tenant_id = '11111111-1111-1111-1111-111111111111';
insert into public.products (id, tenant_id, sku, name, metal, price_cents, stock_qty)
values ('e4000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
        'PAY-CARD', 'Card ring', 'silver_925', 1000, 3);

-- ---------------------------------------------------------------------------
-- 1. Who may run what
-- ---------------------------------------------------------------------------
select ok(has_function_privilege('payments_writer',
          'public.stripe_apply_session(text, text, text, boolean, uuid, uuid, text, text, text, bigint, text)', 'execute'),
          'payments_writer executes stripe_apply_session');
select ok(not has_function_privilege(r, 'public.stripe_apply_session(text, text, text, boolean, uuid, uuid, text, text, text, bigint, text)', 'execute'),
          r || ' cannot execute stripe_apply_session')
  from unnest(array['anon', 'authenticated', 'service_role', 'storefront_writer', 'storefront_reader']) r;
select ok(not has_function_privilege(r, 'public.stripe_link_account(uuid, text)', 'execute'),
          r || ' cannot execute stripe_link_account')
  from unnest(array['anon', 'authenticated', 'storefront_writer']) r;
select ok(not has_function_privilege('payments_writer', 'public.storefront_card_payment_start(uuid, uuid)', 'execute'),
          'payments_writer cannot start a card payment');
select ok(not has_table_privilege('payments_writer', 'public.' || t, 'select'),
          'payments_writer has no grant on ' || t)
  from unnest(array['payments', 'invoices', 'payment_accounts', 'stripe_events']) t;
select throws_ok($q$select pg_temp.writer('select count(*)::text from public.payments')$q$,
                 '42501', null, 'payments_writer cannot read payments directly');

-- An anonymous caller and another shop's owner, through the API's switch.
select pg_temp.set_claims('22222222-2222-2222-2222-222222222222', 'owner');
select throws_ok($q$select pg_temp.as_role('authenticated', $$select public.stripe_link_account('11111111-1111-1111-1111-111111111111', 'acct_Evil')$$)$q$,
                 '42501', null, 'a signed-in owner cannot link an account');
select throws_ok($q$select pg_temp.as_role('anon', $$select public.stripe_record_event('evt_x', 'x', null, false)$$)$q$,
                 '42501', null, 'anon cannot record an event');
select set_config('request.jwt.claims', '', true);

-- ---------------------------------------------------------------------------
-- 2. payment_accounts: owner reads, nobody with an app role writes
-- ---------------------------------------------------------------------------
select is(pg_temp.writer($q$select public.stripe_link_account('11111111-1111-1111-1111-111111111111', 'acct_ShopA')$q$),
          'acct_ShopA', 'the writer links a shop''s new account');
select is(pg_temp.writer($q$select public.stripe_link_account('11111111-1111-1111-1111-111111111111', 'acct_Other')$q$),
          'acct_ShopA', 'linking again keeps the account in force');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select is(pg_temp.as_role('authenticated', 'select external_account_id from public.payment_accounts'),
          'acct_ShopA', 'the owner reads the shop''s account');
select throws_ok($q$select pg_temp.as_role('authenticated', $$update public.payment_accounts set charges_enabled = true returning 'x'$$)$q$,
                 '42501', null, 'the owner cannot change it');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.as_role('authenticated', 'select count(*)::text from public.payment_accounts'),
          '0', 'staff see no account');
select pg_temp.set_claims('22222222-2222-2222-2222-222222222222', 'owner');
select is(pg_temp.as_role('authenticated', 'select count(*)::text from public.payment_accounts'),
          '0', 'another shop''s owner sees none');
select set_config('request.jwt.claims', '', true);

-- ---------------------------------------------------------------------------
-- 3. Card availability and attempts
-- ---------------------------------------------------------------------------
select pg_temp.order_('e5000000-0000-0000-0000-000000000001');
select is(pg_temp.as_role('storefront_writer', $q$select (public.storefront_card_payment_start('11111111-1111-1111-1111-111111111111', 'e5000000-0000-0000-0000-000000000001') ->> 'status')$q$),
          'card_off', 'no card until the account can take charges');
select is((select card_enabled from storefront.store where false), null, 'store view has card_enabled');
select is(pg_temp.writer($q$select public.stripe_apply_account(null, null, 'acct_ShopA', false, true, true, false)$q$),
          'applied', 'onboarding return: charges enabled');
select set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true);
select is(pg_temp.as_role('storefront_reader', 'select card_enabled::text from storefront.store'), 'true',
          'the store says card is available');

select is(pg_temp.as_role('storefront_writer', $q$select (public.storefront_card_payment_start('11111111-1111-1111-1111-111111111111', 'e5000000-0000-0000-0000-000000000001') - 'payment_id' - 'invoice_id' - 'order_ref')::text$q$)::jsonb,
          '{"status": "start", "amount_cents": 1080, "currency": "USD", "account_id": "acct_ShopA", "open_sessions": []}'::jsonb,
          'a card attempt is a pending row for the balance');
select is((select status || '/' || provider || '/' || method::text from public.payments
            where invoice_id = 'e5000000-0000-0000-0000-000000000001'), 'pending/stripe/card',
          'the pending row');
select is(pg_temp.as_role('storefront_writer', format('select coalesce(public.storefront_card_payment_failed(%L, %L)::text, '''')',
            '11111111-1111-1111-1111-111111111111', (select id from public.payments where invoice_id = 'e5000000-0000-0000-0000-000000000001'))),
          '', 'Stripe unreachable: the attempt is marked failed');
select is((select status from public.payments where invoice_id = 'e5000000-0000-0000-0000-000000000001'), 'failed',
          'and stays on the order as failed');

select pg_temp.start('e5000000-0000-0000-0000-000000000001', 'cs_test_A1');
select is(pg_temp.as_role('storefront_writer', $q$select (public.storefront_card_payment_start('11111111-1111-1111-1111-111111111111', 'e5000000-0000-0000-0000-000000000001'))::text$q$)::jsonb,
          '{"status": "reuse", "checkout_url": "https://checkout.stripe.com/c/pay/cs_test_A1"}'::jsonb,
          'Pay now again reuses the open session for the same balance');
select throws_ok(format($q$select pg_temp.as_role('storefront_writer', %L)$q$,
                        format('select public.storefront_card_payment_attach(%L, %L, %L, %L, now())::text',
                               '11111111-1111-1111-1111-111111111111', (select id from public.payments where external_ref = 'cs_test_A1'),
                               'cs_test_Other', 'https://x')),
                 'P0002', null, 'a session is attached once');

-- Stripe's minimum: a 30-cent balance is paid with the shop.
select pg_temp.order_('e5000000-0000-0000-0000-000000000002');
insert into public.payments (tenant_id, invoice_id, kind, method, amount_cents, currency)
values ('11111111-1111-1111-1111-111111111111', 'e5000000-0000-0000-0000-000000000002', 'payment', 'cash', 1050, 'USD');
select is(pg_temp.as_role('storefront_writer', $q$select (public.storefront_card_payment_start('11111111-1111-1111-1111-111111111111', 'e5000000-0000-0000-0000-000000000002') ->> 'status')$q$),
          'too_small', 'a balance under 50 cents can''t be paid by card');

-- ---------------------------------------------------------------------------
-- 4. checkout.session.completed: paid once, settled in the same transaction
-- ---------------------------------------------------------------------------
select is(pg_temp.session_event('evt_wrong_acct', 'completed', (select id from public.payments where external_ref = 'cs_test_A1'), 'acct_Other'),
          'no_match', 'another account''s event changes nothing');
select is(pg_temp.session_event('evt_wrong_amount', 'completed', (select id from public.payments where external_ref = 'cs_test_A1'), p_amount => 1),
          'no_match', 'a different amount changes nothing');
select is(pg_temp.session_event('evt_live', 'completed', (select id from public.payments where external_ref = 'cs_test_A1'), p_live => true),
          'live_refused', 'a live-mode event is refused (test mode only)');
select is(pg_temp.session_event('evt_unpaid', 'completed', (select id from public.payments where external_ref = 'cs_test_A1'), p_status => 'unpaid'),
          'ignored', 'a completed session that isn''t paid changes nothing');
select is(pg_temp.status('e5000000-0000-0000-0000-000000000001'), 'quote', 'still a quote after all that');

select is(pg_temp.session_event('evt_paid_A1', 'completed', (select id from public.payments where external_ref = 'cs_test_A1')),
          'applied', 'the real event applies');
select is((select status || '/' || external_id from public.payments where external_ref = 'cs_test_A1'),
          'succeeded/pi_' || replace((select id from public.payments where external_ref = 'cs_test_A1')::text, '-', ''),
          'the payment succeeded with its PaymentIntent');
select is(pg_temp.status('e5000000-0000-0000-0000-000000000001') || '/' ||
          (select payment_method::text from public.invoices where id = 'e5000000-0000-0000-0000-000000000001'),
          'paid/card', 'paid in full, by card');
select is((select stock_qty from public.products where id = 'e4000000-0000-0000-0000-000000000001'), 2, 'stock went down once');
select is(pg_temp.session_event('evt_paid_A1', 'completed', (select id from public.payments where external_ref = 'cs_test_A1')),
          'duplicate', 'a replay is a duplicate');
select is(pg_temp.session_event('evt_paid_A1_again', 'completed', (select id from public.payments where external_ref = 'cs_test_A1')),
          'unchanged', 'another event for the same session changes nothing');
select is(pg_temp.session_event('evt_late_expiry', 'expired', (select id from public.payments where external_ref = 'cs_test_A1')),
          'unchanged', 'a late expiry never undoes a payment');
select is((select stock_qty from public.products where id = 'e4000000-0000-0000-0000-000000000001'), 2, 'stock still went down once');
-- (event_type is not null: the seed's own event row has none.)
select is((select string_agg(event_id, ',' order by event_id) from public.stripe_events
            where event_type is not null and outcome = 'applied'
              and tenant_id = '11111111-1111-1111-1111-111111111111'), 'evt_paid_A1', 'one event applied, with its shop');
select is((select outcome from public.stripe_events where event_id = 'evt_wrong_acct'), 'no_match', 'the mismatch is recorded');

-- Stock short at payment: the payment is kept, the invoice stays a quote, a replay changes nothing.
select pg_temp.order_('e5000000-0000-0000-0000-000000000003', 5);  -- needs 5, 2 left
select pg_temp.start('e5000000-0000-0000-0000-000000000003', 'cs_test_Short');
select is(pg_temp.session_event('evt_short', 'completed', (select id from public.payments where external_ref = 'cs_test_Short')),
          'stock_short', 'paid, but stock is short');
select is(pg_temp.status('e5000000-0000-0000-0000-000000000003') || '/' ||
          public.invoice_amount_paid('e5000000-0000-0000-0000-000000000003'),
          'quote/5400', 'the payment is kept and the invoice stays a quote');
select is((select outcome from public.stripe_events where event_id = 'evt_short'), 'stock_short', 'recorded as stock short');
select is(pg_temp.session_event('evt_short', 'completed', (select id from public.payments where external_ref = 'cs_test_Short')),
          'duplicate', 'its replay is a duplicate');
select is(pg_temp.status('e5000000-0000-0000-0000-000000000003') || '/' ||
          public.invoice_amount_paid('e5000000-0000-0000-0000-000000000003') || '/' ||
          (select stock_qty from public.products where id = 'e4000000-0000-0000-0000-000000000001'),
          'quote/5400/2', 'and changes nothing');

-- Expired: pending -> expired.
select pg_temp.order_('e5000000-0000-0000-0000-000000000004');
select pg_temp.start('e5000000-0000-0000-0000-000000000004', 'cs_test_Exp');
select is(pg_temp.session_event('evt_exp', 'expired', (select id from public.payments where external_ref = 'cs_test_Exp')),
          'applied', 'an expired session');
select is((select status from public.payments where external_ref = 'cs_test_Exp'), 'expired', 'is expired');

-- ---------------------------------------------------------------------------
-- 5. Refunds (in the Stripe Dashboard)
-- ---------------------------------------------------------------------------
select is(pg_temp.refund_event('evt_r1_created', 'created', 're_1', (select id from public.payments where external_ref = 'cs_test_A1'), 500, 'pending'),
          'applied', 'a pending partial refund is recorded');
select is(public.invoice_amount_paid('e5000000-0000-0000-0000-000000000001'), 1080::bigint, 'a pending refund counts for nothing');
select is(pg_temp.refund_event('evt_r1_updated', 'updated', 're_1', (select id from public.payments where external_ref = 'cs_test_A1'), 500, 'succeeded'),
          'applied', 'it succeeds');
select is(public.invoice_amount_paid('e5000000-0000-0000-0000-000000000001'), 580::bigint, '500 refunded');
select is((select refunded_at from public.invoices where id = 'e5000000-0000-0000-0000-000000000001'), null::timestamptz,
          'a partial refund doesn''t mark the invoice refunded');
select is(pg_temp.refund_event('evt_r1_old', 'created', 're_1', (select id from public.payments where external_ref = 'cs_test_A1'), 500, 'pending'),
          'unchanged', 'an older event arriving late never moves it back');
select is(pg_temp.refund_event('evt_r1_updated', 'updated', 're_1', (select id from public.payments where external_ref = 'cs_test_A1'), 500, 'succeeded'),
          'duplicate', 'a replayed refund event is a duplicate');
select is(pg_temp.refund_event('evt_r_unknown', 'created', 're_9', 'e5000000-0000-0000-0000-000000000099', 100, 'succeeded'),
          'no_match', 'a refund of an unknown payment changes nothing');
select is(pg_temp.refund_event('evt_r2', 'created', 're_2', (select id from public.payments where external_ref = 'cs_test_A1'), 580, 'succeeded'),
          'applied', 'the rest is refunded');
select isnt((select refunded_at from public.invoices where id = 'e5000000-0000-0000-0000-000000000001'), null::timestamptz,
            'everything refunded: the invoice is refunded');
select is((select stock_qty from public.products where id = 'e4000000-0000-0000-0000-000000000001'), 2, 'a refund never restocks');
select is(pg_temp.refund_event('evt_r2_failed', 'failed', 're_2', (select id from public.payments where external_ref = 'cs_test_A1'), 580, 'failed'),
          'applied', 'a refund can still fail after it succeeded');
select is(public.invoice_amount_paid('e5000000-0000-0000-0000-000000000001'), 580::bigint, 'and then counts for nothing');

-- ---------------------------------------------------------------------------
-- 6. Accounts and other events
-- ---------------------------------------------------------------------------
select is(pg_temp.writer($q$select public.stripe_apply_account('evt_acct_unknown', 'account.updated', 'acct_Nobody', false, true, true, false)$q$),
          'no_match', 'an unknown account changes nothing');
select is(pg_temp.writer($q$select public.stripe_apply_account('evt_deauth', 'account.application.deauthorized', 'acct_ShopA', false, null, null, true)$q$),
          'applied', 'a disconnected account');
select is(pg_temp.as_role('storefront_reader', 'select card_enabled::text from storefront.store'), 'false',
          'turns card off');
select is(pg_temp.writer($q$select public.stripe_record_event('evt_other', 'customer.created', 'acct_ShopA', false)$q$),
          'ignored', 'any other event is recorded as ignored');
select is(pg_temp.writer($q$select public.stripe_record_event('evt_other', 'customer.created', 'acct_ShopA', false)$q$),
          'duplicate', 'once');

select * from finish();
rollback;
