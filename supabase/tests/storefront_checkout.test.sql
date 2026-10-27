begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- ---------------------------------------------------------------------------
-- Session-local helpers (as in the other test files).
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

-- Storefront A2 schema (migrations 20261027000200 and 20261027000300): checkout settings,
-- invoice checkout columns and the totals rule, order_contacts and its RLS, the rate-limit
-- table, personalization fields and the answer snapshot on lines.

-- ---------------------------------------------------------------------------
-- 1. Storefront settings
-- ---------------------------------------------------------------------------
select throws_ok(
  $q$update public.storefront_settings set pickup_enabled = false where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
  '23514', null, 'pay on pickup needs pickup (A has pay on pickup on)');
select throws_ok(
  $q$update public.storefront_settings set ship_flat_cents = -1 where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
  '23514', null, 'a negative flat shipping rate is refused');
select throws_ok(
  $q$update public.storefront_settings set free_ship_over_cents = -1 where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
  '23514', null, 'a negative free-shipping threshold is refused');
select throws_ok(
  $q$update public.storefront_settings set manual_payment_instructions = repeat('x', 2001) where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
  '23514', null, 'payment instructions over 2000 characters are refused');
select throws_ok(
  $q$update public.storefront_settings set pickup_note = '' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
  '23514', null, 'an empty pickup note is refused (null means none)');

-- ---------------------------------------------------------------------------
-- 2. Invoices
-- ---------------------------------------------------------------------------
select is(
  (select count(*)::int from public.invoices where total_cents <> subtotal_cents + tax_cents + shipping_cents),
  0, 'every invoice satisfies total = subtotal + tax + shipping');
select throws_ok(
  $q$insert into public.invoices (tenant_id, customer_name, subtotal_cents, tax_cents, shipping_cents, total_cents) values ('11111111-1111-1111-1111-111111111111', 'X', 1000, 80, 600, 1080)$q$,
  '23514', null, 'a total that leaves out shipping is refused');
select lives_ok(
  $q$insert into public.invoices (tenant_id, customer_name, subtotal_cents, tax_cents, shipping_cents, total_cents) values ('11111111-1111-1111-1111-111111111111', 'X', 1000, 80, 600, 1680)$q$,
  'a total that includes shipping is accepted');
select throws_ok(
  $q$insert into public.invoices (tenant_id, customer_name, source) values ('11111111-1111-1111-1111-111111111111', 'X', 'storefront')$q$,
  '23514', null, 'a storefront order needs fulfillment, payment choice, checkout key, fingerprint and reference');
select throws_ok(
  $q$insert into public.invoices (tenant_id, customer_name, shipping_cents, total_cents) values ('11111111-1111-1111-1111-111111111111', 'X', -1, -1)$q$,
  '23514', null, 'negative shipping is refused');
select throws_ok(
  $q$insert into public.invoices (tenant_id, customer_name, order_ref) values ('11111111-1111-1111-1111-111111111111', 'X', 'W-ILOU0000')$q$,
  '23514', null, 'an order reference outside Crockford base32 is refused');
select throws_ok(
  $q$insert into public.invoices (tenant_id, customer_name, fulfillment) values ('11111111-1111-1111-1111-111111111111', 'X', 'drone')$q$,
  '23514', null, 'an unknown fulfillment is refused');
select throws_ok(
  $q$insert into public.invoices (tenant_id, customer_name, payment_choice) values ('11111111-1111-1111-1111-111111111111', 'X', 'cheque')$q$,
  '23514', null, 'an unknown payment choice is refused (card is one since Slice B2)');

insert into public.invoices (id, tenant_id, customer_name, order_ref, checkout_key)
values ('5c000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'X',
        'W-ABCDEFGH', '5c100000-0000-0000-0000-000000000001');
select throws_ok(
  $q$insert into public.invoices (tenant_id, customer_name, order_ref) values ('11111111-1111-1111-1111-111111111111', 'Y', 'W-ABCDEFGH')$q$,
  '23505', null, 'an order reference is unique per tenant');
select throws_ok(
  $q$insert into public.invoices (tenant_id, customer_name, checkout_key) values ('11111111-1111-1111-1111-111111111111', 'Y', '5c100000-0000-0000-0000-000000000001')$q$,
  '23505', null, 'a checkout key is unique per tenant');
select lives_ok(
  $q$insert into public.invoices (tenant_id, customer_name, order_ref, checkout_key) values ('22222222-2222-2222-2222-222222222222', 'Y', 'W-ABCDEFGH', '5c100000-0000-0000-0000-000000000001')$q$,
  'another tenant may use the same reference and key');

-- The staff financial guard covers shipping.
insert into public.invoices (id, tenant_id, customer_name, subtotal_cents, tax_cents, shipping_cents, total_cents)
values ('5c000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'Paid', 1000, 0, 600, 1600);
insert into public.payments (tenant_id, invoice_id, kind, method, amount_cents, currency)
values ('11111111-1111-1111-1111-111111111111', '5c000000-0000-0000-0000-000000000002', 'payment', 'cash', 1600, 'USD');
update public.invoices set order_status = 'paid', payment_method = 'cash' where id = '5c000000-0000-0000-0000-000000000002';
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
  $q$update public.invoices set shipping_cents = 0, total_cents = 1000 where id = '5c000000-0000-0000-0000-000000000002'$q$),
  '42501', null, 'staff cannot change shipping on an invoice past quote');

-- ---------------------------------------------------------------------------
-- 3. order_contacts
-- ---------------------------------------------------------------------------
insert into public.order_contacts (invoice_id, tenant_id, first_name, last_name, email, ship_line1,
                                   ship_city, ship_state, ship_postal_code, ship_country)
values ('5c000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Sarah',
        'Jenkins', 'sarah@example.com', '1 Main St', 'Portland', 'ME', '04101', 'US');

select throws_ok(
  $q$insert into public.order_contacts (invoice_id, tenant_id, first_name, last_name, email, ship_line1) values ('5c000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'A', 'B', 'a@example.com', '1 Main St')$q$,
  '23514', null, 'a partial ship-to address is refused');
select throws_ok(
  $q$insert into public.order_contacts (invoice_id, tenant_id, first_name, last_name, email, ship_line1, ship_city, ship_state, ship_postal_code, ship_country) values ('5c000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'A', 'B', 'a@example.com', '1 Main St', 'Toronto', 'ON', 'M5V 2T6', 'CA')$q$,
  '23514', null, 'a ship-to outside the US is refused (v1)');
select throws_ok(
  $q$insert into public.order_contacts (invoice_id, tenant_id, first_name, last_name, email) values ('5c000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'A', 'B', 'not-an-email')$q$,
  '23514', null, 'a malformed email is refused');
select throws_ok(
  $q$insert into public.order_contacts (invoice_id, tenant_id, first_name, last_name, email) values ('5c000000-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222', 'A', 'B', 'a@example.com')$q$,
  '23503', null, 'a contact can''t point at another tenant''s invoice');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select is(pg_temp.count_as('authenticated', 'select count(*) from public.order_contacts'), 1::bigint,
          'owner reads the contacts of its orders');
select is(pg_temp.write_as('authenticated',
  $q$update public.order_contacts set phone = '555' where invoice_id = '5c000000-0000-0000-0000-000000000001'$q$),
  1, 'owner can correct a contact');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
  $q$delete from public.order_contacts where invoice_id = '5c000000-0000-0000-0000-000000000001'$q$),
  '42501', null, 'owner cannot delete a contact (it goes with its order)');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
  $q$insert into public.order_contacts (invoice_id, tenant_id, first_name, last_name, email) values ('5c000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'A', 'B', 'a@example.com')$q$),
  '42501', null, 'owner cannot insert a contact (only the checkout does)');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.count_as('authenticated', 'select count(*) from public.order_contacts'), 1::bigint,
          'staff reads the contacts (to fulfil orders)');
select is(pg_temp.write_as('authenticated',
  $q$update public.order_contacts set phone = '555' where invoice_id = '5c000000-0000-0000-0000-000000000001'$q$),
  0, 'staff cannot change a contact');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'cpa');
select is(pg_temp.count_as('authenticated', 'select count(*) from public.order_contacts'), 0::bigint,
          'cpa never sees shopper contacts');

select pg_temp.set_claims('22222222-2222-2222-2222-222222222222', 'owner');
select is(pg_temp.count_as('authenticated', 'select count(*) from public.order_contacts'), 0::bigint,
          'another tenant''s owner sees none of A''s contacts');

select throws_ok(format('select pg_temp.count_as(%L, %L)', 'anon', 'select count(*) from public.order_contacts'),
                 '42501', null, 'anon cannot read contacts');

-- ---------------------------------------------------------------------------
-- 4. public_rate_hits: no app access at all
-- ---------------------------------------------------------------------------
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select throws_ok(format('select pg_temp.count_as(%L, %L)', 'authenticated', 'select count(*) from public.public_rate_hits'),
                 '42501', null, 'owner cannot read the rate-limit counters');
select throws_ok(format('select pg_temp.count_as(%L, %L)', 'anon', 'select count(*) from public.public_rate_hits'),
                 '42501', null, 'anon cannot read the rate-limit counters');
select throws_ok(
  $q$insert into public.public_rate_hits (scope, key_hash) values ('order_ip', 'not-a-hash')$q$,
  '23514', null, 'counters store SHA-256 hashes only');

-- ---------------------------------------------------------------------------
-- 5. Personalization fields
-- ---------------------------------------------------------------------------
insert into public.product_personalization_fields (id, tenant_id, product_id, position, label, kind, required, max_length, choices)
values
  ('5c200000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000003', 1, 'Ring size', 'choice', true, null, '{5,6,7,8}'),
  ('5c200000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000003', 2, 'Engraving', 'text', false, 20, null);

select throws_ok(
  $q$insert into public.product_personalization_fields (tenant_id, product_id, position, label, kind, max_length) values ('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000003', 6, 'Sixth', 'text', 10)$q$,
  '23514', null, 'at most five fields: position 6 is refused');
select throws_ok(
  $q$insert into public.product_personalization_fields (tenant_id, product_id, position, label, kind, max_length) values ('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000003', 1, 'Again', 'text', 10)$q$,
  '23505', null, 'positions are unique per product');
select throws_ok(
  $q$insert into public.product_personalization_fields (tenant_id, product_id, position, label, kind, max_length) values ('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000003', 3, 'ring SIZE', 'text', 10)$q$,
  '23505', null, 'labels are unique per product, ignoring case');
select throws_ok(
  $q$insert into public.product_personalization_fields (tenant_id, product_id, position, label, kind, choices) values ('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000003', 3, 'Metal', 'choice', '{Gold,gold}')$q$,
  '23514', null, 'duplicate choices are refused');
select throws_ok(
  $q$insert into public.product_personalization_fields (tenant_id, product_id, position, label, kind, choices) values ('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000003', 3, 'Metal', 'choice', '{}')$q$,
  '23514', null, 'a choice field needs at least one choice');
select throws_ok(
  $q$insert into public.product_personalization_fields (tenant_id, product_id, position, label, kind, choices) values ('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000003', 3, 'Metal', 'choice', '{" Gold"}')$q$,
  '23514', null, 'choices are stored trimmed');
select throws_ok(
  $q$insert into public.product_personalization_fields (tenant_id, product_id, position, label, kind, max_length, choices) values ('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000003', 3, 'Mixed', 'text', 10, '{A}')$q$,
  '23514', null, 'a text field has no choices');
select throws_ok(
  $q$insert into public.product_personalization_fields (tenant_id, product_id, position, label, kind, max_length) values ('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000003', 3, 'Long', 'text', 201)$q$,
  '23514', null, 'a text field allows at most 200 characters');
select throws_ok(
  $q$insert into public.product_personalization_fields (tenant_id, product_id, position, label, kind, max_length) values ('22222222-2222-2222-2222-222222222222', 'a1000000-0000-0000-0000-000000000003', 3, 'Cross', 'text', 10)$q$,
  '23503', null, 'a field can''t belong to another tenant''s product');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select is(pg_temp.write_as('authenticated',
  $q$insert into public.product_personalization_fields (tenant_id, product_id, position, label, kind, max_length) values ('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000001', 1, 'Initial', 'text', 1)$q$),
  1, 'owner can add a field');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.count_as('authenticated', 'select count(*) from public.product_personalization_fields'), 2::bigint,
          'staff reads the fields');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
  $q$insert into public.product_personalization_fields (tenant_id, product_id, position, label, kind, max_length) values ('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000001', 1, 'Initial', 'text', 1)$q$),
  '42501', null, 'staff cannot add a field');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'cpa');
select is(pg_temp.count_as('authenticated', 'select count(*) from public.product_personalization_fields'), 0::bigint,
          'cpa sees no fields');

-- ---------------------------------------------------------------------------
-- 6. Answers on lines
-- ---------------------------------------------------------------------------
create temp table snapshots (doc jsonb, ok boolean);
insert into snapshots values
  ('[{"label": "Ring size", "value": "7"}]', true),
  ('[{"label": "A", "value": "1"}, {"label": "B", "value": "2"}, {"label": "C", "value": "3"}, {"label": "D", "value": "4"}, {"label": "E", "value": "5"}]', true),
  ('[]', false),
  ('{"label": "A", "value": "1"}', false),
  ('[{"label": "A"}]', false),
  ('[{"label": "A", "value": "1", "extra": "x"}]', false),
  ('[{"label": "A", "value": 7}]', false),
  ('[{"label": "", "value": "1"}]', false),
  ('[{"label": "A", "value": ""}]', false),
  ('"text"', false);
insert into snapshots
  select jsonb_build_array(jsonb_build_object('label', 'A', 'value', repeat('x', 201))), false
  union all select jsonb_agg(jsonb_build_object('label', 'L' || n, 'value', 'v')), false from generate_series(1, 6) n;

select is(public.personalization_snapshot_ok(doc), ok, 'snapshot check: ' || left(doc::text, 60))
  from snapshots order by doc::text;

select * from finish();
rollback;
