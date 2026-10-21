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

-- Invoice rules (Schema §5): forward-only order_status, numbering, automation events,
-- stock-low events, and the wholesale check constraints.
-- After the seed, tenant A's counters are next_quote_number = 43, next_invoice_number = 90.

create temp table yr as select to_char(now() at time zone 'UTC', 'YYYY') as y;
grant select on yr to public;

-- ---------------------------------------------------------------------------
-- Numbering on insert
-- ---------------------------------------------------------------------------
insert into public.invoices (id, tenant_id, customer_name, quote_number, invoice_number, paid_at)
values ('c0000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
        'Test Quote', 'CLIENT-SET', 'CLIENT-SET', '2000-01-01');

select is((select quote_number from public.invoices where id = 'c0000000-0000-0000-0000-000000000001'),
          'QTE-' || (select y from yr) || '-043',
          'quote_number assigned from next_quote_number; client value ignored');
select is((select invoice_number from public.invoices where id = 'c0000000-0000-0000-0000-000000000001'),
          null::text, 'a quote has no invoice_number, even if the client sent one');
select is((select paid_at from public.invoices where id = 'c0000000-0000-0000-0000-000000000001'),
          null::timestamptz, 'a quote has no paid_at, even if the client sent one');
select is((select next_quote_number from public.company_settings
            where tenant_id = '11111111-1111-1111-1111-111111111111'),
          44, 'next_quote_number incremented');
select is((select count(*)::int from public.automation_events
            where event_type = 'order_placed'
              and payload ->> 'invoice_id' = 'c0000000-0000-0000-0000-000000000001'),
          1, 'order_placed event written on insert');

-- ---------------------------------------------------------------------------
-- quote -> paid stamps paid_at and assigns invoice_number
-- ---------------------------------------------------------------------------
update public.invoices set order_status = 'paid' where id = 'c0000000-0000-0000-0000-000000000001';

select is((select invoice_number from public.invoices where id = 'c0000000-0000-0000-0000-000000000001'),
          'INV-' || (select y from yr) || '-090', 'invoice_number assigned when paid');
select isnt((select paid_at from public.invoices where id = 'c0000000-0000-0000-0000-000000000001'),
            null::timestamptz, 'paid_at stamped when paid');
select is((select next_invoice_number from public.company_settings
            where tenant_id = '11111111-1111-1111-1111-111111111111'),
          91, 'next_invoice_number incremented');
select is((select count(*)::int from public.automation_events
            where event_type = 'order_status_changed'
              and payload ->> 'invoice_id' = 'c0000000-0000-0000-0000-000000000001'
              and payload ->> 'from_status' = 'quote' and payload ->> 'to_status' = 'paid'),
          1, 'order_status_changed event written for quote -> paid');

-- ---------------------------------------------------------------------------
-- Forward only
-- ---------------------------------------------------------------------------
select lives_ok($$update public.invoices set order_status = 'shipped' where id = 'c0000000-0000-0000-0000-000000000001'$$,
                'forward skip paid -> shipped is allowed');
select throws_ok($$update public.invoices set order_status = 'paid' where id = 'c0000000-0000-0000-0000-000000000001'$$,
                 '23514', null, 'backward shipped -> paid is rejected');
select throws_ok($$update public.invoices set order_status = 'quote' where id = 'c0000000-0000-0000-0000-000000000001'$$,
                 '23514', null, 'backward shipped -> quote is rejected');
select lives_ok($$update public.invoices set order_status = 'shipped', notes = 'x' where id = 'c0000000-0000-0000-0000-000000000001'$$,
                'same-status update is allowed');
select lives_ok($$update public.invoices set order_status = 'delivered' where id = 'c0000000-0000-0000-0000-000000000001'$$,
                'shipped -> delivered is allowed');
select throws_ok($$update public.invoices set order_status = 'in_production' where id = 'c0000000-0000-0000-0000-000000000001'$$,
                 '23514', null, 'backward delivered -> in_production is rejected');
select is((select order_status::text from public.invoices where id = 'c0000000-0000-0000-0000-000000000001'),
          'delivered', 'status is delivered after the rejected moves');
select is((select count(*)::int from public.automation_events
            where event_type = 'order_status_changed'
              and payload ->> 'invoice_id' = 'c0000000-0000-0000-0000-000000000001'),
          3, 'one status event per real change (quote->paid, paid->shipped, shipped->delivered)');

-- Forward-only applies to staff too.
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
                        $q$update public.invoices set order_status = 'paid' where id = 'c0000000-0000-0000-0000-000000000001'$q$),
                 '23514', null, 'staff cannot move an invoice backward');
reset role;
-- Back to the migration/service role: no app_role claim, so the app-user guards don't apply.
select set_config('request.jwt.claims', '', true);

-- ---------------------------------------------------------------------------
-- Numbers and paid_at are immutable
-- ---------------------------------------------------------------------------
update public.invoices
   set quote_number = 'HACK', invoice_number = 'HACK', paid_at = '2000-01-01'
 where id = 'c0000000-0000-0000-0000-000000000001';

select is((select quote_number from public.invoices where id = 'c0000000-0000-0000-0000-000000000001'),
          'QTE-' || (select y from yr) || '-043', 'quote_number cannot be changed');
select is((select invoice_number from public.invoices where id = 'c0000000-0000-0000-0000-000000000001'),
          'INV-' || (select y from yr) || '-090', 'invoice_number cannot be changed');
select ok((select paid_at > '2001-01-01' from public.invoices where id = 'c0000000-0000-0000-0000-000000000001'),
          'paid_at cannot be changed');

-- ---------------------------------------------------------------------------
-- Insert directly as paid, and quote -> shipped skip, both get an invoice number
-- ---------------------------------------------------------------------------
insert into public.invoices (id, tenant_id, customer_name, order_status, source)
values ('c0000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111',
        'Storefront Shopper', 'paid', 'storefront');

select is((select quote_number || ' ' || invoice_number from public.invoices
            where id = 'c0000000-0000-0000-0000-000000000002'),
          'QTE-' || (select y from yr) || '-044 INV-' || (select y from yr) || '-091',
          'insert directly as paid gets both numbers');
select isnt((select paid_at from public.invoices where id = 'c0000000-0000-0000-0000-000000000002'),
            null::timestamptz, 'insert directly as paid stamps paid_at');

insert into public.invoices (id, tenant_id, customer_name)
values ('c0000000-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111', 'Skip Ahead');
update public.invoices set order_status = 'shipped' where id = 'c0000000-0000-0000-0000-000000000003';

select is((select invoice_number from public.invoices where id = 'c0000000-0000-0000-0000-000000000003'),
          'INV-' || (select y from yr) || '-092', 'quote -> shipped skip assigns the invoice number');

-- ---------------------------------------------------------------------------
-- Padding, per-tenant counters, and missing settings
-- ---------------------------------------------------------------------------
update public.company_settings set next_quote_number = 1000
 where tenant_id = '11111111-1111-1111-1111-111111111111';
insert into public.invoices (id, tenant_id, customer_name)
values ('c0000000-0000-0000-0000-000000000004', '11111111-1111-1111-1111-111111111111', 'Big Number');
select is((select quote_number from public.invoices where id = 'c0000000-0000-0000-0000-000000000004'),
          'QTE-' || (select y from yr) || '-1000', 'numbers past 999 are not truncated');

insert into public.invoices (id, tenant_id, customer_name)
values ('c0000000-0000-0000-0000-000000000005', '22222222-2222-2222-2222-222222222222', 'Tenant B');
select is((select quote_number from public.invoices where id = 'c0000000-0000-0000-0000-000000000005'),
          'QTE-' || (select y from yr) || '-002', 'each tenant has its own counter');

insert into public.tenants (id, name) values ('33333333-3333-3333-3333-333333333333', 'No Settings');
select throws_ok($$insert into public.invoices (tenant_id, customer_name) values ('33333333-3333-3333-3333-333333333333', 'X')$$,
                 '23503', null, 'invoice insert fails when the tenant has no company_settings');

-- ---------------------------------------------------------------------------
-- Wholesale check constraints
-- ---------------------------------------------------------------------------
select throws_ok($$insert into public.customers (tenant_id, customer_type, first_name, company_name)
                   values ('11111111-1111-1111-1111-111111111111', 'retail', 'X', 'Co')$$,
                 '23514', null, 'retail customer cannot have a company_name');
select throws_ok($$insert into public.customers (tenant_id, customer_type, first_name, tax_exempt)
                   values ('11111111-1111-1111-1111-111111111111', 'retail', 'X', true)$$,
                 '23514', null, 'retail customer cannot be tax exempt');
select throws_ok($$insert into public.customers (tenant_id, customer_type, first_name, wholesale_discount_bps)
                   values ('11111111-1111-1111-1111-111111111111', 'retail', 'X', 500)$$,
                 '23514', null, 'retail customer cannot have a wholesale discount');
select throws_ok($$insert into public.customers (tenant_id, customer_type, first_name, payment_terms)
                   values ('11111111-1111-1111-1111-111111111111', 'retail', 'X', 'net_30')$$,
                 '23514', null, 'retail customer cannot have payment terms');
select throws_ok($$insert into public.customers (tenant_id, customer_type, first_name)
                   values ('11111111-1111-1111-1111-111111111111', 'wholesale', 'X')$$,
                 '23514', null, 'wholesale customer requires a company_name');
select throws_ok($$insert into public.customers (tenant_id, customer_type, first_name, company_name, wholesale_discount_bps)
                   values ('11111111-1111-1111-1111-111111111111', 'wholesale', 'X', 'Co', 10001)$$,
                 '23514', null, 'wholesale discount cannot exceed 100%');
select throws_ok($$insert into public.customers (tenant_id, customer_type, first_name, company_name, wholesale_discount_bps)
                   values ('11111111-1111-1111-1111-111111111111', 'wholesale', 'X', 'Co', -1)$$,
                 '23514', null, 'wholesale discount cannot be negative');
select lives_ok($$insert into public.customers (tenant_id, customer_type, first_name, company_name, tax_exempt,
                                                resale_certificate_number, wholesale_discount_bps, payment_terms)
                  values ('11111111-1111-1111-1111-111111111111', 'wholesale', 'X', 'Co', true, 'RC-1', 2000, 'net_60')$$,
                'complete wholesale customer is accepted');
select lives_ok($$insert into public.customers (tenant_id, first_name) values ('11111111-1111-1111-1111-111111111111', 'Plain')$$,
                'plain retail customer is accepted');
select throws_ok($$update public.customers set customer_type = 'retail' where id = 'a5000000-0000-0000-0000-000000000003'$$,
                 '23514', null, 'a wholesale customer cannot become retail while keeping wholesale fields');

-- ---------------------------------------------------------------------------
-- stock_low / material_low fire only when crossing below the threshold
-- ---------------------------------------------------------------------------
-- Celestial Charm a1…04: stock 12, threshold 3.
update public.products set stock_qty = 2 where id = 'a1000000-0000-0000-0000-000000000004';
select is((select count(*)::int from public.automation_events
            where event_type = 'stock_low' and payload ->> 'product_id' = 'a1000000-0000-0000-0000-000000000004'),
          1, 'stock_low fires when stock crosses below the threshold');

update public.products set stock_qty = 1 where id = 'a1000000-0000-0000-0000-000000000004';
select is((select count(*)::int from public.automation_events
            where event_type = 'stock_low' and payload ->> 'product_id' = 'a1000000-0000-0000-0000-000000000004'),
          1, 'stock_low does not fire again while already below');

update public.products set stock_qty = 5 where id = 'a1000000-0000-0000-0000-000000000004';
update public.products set stock_qty = 0 where id = 'a1000000-0000-0000-0000-000000000004';
select is((select count(*)::int from public.automation_events
            where event_type = 'stock_low' and payload ->> 'product_id' = 'a1000000-0000-0000-0000-000000000004'),
          2, 'stock_low fires again after a restock and a new crossing');

update public.products set stock_qty = 3 where id = 'a1000000-0000-0000-0000-000000000001';
select is((select count(*)::int from public.automation_events
            where event_type = 'stock_low' and payload ->> 'product_id' = 'a1000000-0000-0000-0000-000000000001'),
          0, 'stock_low does not fire at exactly the threshold');

update public.products set name = 'Engagement Ring (renamed)' where id = 'a1000000-0000-0000-0000-000000000003';
select is((select count(*)::int from public.automation_events
            where event_type = 'stock_low' and payload ->> 'product_id' = 'a1000000-0000-0000-0000-000000000003'),
          0, 'non-stock updates on a low product do not fire stock_low');

-- 925 Silver blanks a3…01: 18 on hand, threshold 10.
update public.materials set qty_on_hand = 5 where id = 'a3000000-0000-0000-0000-000000000001';
select is((select count(*)::int from public.automation_events
            where event_type = 'material_low' and payload ->> 'material_id' = 'a3000000-0000-0000-0000-000000000001'),
          1, 'material_low fires when qty crosses below the reorder threshold');

-- Packaging boxes a3…03: 212 on hand, threshold 50.
update public.materials set qty_on_hand = 100 where id = 'a3000000-0000-0000-0000-000000000003';
select is((select count(*)::int from public.automation_events
            where event_type = 'material_low' and payload ->> 'material_id' = 'a3000000-0000-0000-0000-000000000003'),
          0, 'material_low does not fire while above the threshold');

-- Inserts never fire low-stock events (the seeded Engagement Ring starts below threshold).
select is((select count(*)::int from public.automation_events where event_type = 'stock_low'
            and payload ->> 'product_id' = 'a1000000-0000-0000-0000-000000000003'),
          0, 'inserting a product below threshold does not fire stock_low');

select * from finish();
rollback;
