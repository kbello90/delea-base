begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

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

create function pg_temp.hash(p text)
returns text
language sql
as $$ select encode(extensions.digest(p, 'sha256'), 'hex') $$;

-- Prices a cart for tenant A as storefront_writer.
create function pg_temp.price(p_cart jsonb, p_fulfillment text)
returns jsonb
language sql
as $$
  select pg_temp.json_as('storefront_writer', format(
    'select public.storefront_price_cart(%L, %L::jsonb, %L)',
    '11111111-1111-1111-1111-111111111111', p_cart, p_fulfillment));
$$;

-- Places an order for tenant A as storefront_writer.
create function pg_temp.place(p_key text, p_order jsonb, p_ip text default 'ip-1', p_email text default 'shopper@example.com')
returns jsonb
language sql
as $$
  select pg_temp.json_as('storefront_writer', format(
    'select public.storefront_place_order(%L, %L, %L, %L::jsonb, %L, %L)',
    '11111111-1111-1111-1111-111111111111', p_key, pg_temp.hash(p_order::text), p_order,
    pg_temp.hash(p_ip), pg_temp.hash(lower(p_email))));
$$;

-- An order for tenant A: one necklace (9800), shipped to Portland, paid by bank transfer.
create function pg_temp.order_doc(p_expected bigint default 11184, p_email text default 'shopper@example.com')
returns jsonb
language sql
as $$
  select jsonb_build_object(
    'items', jsonb_build_array(jsonb_build_object('product_id', 'a1000000-0000-0000-0000-000000000001', 'qty', 1)),
    'fulfillment', 'ship',
    'payment_choice', 'bank_transfer',
    'expected_total_cents', p_expected,
    'contact', jsonb_build_object('first_name', 'Sarah', 'last_name', 'Jenkins', 'email', p_email, 'phone', '555-0142'),
    'ship_to', jsonb_build_object('line1', '1 Main St', 'city', 'Portland', 'state', 'ME', 'postal_code', '04101'),
    'note', 'Gift wrap, please');
$$;

-- Storefront A2 orders (migration 20261027000400): who may call the checkout functions,
-- the storefront pricing rule, placing an order, idempotency, limits, and tracking.
-- Tenant A (seed): tax 8%, ships for $6 (free over $150), pickup, bank transfer, pay on
-- pickup. Product a1..01 (Custom Initial Necklace) is $98 with 25 in stock.

-- ---------------------------------------------------------------------------
-- 1. Privileges
-- ---------------------------------------------------------------------------
create temp table checkout_functions (sig text primary key, reader boolean);
insert into checkout_functions values
  ('storefront_price_cart(uuid,jsonb,text)', true),
  ('storefront_find_order(uuid,uuid,text)', false),
  ('storefront_count_attempt(text)', false),
  ('storefront_place_order(uuid,uuid,text,jsonb,text,text)', false);

select ok((select p.prosecdef and p.proconfig @> array['search_path=""'] from pg_proc p
            where p.oid = ('public.' || sig)::regprocedure),
          sig || ' is SECURITY DEFINER with an empty search_path')
  from checkout_functions order by sig;

select ok(not has_function_privilege(r, ('public.' || sig)::regprocedure, 'execute'),
          r || ' cannot execute ' || sig)
  from checkout_functions, unnest(array['anon', 'authenticated', 'service_role', 'ai_readonly']) r
 order by sig, r;

select ok(has_function_privilege('storefront_writer', ('public.' || sig)::regprocedure, 'execute'),
          'storefront_writer can execute ' || sig)
  from checkout_functions order by sig;

select is(has_function_privilege('storefront_reader', ('public.' || sig)::regprocedure, 'execute'), reader,
          'storefront_reader ' || case when reader then 'can' else 'cannot' end || ' execute ' || sig)
  from checkout_functions order by sig;

select is(
  (select array_agg(c.relname::text order by c.relname)
     from pg_class c
    where c.relnamespace = 'public'::regnamespace
      and c.relkind in ('r', 'p', 'v', 'm')
      and has_table_privilege('storefront_writer', c.oid,
            'select, insert, update, delete, truncate, references, trigger')),
  null::text[],
  'storefront_writer holds no privilege on any public table');

-- An anonymous caller, and a signed-in owner of another tenant, are refused outright.
select throws_ok(
  format('select pg_temp.json_as(%L, %L)', 'anon',
         format('select public.storefront_place_order(%L, %L, %L, %L::jsonb, %L, %L)',
                '11111111-1111-1111-1111-111111111111', gen_random_uuid(), pg_temp.hash('f'),
                pg_temp.order_doc(), pg_temp.hash('ip'), pg_temp.hash('e'))),
  '42501', null, 'anon cannot place an order');
select pg_temp.set_claims('22222222-2222-2222-2222-222222222222', 'owner');
select throws_ok(
  format('select pg_temp.json_as(%L, %L)', 'authenticated',
         format('select public.storefront_place_order(%L, %L, %L, %L::jsonb, %L, %L)',
                '11111111-1111-1111-1111-111111111111', gen_random_uuid(), pg_temp.hash('f'),
                pg_temp.order_doc(), pg_temp.hash('ip'), pg_temp.hash('e'))),
  '42501', null, 'another tenant''s signed-in owner cannot place an order in tenant A');
select throws_ok(
  format('select pg_temp.json_as(%L, %L)', 'authenticated',
         format('select public.storefront_price_cart(%L, %L::jsonb, %L)',
                '11111111-1111-1111-1111-111111111111', '[]', null)),
  '42501', null, 'a signed-in user cannot call the pricing function');
select throws_ok(
  format('select pg_temp.json_as(%L, %L)', 'anon',
         format('select to_jsonb(public.storefront_count_attempt(%L))', pg_temp.hash('ip'))),
  '42501', null, 'anon cannot touch the attempt counter');

-- ---------------------------------------------------------------------------
-- 2. Pricing
-- ---------------------------------------------------------------------------
create temp table prices (label text, cart jsonb, fulfillment text, expected jsonb);
insert into prices values
  ('ship, below the free threshold: $6 shipping, tax on the goods only',
   '[{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": 1}]', 'ship',
   '{"subtotal_cents": 9800, "shipping_cents": 600, "tax_rate_bps": 800, "tax_cents": 784, "total_cents": 11184}'),
  ('ship, over the free threshold: no shipping',
   '[{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": 2}]', 'ship',
   '{"subtotal_cents": 19600, "shipping_cents": 0, "tax_cents": 1568, "total_cents": 21168}'),
  ('pickup: no shipping',
   '[{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": 1}]', 'pickup',
   '{"subtotal_cents": 9800, "shipping_cents": 0, "tax_cents": 784, "total_cents": 10584}'),
  ('not chosen yet: shipping null and left out of the total',
   '[{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": 1}]', null,
   '{"subtotal_cents": 9800, "shipping_cents": null, "tax_cents": 784, "total_cents": 10584}');

select is(
  (select jsonb_object_agg(k, pg_temp.price(cart, fulfillment) -> k) from jsonb_object_keys(expected) k),
  expected, 'price: ' || label)
  from prices order by label;

update public.storefront_settings set tax_shipping = true where tenant_id = '11111111-1111-1111-1111-111111111111';
select is(pg_temp.price('[{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": 1}]', 'ship') -> 'tax_cents',
          '832'::jsonb, 'with tax on shipping, the tax base includes shipping (10400 x 8% = 832)');
update public.storefront_settings set tax_shipping = false where tenant_id = '11111111-1111-1111-1111-111111111111';

-- Half-up rounding: 100 cents at 5.5% is 5.5 cents of tax, rounded to 6.
insert into public.products (id, tenant_id, sku, name, metal, price_cents, stock_qty, is_published)
values ('5d000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'SO-PENNY', 'Penny', 'other', 100, 10, true);
update public.company_settings set default_tax_rate_bps = 550 where tenant_id = '11111111-1111-1111-1111-111111111111';
select is(pg_temp.price('[{"product_id": "5d000000-0000-0000-0000-000000000001", "qty": 1}]', 'pickup') -> 'tax_cents',
          '6'::jsonb, 'tax rounds half up (5.5 cents -> 6)');
update public.company_settings set default_tax_rate_bps = 800 where tenant_id = '11111111-1111-1111-1111-111111111111';

-- Sale prices: active is used, expired is not.
insert into public.products (id, tenant_id, sku, name, metal, price_cents, sale_price_cents, sale_ends_at, stock_qty, is_published)
values
  ('5d000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'SO-SALE', 'On sale', 'other', 10000, 7500, now() + interval '1 day', 10, true),
  ('5d000000-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111', 'SO-ENDED', 'Sale ended', 'other', 10000, 7500, now() - interval '1 day', 10, true);
select is(pg_temp.price('[{"product_id": "5d000000-0000-0000-0000-000000000002", "qty": 1}]', 'pickup') #> '{lines,0,unit_price_cents}',
          '7500'::jsonb, 'an active sale price is charged');
select is(pg_temp.price('[{"product_id": "5d000000-0000-0000-0000-000000000003", "qty": 1}]', 'pickup') #> '{lines,0,unit_price_cents}',
          '10000'::jsonb, 'an ended sale is not');

-- Availability problems.
insert into public.products (id, tenant_id, sku, name, metal, price_cents, stock_qty, is_published, made_to_order, lead_time_note)
values
  ('5d000000-0000-0000-0000-000000000004', '11111111-1111-1111-1111-111111111111', 'SO-HIDDEN', 'Hidden', 'other', 1000, 10, false, false, null),
  ('5d000000-0000-0000-0000-000000000005', '11111111-1111-1111-1111-111111111111', 'SO-MADE', 'Made', 'other', 1000, 0, true, true, 'Ships in 3 weeks');
select is(pg_temp.price('[{"product_id": "5d000000-0000-0000-0000-000000000004", "qty": 1}]', 'pickup') #>> '{lines,0,problem}',
          'unavailable', 'an unpublished product is unavailable');
select is(pg_temp.price('[{"product_id": "22222222-0000-0000-0000-000000000000", "qty": 1}]', 'pickup') #>> '{lines,0,problem}',
          'unavailable', 'an unknown product is unavailable');
select is(pg_temp.price('[{"product_id": "b2000000-0000-0000-0000-000000000001", "qty": 1}]', 'pickup') #>> '{lines,0,problem}',
          'unavailable', 'another tenant''s product is unavailable');
select is(pg_temp.price('[{"product_id": "a1000000-0000-0000-0000-000000000003", "qty": 3}]', 'pickup') #>> '{lines,0,problem}',
          'unavailable', 'more than the stock (2) is unavailable');
select is(pg_temp.price('[{"product_id": "a1000000-0000-0000-0000-000000000003", "qty": 2}, {"product_id": "a1000000-0000-0000-0000-000000000003", "qty": 1}]', 'pickup') #>> '{lines,1,problem}',
          'unavailable', 'stock is checked on the quantity summed over lines (2 + 1 > 2)');
select is(pg_temp.price('[{"product_id": "a1000000-0000-0000-0000-000000000003", "qty": 2}]', 'pickup') #>> '{lines,0,problem}',
          null, 'exactly the stock is fine');
select is((pg_temp.price('[{"product_id": "5d000000-0000-0000-0000-000000000005", "qty": 9}]', 'pickup') -> 'lines' -> 0)
            - array['position', 'product_id', 'unit_price_cents', 'line_total_cents', 'qty', 'name'],
          '{"problem": null, "made_to_order": true, "lead_time_note": "Ships in 3 weeks", "personalization": null}'::jsonb,
          'a made-to-order product is never short and carries its lead time');
select ok((pg_temp.price('[{"product_id": "5d000000-0000-0000-0000-000000000004", "qty": 1}]', 'pickup') ->> 'has_problems')::boolean,
          'has_problems is set when a line has a problem');

-- Bad input and closed options.
create temp table bad_carts (label text, cart jsonb, fulfillment text, error text);
insert into bad_carts values
  ('an empty cart', '[]', 'ship', 'invalid'),
  ('qty 0', '[{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": 0}]', 'ship', 'invalid'),
  ('qty 100', '[{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": 100}]', 'ship', 'invalid'),
  ('qty as text', '[{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": "1"}]', 'ship', 'invalid'),
  ('a fractional qty', '[{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": 1.5}]', 'ship', 'invalid'),
  ('a product id that isn''t a uuid', '[{"product_id": "x", "qty": 1}]', 'ship', 'invalid'),
  ('personalization that isn''t an object', '[{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": 1, "personalization": []}]', 'ship', 'invalid'),
  ('an unknown fulfillment', '[{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": 1}]', 'drone', 'invalid'),
  ('not a list', '{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": 1}', 'ship', 'invalid');
select is(pg_temp.price(cart, fulfillment) ->> 'error', error, 'refused: ' || label) from bad_carts order by label;

update public.storefront_settings set ship_enabled = false where tenant_id = '11111111-1111-1111-1111-111111111111';
select is(pg_temp.price('[{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": 1}]', 'ship') ->> 'error',
          'fulfillment', 'shipping when the shop doesn''t ship is refused');
update public.storefront_settings set ship_enabled = true where tenant_id = '11111111-1111-1111-1111-111111111111';

-- ---------------------------------------------------------------------------
-- 3. Personalization answers
-- ---------------------------------------------------------------------------
insert into public.product_personalization_fields (id, tenant_id, product_id, position, label, kind, required, max_length, choices)
values
  ('5d100000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000003', 1, 'Ring size', 'choice', true, null, '{5,6,7,8}'),
  ('5d100000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000003', 2, 'Engraving', 'text', false, 10, null);

create temp table answers (label text, personalization jsonb, problem text, snapshot jsonb);
insert into answers values
  ('both answered, trimmed, in field order',
   '{"5d100000-0000-0000-0000-000000000002": "  K & J ", "5d100000-0000-0000-0000-000000000001": "7"}', null,
   '[{"label": "Ring size", "value": "7"}, {"label": "Engraving", "value": "K & J"}]'),
  ('the optional one left out',
   '{"5d100000-0000-0000-0000-000000000001": "6"}', null, '[{"label": "Ring size", "value": "6"}]'),
  ('the required one missing', '{"5d100000-0000-0000-0000-000000000002": "K"}', 'options', null),
  ('the required one blank', '{"5d100000-0000-0000-0000-000000000001": "   "}', 'options', null),
  ('a choice not in the list', '{"5d100000-0000-0000-0000-000000000001": "9"}', 'options', null),
  ('text over its length', '{"5d100000-0000-0000-0000-000000000001": "7", "5d100000-0000-0000-0000-000000000002": "ABCDEFGHIJK"}', 'options', null),
  ('a control character', '{"5d100000-0000-0000-0000-000000000001": "7", "5d100000-0000-0000-0000-000000000002": "A\nB"}', 'options', null),
  ('a field of another product', '{"5d100000-0000-0000-0000-000000000001": "7", "5d200000-0000-0000-0000-000000000009": "x"}', 'options', null),
  ('a number instead of text', '{"5d100000-0000-0000-0000-000000000001": 7}', 'options', null);

select is(
  pg_temp.price(jsonb_build_array(jsonb_build_object(
    'product_id', 'a1000000-0000-0000-0000-000000000003', 'qty', 1, 'personalization', personalization)), 'pickup')
    #>> '{lines,0,problem}',
  problem, 'personalization problem: ' || label)
  from answers order by label;
select is(
  pg_temp.price(jsonb_build_array(jsonb_build_object(
    'product_id', 'a1000000-0000-0000-0000-000000000003', 'qty', 1, 'personalization', personalization)), 'pickup')
    #> '{lines,0,personalization}',
  snapshot, 'personalization snapshot: ' || label)
  from answers where problem is null order by label;

-- ---------------------------------------------------------------------------
-- 4. Placing an order
-- ---------------------------------------------------------------------------
create temp table placed as
  select pg_temp.place('5d200000-0000-0000-0000-000000000001', pg_temp.order_doc()) as result;

select is((select result ->> 'status' from placed), 'created', 'an order is created');
select matches((select result ->> 'order_ref' from placed), '^W-[0-9A-HJKMNP-TV-Z]{8}$', 'it gets a W- reference');

create temp table created_order as
  select i.* from public.invoices i, placed
   where i.tenant_id = '11111111-1111-1111-1111-111111111111'
     and i.public_token = (placed.result ->> 'public_token')::uuid;

select is(
  (select jsonb_build_object('status', order_status, 'source', source, 'customer_name', customer_name,
                             'subtotal', subtotal_cents, 'shipping', shipping_cents, 'tax_rate', tax_rate_bps,
                             'tax', tax_cents, 'total', total_cents, 'fulfillment', fulfillment,
                             'payment_choice', payment_choice, 'paid_at', paid_at, 'payment_method', payment_method)
     from created_order),
  '{"status": "quote", "source": "storefront", "customer_name": "Sarah Jenkins", "subtotal": 9800, "shipping": 600, "tax_rate": 800, "tax": 784, "total": 11184, "fulfillment": "ship", "payment_choice": "bank_transfer", "paid_at": null, "payment_method": null}'::jsonb,
  'the order is an unpaid storefront quote with the server''s amounts');
select matches((select quote_number from created_order), '^QTE-', 'it gets a quote number');
select is((select count(*)::int from public.invoice_line_items where invoice_id = (select id from created_order)), 1, 'one line');
select is((select description || ' x' || qty || ' @' || unit_price_cents from public.invoice_line_items
            where invoice_id = (select id from created_order)), 'Custom Initial Necklace x1 @9800', 'the line snapshots name and price');
select is((select first_name || ' ' || last_name || ', ' || ship_city || ' ' || ship_state || ', ' || ship_country || ', ' || note
             from public.order_contacts where invoice_id = (select id from created_order)),
          'Sarah Jenkins, Portland ME, US, Gift wrap, please', 'the contact and ship-to are stored');
select is((select customer_type::text || ' ' || email || ' ' || marketing_opt_in from public.customers
            where id = (select customer_id from created_order)),
          'retail shopper@example.com false', 'a new retail customer, no marketing opt-in');
select is((select count(*)::int from public.automation_events
            where event_type = 'order_placed' and payload ->> 'invoice_id' = (select id::text from created_order)
              and payload ->> 'source' = 'storefront'), 1, 'order_placed fires with source storefront');
select is((select stock_qty from public.products where id = 'a1000000-0000-0000-0000-000000000001'), 24,
          'stock is not decremented until the order is paid');

-- Idempotency: the same key and request returns the same order; a different request conflicts.
select is((select pg_temp.place('5d200000-0000-0000-0000-000000000001', pg_temp.order_doc()) - 'status'),
          (select result - 'status' from placed), 'a replay returns the same order');
select is(pg_temp.place('5d200000-0000-0000-0000-000000000001', pg_temp.order_doc()) ->> 'status', 'replayed',
          'a replay says so');
select is(pg_temp.place('5d200000-0000-0000-0000-000000000001', pg_temp.order_doc(p_email => 'other@example.com')) ->> 'status',
          'conflict', 'the same key with a different request is a conflict');
select is((select count(*)::int from public.invoices where checkout_key = '5d200000-0000-0000-0000-000000000001'), 1,
          'still exactly one order for that key');
select is(pg_temp.json_as('storefront_writer', format(
            'select public.storefront_find_order(%L, %L, %L)', '11111111-1111-1111-1111-111111111111',
            '5d200000-0000-0000-0000-000000000001', pg_temp.hash(pg_temp.order_doc()::text))) ->> 'status',
          'replayed', 'the lookup before Turnstile finds it too');
select is(pg_temp.json_as('storefront_writer', format(
            'select public.storefront_find_order(%L, %L, %L)', '22222222-2222-2222-2222-222222222222',
            '5d200000-0000-0000-0000-000000000001', pg_temp.hash(pg_temp.order_doc()::text))) ->> 'status',
          'none', 'another tenant never finds it');

-- Refusals write nothing.
create temp table before_refusals as select count(*) as n from public.invoices;
select is(pg_temp.place('5d200000-0000-0000-0000-000000000002', pg_temp.order_doc(p_expected => 10000)) ->> 'status',
          'price_changed', 'a total that doesn''t match the server''s is refused');
select is(pg_temp.place('5d200000-0000-0000-0000-000000000003',
            pg_temp.order_doc() || '{"items": [{"product_id": "a1000000-0000-0000-0000-000000000003", "qty": 1}]}') ->> 'status',
          'unavailable', 'a required personalization left out is refused');
select is(pg_temp.place('5d200000-0000-0000-0000-000000000004',
            pg_temp.order_doc() || '{"payment_choice": "pay_on_pickup"}') #>> '{reason}',
          'payment_choice', 'paying on pickup with shipping is refused');
select is(pg_temp.place('5d200000-0000-0000-0000-000000000005', pg_temp.order_doc() - 'ship_to') #>> '{reason}',
          'address', 'shipping without an address is refused');
select is(pg_temp.place('5d200000-0000-0000-0000-000000000006',
            pg_temp.order_doc(p_expected => 10584) || '{"fulfillment": "pickup"}') #>> '{reason}',
          'address', 'pickup with an address is refused');
select is(pg_temp.place('5d200000-0000-0000-0000-000000000007', pg_temp.order_doc() - 'fulfillment') #>> '{reason}',
          'fulfillment', 'an order without fulfillment is refused');
update public.storefront_settings set bank_transfer_enabled = false where tenant_id = '11111111-1111-1111-1111-111111111111';
select is(pg_temp.place('5d200000-0000-0000-0000-000000000008', pg_temp.order_doc()) #>> '{reason}',
          'payment_choice', 'bank transfer when the shop doesn''t take it is refused');
update public.storefront_settings set bank_transfer_enabled = true where tenant_id = '11111111-1111-1111-1111-111111111111';
select is((select count(*) from public.invoices), (select n from before_refusals), 'no refused order wrote an invoice');

-- Pickup and pay on pickup, with a personalized ring.
select is(pg_temp.place('5d200000-0000-0000-0000-000000000009', jsonb_build_object(
    'items', jsonb_build_array(jsonb_build_object('product_id', 'a1000000-0000-0000-0000-000000000003', 'qty', 1,
               'personalization', jsonb_build_object('5d100000-0000-0000-0000-000000000001', '7'))),
    'fulfillment', 'pickup', 'payment_choice', 'pay_on_pickup', 'expected_total_cents', 15660,
    'contact', jsonb_build_object('first_name', 'Priya', 'last_name', 'Nair', 'email', 'priya@example.com')),
    p_ip => 'ip-2', p_email => 'priya@example.com') ->> 'status',
  'created', 'a pickup order paid on pickup is created');
select is((select li.personalization from public.invoice_line_items li
             join public.invoices i on i.id = li.invoice_id
            where i.checkout_key = '5d200000-0000-0000-0000-000000000009'),
          '[{"label": "Ring size", "value": "7"}]'::jsonb, 'the answers are stored on the line as a snapshot');

-- Customers: an existing retail customer is reused (never updated); a wholesale one never is.
insert into public.customers (id, tenant_id, customer_type, first_name, last_name, email)
values ('5d300000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'retail', 'Laura', 'Bennett', 'Laura@Example.com');
insert into public.customers (id, tenant_id, customer_type, first_name, company_name, email, wholesale_discount_bps, tax_exempt)
values ('5d300000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'wholesale', 'Buyer', 'Big Shop', 'buyer@example.com', 2500, true);

select is(pg_temp.place('5d200000-0000-0000-0000-000000000010', pg_temp.order_doc(p_email => 'laura@example.com'),
                        p_ip => 'ip-3', p_email => 'laura@example.com') ->> 'status', 'created', 'order by a known email');
select is((select customer_id from public.invoices where checkout_key = '5d200000-0000-0000-0000-000000000010'),
          '5d300000-0000-0000-0000-000000000001'::uuid, 'the existing retail customer is reused (email ignores case)');
select is((select first_name || ' ' || last_name from public.customers where id = '5d300000-0000-0000-0000-000000000001'),
          'Laura Bennett', 'and is not updated with the shopper''s name');

select is(pg_temp.place('5d200000-0000-0000-0000-000000000011', pg_temp.order_doc(p_email => 'buyer@example.com'),
                        p_ip => 'ip-4', p_email => 'buyer@example.com') ->> 'status', 'created', 'order by a wholesale email');
select isnt((select customer_id from public.invoices where checkout_key = '5d200000-0000-0000-0000-000000000011'),
            '5d300000-0000-0000-0000-000000000002'::uuid, 'the wholesale customer is not attached');
select is((select tax_cents || '/' || total_cents from public.invoices where checkout_key = '5d200000-0000-0000-0000-000000000011'),
          '784/11184', 'and the order is at retail with tax (no wholesale discount, no exemption)');

-- ---------------------------------------------------------------------------
-- 5. Limits
-- ---------------------------------------------------------------------------
insert into public.public_rate_hits (scope, key_hash)
  select 'order_ip', pg_temp.hash('busy-ip') from generate_series(1, 5);
select is(pg_temp.place('5d200000-0000-0000-0000-000000000020', pg_temp.order_doc(p_email => 'a@example.com'), p_ip => 'busy-ip', p_email => 'a@example.com'),
          '{"status": "rate_limited", "scope": "ip"}'::jsonb, 'six orders from one address in an hour: refused');
insert into public.public_rate_hits (scope, key_hash)
  select 'order_email', pg_temp.hash('busy@example.com') from generate_series(1, 3);
select is(pg_temp.place('5d200000-0000-0000-0000-000000000021', pg_temp.order_doc(p_email => 'busy@example.com'), p_ip => 'ip-5', p_email => 'busy@example.com'),
          '{"status": "rate_limited", "scope": "email"}'::jsonb, 'four orders for one email in an hour: refused');
insert into public.public_rate_hits (scope, key_hash, created_at)
  select 'order_email', pg_temp.hash('old@example.com'), now() - interval '2 hours' from generate_series(1, 5);
select is(pg_temp.place('5d200000-0000-0000-0000-000000000022', pg_temp.order_doc(p_email => 'old@example.com'), p_ip => 'ip-6', p_email => 'old@example.com') ->> 'status',
          'created', 'hits older than an hour don''t count');
insert into public.public_rate_hits (scope, key_hash)
  select 'order_tenant', pg_temp.hash('11111111-1111-1111-1111-111111111111') from generate_series(1, 30);
select is(pg_temp.place('5d200000-0000-0000-0000-000000000023', pg_temp.order_doc(p_email => 'b@example.com'), p_ip => 'ip-7', p_email => 'b@example.com'),
          '{"status": "rate_limited", "scope": "shop"}'::jsonb, 'more than 30 orders an hour in one shop: refused');

select is(
  (select array_agg(pg_temp.json_as('storefront_writer', format('select to_jsonb(public.storefront_count_attempt(%L))', pg_temp.hash('attempts')))::boolean)
     from generate_series(1, 21)),
  array_fill(true, array[20]) || false, 'twenty attempts an hour per address, then refused');
select throws_ok(
  format('select pg_temp.json_as(%L, %L)', 'storefront_writer', 'select to_jsonb(public.storefront_count_attempt(''raw-ip''))'),
  '22023', null, 'only hashes are accepted');

-- ---------------------------------------------------------------------------
-- 6. A closed shop
-- ---------------------------------------------------------------------------
update public.storefront_settings set storefront_enabled = false where tenant_id = '11111111-1111-1111-1111-111111111111';
select is(pg_temp.place('5d200000-0000-0000-0000-000000000030', pg_temp.order_doc(p_email => 'c@example.com'), p_ip => 'ip-8', p_email => 'c@example.com'),
          '{"status": "closed"}'::jsonb, 'a switched-off store takes no orders');
select is(pg_temp.price('[{"product_id": "a1000000-0000-0000-0000-000000000001", "qty": 1}]', 'ship'),
          '{"ok": false, "error": "closed"}'::jsonb, 'and prices nothing');
update public.storefront_settings set storefront_enabled = true where tenant_id = '11111111-1111-1111-1111-111111111111';
update public.tenants set is_active = false where id = '11111111-1111-1111-1111-111111111111';
select is(pg_temp.place('5d200000-0000-0000-0000-000000000031', pg_temp.order_doc(p_email => 'd@example.com'), p_ip => 'ip-9', p_email => 'd@example.com'),
          '{"status": "closed"}'::jsonb, 'an inactive tenant takes no orders');
update public.tenants set is_active = true where id = '11111111-1111-1111-1111-111111111111';
select is(pg_temp.json_as('storefront_writer', format(
            'select public.storefront_place_order(%L, %L, %L, %L::jsonb, %L, %L)',
            '99999999-9999-9999-9999-999999999999', '5d200000-0000-0000-0000-000000000032',
            pg_temp.hash('f'), pg_temp.order_doc(), pg_temp.hash('ip'), pg_temp.hash('e'))),
          '{"status": "closed"}'::jsonb, 'an unknown tenant takes no orders');

-- ---------------------------------------------------------------------------
-- 7. Tracking: one order, by its token, in its own shop
-- ---------------------------------------------------------------------------
select set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true);
select set_config('app.order_token', (select result ->> 'public_token' from placed), true);
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.orders'), 1::bigint,
          'the token finds its order');
select is(pg_temp.json_as('storefront_reader',
            'select to_jsonb(o) - ''created_at'' - ''order_ref'' - ''payment_instructions'' from storefront.orders o'),
          '{"order_status": "quote", "paid": false, "cancelled": false, "refunded": false, "fulfillment": "ship", "payment_choice": "bank_transfer", "subtotal_cents": 9800, "shipping_cents": 600, "tax_rate_bps": 800, "tax_cents": 784, "total_cents": 11184, "first_name": "Sarah", "ship_city": "Portland", "ship_state": "ME", "pickup_note": null}'::jsonb,
          'tracking shows the order with the first name and city/state only');
select ok(pg_temp.json_as('storefront_reader', 'select to_jsonb(payment_instructions) from storefront.orders') is not null
          and pg_temp.json_as('storefront_reader', 'select to_jsonb(payment_instructions) from storefront.orders') <> 'null'::jsonb,
          'payment instructions show while unpaid');
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.order_lines'), 1::bigint,
          'and its lines');

update public.invoices set order_status = 'paid', payment_method = 'bank_transfer'
 where public_token = (select (result ->> 'public_token')::uuid from placed);
select is(pg_temp.json_as('storefront_reader', 'select to_jsonb(payment_instructions) from storefront.orders'),
          null::jsonb, 'payment instructions disappear once paid');

select set_config('app.tenant_id', '22222222-2222-2222-2222-222222222222', true);
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.orders'), 0::bigint,
          'the token in another shop finds nothing');
select set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true);
select set_config('app.order_token', gen_random_uuid()::text, true);
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.orders'), 0::bigint,
          'an unknown token finds nothing');
select set_config('app.order_token', '', true);
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.orders'), 0::bigint,
          'no token finds nothing (never a list of orders)');
select is(pg_temp.count_as('storefront_reader', 'select count(*) from storefront.order_lines'), 0::bigint,
          'and no lines');

select * from finish();
rollback;
