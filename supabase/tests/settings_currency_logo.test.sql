begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migration 20261023000200: the currency guard, brand_config.logo_path and the brand-assets
-- bucket. Everything is rolled back.

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

-- Runs a write as p_role, returns the row count and rolls the write back. Errors propagate.
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

-- A tenant with company settings and, optionally, one row of one kind of money data.
create function pg_temp.tenant(p_id uuid, p_data text)
returns void
language plpgsql
as $$
begin
  insert into public.tenants (id, name) values (p_id, 'Currency test ' || coalesce(p_data, 'empty'));
  insert into public.company_settings (tenant_id, currency) values (p_id, 'USD');
  if p_data = 'product' then
    insert into public.products (tenant_id, sku, name, metal, price_cents) values (p_id, 'C-1', 'Ring', 'other', 100);
  elsif p_data = 'material' then
    insert into public.materials (tenant_id, name, unit, unit_cost_cents) values (p_id, 'Wire', 'm', 100);
  elsif p_data = 'invoice' then
    insert into public.invoices (tenant_id, customer_name) values (p_id, 'Walk-in customer');
  elsif p_data = 'expense' then
    insert into public.expenses (tenant_id, expense_date, description, amount_cents) values (p_id, '2026-10-01', 'Rent', 100);
  end if;
end;
$$;

create temp table currency_sql (name text primary key, sql text not null);
grant select on currency_sql to authenticated, service_role;

-- ===========================================================================
-- 1. Currency guard
-- ===========================================================================
select has_trigger('public', 'company_settings', 'company_settings_10_currency_guard', 'the currency guard exists');

select pg_temp.tenant('c0000000-0000-4000-8000-000000000001', null);
select lives_ok($q$update public.company_settings set currency = 'CAD' where tenant_id = 'c0000000-0000-4000-8000-000000000001'$q$,
                'a tenant with no data can still change its currency');
select is((select currency::text from public.company_settings where tenant_id = 'c0000000-0000-4000-8000-000000000001'),
          'CAD', 'and the change is stored');
select pg_temp.set_claims('c0000000-0000-4000-8000-000000000001', 'owner');
select is(pg_temp.write_as('authenticated',
  $q$update public.company_settings set currency = 'EUR' where tenant_id = 'c0000000-0000-4000-8000-000000000001'$q$),
  1, 'the guard allows the owner too while there is no data');
select pg_temp.no_claims();

select pg_temp.tenant('c0000000-0000-4000-8000-000000000002', 'product');
select pg_temp.tenant('c0000000-0000-4000-8000-000000000003', 'material');
select pg_temp.tenant('c0000000-0000-4000-8000-000000000004', 'invoice');
select pg_temp.tenant('c0000000-0000-4000-8000-000000000005', 'expense');
select throws_ok(format($q$update public.company_settings set currency = 'EUR' where tenant_id = %L$q$, t),
                 '23514', 'The currency can''t change once you have products, materials, invoices or expenses.',
                 'postgres cannot change the currency of a tenant with a ' || kind)
  from (values ('c0000000-0000-4000-8000-000000000002', 'product'),
               ('c0000000-0000-4000-8000-000000000003', 'material'),
               ('c0000000-0000-4000-8000-000000000004', 'invoice'),
               ('c0000000-0000-4000-8000-000000000005', 'expense')) v(t, kind);

insert into currency_sql values
  ('a_to_eur', $q$update public.company_settings set currency = 'EUR' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
  ('a_same', $q$update public.company_settings set currency = 'USD', legal_name = 'Same currency' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
  ('a_name', $q$update public.company_settings set legal_name = 'Renamed' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$);
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', (select sql from currency_sql where name = 'a_to_eur')),
                 '23514', 'The currency can''t change once you have products, materials, invoices or expenses.',
                 'the owner of tenant A (which has data) cannot change its currency');
select is(pg_temp.write_as('authenticated', (select sql from currency_sql where name = 'a_same')), 1,
          'writing the same currency (with other changes) is fine');
select is(pg_temp.write_as('authenticated', (select sql from currency_sql where name = 'a_name')), 1,
          'other company settings still change');
select pg_temp.no_claims();
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'service_role', (select sql from currency_sql where name = 'a_to_eur')),
                 '23514', null, 'service_role cannot change it either');
select is((select currency::text from public.company_settings where tenant_id = '11111111-1111-1111-1111-111111111111'),
          'USD', 'tenant A is still USD');

-- ===========================================================================
-- 2. Logo path
-- ===========================================================================
select hasnt_column('public', 'brand_config', 'logo_url', 'the free-text logo_url is gone');
select has_column('public', 'brand_config', 'logo_path', 'brand_config.logo_path exists');
select lives_ok($q$update public.brand_config set logo_path =
  '11111111-1111-1111-1111-111111111111/logo-0b1c2d3e-0000-4000-8000-000000000001.webp'
  where tenant_id = '11111111-1111-1111-1111-111111111111'$q$, 'a logo under its own tenant is accepted');
select throws_ok(format($q$update public.brand_config set logo_path = %L where tenant_id = '11111111-1111-1111-1111-111111111111'$q$, p),
                 '23514', null, 'refused: ' || why)
  from (values ('22222222-2222-2222-2222-222222222222/logo-0b1c2d3e-0000-4000-8000-000000000001.webp', 'another tenant'),
               ('11111111-1111-1111-1111-111111111111/logo-0b1c2d3e-0000-4000-8000-000000000001.png', 'not WebP'),
               ('11111111-1111-1111-1111-111111111111/brand/../logo-0b1c2d3e-0000-4000-8000-000000000001.webp', 'a path that climbs'),
               ('11111111-1111-1111-1111-111111111111/products/x.webp', 'not a logo name'),
               ('https://evil.example/logo.webp', 'a URL')) v(p, why);

-- ===========================================================================
-- 3. The bucket
-- ===========================================================================
select results_eq(
  $q$select public, file_size_limit::bigint, allowed_mime_types from storage.buckets where id = 'brand-assets'$q$,
  $q$values (true, 1048576::bigint, '{image/webp}'::text[])$q$,
  'brand-assets is public, WebP only, 1 MiB');
select is((select count(*)::int from pg_policies
            where schemaname = 'storage' and tablename = 'objects'
              and policyname like 'brand_assets_no_client_%' and permissive = 'RESTRICTIVE'
              and roles @> '{anon,authenticated}'),
          4, 'four RESTRICTIVE no-client policies on brand-assets');

insert into storage.objects (bucket_id, name) values
  ('brand-assets', '11111111-1111-1111-1111-111111111111/logo-0b1c2d3e-0000-4000-8000-000000000001.webp');
create temp table bucket_sql (name text primary key, sql text not null);
grant select on bucket_sql to authenticated, anon;
insert into bucket_sql values
  ('insert', $q$insert into storage.objects (bucket_id, name) values ('brand-assets', '22222222-2222-2222-2222-222222222222/logo-x.webp')$q$),
  ('update', $q$update storage.objects set metadata = '{}' where bucket_id = 'brand-assets'$q$),
  ('delete', $q$delete from storage.objects where bucket_id = 'brand-assets'$q$),
  ('select', $q$select count(*) from storage.objects where bucket_id = 'brand-assets'$q$);
select set_config('storage.allow_delete_query', 'true', true);

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', (select sql from bucket_sql where name = 'insert')),
                 '42501', null, 'an owner cannot write to brand-assets directly');
select is(pg_temp.write_as('authenticated', (select sql from bucket_sql where name = 'update')), 0,
          'an owner cannot update logo objects');
select is(pg_temp.write_as('authenticated', (select sql from bucket_sql where name = 'delete')), 0,
          'an owner cannot delete logo objects');
select is(pg_temp.count_as('authenticated', (select sql from bucket_sql where name = 'select')), 0::bigint,
          'an owner cannot list logo objects');
select pg_temp.no_claims();
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'anon', (select sql from bucket_sql where name = 'insert')),
                 '42501', null, 'anon cannot write to brand-assets');
select is(pg_temp.count_as('anon', (select sql from bucket_sql where name = 'select')), 0::bigint,
          'anon cannot list logo objects');

select * from finish();
rollback;
