begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migration 20261022000200: invoices.payment_method (null on quotes, staff can't change it
-- once set), the closed-invoice guard (no status moves on a cancelled or refunded invoice,
-- no refund of a cancelled one, no cancel of a refunded one) and the reworded messages.
-- Everything is rolled back.

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

create function pg_temp.no_claims()
returns void
language sql
as $$ select set_config('request.jwt.claims', '', true); $$;

-- Runs a write as p_role and KEEPS it. Errors propagate.
create function pg_temp.exec_as(p_role text, p_sql text)
returns integer
language plpgsql
as $$
declare
  n integer;
begin
  perform set_config('role', p_role, true);
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

-- A one-line invoice of product T-PM (stock 50) in tenant A, as a quote. Created without
-- claims (like the seed), so it can be moved anywhere afterwards.
create function pg_temp.invoice(p_id uuid)
returns void
language plpgsql
as $$
begin
  perform pg_temp.no_claims();
  insert into public.invoices (id, tenant_id, customer_name)
  values (p_id, '11111111-1111-1111-1111-111111111111', 'PM test');
  insert into public.invoice_line_items
    (invoice_id, tenant_id, product_id, description, qty, unit_price_cents, line_total_cents)
  values (p_id, '11111111-1111-1111-1111-111111111111', 'e1000000-0000-0000-0000-000000000001',
          'PM ring', 1, 1000, 1000);
end;
$$;

create function pg_temp.status(p_id uuid)
returns text
language sql
as $$ select order_status::text from public.invoices where id = p_id; $$;

insert into public.products (id, tenant_id, sku, name, metal, price_cents, stock_qty)
values ('e1000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
        'T-PM', 'PM ring', 'other', 1000, 50);

-- ===========================================================================
-- 1. The column and its check
-- ===========================================================================
select has_type('public', 'payment_method', 'payment_method enum exists');
select enum_has_labels('public', 'payment_method', array['cash', 'card', 'bank_transfer', 'other'],
                       'payment_method values');
select has_column('public', 'invoices', 'payment_method', 'invoices.payment_method exists');
select col_is_null('public', 'invoices', 'payment_method',
                   'payment_method is nullable (invoices paid before it existed have none)');
select col_hasnt_default('public', 'invoices', 'payment_method', 'payment_method has no default');

select pg_temp.no_claims();
select throws_ok($q$insert into public.invoices (tenant_id, customer_name, payment_method)
                    values ('11111111-1111-1111-1111-111111111111', 'PM quote', 'cash')$q$,
                 '23514', null, 'a quote cannot have a payment method, even for postgres');

select pg_temp.invoice('f1000000-0000-0000-0000-000000000001');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select throws_ok(format('select pg_temp.exec_as(%L, %L)', 'authenticated',
  $q$update public.invoices set payment_method = 'card' where id = 'f1000000-0000-0000-0000-000000000001'$q$),
  '23514', null, 'the owner cannot set a payment method on a quote');

-- ===========================================================================
-- 2. Set when paid; staff can't change it once set; the owner can
-- ===========================================================================
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.exec_as('authenticated',
  $q$update public.invoices set order_status = 'paid', payment_method = 'cash' where id = 'f1000000-0000-0000-0000-000000000001'$q$),
  1, 'staff can mark a quote paid with a payment method');
select is((select payment_method::text from public.invoices where id = 'f1000000-0000-0000-0000-000000000001'),
          'cash', 'the payment method is stored');
select throws_ok(format('select pg_temp.exec_as(%L, %L)', 'authenticated',
  $q$update public.invoices set payment_method = 'card' where id = 'f1000000-0000-0000-0000-000000000001'$q$),
  '42501', 'staff cannot change the payment method once it is set',
  'staff cannot change a payment method that is set');
select throws_ok(format('select pg_temp.exec_as(%L, %L)', 'authenticated',
  $q$update public.invoices set payment_method = null where id = 'f1000000-0000-0000-0000-000000000001'$q$),
  '42501', 'staff cannot change the payment method once it is set',
  'staff cannot clear a payment method that is set');
select is(pg_temp.exec_as('authenticated',
  $q$update public.invoices set notes = 'gift wrap' where id = 'f1000000-0000-0000-0000-000000000001'$q$),
  1, 'staff can still edit the notes of a paid invoice');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select is(pg_temp.exec_as('authenticated',
  $q$update public.invoices set payment_method = 'card' where id = 'f1000000-0000-0000-0000-000000000001'$q$),
  1, 'the owner can correct the payment method');
select is((select payment_method::text from public.invoices where id = 'f1000000-0000-0000-0000-000000000001'),
          'card', 'the correction is stored');

-- An invoice paid before the column existed (seed: Sarah's a7…01) has none; staff may fill it.
select is((select payment_method from public.invoices where id = 'a7000000-0000-0000-0000-000000000001'),
          null, 'the seed''s paid invoice has no payment method');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.exec_as('authenticated',
  $q$update public.invoices set payment_method = 'other' where id = 'a7000000-0000-0000-0000-000000000001'$q$),
  1, 'staff can record a missing payment method');

-- ===========================================================================
-- 3. Cancelled and refunded invoices, for every caller
-- ===========================================================================
-- f…02 paid then cancelled; f…03 paid then refunded.
select pg_temp.invoice('f1000000-0000-0000-0000-000000000002');
select pg_temp.invoice('f1000000-0000-0000-0000-000000000003');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select pg_temp.exec_as('authenticated', $q$update public.invoices set order_status = 'paid', payment_method = 'card'
  where id in ('f1000000-0000-0000-0000-000000000002', 'f1000000-0000-0000-0000-000000000003')$q$);
select is(pg_temp.exec_as('authenticated',
  $q$update public.invoices set cancelled_at = now() where id = 'f1000000-0000-0000-0000-000000000002'$q$),
  1, 'the owner cancels a paid invoice');
select is(pg_temp.exec_as('authenticated',
  $q$update public.invoices set refunded_at = now() where id = 'f1000000-0000-0000-0000-000000000003'$q$),
  1, 'the owner refunds a paid invoice');

create function pg_temp.as_case(p_app_role text)
returns void
language plpgsql
as $$
begin
  if p_app_role is null then
    perform pg_temp.no_claims();
  else
    perform pg_temp.set_claims('11111111-1111-1111-1111-111111111111', p_app_role);
  end if;
end;
$$;

select pg_temp.as_case('owner');
select throws_ok(format('select pg_temp.exec_as(%L, %L)', 'authenticated',
  $q$update public.invoices set order_status = 'shipped' where id = 'f1000000-0000-0000-0000-000000000002'$q$),
  'P0001', 'This invoice is cancelled, so its status can''t change.', 'owner: no status move on a cancelled invoice');
select pg_temp.as_case('staff');
select throws_ok(format('select pg_temp.exec_as(%L, %L)', 'authenticated',
  $q$update public.invoices set order_status = 'delivered' where id = 'f1000000-0000-0000-0000-000000000002'$q$),
  'P0001', 'This invoice is cancelled, so its status can''t change.', 'staff: no status move on a cancelled invoice');
select pg_temp.as_case(null);
select throws_ok($q$update public.invoices set order_status = 'in_production' where id = 'f1000000-0000-0000-0000-000000000002'$q$,
  'P0001', 'This invoice is cancelled, so its status can''t change.',
  'postgres / service role: no status move on a cancelled invoice either');
select throws_ok($q$update public.invoices set order_status = 'quote' where id = 'f1000000-0000-0000-0000-000000000002'$q$,
  'P0001', 'This invoice is cancelled, so its status can''t change.',
  'a backward move on a cancelled invoice reports the cancellation');

select pg_temp.as_case('owner');
select throws_ok(format('select pg_temp.exec_as(%L, %L)', 'authenticated',
  $q$update public.invoices set order_status = 'shipped' where id = 'f1000000-0000-0000-0000-000000000003'$q$),
  'P0001', 'This invoice is refunded, so its status can''t change.', 'owner: no status move on a refunded invoice');
select pg_temp.as_case('staff');
select throws_ok(format('select pg_temp.exec_as(%L, %L)', 'authenticated',
  $q$update public.invoices set order_status = 'delivered' where id = 'f1000000-0000-0000-0000-000000000003'$q$),
  'P0001', 'This invoice is refunded, so its status can''t change.', 'staff: no status move on a refunded invoice');

select pg_temp.as_case('owner');
select throws_ok(format('select pg_temp.exec_as(%L, %L)', 'authenticated',
  $q$update public.invoices set refunded_at = now() where id = 'f1000000-0000-0000-0000-000000000002'$q$),
  'P0001', 'A cancelled invoice can''t be refunded.', 'owner cannot refund a cancelled invoice');
select throws_ok(format('select pg_temp.exec_as(%L, %L)', 'authenticated',
  $q$update public.invoices set cancelled_at = now() where id = 'f1000000-0000-0000-0000-000000000003'$q$),
  'P0001', 'A refunded invoice can''t be cancelled.', 'owner cannot cancel a refunded invoice');
select pg_temp.as_case(null);
select throws_ok($q$update public.invoices set refunded_at = now() where id = 'f1000000-0000-0000-0000-000000000002'$q$,
  'P0001', 'A cancelled invoice can''t be refunded.', 'postgres cannot refund a cancelled invoice');
select throws_ok($q$update public.invoices set cancelled_at = now() where id = 'f1000000-0000-0000-0000-000000000003'$q$,
  'P0001', 'A refunded invoice can''t be cancelled.', 'postgres cannot cancel a refunded invoice');

-- Refund and cancel in one update is refused too (whichever way round it is read).
select pg_temp.invoice('f1000000-0000-0000-0000-000000000004');
select pg_temp.exec_as('postgres', $q$update public.invoices set order_status = 'paid' where id = 'f1000000-0000-0000-0000-000000000004'$q$);
select throws_ok($q$update public.invoices set refunded_at = now(), cancelled_at = now() where id = 'f1000000-0000-0000-0000-000000000004'$q$,
  'P0001', 'A cancelled invoice can''t be refunded.', 'refund and cancel together are refused');

-- Nothing changed on the refused rows.
select results_eq(
  $q$select id::text, order_status::text, refunded_at is null, cancelled_at is null from public.invoices
      where id in ('f1000000-0000-0000-0000-000000000002', 'f1000000-0000-0000-0000-000000000003',
                   'f1000000-0000-0000-0000-000000000004') order by id$q$,
  $q$values ('f1000000-0000-0000-0000-000000000002', 'paid', true, false),
            ('f1000000-0000-0000-0000-000000000003', 'paid', false, true),
            ('f1000000-0000-0000-0000-000000000004', 'paid', true, true)$q$,
  'the refused moves changed nothing');

-- Other edits still work on a closed invoice.
select pg_temp.as_case('staff');
select is(pg_temp.exec_as('authenticated',
  $q$update public.invoices set notes = 'customer called' where id = 'f1000000-0000-0000-0000-000000000002'$q$),
  1, 'notes can still change on a cancelled invoice');
select pg_temp.as_case('owner');
select is(pg_temp.exec_as('authenticated',
  $q$update public.invoices set payment_method = 'cash' where id = 'f1000000-0000-0000-0000-000000000003'$q$),
  1, 'the owner can still correct the payment method of a refunded invoice');

-- ===========================================================================
-- 4. Plain-language messages
-- ===========================================================================
select pg_temp.no_claims();
select throws_ok($q$update public.invoices set order_status = 'quote' where id = 'f1000000-0000-0000-0000-000000000001'$q$,
  '23514', 'This invoice can''t move back to quote.', 'backward move: plain message');
update public.invoices set order_status = 'shipped' where id = 'f1000000-0000-0000-0000-000000000001';
select throws_ok($q$update public.invoices set order_status = 'in_production' where id = 'f1000000-0000-0000-0000-000000000001'$q$,
  '23514', 'This invoice can''t move back to in production.', 'underscores read as spaces');

select pg_temp.invoice('f1000000-0000-0000-0000-000000000005');
update public.invoice_line_items set qty = 51 where invoice_id = 'f1000000-0000-0000-0000-000000000005';
select throws_ok($q$update public.invoices set order_status = 'paid' where id = 'f1000000-0000-0000-0000-000000000005'$q$,
  'P0001', 'Not enough stock for SKU T-PM: 46 available, 51 needed.', 'stock shortage: plain message');

select * from finish();
rollback;
