begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migrations 20261028000100-0400: the payments ledger, the paid-in-full guard, deposits,
-- production start, invoice_settle() and the tracking balance. Everything is rolled back.

-- Members of tenant A (owner, staff, cpa) and tenant B (owner), real auth users so
-- recorded_by and voided_by can point at them.
insert into auth.users (id, email) values
  ('ee000000-0000-0000-0000-00000000000a', 'pay-owner-a@test.local'),
  ('ee000000-0000-0000-0000-00000000000b', 'pay-staff-a@test.local'),
  ('ee000000-0000-0000-0000-00000000000c', 'pay-cpa-a@test.local'),
  ('ee000000-0000-0000-0000-00000000000d', 'pay-owner-b@test.local');

create function pg_temp.as_member(p_who text)
returns void
language plpgsql
as $$
begin
  perform set_config('request.jwt.claims', json_build_object(
    'sub', case p_who when 'owner' then 'ee000000-0000-0000-0000-00000000000a'
                      when 'staff' then 'ee000000-0000-0000-0000-00000000000b'
                      when 'cpa' then 'ee000000-0000-0000-0000-00000000000c'
                      else 'ee000000-0000-0000-0000-00000000000d' end,
    'role', 'authenticated',
    'tenant_id', case when p_who = 'owner_b' then '22222222-2222-2222-2222-222222222222'
                      else '11111111-1111-1111-1111-111111111111' end,
    'app_role', case when p_who = 'owner_b' then 'owner' else p_who end)::text, true);
end;
$$;

create function pg_temp.no_claims()
returns void
language sql
as $$ select set_config('request.jwt.claims', '', true); $$;

-- Runs p_sql as `authenticated` (with the claims already set) and KEEPS it; returns the row
-- count. Errors propagate.
create function pg_temp.run(p_sql text)
returns integer
language plpgsql
as $$
declare
  n integer;
begin
  perform set_config('role', 'authenticated', true);
  begin
    execute p_sql;
    get diagnostics n = row_count;
  exception when others then
    perform set_config('role', 'none', true);
    raise;
  end;
  perform set_config('role', 'none', true);
  return n;
end;
$$;

create function pg_temp.count_as(p_sql text)
returns bigint
language plpgsql
as $$
declare
  n bigint;
begin
  perform set_config('role', 'authenticated', true);
  execute p_sql into n;
  perform set_config('role', 'none', true);
  return n;
end;
$$;

-- A quote in tenant A: p_qty of product PAY-RING (1000 each, stock 5) plus 8% tax.
create function pg_temp.quote(p_id uuid, p_qty integer default 1)
returns void
language plpgsql
as $$
begin
  perform pg_temp.no_claims();
  insert into public.invoices (id, tenant_id, customer_name, subtotal_cents, tax_rate_bps,
                               tax_cents, total_cents)
  values (p_id, '11111111-1111-1111-1111-111111111111', 'Pay test', 1000 * p_qty, 800,
          80 * p_qty, 1080 * p_qty);
  insert into public.invoice_line_items
    (invoice_id, tenant_id, product_id, description, qty, unit_price_cents, line_total_cents)
  values (p_id, '11111111-1111-1111-1111-111111111111', 'e2000000-0000-0000-0000-000000000001',
          'Pay ring', p_qty, 1000, 1000 * p_qty);
end;
$$;

create function pg_temp.pay(p_invoice uuid, p_cents bigint, p_kind text default 'payment')
returns text
language sql
as $$
  select format($q$insert into public.payments (tenant_id, invoice_id, kind, method, amount_cents, currency)
                   values ('11111111-1111-1111-1111-111111111111', %L, %L, 'cash', %s, 'USD')$q$,
                p_invoice, p_kind, p_cents);
$$;

create function pg_temp.paid(p_invoice uuid)
returns bigint
language sql
as $$ select public.invoice_amount_paid(p_invoice); $$;

create function pg_temp.status(p_id uuid)
returns text
language sql
as $$ select order_status::text from public.invoices where id = p_id; $$;

insert into public.tenant_members (user_id, tenant_id, app_role) values
  ('ee000000-0000-0000-0000-00000000000a', '11111111-1111-1111-1111-111111111111', 'owner'),
  ('ee000000-0000-0000-0000-00000000000b', '11111111-1111-1111-1111-111111111111', 'staff'),
  ('ee000000-0000-0000-0000-00000000000c', '11111111-1111-1111-1111-111111111111', 'cpa'),
  ('ee000000-0000-0000-0000-00000000000d', '22222222-2222-2222-2222-222222222222', 'owner');

insert into public.products (id, tenant_id, sku, name, metal, price_cents, stock_qty)
values ('e2000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
        'PAY-RING', 'Pay ring', 'silver_925', 1000, 5);

-- ---------------------------------------------------------------------------
-- 0. The seed's ledger matches its invoices
-- ---------------------------------------------------------------------------
select is((select count(*)::int from public.invoices i
            where i.paid_at is not null
              and public.invoice_amount_paid(i.id)
                  <> case when i.refunded_at is null then i.total_cents else 0 end),
          0, 'every paid seed invoice''s payments equal its total');

-- ---------------------------------------------------------------------------
-- 1. Who may record what
-- ---------------------------------------------------------------------------
select pg_temp.quote('e3000000-0000-0000-0000-000000000001');  -- total 1080

select pg_temp.as_member('owner');
select is(pg_temp.run(format(
  $q$insert into public.payments (tenant_id, invoice_id, kind, method, amount_cents, currency,
       status, received_at, recorded_by, note)
     values ('11111111-1111-1111-1111-111111111111', 'e3000000-0000-0000-0000-000000000001',
             'payment', 'bank_transfer', 300, 'EUR', 'pending', '2020-01-01',
             'ee000000-0000-0000-0000-00000000000d', 'Deposit')$q$)), 1,
  'the owner records a deposit on a quote');
select is((select row(status, recorded_by, currency::text, received_at > now() - interval '1 minute')::text
             from public.payments where invoice_id = 'e3000000-0000-0000-0000-000000000001'),
          row('succeeded', 'ee000000-0000-0000-0000-00000000000a'::uuid, 'USD', true)::text,
          'an app user''s payment is succeeded, received now, in the shop''s currency, recorded by them');

select pg_temp.as_member('staff');
select is(pg_temp.run(pg_temp.pay('e3000000-0000-0000-0000-000000000001', 200)), 1,
          'staff record a payment');
select throws_ok(format('select pg_temp.run(%L)', pg_temp.pay('e3000000-0000-0000-0000-000000000001', 100, 'refund')),
                 '42501', null, 'staff cannot record a refund');
select is(pg_temp.run($q$update public.payments set status = 'voided'
                          where invoice_id = 'e3000000-0000-0000-0000-000000000001'$q$), 0,
          'staff cannot void a payment (no rows)');

select pg_temp.as_member('cpa');
select is(pg_temp.count_as($q$select count(*) from public.payments
                                where invoice_id = 'e3000000-0000-0000-0000-000000000001'$q$), 2::bigint,
          'the CPA reads payments');
select throws_ok(format('select pg_temp.run(%L)', pg_temp.pay('e3000000-0000-0000-0000-000000000001', 100)),
                 '42501', null, 'the CPA cannot record a payment');
select is(pg_temp.run($q$update public.payments set method = 'other'
                          where invoice_id = 'e3000000-0000-0000-0000-000000000001'$q$), 0,
          'the CPA cannot change a payment (no rows)');

select pg_temp.as_member('owner_b');
select is(pg_temp.count_as($q$select count(*) from public.payments
                                where invoice_id = 'e3000000-0000-0000-0000-000000000001'$q$), 0::bigint,
          'another shop''s owner sees none of them');
select throws_ok(format('select pg_temp.run(%L)', pg_temp.pay('e3000000-0000-0000-0000-000000000001', 100)),
                 '42501', null, 'another shop''s owner cannot pay into this shop');

select pg_temp.as_member('owner');
select throws_ok($q$select pg_temp.run('delete from public.payments')$q$,
                 '42501', null, 'nobody with an app role deletes payments');
select throws_ok($q$select pg_temp.run($$insert into public.payments (tenant_id, invoice_id, kind, provider, method, amount_cents, currency)
                    values ('11111111-1111-1111-1111-111111111111', 'e3000000-0000-0000-0000-000000000001', 'payment', 'stripe', 'card', 100, 'USD')$$)$q$,
                 '42501', null, 'app users cannot record Stripe payments');
select throws_ok($q$select pg_temp.run($$update public.payments set amount_cents = 1 where invoice_id = 'e3000000-0000-0000-0000-000000000001'$$)$q$,
                 '42501', null, 'the owner cannot change an amount');
select is(pg_temp.run($q$update public.payments set method = 'card'
                          where invoice_id = 'e3000000-0000-0000-0000-000000000001' and amount_cents = 200$q$), 1,
          'the owner can correct a method');

-- ---------------------------------------------------------------------------
-- 2. Amounts
-- ---------------------------------------------------------------------------
select is(pg_temp.paid('e3000000-0000-0000-0000-000000000001'), 500::bigint, '500 of 1080 received');
select throws_ok(format('select pg_temp.run(%L)', pg_temp.pay('e3000000-0000-0000-0000-000000000001', 581)),
                 '23514', 'That is more than the 5.80 USD still due.', 'no payment beyond the balance');
select throws_ok(format('select pg_temp.run(%L)', pg_temp.pay('e3000000-0000-0000-0000-000000000001', 0)),
                 '23514', null, 'no zero payment');
select throws_ok(format('select pg_temp.run(%L)', pg_temp.pay('e3000000-0000-0000-0000-000000000001', 501, 'refund')),
                 '23514', 'That is more than the 5.00 USD received.', 'no refund beyond what was received');
select pg_temp.no_claims();
select throws_ok(pg_temp.pay('e3000000-0000-0000-0000-000000000001', 581),
                 '23514', null, 'the balance rule holds for the service too');

-- ---------------------------------------------------------------------------
-- 3. Rule 1: paid in full before leaving quote, for every caller
-- ---------------------------------------------------------------------------
select pg_temp.no_claims();
select throws_ok($q$update public.invoices set order_status = 'paid' where id = 'e3000000-0000-0000-0000-000000000001'$q$,
                 'P0001', 'This invoice isn''t paid in full: 5.00 USD of 10.80 USD received.',
                 'the service cannot move a part-paid quote to paid');
select pg_temp.as_member('owner');
select throws_ok($q$select pg_temp.run($$update public.invoices set order_status = 'shipped', payment_method = 'cash' where id = 'e3000000-0000-0000-0000-000000000001'$$)$q$,
                 'P0001', null, 'nor can the owner, even skipping to shipped');
select pg_temp.as_member('staff');
select throws_ok($q$select pg_temp.run($$update public.invoices set order_status = 'paid', payment_method = 'cash' where id = 'e3000000-0000-0000-0000-000000000001'$$)$q$,
                 'P0001', null, 'nor staff');
select is(pg_temp.status('e3000000-0000-0000-0000-000000000001'), 'quote', 'it is still a quote');

-- ---------------------------------------------------------------------------
-- 4. Rule 2: production starts once, on a quote holding a deposit
-- ---------------------------------------------------------------------------
select pg_temp.quote('e3000000-0000-0000-0000-000000000002');  -- nothing paid
select pg_temp.as_member('staff');
select throws_ok($q$select pg_temp.run($$update public.invoices set production_started_at = now() where id = 'e3000000-0000-0000-0000-000000000002'$$)$q$,
                 'P0001', 'Production can be started only on an open quote that holds a deposit.',
                 'no production start without a deposit');
select is(pg_temp.run($q$update public.invoices set production_started_at = '2020-01-01'
                          where id = 'e3000000-0000-0000-0000-000000000001'$q$), 1,
          'staff start production on a quote with a deposit');
select ok((select production_started_at > now() - interval '1 minute' from public.invoices
            where id = 'e3000000-0000-0000-0000-000000000001'),
          'the start is stamped now, whatever was sent');
select throws_ok($q$select pg_temp.run($$update public.invoices set production_started_at = null where id = 'e3000000-0000-0000-0000-000000000001'$$)$q$,
                 '23514', null, 'production start cannot be cleared');
select pg_temp.no_claims();
select throws_ok($q$update public.invoices set production_started_at = now() - interval '1 day' where id = 'e3000000-0000-0000-0000-000000000001'$q$,
                 '23514', null, 'nor changed, even by the service');

-- Rule 5: the lines lock, the total can't drop below what was paid, no cancel, no delete.
select pg_temp.as_member('owner');
select throws_ok($q$select pg_temp.run($$update public.invoice_line_items set qty = 2 where invoice_id = 'e3000000-0000-0000-0000-000000000001'$$)$q$,
                 '42501', 'Production has started, so the items can''t change.', 'lines lock once production starts');
select throws_ok($q$select pg_temp.run($$update public.invoices set subtotal_cents = 400, tax_cents = 0, total_cents = 400 where id = 'e3000000-0000-0000-0000-000000000001'$$)$q$,
                 'P0001', 'The total can''t be less than the 5.00 USD already paid.', 'the total stays at or above what was paid');
select throws_ok($q$select pg_temp.run($$update public.invoices set cancelled_at = now() where id = 'e3000000-0000-0000-0000-000000000001'$$)$q$,
                 'P0001', 'This quote holds a deposit. Refund it before cancelling.', 'no cancel while a deposit is held');
select throws_ok($q$select pg_temp.run($$delete from public.invoices where id = 'e3000000-0000-0000-0000-000000000001'$$)$q$,
                 'P0001', null, 'no delete of a quote with payments');

-- ---------------------------------------------------------------------------
-- 5. Rule 3: invoice_settle
-- ---------------------------------------------------------------------------
select pg_temp.as_member('owner');
select is((select public.invoice_settle('e3000000-0000-0000-0000-000000000001')), 'balance_due',
          'settle with a balance due changes nothing');
select pg_temp.as_member('cpa');
select throws_ok($q$select public.invoice_settle('e3000000-0000-0000-0000-000000000001')$q$,
                 '42501', null, 'the CPA cannot settle');
select pg_temp.as_member('staff');
select pg_temp.run(pg_temp.pay('e3000000-0000-0000-0000-000000000001', 580));
select is((select public.invoice_settle('e3000000-0000-0000-0000-000000000001')), 'completed',
          'paying the rest completes it');
select is(pg_temp.status('e3000000-0000-0000-0000-000000000001'), 'in_production',
          'production had started, so it is in production');
select is((select payment_method::text from public.invoices where id = 'e3000000-0000-0000-0000-000000000001'),
          'other', 'mixed methods are recorded as other');
select is((select stock_qty from public.products where id = 'e2000000-0000-0000-0000-000000000001'), 4,
          'stock went down once');
select is((select public.invoice_settle('e3000000-0000-0000-0000-000000000001')), 'unchanged',
          'settling again does nothing');
select is((select stock_qty from public.products where id = 'e2000000-0000-0000-0000-000000000001'), 4,
          'and stock is not taken twice');
select pg_temp.as_member('owner');
select throws_ok($q$select pg_temp.run($$update public.payments set status = 'voided' where invoice_id = 'e3000000-0000-0000-0000-000000000001' and amount_cents = 580$$)$q$,
                 'P0001', null, 'no void once the invoice is paid');
select throws_ok(format('select pg_temp.run(%L)', pg_temp.pay('e3000000-0000-0000-0000-000000000001', 1)),
                 'P0001', null, 'a paid invoice takes no more payments');

-- Paid in full without production: paid. One payment method: that method.
select pg_temp.quote('e3000000-0000-0000-0000-000000000003');
select pg_temp.as_member('owner');
select pg_temp.run(pg_temp.pay('e3000000-0000-0000-0000-000000000003', 1080));
select is((select public.invoice_settle('e3000000-0000-0000-0000-000000000003')), 'completed', 'paid in one go');
select is((select order_status::text || '/' || payment_method::text from public.invoices
            where id = 'e3000000-0000-0000-0000-000000000003'), 'paid/cash',
          'no production: paid, by cash');

-- Paid in full but stock short: the payment stays, the invoice stays a quote.
select pg_temp.quote('e3000000-0000-0000-0000-000000000004', 9);  -- needs 9, 3 left
select pg_temp.as_member('owner');
select pg_temp.run(pg_temp.pay('e3000000-0000-0000-0000-000000000004', 9720));
select is((select public.invoice_settle('e3000000-0000-0000-0000-000000000004')), 'stock_short',
          'stock short is reported');
select is(pg_temp.status('e3000000-0000-0000-0000-000000000004') || '/' || pg_temp.paid('e3000000-0000-0000-0000-000000000004'),
          'quote/9720', 'the payment is kept and the invoice stays a quote');
select is((select stock_qty from public.products where id = 'e2000000-0000-0000-0000-000000000001'), 3,
          'no stock was taken');

-- ---------------------------------------------------------------------------
-- 6. Void and refund a deposit, then cancel
-- ---------------------------------------------------------------------------
select pg_temp.quote('e3000000-0000-0000-0000-000000000005');
select pg_temp.as_member('owner');
select pg_temp.run(pg_temp.pay('e3000000-0000-0000-0000-000000000005', 100));
select pg_temp.run(pg_temp.pay('e3000000-0000-0000-0000-000000000005', 200));
select is(pg_temp.run($q$update public.payments set status = 'voided'
                          where invoice_id = 'e3000000-0000-0000-0000-000000000005' and amount_cents = 100$q$), 1,
          'the owner voids a mistaken payment on a quote');
select is((select voided_by from public.payments where invoice_id = 'e3000000-0000-0000-0000-000000000005' and amount_cents = 100),
          'ee000000-0000-0000-0000-00000000000a'::uuid, 'voided by the owner');
select is(pg_temp.paid('e3000000-0000-0000-0000-000000000005'), 200::bigint, 'a voided payment counts for nothing');
select throws_ok($q$select pg_temp.run($$update public.payments set status = 'succeeded' where invoice_id = 'e3000000-0000-0000-0000-000000000005' and amount_cents = 100$$)$q$,
                 '23514', null, 'a void cannot be undone');
select pg_temp.run(pg_temp.pay('e3000000-0000-0000-0000-000000000005', 200, 'refund'));
select throws_ok($q$select pg_temp.run($$update public.payments set status = 'voided' where invoice_id = 'e3000000-0000-0000-0000-000000000005' and kind = 'payment' and amount_cents = 200$$)$q$,
                 'P0001', 'Void the refund first: this payment has been refunded.', 'a refunded payment cannot be voided');
select is(pg_temp.paid('e3000000-0000-0000-0000-000000000005'), 0::bigint, 'the deposit is refunded');
select is(pg_temp.run($q$update public.invoices set cancelled_at = now() where id = 'e3000000-0000-0000-0000-000000000005'$q$), 1,
          'with nothing held, the quote can be cancelled');
select throws_ok(format('select pg_temp.run(%L)', pg_temp.pay('e3000000-0000-0000-0000-000000000005', 1)),
                 'P0001', null, 'a cancelled invoice takes no payments');
select pg_temp.no_claims();
select lives_ok(pg_temp.pay('e3000000-0000-0000-0000-000000000005', 1),
                'the service may still record money on it (history, or a card payment that really happened)');

-- ---------------------------------------------------------------------------
-- 7. The service's rows (Slice B2 writes these): forward only, unique Stripe ids
-- ---------------------------------------------------------------------------
select pg_temp.quote('e3000000-0000-0000-0000-000000000006');
select pg_temp.no_claims();
insert into public.payments (tenant_id, invoice_id, kind, provider, method, status, amount_cents, currency, external_ref)
values ('11111111-1111-1111-1111-111111111111', 'e3000000-0000-0000-0000-000000000006', 'payment', 'stripe', 'card',
        'pending', 1080, 'USD', 'cs_test_1');
select is(pg_temp.paid('e3000000-0000-0000-0000-000000000006'), 0::bigint, 'a pending payment counts for nothing');
select lives_ok($q$update public.payments set status = 'succeeded', external_id = 'pi_test_1' where external_ref = 'cs_test_1'$q$,
                'pending moves to succeeded, with its PaymentIntent');
select throws_ok($q$update public.payments set status = 'expired' where external_ref = 'cs_test_1'$q$,
                 '23514', null, 'a succeeded payment never expires');
select throws_ok($q$update public.payments set external_id = 'pi_other' where external_ref = 'cs_test_1'$q$,
                 '42501', null, 'its PaymentIntent never changes');
select throws_ok($q$insert into public.payments (tenant_id, invoice_id, kind, provider, method, status, amount_cents, currency, external_id)
                    values ('11111111-1111-1111-1111-111111111111', 'e3000000-0000-0000-0000-000000000006', 'refund', 'stripe', 'card', 'succeeded', 10, 'USD', 'pi_test_1')$q$,
                 '23505', null, 'a Stripe id is recorded once');
select pg_temp.as_member('owner');
select is(pg_temp.run($q$update public.payments set status = 'voided' where external_ref = 'cs_test_1'$q$), 0,
          'card payments are not voided in DELEA (no rows)');

-- ---------------------------------------------------------------------------
-- 8. Grants on the functions
-- ---------------------------------------------------------------------------
select ok(not has_function_privilege('anon', 'public.invoice_settle(uuid)', 'execute'),
          'anon cannot execute invoice_settle');
select ok(not has_function_privilege('storefront_reader', 'public.invoice_amount_paid(uuid)', 'execute'),
          'storefront_reader cannot execute invoice_amount_paid');
select ok(not has_function_privilege('authenticated', 'public.payments_insert_guard()', 'execute'),
          'the trigger functions are not callable');

select * from finish();
rollback;
