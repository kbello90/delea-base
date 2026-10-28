begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Slice D2 (migration 20261029000300): what a customer email may say about an order, the
-- carrier and tracking number, and the customer email settings. Tenant A (seed) takes website
-- orders; product a1..01 (Custom Initial Necklace) is $98.

-- ---------------------------------------------------------------------------
-- Session-local helpers.
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

-- Runs a query returning one jsonb value as p_role.
create function pg_temp.json_as(p_role text, p_query text)
returns jsonb
language plpgsql
as $$
declare
  v jsonb;
begin
  perform set_config('role', p_role, true);
  begin
    execute p_query into v;
  exception when others then
    perform set_config('role', 'none', true);
    raise;
  end;
  perform set_config('role', 'none', true);
  return v;
end;
$$;

create function pg_temp.hash(p text)
returns text
language sql
as $$ select encode(extensions.digest(p, 'sha256'), 'hex') $$;

create function pg_temp.context(p_invoice uuid, p_tenant uuid default '11111111-1111-1111-1111-111111111111')
returns jsonb
language sql
as $$
  select pg_temp.json_as('email_worker',
    format('select public.email_customer_context(%L, %L)', p_tenant, p_invoice));
$$;

-- A website order for tenant A, shipped to a street address, with a phone number and a note.
create temp table placed as
  select pg_temp.json_as('storefront_writer', format(
    'select public.storefront_place_order(%L, %L, %L, %L::jsonb, %L, %L)',
    '11111111-1111-1111-1111-111111111111', 'c2000000-0000-0000-0000-000000000001',
    pg_temp.hash('order'), o, pg_temp.hash('ip-d2'), pg_temp.hash('shopper@example.com'))) as result
  from (select jsonb_build_object(
    'items', jsonb_build_array(jsonb_build_object('product_id', 'a1000000-0000-0000-0000-000000000001', 'qty', 1)),
    'fulfillment', 'ship',
    'payment_choice', 'bank_transfer',
    'expected_total_cents', 11184,
    'contact', jsonb_build_object('first_name', 'Sarah', 'last_name', 'Jenkins', 'email', 'Shopper@Example.com',
                                  'phone', '555-0142'),
    'ship_to', jsonb_build_object('line1', '12 Harbor Lane', 'line2', 'Apt 3', 'city', 'Portland', 'state', 'ME',
                                  'postal_code', '04101'),
    'note', 'Gift wrap, please') o) doc;
create temp table d2_order as
  select i.* from public.invoices i, placed
   where i.tenant_id = '11111111-1111-1111-1111-111111111111'
     and i.public_token = (placed.result ->> 'public_token')::uuid;
select is((select result ->> 'status' from placed), 'created', 'a website order to write emails about');

-- ---------------------------------------------------------------------------
-- 1. Who may read it
-- ---------------------------------------------------------------------------
select ok(not has_function_privilege('authenticated', 'public.email_customer_context(uuid, uuid)', 'execute'),
          'authenticated cannot read a customer email context');
select ok(not has_function_privilege('anon', 'public.email_customer_context(uuid, uuid)', 'execute'), 'nor anon');
select ok(not has_function_privilege('service_role', 'public.email_customer_context(uuid, uuid)', 'execute'),
          'nor the service role');
select ok(not has_function_privilege('storefront_writer', 'public.email_customer_context(uuid, uuid)', 'execute'),
          'nor the checkout');
select ok(has_function_privilege('email_worker', 'public.email_customer_context(uuid, uuid)', 'execute'),
          'email_worker can');

-- ---------------------------------------------------------------------------
-- 2. What it says, and what it never says
-- ---------------------------------------------------------------------------
create temp table ctx as select pg_temp.context((select id from d2_order)) as c;
select is((select c ->> 'to' from ctx), 'shopper@example.com', 'to the shopper''s email, lowercased');
select is((select c ->> 'order_ref' from ctx), (select order_ref from d2_order), 'the W- reference');
select is((select c ->> 'public_token' from ctx), (select public_token::text from d2_order), 'the tracking token');
select is((select (c ->> 'total_cents')::int from ctx), 11184, 'the total');
select is((select (c ->> 'balance_cents')::int from ctx), 11184, 'nothing paid yet');
select is((select c -> 'lines' -> 0 ->> 'description' from ctx), 'Custom Initial Necklace', 'the items');
select is((select c ->> 'storefront_domain' from ctx),
          (select min(domain) from public.tenant_domains
            where tenant_id = '11111111-1111-1111-1111-111111111111' and kind = 'storefront'),
          'the shop''s storefront address');
select is((select c -> 'switches' ->> 'shipped' from ctx), 'true', 'customer emails are on by default');
select ok((select c::text from ctx) not like '%Harbor%', 'never the street address');
select ok((select c::text from ctx) not like '%Apt 3%', 'nor its second line');
select ok((select c::text from ctx) not like '%555-0142%', 'nor the phone number');
select ok((select c::text from ctx) not like '%Portland%' and (select c::text from ctx) not like '%04101%',
          'nor the city or ZIP code');
select ok((select c::text from ctx) not like '%Gift wrap%', 'nor the shopper''s note');
select ok(not ((select c from ctx) ?| array['phone', 'ship_line1', 'ship_line2', 'ship_city', 'ship_postal_code', 'note']),
          'and has no field for any of them');

insert into public.invoices (id, tenant_id, customer_name, subtotal_cents, tax_cents, total_cents)
values ('c2000000-0000-0000-0000-0000000000a1', '11111111-1111-1111-1111-111111111111', 'Counter sale', 1000, 0, 1000);
select is(pg_temp.context('c2000000-0000-0000-0000-0000000000a1'), null, 'an admin invoice has no customer emails');
select is(pg_temp.context((select id from d2_order), '22222222-2222-2222-2222-222222222222'), null,
          'another shop can''t read this order');

-- ---------------------------------------------------------------------------
-- 3. Carrier and tracking number
-- ---------------------------------------------------------------------------
select throws_ok(format($q$update public.invoices set carrier = 'USPS', tracking_number = '9400 1000 0000'
                            where id = %L$q$, (select id from d2_order)),
                 '23514', null, 'an unshipped order has no tracking number');
insert into public.payments (tenant_id, invoice_id, kind, method, amount_cents, currency)
select tenant_id, id, 'payment', 'bank_transfer', total_cents, 'USD' from d2_order;
update public.invoices set order_status = 'paid' where id = (select id from d2_order);
select throws_ok(format($q$update public.invoices set order_status = 'shipped', carrier = 'USPS',
                            tracking_number = '<script>' where id = %L$q$, (select id from d2_order)),
                 '23514', null, 'a tracking number is letters, digits, spaces and hyphens only');
select throws_ok(format($q$update public.invoices set order_status = 'shipped', carrier = repeat('x', 41)
                            where id = %L$q$, (select id from d2_order)),
                 '23514', null, 'a carrier is at most 40 characters');
select lives_ok(format($q$update public.invoices set order_status = 'shipped', carrier = 'USPS',
                           tracking_number = '9400 1000-0000' where id = %L$q$, (select id from d2_order)),
                'shipped with a carrier and tracking number');
select is((select pg_temp.context((select id from d2_order)) ->> 'tracking_number'), '9400 1000-0000',
          'the Shipped email can show it');

select set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true);
select set_config('app.order_token', (select public_token::text from d2_order), true);
select is(pg_temp.json_as('storefront_reader', 'select jsonb_build_object(''carrier'', carrier, ''tracking_number'', tracking_number) from storefront.orders'),
          '{"carrier": "USPS", "tracking_number": "9400 1000-0000"}'::jsonb, 'and so can the tracking page');

-- ---------------------------------------------------------------------------
-- 4. Customer email settings
-- ---------------------------------------------------------------------------
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select lives_ok($q$select pg_temp.json_as('authenticated', $$
  with s as (insert into public.email_settings (tenant_id, customer_shipped, intro_shipped)
             values ('11111111-1111-1111-1111-111111111111', false, 'Thank you for shopping small!')
             on conflict (tenant_id) do update set customer_shipped = excluded.customer_shipped,
                                                   intro_shipped = excluded.intro_shipped
             returning 1) select to_jsonb(count(*)) from s$$)$q$,
                'the owner switches a customer email off and writes its intro');
select set_config('request.jwt.claims', '', true);
select is((select pg_temp.context((select id from d2_order)) -> 'switches' ->> 'shipped'), 'false', 'the switch is read');
select is((select pg_temp.context((select id from d2_order)) -> 'intros' ->> 'shipped'), 'Thank you for shopping small!',
          'and the intro');
select throws_ok($q$update public.email_settings set intro_order_received = repeat('x', 301)
                     where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
                 '23514', null, 'an intro is at most 300 characters');
select throws_ok($q$update public.email_settings set intro_order_received = ''
                     where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
                 '23514', null, 'an empty intro is stored as none');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.json_as('authenticated', $$select to_jsonb(count(*)) from public.email_settings$$), '0'::jsonb,
          'staff can''t see the email settings');
select set_config('request.jwt.claims', '', true);

select * from finish();
rollback;
