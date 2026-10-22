begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- ---------------------------------------------------------------------------
-- Session-local helpers. Statements run as the given role and the role is restored, so the
-- pgTAP assertions themselves always run as the test owner.
-- ---------------------------------------------------------------------------
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

-- Runs a single-value query (e.g. select count(*) ...) as p_role.
create function pg_temp.count_as(p_role text, p_query text)
returns bigint
language plpgsql
as $$
declare
  n bigint;
begin
  perform set_config('role', p_role, true);
  begin
    execute p_query into n;
  exception when others then
    perform set_config('role', 'none', true);
    raise;
  end;
  perform set_config('role', 'none', true);
  return n;
end;
$$;

-- Runs a write as p_role, returns the affected row count, and rolls the write back.
-- Errors (permission denied, RLS violations, trigger rejections) propagate.
create function pg_temp.write_as(p_role text, p_sql text)
returns integer
language plpgsql
as $$
declare
  n integer := 0;
begin
  perform set_config('role', p_role, true);
  begin
    execute p_sql;
    get diagnostics n = row_count;
    raise exception 'dry run' using errcode = 'P0099';
  exception
    when sqlstate 'P0099' then
      null;
    when others then
      perform set_config('role', 'none', true);
      raise;
  end;
  perform set_config('role', 'none', true);
  return n;
end;
$$;


-- Migration 20261020001200: current_tax_rate_bps(), quote-only line items (policies + guard
-- trigger), invoices created as quotes by app users, and the stock decrement when an invoice
-- leaves 'quote'. Seed: a7…01 is Sarah's paid invoice, a7…02 is Michael's quote.

-- Runs a write as p_role and KEEPS it (write_as rolls back). Errors propagate.
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

create function pg_temp.no_claims()
returns void
language sql
as $$ select set_config('request.jwt.claims', '', true); $$;

create function pg_temp.stock(p_sku text)
returns integer
language sql
as $$ select stock_qty from public.products where sku = p_sku; $$;

-- ===========================================================================
-- 1. current_tax_rate_bps()
-- ===========================================================================
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select is(pg_temp.count_as('authenticated', 'select public.current_tax_rate_bps()'), 800::bigint,
          'owner of A reads the tax rate (800 bps)');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.count_as('authenticated', 'select public.current_tax_rate_bps()'), 800::bigint,
          'staff of A reads the tax rate without access to company_settings');
select is(pg_temp.count_as('authenticated', 'select count(*) from public.company_settings'), 0::bigint,
          'staff still cannot read company_settings itself');
select pg_temp.set_claims('22222222-2222-2222-2222-222222222222', 'owner');
select is(pg_temp.count_as('authenticated', 'select public.current_tax_rate_bps()'), 550::bigint,
          'owner of B reads tenant B''s rate (550 bps)');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'cpa');
select throws_ok(format('select pg_temp.count_as(%L, %L)', 'authenticated', 'select public.current_tax_rate_bps()'),
                 '42501', 'only owner or staff can read the tax rate', 'cpa is refused');

select set_config('request.jwt.claims',
  '{"role":"authenticated","tenant_id":"11111111-1111-1111-1111-111111111111"}', true);
select throws_ok(format('select pg_temp.count_as(%L, %L)', 'authenticated', 'select public.current_tax_rate_bps()'),
                 '42501', null, 'a token without app_role is refused');

select set_config('request.jwt.claims', '{"role":"authenticated","app_role":"owner"}', true);
select throws_ok(format('select pg_temp.count_as(%L, %L)', 'authenticated', 'select public.current_tax_rate_bps()'),
                 '42501', 'no tenant in the token', 'a token without tenant_id is refused');

select pg_temp.no_claims();
insert into public.tenants (id, name) values ('33333333-3333-3333-3333-333333333333', 'No Settings');
select pg_temp.set_claims('33333333-3333-3333-3333-333333333333', 'owner');
select throws_ok(format('select pg_temp.count_as(%L, %L)', 'authenticated', 'select public.current_tax_rate_bps()'),
                 'P0001', 'company settings are missing for this tenant', 'a tenant without settings gets P0001');

select ok(has_function_privilege('authenticated', 'public.current_tax_rate_bps()', 'execute'),
          'authenticated can execute current_tax_rate_bps');
select ok(not has_function_privilege('anon', 'public.current_tax_rate_bps()', 'execute'),
          'anon cannot execute current_tax_rate_bps');
select ok(not has_function_privilege('service_role', 'public.current_tax_rate_bps()', 'execute'),
          'service_role cannot execute current_tax_rate_bps');
select ok(not has_function_privilege('ai_readonly', 'public.current_tax_rate_bps()', 'execute'),
          'ai_readonly cannot execute current_tax_rate_bps');
select is((select count(*)::int from pg_proc p, aclexplode(p.proacl) a
            where p.oid = 'public.current_tax_rate_bps()'::regprocedure and a.grantee = 0),
          0, 'PUBLIC has no EXECUTE on current_tax_rate_bps');
select ok((select prosecdef and 'search_path=""' = any (proconfig) from pg_proc
            where oid = 'public.current_tax_rate_bps()'::regprocedure),
          'current_tax_rate_bps is SECURITY DEFINER with an empty search_path');

-- ===========================================================================
-- 2. Line items: writable only while the invoice is a quote
-- ===========================================================================
create temp table line_sql (name text primary key, sql text not null);
insert into line_sql values
  ('insert_quote', $q$insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents) values ('a7000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'Extra', 1, 100, 100)$q$),
  ('update_quote', $q$update public.invoice_line_items set qty = 2 where invoice_id = 'a7000000-0000-0000-0000-000000000002'$q$),
  ('delete_quote', $q$delete from public.invoice_line_items where invoice_id = 'a7000000-0000-0000-0000-000000000002'$q$),
  ('insert_paid',  $q$insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents) values ('a7000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Extra', 1, 100, 100)$q$),
  ('update_paid',  $q$update public.invoice_line_items set qty = 2 where invoice_id = 'a7000000-0000-0000-0000-000000000001'$q$),
  ('delete_paid',  $q$delete from public.invoice_line_items where invoice_id = 'a7000000-0000-0000-0000-000000000001'$q$),
  ('move_to_paid', $q$update public.invoice_line_items set invoice_id = 'a7000000-0000-0000-0000-000000000001' where invoice_id = 'a7000000-0000-0000-0000-000000000002'$q$);
grant select on line_sql to public;

create function pg_temp.line(p_name text) returns text language sql
as $$ select sql from pg_temp.line_sql where name = p_name; $$;

-- Owner and staff: full write access on quote lines; nothing on paid lines.
create function pg_temp.check_quote_only(p_app_role text)
returns setof text
language plpgsql
as $$
begin
  perform pg_temp.set_claims('11111111-1111-1111-1111-111111111111', p_app_role);
  return next is(pg_temp.write_as('authenticated', pg_temp.line('insert_quote')), 1,
                 p_app_role || ' can add a line to a quote');
  return next is(pg_temp.write_as('authenticated', pg_temp.line('update_quote')), 1,
                 p_app_role || ' can edit a line on a quote');
  return next is(pg_temp.write_as('authenticated', pg_temp.line('delete_quote')), 1,
                 p_app_role || ' can delete a line on a quote');
  return next throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', pg_temp.line('insert_paid')),
                        '42501', 'line items can only change while the invoice is a quote',
                        p_app_role || ' cannot add a line to a paid invoice (guard)');
  return next is(pg_temp.write_as('authenticated', pg_temp.line('update_paid')), 0,
                 p_app_role || ' cannot edit a paid invoice''s line (policy: 0 rows)');
  return next is(pg_temp.write_as('authenticated', pg_temp.line('delete_paid')), 0,
                 p_app_role || ' cannot delete a paid invoice''s line (policy: 0 rows)');
  return next throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', pg_temp.line('move_to_paid')),
                        '42501', null, p_app_role || ' cannot move a quote line onto a paid invoice');
end;
$$;

select * from pg_temp.check_quote_only('owner');
select * from pg_temp.check_quote_only('staff');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select is(pg_temp.count_as('authenticated',
  $$select count(*) from public.invoice_line_items where invoice_id = 'a7000000-0000-0000-0000-000000000001'$$),
  1::bigint, 'owner can still read a paid invoice''s lines');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'cpa');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', pg_temp.line('insert_quote')),
                 '42501', null, 'cpa cannot add a line, even to a quote');
select is(pg_temp.write_as('authenticated', pg_temp.line('update_quote')), 0, 'cpa cannot edit a line');
select is(pg_temp.write_as('authenticated', pg_temp.line('delete_quote')), 0, 'cpa cannot delete a line');

select pg_temp.set_claims('22222222-2222-2222-2222-222222222222', 'owner');
select is(pg_temp.write_as('authenticated', pg_temp.line('update_quote')), 0,
          'owner of B cannot edit tenant A''s quote lines');
select is(pg_temp.write_as('authenticated', pg_temp.line('delete_quote')), 0,
          'owner of B cannot delete tenant A''s quote lines');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', pg_temp.line('insert_quote')),
                 '42501', null, 'owner of B cannot add a line to tenant A''s quote');

-- Service role (no app_role): exempt, e.g. webhooks and maintenance.
select set_config('request.jwt.claims', '{"role":"service_role"}', true);
select is(pg_temp.write_as('service_role', pg_temp.line('insert_paid')), 1,
          'service role can add a line to a paid invoice');
select is(pg_temp.write_as('service_role', pg_temp.line('update_paid')), 1,
          'service role can edit a paid invoice''s line');
select is(pg_temp.write_as('service_role', pg_temp.line('delete_paid')), 1,
          'service role can delete a paid invoice''s line');

-- ===========================================================================
-- 3. App users create invoices as quotes
-- ===========================================================================
create function pg_temp.check_insert_guard(p_app_role text)
returns setof text
language plpgsql
as $$
begin
  perform pg_temp.set_claims('11111111-1111-1111-1111-111111111111', p_app_role);
  return next throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
      $q$insert into public.invoices (tenant_id, customer_name, order_status) values ('11111111-1111-1111-1111-111111111111', 'X', 'paid')$q$),
    '23514', 'invoices must be created as a quote (got paid); add the line items, then move the status forward',
    p_app_role || ' cannot create an invoice as paid');
  return next throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
      $q$insert into public.invoices (tenant_id, customer_name, order_status) values ('11111111-1111-1111-1111-111111111111', 'X', 'shipped')$q$),
    '23514', null, p_app_role || ' cannot create an invoice as shipped');
  return next is(pg_temp.write_as('authenticated',
      $q$insert into public.invoices (tenant_id, customer_name) values ('11111111-1111-1111-1111-111111111111', 'X')$q$),
    1, p_app_role || ' can create a quote');
end;
$$;

select * from pg_temp.check_insert_guard('owner');
select * from pg_temp.check_insert_guard('staff');

select set_config('request.jwt.claims', '{"role":"service_role"}', true);
select is(pg_temp.write_as('service_role',
  $q$insert into public.invoices (tenant_id, customer_name, order_status, source) values ('11111111-1111-1111-1111-111111111111', 'Checkout', 'paid', 'storefront')$q$),
  1, 'service role can still create an invoice as paid (checkout path)');
select pg_temp.no_claims();
insert into public.invoices (id, tenant_id, customer_name, order_status)
values ('d0000000-0000-0000-0000-0000000000aa', '11111111-1111-1111-1111-111111111111', 'Direct', 'paid');
select ok((select quote_number is not null and invoice_number is not null and paid_at is not null
             from public.invoices where id = 'd0000000-0000-0000-0000-0000000000aa'),
          'an invoice created as paid by the service role still gets both numbers');

-- ===========================================================================
-- 4. Stock decrements once, when an invoice leaves 'quote'
-- ===========================================================================
select pg_temp.no_claims();
insert into public.products (id, tenant_id, sku, name, metal, price_cents, stock_qty, low_stock_threshold) values
  ('e0000000-0000-0000-0000-00000000000a', '11111111-1111-1111-1111-111111111111', 'T-X', 'Test X', 'other', 1000, 5, 3),
  ('e0000000-0000-0000-0000-00000000000b', '11111111-1111-1111-1111-111111111111', 'T-Y', 'Test Y', 'other', 1000, 2, 1);

-- Staff build a quote: X twice (2 + 1), Y x2, and a line with no product.
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select pg_temp.exec_as('authenticated', $q$
  insert into public.invoices (id, tenant_id, customer_name)
  values ('d0000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Stock 1')$q$);
select pg_temp.exec_as('authenticated', $q$
  insert into public.invoice_line_items (invoice_id, tenant_id, product_id, description, qty, unit_price_cents, line_total_cents) values
    ('d0000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-00000000000a', 'X', 2, 1000, 2000),
    ('d0000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-00000000000a', 'X', 1, 1000, 1000),
    ('d0000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-00000000000b', 'Y', 2, 1000, 2000),
    ('d0000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', null, 'Custom engraving', 1, 500, 500)$q$);

select is(pg_temp.stock('T-X'), 5, 'adding lines to a quote does not touch stock');
select is(pg_temp.exec_as('authenticated',
  $q$update public.invoices set order_status = 'paid' where id = 'd0000000-0000-0000-0000-000000000001'$q$),
  1, 'staff can move the quote to paid (the decrement runs as definer)');
select is(pg_temp.stock('T-X'), 2, 'X decremented by the sum of its lines (5 - 3)');
select is(pg_temp.stock('T-Y'), 0, 'Y decremented (2 - 2)');
select is((select count(*)::int from public.automation_events
            where event_type = 'stock_low' and payload ->> 'product_id' = 'e0000000-0000-0000-0000-00000000000a'),
          1, 'the decrement crossing the threshold writes a stock_low event');

select pg_temp.exec_as('authenticated',
  $q$update public.invoices set order_status = 'shipped' where id = 'd0000000-0000-0000-0000-000000000001'$q$);
select is(pg_temp.stock('T-X'), 2, 'a later move (paid -> shipped) does not decrement again');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select pg_temp.exec_as('authenticated',
  $q$update public.invoices set refunded_at = now() where id = 'd0000000-0000-0000-0000-000000000001'$q$);
select is(pg_temp.stock('T-X'), 2, 'a refund does not restock (v1)');

-- Insufficient stock: the whole transition fails, nothing moves.
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select pg_temp.exec_as('authenticated', $q$
  insert into public.invoices (id, tenant_id, customer_name)
  values ('d0000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'Stock 2')$q$);
select pg_temp.exec_as('authenticated', $q$
  insert into public.invoice_line_items (invoice_id, tenant_id, product_id, description, qty, unit_price_cents, line_total_cents) values
    ('d0000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-00000000000a', 'X', 1, 1000, 1000),
    ('d0000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-00000000000b', 'Y', 1, 1000, 1000)$q$);
select throws_ok(format('select pg_temp.exec_as(%L, %L)', 'authenticated',
  $q$update public.invoices set order_status = 'paid' where id = 'd0000000-0000-0000-0000-000000000002'$q$),
  'P0001', 'Not enough stock for SKU T-Y: 0 available, 1 needed.',
  'insufficient stock names the SKU and the available quantity');
select is((select order_status::text from public.invoices where id = 'd0000000-0000-0000-0000-000000000002'),
          'quote', 'the invoice stays a quote');
select is(pg_temp.stock('T-X'), 2, 'no partial decrement of the other line''s product');

-- Same product on two lines: quantities are summed before the check.
select pg_temp.exec_as('authenticated', $q$
  insert into public.invoices (id, tenant_id, customer_name)
  values ('d0000000-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111', 'Stock 3')$q$);
select pg_temp.exec_as('authenticated', $q$
  insert into public.invoice_line_items (invoice_id, tenant_id, product_id, description, qty, unit_price_cents, line_total_cents) values
    ('d0000000-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-00000000000a', 'X', 2, 1000, 2000),
    ('d0000000-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-00000000000a', 'X', 2, 1000, 2000)$q$);
select throws_ok(format('select pg_temp.exec_as(%L, %L)', 'authenticated',
  $q$update public.invoices set order_status = 'paid' where id = 'd0000000-0000-0000-0000-000000000003'$q$),
  'P0001', 'Not enough stock for SKU T-X: 2 available, 4 needed.',
  'two lines of 2 against stock 2 are refused (quantities summed)');
select is(pg_temp.stock('T-X'), 2, 'stock unchanged after the refused sale');

-- Skipping from quote straight to shipped also decrements (it leaves 'quote').
select pg_temp.exec_as('authenticated', $q$
  insert into public.invoices (id, tenant_id, customer_name)
  values ('d0000000-0000-0000-0000-000000000004', '11111111-1111-1111-1111-111111111111', 'Stock 4')$q$);
select pg_temp.exec_as('authenticated', $q$
  insert into public.invoice_line_items (invoice_id, tenant_id, product_id, description, qty, unit_price_cents, line_total_cents) values
    ('d0000000-0000-0000-0000-000000000004', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-00000000000a', 'X', 1, 1000, 1000)$q$);
select pg_temp.exec_as('authenticated',
  $q$update public.invoices set order_status = 'shipped' where id = 'd0000000-0000-0000-0000-000000000004'$q$);
select is(pg_temp.stock('T-X'), 1, 'quote -> shipped (a forward skip) decrements once');

-- An invoice with only product-less lines can be paid; nothing to decrement.
select pg_temp.exec_as('authenticated', $q$
  insert into public.invoices (id, tenant_id, customer_name)
  values ('d0000000-0000-0000-0000-000000000005', '11111111-1111-1111-1111-111111111111', 'Stock 5')$q$);
select pg_temp.exec_as('authenticated', $q$
  insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents) values
    ('d0000000-0000-0000-0000-000000000005', '11111111-1111-1111-1111-111111111111', 'Repair', 1, 2500, 2500)$q$);
select is(pg_temp.exec_as('authenticated',
  $q$update public.invoices set order_status = 'paid' where id = 'd0000000-0000-0000-0000-000000000005'$q$),
  1, 'an invoice whose lines have no product can be paid');

-- ===========================================================================
-- 5. Policy AND trigger: a mistakenly wide delete policy is still stopped by the guard
-- ===========================================================================
select pg_temp.no_claims();
create policy zz_test_too_wide_delete on public.invoice_line_items
  for delete to authenticated using (true);
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', pg_temp.line('delete_paid')),
                 '42501', 'line items can only change while the invoice is a quote',
                 'with a too-wide policy, the guard trigger still blocks deleting a paid line');
select pg_temp.no_claims();
drop policy zz_test_too_wide_delete on public.invoice_line_items;

select * from finish();
rollback;
