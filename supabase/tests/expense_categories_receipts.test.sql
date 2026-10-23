begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migration 20261023000100: expense categories as a fixed list, private receipts, and the
-- expense-receipts bucket with no client access. Everything is rolled back.

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

-- Runs a single-value query as p_role.
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

-- ===========================================================================
-- 1. Categories
-- ===========================================================================
select enum_has_labels('public', 'expense_category', array[
  'materials', 'packaging', 'shipping', 'marketing', 'platform_fees', 'software', 'equipment',
  'rent', 'utilities', 'phone', 'insurance', 'professional_services', 'taxes_licenses',
  'travel', 'meals', 'other'], 'the fixed category list');
select col_type_is('public', 'expenses', 'category', 'expense_category', 'category is the enum');
select col_not_null('public', 'expenses', 'category', 'category is required');
select col_default_is('public', 'expenses', 'category', 'other', 'category defaults to other');
select hasnt_column('public', 'expenses', 'receipt_url', 'the free-text receipt_url is gone');

-- The legacy mapping the migration applied to existing rows.
select results_eq(
  $q$select v, public.expense_category_from_legacy(v)::text
       from (values ('services'), (' Services '), ('materials'), ('MARKETING'), ('platform_fees'),
                    (null), (''), ('   '), ('office stuff'), ('servicess')) t(v)$q$,
  $q$values ('services', 'professional_services'), (' Services ', 'professional_services'),
            ('materials', 'materials'), ('MARKETING', 'marketing'), ('platform_fees', 'platform_fees'),
            (null, 'other'), ('', 'other'), ('   ', 'other'), ('office stuff', 'other'),
            ('servicess', 'other')$q$,
  'legacy categories: services -> professional_services; known ones kept; null, empty and unknown -> other');

-- The seed (now written with the new names) and the AI summary by category.
select is((select category::text from public.expenses
            where tenant_id = '22222222-2222-2222-2222-222222222222'),
          'professional_services', 'tenant B''s certification is professional services');
-- Local test only (as in ai_readonly.test.sql): let the test owner switch into ai_readonly.
grant ai_readonly to postgres;
select set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true);
select results_eq(
  $q$select category::text, expense_count, total_cents, deductible_cents from ai.expense_summary
      where month = '2026-10-01' order by category::text$q$,
  $q$values ('marketing', 1, 25000::bigint, 25000::bigint), ('materials', 2, 59500::bigint, 59500::bigint),
            ('meals', 1, 6800::bigint, 0::bigint)$q$,
  'ai.expense_summary for tenant A: by category, totals and deductible unchanged');
select is(pg_temp.count_as('ai_readonly', 'select sum(total_cents)::bigint from ai.expense_summary'),
          91300::bigint, 'ai_readonly still reads the recreated view');
select set_config('app.tenant_id', '22222222-2222-2222-2222-222222222222', true);
select is(pg_temp.count_as('ai_readonly', $q$select sum(total_cents)::bigint from ai.expense_summary where category = 'professional_services'$q$),
          18000::bigint, 'tenant B''s summary uses the mapped category');
select throws_ok($q$select pg_temp.count_as('authenticated', 'select count(*) from ai.expense_summary')$q$,
                 '42501', null, 'authenticated has no grant on ai.expense_summary');
select set_config('app.tenant_id', '', true);

-- ===========================================================================
-- 2. Receipt columns
-- ===========================================================================
insert into public.expenses (id, tenant_id, expense_date, description, amount_cents)
values ('e7000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', '2026-10-20', 'Receipt test', 1000),
       ('e7000000-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222', '2026-10-20', 'Receipt test B', 1000);
select is((select category::text from public.expenses where id = 'e7000000-0000-0000-0000-000000000001'),
          'other', 'an expense without a category is other');

create function pg_temp.set_receipt(p_id uuid, p_path text, p_type text default 'image/jpeg', p_size integer default 1000)
returns void
language sql
as $$
  update public.expenses
     set receipt_path = p_path, receipt_content_type = p_type, receipt_size_bytes = p_size,
         receipt_uploaded_at = now()
   where id = p_id;
$$;

select lives_ok($q$select pg_temp.set_receipt('e7000000-0000-0000-0000-000000000001',
  '11111111-1111-1111-1111-111111111111/e7000000-0000-0000-0000-000000000001/0b1c2d3e-0000-4000-8000-000000000001.jpg')$q$,
  'a receipt under its own tenant and expense is accepted');
select lives_ok($q$select pg_temp.set_receipt('e7000000-0000-0000-0000-000000000001',
  '11111111-1111-1111-1111-111111111111/e7000000-0000-0000-0000-000000000001/0b1c2d3e-0000-4000-8000-000000000002.pdf', 'application/pdf')$q$,
  'a PDF receipt is accepted');
select throws_ok($q$select pg_temp.set_receipt('e7000000-0000-0000-0000-000000000001',
  '22222222-2222-2222-2222-222222222222/e7000000-0000-0000-0000-000000000001/0b1c2d3e-0000-4000-8000-000000000001.jpg')$q$,
  '23514', null, 'a path under another tenant is refused');
select throws_ok($q$select pg_temp.set_receipt('e7000000-0000-0000-0000-000000000001',
  '11111111-1111-1111-1111-111111111111/e7000000-0000-0000-0000-000000000002/0b1c2d3e-0000-4000-8000-000000000001.jpg')$q$,
  '23514', null, 'a path under another expense is refused');
select throws_ok($q$select pg_temp.set_receipt('e7000000-0000-0000-0000-000000000001',
  '11111111-1111-1111-1111-111111111111/e7000000-0000-0000-0000-000000000001/../other/x.jpg')$q$,
  '23514', null, 'a path that climbs out is refused');
select throws_ok($q$select pg_temp.set_receipt('e7000000-0000-0000-0000-000000000001',
  '11111111-1111-1111-1111-111111111111/e7000000-0000-0000-0000-000000000001/0b1c2d3e-0000-4000-8000-000000000001.exe')$q$,
  '23514', null, 'an unknown file extension is refused');
select throws_ok($q$select pg_temp.set_receipt('e7000000-0000-0000-0000-000000000001',
  '11111111-1111-1111-1111-111111111111/e7000000-0000-0000-0000-000000000001/0b1c2d3e-0000-4000-8000-000000000001.jpg', 'text/html')$q$,
  '23514', null, 'a content type outside the list is refused');
select throws_ok($q$select pg_temp.set_receipt('e7000000-0000-0000-0000-000000000001',
  '11111111-1111-1111-1111-111111111111/e7000000-0000-0000-0000-000000000001/0b1c2d3e-0000-4000-8000-000000000001.jpg', 'image/jpeg', 10485761)$q$,
  '23514', null, 'a receipt over 10 MiB is refused');
select throws_ok($q$update public.expenses set receipt_size_bytes = null where id = 'e7000000-0000-0000-0000-000000000001'$q$,
  '23514', null, 'receipt columns are set or cleared together');

-- Only the owner can write expenses (and so receipts); cpa and staff match no row.
create temp table receipt_sql (name text primary key, sql text not null);
grant select on receipt_sql to authenticated;
insert into receipt_sql values ('clear', $q$update public.expenses set receipt_path = null, receipt_content_type = null,
  receipt_size_bytes = null, receipt_uploaded_at = null where id = 'e7000000-0000-0000-0000-000000000001'$q$);
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'cpa');
select is(pg_temp.write_as('authenticated', (select sql from receipt_sql where name = 'clear')), 0,
          'cpa cannot change a receipt');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.write_as('authenticated', (select sql from receipt_sql where name = 'clear')), 0,
          'staff cannot change a receipt');
select pg_temp.set_claims('22222222-2222-2222-2222-222222222222', 'owner');
select is(pg_temp.write_as('authenticated', (select sql from receipt_sql where name = 'clear')), 0,
          'another tenant''s owner cannot change a receipt');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select is(pg_temp.write_as('authenticated', (select sql from receipt_sql where name = 'clear')), 1,
          'the owner can');
select pg_temp.no_claims();

-- ===========================================================================
-- 3. The bucket: private, limited, no client access
-- ===========================================================================
select results_eq(
  $q$select public, file_size_limit::bigint, allowed_mime_types from storage.buckets where id = 'expense-receipts'$q$,
  $q$values (false, 10485760::bigint, '{image/jpeg,image/png,image/webp,application/pdf}'::text[])$q$,
  'expense-receipts is private, 10 MiB, images and PDF only');
select is((select count(*)::int from pg_policies
            where schemaname = 'storage' and tablename = 'objects'
              and policyname like 'expense_receipts_no_client_%' and permissive = 'RESTRICTIVE'
              and roles @> '{anon,authenticated}'),
          4, 'four RESTRICTIVE no-client policies on expense-receipts');

insert into storage.objects (bucket_id, name) values
  ('expense-receipts', '11111111-1111-1111-1111-111111111111/e7000000-0000-0000-0000-000000000001/r.jpg');
create temp table bucket_sql (name text primary key, sql text not null);
grant select on bucket_sql to authenticated, anon;
insert into bucket_sql values
  ('insert', $q$insert into storage.objects (bucket_id, name) values ('expense-receipts', '11111111-1111-1111-1111-111111111111/x/y.pdf')$q$),
  ('update', $q$update storage.objects set metadata = '{}' where bucket_id = 'expense-receipts'$q$),
  ('delete', $q$delete from storage.objects where bucket_id = 'expense-receipts'$q$),
  ('select', $q$select count(*) from storage.objects where bucket_id = 'expense-receipts'$q$);
select set_config('storage.allow_delete_query', 'true', true);

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', (select sql from bucket_sql where name = 'insert')),
                 '42501', null, 'an owner cannot write to expense-receipts directly');
select is(pg_temp.write_as('authenticated', (select sql from bucket_sql where name = 'update')), 0,
          'an owner cannot update receipt objects');
select is(pg_temp.write_as('authenticated', (select sql from bucket_sql where name = 'delete')), 0,
          'an owner cannot delete receipt objects');
select is(pg_temp.count_as('authenticated', (select sql from bucket_sql where name = 'select')), 0::bigint,
          'an owner cannot list or read receipt objects');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'cpa');
select is(pg_temp.count_as('authenticated', (select sql from bucket_sql where name = 'select')), 0::bigint,
          'a cpa cannot list or read receipt objects');
select pg_temp.no_claims();
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'anon', (select sql from bucket_sql where name = 'insert')),
                 '42501', null, 'anon cannot write to expense-receipts');
select is(pg_temp.count_as('anon', (select sql from bucket_sql where name = 'select')), 0::bigint,
          'anon cannot list or read receipt objects');

select * from finish();
rollback;
