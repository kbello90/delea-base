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

-- Runs a single-value text query as p_role.
create function pg_temp.text_as(p_role text, p_query text)
returns text
language plpgsql
as $$
declare
  v text;
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

-- Runs a write as p_role, returns the affected row count, and rolls the write back.
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

create function pg_temp.as_tenant(p_tenant uuid)
returns void
language sql
as $$ select set_config('app.tenant_id', coalesce(p_tenant::text, ''), true); $$;

-- Storefront A1 (migration 20261027000100): storefront_reader reads the storefront views
-- only, the views show one active tenant's published catalog with an exact list of columns,
-- the new checks hold, storefront_settings follows the owner/staff matrix, and made-to-order
-- products skip the stock check and decrement.

-- ---------------------------------------------------------------------------
-- 1. Grants
-- ---------------------------------------------------------------------------
select is(
  (select array_agg(c.relname::text order by c.relname)
     from pg_class c
    where c.relnamespace = 'public'::regnamespace
      and c.relkind in ('r', 'p', 'v', 'm')
      and has_table_privilege('storefront_reader', c.oid,
            'select, insert, update, delete, truncate, references, trigger')),
  null::text[],
  'storefront_reader holds no privilege on any public table'
);

select is(
  (select array_agg(p.oid::regprocedure::text order by 1)
     from pg_proc p
    where p.pronamespace = 'public'::regnamespace
      and has_function_privilege('storefront_reader', p.oid, 'execute')),
  array['storefront_price_cart(uuid,jsonb,text)'],
  'storefront_reader can execute the cart pricing function and no other public function'
);

select is(
  (select array_agg(c.relname::text order by c.relname)
     from pg_class c
    where c.relnamespace = 'storefront'::regnamespace),
  array['hosts', 'order_lines', 'orders', 'product_images', 'product_personalization', 'products', 'store'],
  'the storefront schema holds exactly the seven views'
);

select is(
  (select array_agg(c.relname::text order by c.relname)
     from pg_class c
    where c.relnamespace = 'storefront'::regnamespace
      and has_table_privilege('storefront_reader', c.oid, 'select')
      and not has_table_privilege('storefront_reader', c.oid,
                'insert, update, delete, truncate, references, trigger')),
  array['hosts', 'order_lines', 'orders', 'product_images', 'product_personalization', 'products', 'store'],
  'storefront_reader may select from each view and do nothing else'
);

select ok(not has_schema_privilege('anon', 'storefront', 'usage'), 'anon has no usage on the storefront schema');
select ok(not has_schema_privilege('authenticated', 'storefront', 'usage'), 'authenticated has no usage on the storefront schema');
select ok(not has_schema_privilege('ai_readonly', 'storefront', 'usage'), 'ai_readonly has no usage on the storefront schema');

select pg_temp.as_tenant('11111111-1111-1111-1111-111111111111');
select throws_ok(format('select pg_temp.count_as(%L, %L)', 'storefront_reader', 'select count(*) from public.products'),
                 '42501', null, 'storefront_reader cannot read public.products');
select throws_ok(format('select pg_temp.count_as(%L, %L)', 'storefront_reader', 'select count(*) from public.company_settings'),
                 '42501', null, 'storefront_reader cannot read public.company_settings');
select throws_ok(format('select pg_temp.count_as(%L, %L)', 'storefront_reader', 'select count(*) from public.customers'),
                 '42501', null, 'storefront_reader cannot read public.customers');
select throws_ok(format('select pg_temp.count_as(%L, %L)', 'storefront_reader', 'select count(*) from ai.inventory_status'),
                 '42501', null, 'storefront_reader cannot read the ai views');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'storefront_reader',
                        $q$update storefront.products set name = name$q$),
                 null::char(5), null::text, 'storefront_reader cannot update through a view');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'storefront_reader',
                        $q$delete from storefront.product_images$q$),
                 null::char(5), null::text, 'storefront_reader cannot delete through a view');
select throws_ok(format('select pg_temp.count_as(%L, %L)', 'anon', 'select count(*) from storefront.products'),
                 '42501', null, 'anon cannot read the storefront views');

-- ---------------------------------------------------------------------------
-- 2. Exact public columns
-- ---------------------------------------------------------------------------
select is(
  (select array_agg(column_name::text order by ordinal_position)
     from information_schema.columns where table_schema = 'storefront' and table_name = 'products'),
  array['id', 'name', 'description', 'metal', 'price_cents', 'sale_price_cents', 'sale_ends_at',
        'availability', 'made_to_order', 'lead_time_note', 'sort_order'],
  'storefront.products exposes exactly the public product columns (no sku, stock or threshold)'
);

select is(
  (select array_agg(column_name::text order by ordinal_position)
     from information_schema.columns where table_schema = 'storefront' and table_name = 'store'),
  array['business_name', 'tagline', 'primary_color', 'accent_color', 'logo_path', 'hero_eyebrow',
        'hero_title', 'hero_subtitle', 'collection_title', 'font_preset', 'corner_style',
        'mark_style', 'hero_cta_label', 'hero_cta_url', 'currency', 'contact_email',
        'return_policy', 'privacy_policy', 'terms', 'custom_orders_info', 'has_made_to_order',
        'ship_enabled', 'ship_flat_cents', 'free_ship_over_cents', 'tax_shipping', 'pickup_enabled',
        'pickup_note', 'bank_transfer_enabled', 'pay_on_pickup_enabled', 'default_tax_rate_bps'],
  'storefront.store exposes the brand, the currency, the pages and the checkout options only (no tax ID, Stripe, counters or payment instructions)'
);

select is(
  (select array_agg(column_name::text order by ordinal_position)
     from information_schema.columns where table_schema = 'storefront' and table_name = 'hosts'),
  array['domain', 'tenant_id'],
  'storefront.hosts exposes the domain and its tenant only'
);

-- ---------------------------------------------------------------------------
-- 3. Tenant scoping, published products, active tenants, storefront switch
-- ---------------------------------------------------------------------------
-- Test fixtures in tenant A: an unpublished product with an image, and products for every
-- availability state. Rolled back with the test.
insert into public.products
  (id, tenant_id, sku, name, metal, price_cents, sale_price_cents, sale_ends_at, stock_qty,
   low_stock_threshold, is_published, made_to_order, lead_time_note, sort_order)
values
  ('5f000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'SF-HIDDEN',
   'Hidden piece', 'other', 1000, null, null, 5, 3, false, false, null, 50),
  ('5f000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'SF-OUT',
   'Sold out piece', 'other', 1000, null, null, 0, 3, true, false, null, 51),
  ('5f000000-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111', 'SF-LOW',
   'Low piece', 'other', 1000, 800, now() + interval '1 day', 2, 3, true, false, null, 52),
  ('5f000000-0000-0000-0000-000000000004', '11111111-1111-1111-1111-111111111111', 'SF-PLENTY',
   'Plenty piece', 'other', 1000, 800, now() - interval '1 day', 10, 3, true, false, null, 53),
  ('5f000000-0000-0000-0000-000000000005', '11111111-1111-1111-1111-111111111111', 'SF-MTO',
   'Made piece', 'other', 1000, null, null, 0, 3, true, true, 'Ships in 2-3 weeks', 54);

insert into public.product_images
  (id, tenant_id, product_id, position, is_primary, large_path, thumb_path, width, height)
values
  ('5f100000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
   '5f000000-0000-0000-0000-000000000001', 0, true,
   '11111111-1111-1111-1111-111111111111/5f000000-0000-0000-0000-000000000001/5f100000-0000-0000-0000-000000000001-1200.webp',
   '11111111-1111-1111-1111-111111111111/5f000000-0000-0000-0000-000000000001/5f100000-0000-0000-0000-000000000001-400.webp',
   1200, 1200),
  ('5f100000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111',
   '5f000000-0000-0000-0000-000000000003', 0, true,
   '11111111-1111-1111-1111-111111111111/5f000000-0000-0000-0000-000000000003/5f100000-0000-0000-0000-000000000002-1200.webp',
   '11111111-1111-1111-1111-111111111111/5f000000-0000-0000-0000-000000000003/5f100000-0000-0000-0000-000000000002-400.webp',
   1200, 1200);

select pg_temp.as_tenant('11111111-1111-1111-1111-111111111111');
select is(
  pg_temp.count_as('storefront_reader', 'select count(*) from storefront.products'),
  (select count(*) from public.products
    where tenant_id = '11111111-1111-1111-1111-111111111111' and is_published),
  'tenant A sees exactly its published products'
);
select is(
  pg_temp.count_as('storefront_reader',
    $q$select count(*) from storefront.products where id = '5f000000-0000-0000-0000-000000000001'$q$),
  0::bigint, 'an unpublished product is not in the view');
select is(
  pg_temp.count_as('storefront_reader',
    $q$select count(*) from storefront.product_images where product_id = '5f000000-0000-0000-0000-000000000001'$q$),
  0::bigint, 'images of an unpublished product are not in the view');
select is(
  pg_temp.count_as('storefront_reader',
    $q$select count(*) from storefront.product_images where product_id = '5f000000-0000-0000-0000-000000000003'$q$),
  1::bigint, 'images of a published product are');
select is(
  pg_temp.count_as('storefront_reader',
    $q$select count(*) from storefront.products where id::text like 'b2%'$q$),
  0::bigint, 'tenant A sees none of tenant B''s products');
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.store'), 1::bigint,
          'tenant A has one store row');
select is(pg_temp.text_as('storefront_reader', 'select business_name from storefront.store'), 'DELEA',
          'the store row is tenant A''s');

select pg_temp.as_tenant('22222222-2222-2222-2222-222222222222');
select is(
  pg_temp.count_as('storefront_reader',
    $q$select count(*) from storefront.products where id::text like 'a1%' or id::text like '5f%'$q$),
  0::bigint, 'tenant B sees none of tenant A''s products');
select is(pg_temp.text_as('storefront_reader', 'select business_name from storefront.store'), 'Aurora Gems',
          'tenant B sees its own store row');

select pg_temp.as_tenant(null);
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.products'), 0::bigint,
          'no tenant set: no products');
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.store'), 0::bigint,
          'no tenant set: no store');
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.product_images'), 0::bigint,
          'no tenant set: no images');

select is(
  pg_temp.text_as('storefront_reader', $q$select tenant_id::text from storefront.hosts where domain = 'localhost:5174'$q$),
  '11111111-1111-1111-1111-111111111111', 'localhost:5174 is tenant A''s storefront');
select is(
  pg_temp.text_as('storefront_reader', $q$select tenant_id::text from storefront.hosts where domain = 'aurora.localhost:5174'$q$),
  '22222222-2222-2222-2222-222222222222', 'aurora.localhost:5174 is tenant B''s storefront');
select is(
  pg_temp.count_as('storefront_reader', $q$select count(*) from storefront.hosts where domain = 'localhost:5173'$q$),
  0::bigint, 'an admin domain is not a storefront host');

-- An inactive tenant disappears everywhere.
update public.tenants set is_active = false where id = '22222222-2222-2222-2222-222222222222';
select pg_temp.as_tenant('22222222-2222-2222-2222-222222222222');
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.store'), 0::bigint,
          'an inactive tenant has no store row');
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.products'), 0::bigint,
          'an inactive tenant has no public products');
select is(
  pg_temp.count_as('storefront_reader', $q$select count(*) from storefront.hosts where tenant_id = '22222222-2222-2222-2222-222222222222'$q$),
  0::bigint, 'an inactive tenant has no storefront host');
update public.tenants set is_active = true where id = '22222222-2222-2222-2222-222222222222';

-- So does a tenant whose storefront is switched off.
update public.storefront_settings set storefront_enabled = false
 where tenant_id = '22222222-2222-2222-2222-222222222222';
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.store'), 0::bigint,
          'storefront off: no store row');
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.products'), 0::bigint,
          'storefront off: no public products');
select is(
  pg_temp.count_as('storefront_reader', $q$select count(*) from storefront.hosts where tenant_id = '22222222-2222-2222-2222-222222222222'$q$),
  0::bigint, 'storefront off: no storefront host');
update public.storefront_settings set storefront_enabled = true
 where tenant_id = '22222222-2222-2222-2222-222222222222';

-- ---------------------------------------------------------------------------
-- 4. Availability, sale price, made-to-order fields
-- ---------------------------------------------------------------------------
select pg_temp.as_tenant('11111111-1111-1111-1111-111111111111');
select is(pg_temp.text_as('storefront_reader',
  $q$select availability from storefront.products where id = '5f000000-0000-0000-0000-000000000002'$q$),
  'out_of_stock', 'stock 0: out_of_stock');
select is(pg_temp.text_as('storefront_reader',
  $q$select availability from storefront.products where id = '5f000000-0000-0000-0000-000000000003'$q$),
  'low', 'stock below the threshold: low');
select is(pg_temp.text_as('storefront_reader',
  $q$select availability from storefront.products where id = '5f000000-0000-0000-0000-000000000004'$q$),
  'in_stock', 'stock at or above the threshold: in_stock');
select is(pg_temp.text_as('storefront_reader',
  $q$select availability from storefront.products where id = '5f000000-0000-0000-0000-000000000005'$q$),
  'made_to_order', 'made to order (even with stock 0): made_to_order');
select is(pg_temp.text_as('storefront_reader',
  $q$select lead_time_note from storefront.products where id = '5f000000-0000-0000-0000-000000000005'$q$),
  'Ships in 2-3 weeks', 'the lead-time note is public');
select is(pg_temp.text_as('storefront_reader',
  $q$select sale_price_cents::text from storefront.products where id = '5f000000-0000-0000-0000-000000000003'$q$),
  '800', 'an active sale price is shown');
select is(pg_temp.text_as('storefront_reader',
  $q$select coalesce(sale_price_cents::text, 'none') || '/' || coalesce(sale_ends_at::text, 'none') from storefront.products where id = '5f000000-0000-0000-0000-000000000004'$q$),
  'none/none', 'an expired sale price and its end date are hidden');
select is(pg_temp.text_as('storefront_reader', 'select has_made_to_order::text from storefront.store'),
  'true', 'the store says it has made-to-order products');

-- ---------------------------------------------------------------------------
-- 5. Checks
-- ---------------------------------------------------------------------------
select throws_ok(
  $q$update public.products set lead_time_note = repeat('x', 61) where id = '5f000000-0000-0000-0000-000000000005'$q$,
  '23514', null, 'a lead-time note over 60 characters is refused');
select throws_ok(
  $q$update public.products set lead_time_note = '' where id = '5f000000-0000-0000-0000-000000000005'$q$,
  '23514', null, 'an empty lead-time note is refused (null means none)');

create temp table cta_urls (url text primary key, ok boolean not null);
insert into cta_urls values
  ('https://wa.me/15550100142', true),
  ('https://example.com', true),
  ('https://shop.example.com:8443/custom?ref=hero#top', true),
  ('tel:+15550100142', true),
  ('tel:555-010-0142', true),
  ('http://example.com', false),
  ('javascript:alert(1)', false),
  ('JavaScript:alert(1)', false),
  ('data:text/html,hi', false),
  ('https://wa.me@evil.example', false),
  ('https://user:pass@example.com', false),
  ('https://', false),
  ('https://localhost', false),
  ('https://exa mple.com', false),
  ('https://example.com/"onmouseover=x', false),
  ('tel:', false),
  ('tel:call-me', false),
  ('mailto:a@example.com', false),
  ('//example.com', false),
  ('', false);

select lives_ok(
  format($q$update public.brand_config set hero_cta_url = %L where tenant_id = '11111111-1111-1111-1111-111111111111'$q$, url),
  'hero button link accepted: ' || url)
from cta_urls where ok order by url;

select throws_ok(
  format($q$update public.brand_config set hero_cta_url = %L where tenant_id = '11111111-1111-1111-1111-111111111111'$q$, url),
  '23514', null, 'hero button link refused: ' || url)
from cta_urls where not ok order by url;

select throws_ok(
  $q$update public.brand_config set hero_cta_url = 'https://example.com/' || repeat('a', 500) where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
  '23514', null, 'a hero button link over 500 characters is refused');
select throws_ok(
  $q$update public.brand_config set font_preset = 'comic' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
  '23514', null, 'an unknown font preset is refused');
select throws_ok(
  $q$update public.brand_config set corner_style = 'round' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
  '23514', null, 'an unknown corner style is refused');
select throws_ok(
  $q$update public.brand_config set mark_style = 'emoji' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
  '23514', null, 'an unknown mark style is refused');
select throws_ok(
  $q$update public.brand_config set hero_cta_label = repeat('x', 41) where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
  '23514', null, 'a hero button label over 40 characters is refused');
select throws_ok(
  $q$update public.storefront_settings set contact_email = 'not an email' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
  '23514', null, 'a malformed contact email is refused');
select throws_ok(
  $q$update public.storefront_settings set terms = repeat('x', 20001) where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
  '23514', null, 'a page over 20000 characters is refused');

-- ---------------------------------------------------------------------------
-- 6. storefront_settings: owner full, staff read, cpa and other tenants nothing
-- ---------------------------------------------------------------------------
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select is(pg_temp.count_as('authenticated', 'select count(*) from public.storefront_settings'), 1::bigint,
          'owner reads its own storefront settings only');
select is(pg_temp.write_as('authenticated',
  $q$update public.storefront_settings set terms = 'T' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
  1, 'owner can update its storefront settings');
select is(pg_temp.write_as('authenticated',
  $q$update public.storefront_settings set terms = 'T' where tenant_id = '22222222-2222-2222-2222-222222222222'$q$),
  0, 'owner cannot update another tenant''s storefront settings');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
  $q$insert into public.storefront_settings (tenant_id) values ('22222222-2222-2222-2222-222222222222')$q$),
  '42501', null, 'owner cannot insert storefront settings for another tenant');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.count_as('authenticated', 'select count(*) from public.storefront_settings'), 1::bigint,
          'staff reads its tenant''s storefront settings');
select is(pg_temp.write_as('authenticated',
  $q$update public.storefront_settings set terms = 'T' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
  0, 'staff cannot update storefront settings');
select is(pg_temp.write_as('authenticated',
  $q$delete from public.storefront_settings where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
  0, 'staff cannot delete storefront settings');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'cpa');
select is(pg_temp.count_as('authenticated', 'select count(*) from public.storefront_settings'), 0::bigint,
          'cpa cannot read storefront settings');

select throws_ok(format('select pg_temp.count_as(%L, %L)', 'anon', 'select count(*) from public.storefront_settings'),
                 '42501', null, 'anon cannot read storefront settings');

-- ---------------------------------------------------------------------------
-- 7. Made-to-order products skip the stock check and the decrement
-- ---------------------------------------------------------------------------
insert into public.invoices (id, tenant_id, customer_name, order_status)
values ('5f200000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'SF test', 'quote'),
       ('5f200000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'SF test', 'quote');
insert into public.invoice_line_items
  (invoice_id, tenant_id, product_id, description, qty, unit_price_cents, line_total_cents)
values
  -- made to order, stock 0, 3 needed
  ('5f200000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
   '5f000000-0000-0000-0000-000000000005', 'Made piece', 3, 1000, 3000),
  -- stocked, 10 on hand, 2 needed
  ('5f200000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
   '5f000000-0000-0000-0000-000000000004', 'Plenty piece', 2, 1000, 2000),
  -- stocked, 0 on hand, 1 needed
  ('5f200000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111',
   '5f000000-0000-0000-0000-000000000002', 'Sold out piece', 1, 1000, 1000);

select lives_ok(
  $q$update public.invoices set order_status = 'paid', payment_method = 'cash' where id = '5f200000-0000-0000-0000-000000000001'$q$,
  'an invoice with a made-to-order line at stock 0 can be paid');
select is((select stock_qty from public.products where id = '5f000000-0000-0000-0000-000000000005'), 0,
          'the made-to-order product''s stock is not decremented');
select is((select stock_qty from public.products where id = '5f000000-0000-0000-0000-000000000004'), 8,
          'a stocked product on the same invoice is decremented');
select throws_ok(
  $q$update public.invoices set order_status = 'paid', payment_method = 'cash' where id = '5f200000-0000-0000-0000-000000000002'$q$,
  'P0001', 'Not enough stock for SKU SF-OUT: 0 available, 1 needed.',
  'a stocked product is still checked');

select * from finish();
rollback;
