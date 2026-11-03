begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migration 20261102000300 (Slice C1): the In store source, the receipt settings,
-- receipt_settings() and personalization_snapshot(). Everything here is rolled back.

create function pg_temp.set_claims(p_sub uuid, p_tenant text, p_app_role text)
returns void
language plpgsql
as $$
begin
  perform set_config('request.jwt.claims', jsonb_strip_nulls(jsonb_build_object(
    'sub', p_sub, 'role', 'authenticated',
    'tenant_id', p_tenant, 'app_role', p_app_role))::text, true);
end;
$$;

-- Runs a query as `authenticated` and returns its single jsonb value; the role is restored.
create function pg_temp.as_authenticated(p_sql text)
returns jsonb
language plpgsql
as $$
declare
  v jsonb;
begin
  perform set_config('role', 'authenticated', true);
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

insert into auth.users (id, email) values
  ('cccccccc-0000-0000-0000-00000000000a', 'c1-owner-a@test.local'),
  ('cccccccc-0000-0000-0000-00000000000b', 'c1-staff-a@test.local'),
  ('cccccccc-0000-0000-0000-00000000000c', 'c1-cpa-a@test.local'),
  ('cccccccc-0000-0000-0000-00000000000d', 'c1-staff-b@test.local'),
  ('cccccccc-0000-0000-0000-00000000000e', 'c1-nobody@test.local');
insert into public.tenant_members (user_id, tenant_id, app_role) values
  ('cccccccc-0000-0000-0000-00000000000a', '11111111-1111-1111-1111-111111111111', 'owner'),
  ('cccccccc-0000-0000-0000-00000000000b', '11111111-1111-1111-1111-111111111111', 'staff'),
  ('cccccccc-0000-0000-0000-00000000000c', '11111111-1111-1111-1111-111111111111', 'cpa'),
  ('cccccccc-0000-0000-0000-00000000000d', '22222222-2222-2222-2222-222222222222', 'staff');

-- A ring of tenant A asking for an Initial (text, at most 3, required) and a Size (6, 7, 8).
insert into public.products (id, tenant_id, sku, name, metal, price_cents, stock_qty) values
  ('cccccccc-1111-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
   'C1-RING', 'C1 ring', 'other', 10000, 5);
insert into public.product_personalization_fields
  (id, tenant_id, product_id, position, label, kind, required, max_length, choices) values
  ('cccccccc-2222-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
   'cccccccc-1111-0000-0000-000000000001', 1, 'Initial', 'text', true, 3, null),
  ('cccccccc-2222-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111',
   'cccccccc-1111-0000-0000-000000000001', 2, 'Size', 'choice', false, null, '{6,7,8}');

-- ===========================================================================
-- 1. Source
-- ===========================================================================
select lives_ok(
  $$insert into public.invoices (tenant_id, customer_name, source, subtotal_cents, tax_rate_bps,
                                 tax_cents, total_cents)
    values ('11111111-1111-1111-1111-111111111111', 'C1 walk-in', 'in_store', 0, 0, 0, 0)$$,
  'an invoice can be In store');
select throws_ok(
  $$insert into public.invoices (tenant_id, customer_name, source, subtotal_cents, tax_rate_bps,
                                 tax_cents, total_cents)
    values ('11111111-1111-1111-1111-111111111111', 'C1 walk-in', 'pos', 0, 0, 0, 0)$$,
  '23514', null, 'any other source is refused');

-- ===========================================================================
-- 2. Receipt settings: Letter by default; three layouts; a checked footer
-- ===========================================================================
select is(
  (select receipt_layout from public.company_settings
    where tenant_id = '11111111-1111-1111-1111-111111111111'),
  'letter', 'receipts default to Letter');
select col_default_is('public', 'company_settings', 'receipt_layout', 'letter',
                      'the column default is letter');
select lives_ok(
  $$update public.company_settings set receipt_layout = '80mm'
     where tenant_id = '11111111-1111-1111-1111-111111111111'$$, '80mm is a layout');
select lives_ok(
  $$update public.company_settings set receipt_layout = '58mm'
     where tenant_id = '11111111-1111-1111-1111-111111111111'$$, '58mm is a layout');
select throws_ok(
  $$update public.company_settings set receipt_layout = 'a4'
     where tenant_id = '11111111-1111-1111-1111-111111111111'$$,
  '23514', null, 'any other layout is refused');
select lives_ok(
  $$update public.company_settings set receipt_footer = E'Thank you!\nSee you soon.'
     where tenant_id = '11111111-1111-1111-1111-111111111111'$$,
  'a footer may have line breaks');
select throws_ok(
  $$update public.company_settings set receipt_footer = E'tab\there'
     where tenant_id = '11111111-1111-1111-1111-111111111111'$$,
  '23514', null, 'a footer with other control characters is refused');
select throws_ok(
  $$update public.company_settings set receipt_footer = ' padded '
     where tenant_id = '11111111-1111-1111-1111-111111111111'$$,
  '23514', null, 'a footer is stored trimmed');
select throws_ok(
  $$update public.company_settings set receipt_footer = repeat('x', 301)
     where tenant_id = '11111111-1111-1111-1111-111111111111'$$,
  '23514', null, 'a footer is at most 300 characters');

-- ===========================================================================
-- 3. receipt_settings(): owner and staff of the token's tenant only
-- ===========================================================================
select pg_temp.set_claims('cccccccc-0000-0000-0000-00000000000b', '11111111-1111-1111-1111-111111111111', 'staff');
select is(
  pg_temp.as_authenticated('select to_jsonb(r) from public.receipt_settings() r'),
  (select jsonb_build_object('business_name', b.business_name, 'address', c.address,
                             'receipt_layout', '58mm', 'receipt_footer', E'Thank you!\nSee you soon.')
     from public.company_settings c join public.brand_config b using (tenant_id)
    where c.tenant_id = '11111111-1111-1111-1111-111111111111'),
  'staff of A read A''s receipt header, layout and footer');
select pg_temp.set_claims('cccccccc-0000-0000-0000-00000000000a', '11111111-1111-1111-1111-111111111111', 'owner');
select is(
  pg_temp.as_authenticated('select to_jsonb(r) from public.receipt_settings() r') ->> 'receipt_layout',
  '58mm', 'the owner reads them too');
select pg_temp.set_claims('cccccccc-0000-0000-0000-00000000000d', '22222222-2222-2222-2222-222222222222', 'staff');
select is(
  pg_temp.as_authenticated('select to_jsonb(r) from public.receipt_settings() r') ->> 'receipt_layout',
  'letter', 'staff of B read B''s own settings, not A''s');
select pg_temp.set_claims('cccccccc-0000-0000-0000-00000000000c', '11111111-1111-1111-1111-111111111111', 'cpa');
select throws_ok('select pg_temp.as_authenticated(''select to_jsonb(r) from public.receipt_settings() r'')',
                 '42501', 'only owner or staff can print receipts', 'the CPA is refused');
select pg_temp.set_claims('cccccccc-0000-0000-0000-00000000000e', '11111111-1111-1111-1111-111111111111', 'staff');
select throws_ok('select pg_temp.as_authenticated(''select to_jsonb(r) from public.receipt_settings() r'')',
                 '42501', 'not a member of this tenant with this role', 'a non-member claiming staff is refused');
select pg_temp.set_claims('cccccccc-0000-0000-0000-00000000000b', '22222222-2222-2222-2222-222222222222', 'staff');
select throws_ok('select pg_temp.as_authenticated(''select to_jsonb(r) from public.receipt_settings() r'')',
                 '42501', 'not a member of this tenant with this role', 'staff of A claiming tenant B is refused');
select ok(not has_function_privilege('anon', 'public.receipt_settings()', 'execute'),
          'anon can''t run receipt_settings()');
select ok((select prosecdef and proconfig @> array['search_path=""']
             from pg_proc where oid = 'public.receipt_settings()'::regprocedure),
          'receipt_settings() is SECURITY DEFINER with an empty search_path');

-- ===========================================================================
-- 4. personalization_snapshot(): the website's checks, with the caller's rights
-- ===========================================================================
create function pg_temp.snap(p_answers jsonb)
returns jsonb
language sql
as $$
  select pg_temp.as_authenticated(format(
    'select public.personalization_snapshot(%L, %L)',
    'cccccccc-1111-0000-0000-000000000001', p_answers::text));
$$;

select pg_temp.set_claims('cccccccc-0000-0000-0000-00000000000b', '11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.snap('{"cccccccc-2222-0000-0000-000000000001": " KB ", "cccccccc-2222-0000-0000-000000000002": "7"}'),
          '{"ok": true, "snapshot": [{"label": "Initial", "value": "KB"}, {"label": "Size", "value": "7"}]}',
          'answers are trimmed and stored as label/value in field order');
select is(pg_temp.snap('{"cccccccc-2222-0000-0000-000000000001": "M", "cccccccc-2222-0000-0000-000000000002": " "}'),
          '{"ok": true, "snapshot": [{"label": "Initial", "value": "M"}]}',
          'a blank optional answer is left out');
select is(pg_temp.snap('{}'),
          '{"ok": false, "label": "Initial", "reason": "required"}', 'a required field must be answered');
select is(pg_temp.snap('{"cccccccc-2222-0000-0000-000000000001": "ABCD"}'),
          '{"ok": false, "label": "Initial", "reason": "length"}', 'text within its maximum length');
select is(pg_temp.snap('{"cccccccc-2222-0000-0000-000000000001": "A", "cccccccc-2222-0000-0000-000000000002": "9"}'),
          '{"ok": false, "label": "Size", "reason": "choice"}', 'a choice from the list');
select is(pg_temp.snap(jsonb_build_object('cccccccc-2222-0000-0000-000000000001', E'A\tB')),
          '{"ok": false, "label": "Initial", "reason": "characters"}', 'no control characters');
select is(pg_temp.snap('{"cccccccc-2222-0000-0000-000000000001": "A", "dddddddd-0000-0000-0000-000000000000": "x"}'),
          '{"ok": false, "label": null, "reason": "unknown"}', 'only this product''s fields');
select is(pg_temp.snap('{"cccccccc-2222-0000-0000-000000000001": 7}'),
          '{"ok": false, "label": null, "reason": "invalid"}', 'answers are strings');
select is(pg_temp.snap('[]'),
          '{"ok": false, "label": null, "reason": "invalid"}', 'answers are an object');
select is(pg_temp.as_authenticated(
            'select public.personalization_snapshot(''cccccccc-1111-0000-0000-000000000001'', null)')
            ->> 'reason',
          'required', 'no answers at all still checks the required ones');

-- Another shop can't see the fields: answering them is "unknown".
select pg_temp.set_claims('cccccccc-0000-0000-0000-00000000000d', '22222222-2222-2222-2222-222222222222', 'staff');
select is(pg_temp.snap('{"cccccccc-2222-0000-0000-000000000001": "A"}') ->> 'reason',
          'unknown', 'staff of B can''t answer A''s fields');

select ok(not has_function_privilege('anon', 'public.personalization_snapshot(uuid, jsonb)', 'execute'),
          'anon can''t run personalization_snapshot()');
select ok((select not prosecdef and proconfig @> array['search_path=""']
             from pg_proc where oid = 'public.personalization_snapshot(uuid, jsonb)'::regprocedure),
          'personalization_snapshot() runs with the caller''s rights and an empty search_path');

select * from finish();
rollback;
