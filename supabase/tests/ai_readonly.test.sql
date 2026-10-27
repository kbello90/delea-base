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

-- AI read path (Schema §7): ai_readonly can select from the ai views only, every write fails,
-- tenant scoping comes from app.tenant_id, and an unset tenant returns zero rows.

-- Local test only: let the test owner switch into ai_readonly. In production the backend
-- connects as a dedicated non-superuser role that is granted ai_readonly (supabase/README.md).
grant ai_readonly to postgres;

create temp table ai_views (v text primary key);
insert into ai_views values
  ('inventory_status'), ('materials_status'), ('sales_summary'), ('expense_summary'), ('tax_summary');

-- ---------------------------------------------------------------------------
-- Grants (catalog checks)
-- ---------------------------------------------------------------------------
select is(
  (select array_agg(c.relname::text order by c.relname)
     from pg_class c
    where c.relnamespace = 'public'::regnamespace
      and c.relkind in ('r', 'p', 'v', 'm')
      and has_table_privilege('ai_readonly', c.oid,
            'select, insert, update, delete, truncate, references, trigger')),
  null::text[],
  'ai_readonly holds no privilege on any public table');

select is(
  (select array_agg(p.oid::regprocedure::text order by 1)
     from pg_proc p
    where p.pronamespace = 'public'::regnamespace
      and has_function_privilege('ai_readonly', p.oid, 'execute')),
  null::text[],
  'ai_readonly cannot execute any public function');

select ok(has_table_privilege('ai_readonly', format('ai.%I', v), 'select'),
          'ai_readonly can select ai.' || v)
from ai_views order by v;

select ok(not has_table_privilege('ai_readonly', format('ai.%I', v), 'insert, update, delete, truncate'),
          'ai_readonly has no write privilege on ai.' || v)
from ai_views order by v;

select is(
  (select array_agg(c.relname::text order by c.relname)
     from pg_class c
    where c.relnamespace = 'ai'::regnamespace
      and (has_table_privilege('anon', c.oid, 'select')
           or has_table_privilege('authenticated', c.oid, 'select'))),
  null::text[],
  'anon and authenticated cannot read the ai views');

-- ---------------------------------------------------------------------------
-- Unset tenant context returns zero rows (checked before app.tenant_id is ever set)
-- ---------------------------------------------------------------------------
select is(pg_temp.count_as('ai_readonly', format('select count(*) from ai.%I', v)), 0::bigint,
          'ai.' || v || ': unset app.tenant_id returns zero rows')
from ai_views order by v;

select set_config('app.tenant_id', '', true);
select is(pg_temp.count_as('ai_readonly', format('select count(*) from ai.%I', v)), 0::bigint,
          'ai.' || v || ': empty app.tenant_id returns zero rows')
from ai_views order by v;

-- ---------------------------------------------------------------------------
-- Tenant A context
-- ---------------------------------------------------------------------------
select set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true);

select cmp_ok(pg_temp.count_as('ai_readonly', format('select count(*) from ai.%I', v)), '>', 0::bigint,
              'ai.' || v || ': tenant A context returns rows')
from ai_views order by v;

select is(pg_temp.count_as('ai_readonly', 'select count(*) from ai.inventory_status'), 6::bigint,
          'inventory_status shows tenant A''s 6 products');
select is(pg_temp.count_as('ai_readonly',
            $$select count(*) from ai.inventory_status where product_id::text like 'b2000000-%'$$),
          0::bigint, 'inventory_status contains no tenant B products');
select is(pg_temp.count_as('ai_readonly',
            $$select count(*) from ai.materials_status where material_id::text like 'b4000000-%'$$),
          0::bigint, 'materials_status contains no tenant B materials');
select is(pg_temp.count_as('ai_readonly', 'select sum(total_cents)::bigint from ai.sales_summary'),
          29484::bigint, 'sales_summary totals tenant A invoices only (10584 + 18900)');
select is(pg_temp.count_as('ai_readonly', 'select sum(tax_cents)::bigint from ai.tax_summary'),
          784::bigint, 'tax_summary counts tenant A paid tax only');
select is(pg_temp.count_as('ai_readonly', 'select sum(total_cents)::bigint from ai.expense_summary'),
          91300::bigint, 'expense_summary totals tenant A expenses only');
select is(pg_temp.count_as('ai_readonly', 'select sum(deductible_cents)::bigint from ai.expense_summary'),
          84500::bigint, 'expense_summary deductible total excludes non-deductible expenses');
select is(pg_temp.count_as('ai_readonly', 'select count(*) from ai.inventory_status where is_low'),
          1::bigint, 'inventory_status flags the Engagement Ring (2 < 3) as low');

-- Switching the context switches the tenant.
select set_config('app.tenant_id', '22222222-2222-2222-2222-222222222222', true);
select is(pg_temp.count_as('ai_readonly', 'select count(*) from ai.inventory_status'), 2::bigint,
          'tenant B context shows tenant B''s 2 products');
select set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true);

-- ---------------------------------------------------------------------------
-- No public table is reachable: select / insert / update / delete all fail with 42501
-- ---------------------------------------------------------------------------
create temp table public_tables as
  select c.relname::text as tbl,
         (select a.attname::text from pg_attribute a where a.attrelid = c.oid and a.attnum = 1) as col
  from pg_class c
  where c.relnamespace = 'public'::regnamespace and c.relkind = 'r';

select is((select count(*)::int from public_tables), 23, 'checking all 23 public tables');

select throws_ok(format('select pg_temp.count_as(%L, %L)', 'ai_readonly',
                        format('select count(*) from public.%I', tbl)),
                 '42501', null, tbl || ': ai_readonly cannot select')
from public_tables order by tbl;

select throws_ok(format('select pg_temp.write_as(%L, %L)', 'ai_readonly',
                        format('insert into public.%I default values', tbl)),
                 '42501', null, tbl || ': ai_readonly cannot insert')
from public_tables order by tbl;

select throws_ok(format('select pg_temp.write_as(%L, %L)', 'ai_readonly',
                        format('update public.%I set %I = %I', tbl, col, col)),
                 '42501', null, tbl || ': ai_readonly cannot update')
from public_tables order by tbl;

select throws_ok(format('select pg_temp.write_as(%L, %L)', 'ai_readonly',
                        format('delete from public.%I', tbl)),
                 '42501', null, tbl || ': ai_readonly cannot delete')
from public_tables order by tbl;

-- ---------------------------------------------------------------------------
-- Writes through the ai views fail too (any error: permission denied or non-updatable view)
-- ---------------------------------------------------------------------------
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'ai_readonly',
                        format('insert into ai.%I default values', v)),
                 null::char(5), null, 'ai.' || v || ': ai_readonly cannot insert')
from ai_views order by v;

select throws_ok(format('select pg_temp.write_as(%L, %L)', 'ai_readonly',
                        format('update ai.%I set %I = %I', v, col, col)),
                 null::char(5), null, 'ai.' || v || ': ai_readonly cannot update')
from (select v, (select a.attname::text from pg_attribute a
                 where a.attrelid = format('ai.%I', v)::regclass and a.attnum = 1) as col
      from ai_views) x
order by v;

select throws_ok(format('select pg_temp.write_as(%L, %L)', 'ai_readonly',
                        format('delete from ai.%I', v)),
                 null::char(5), null, 'ai.' || v || ': ai_readonly cannot delete')
from ai_views order by v;

-- The base data is untouched.
-- Seed tenants only: other tenants (e.g. a locally loaded showcase) may exist.
select is((select count(*)::int from public.products
            where tenant_id in ('11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222')),
          8, 'products unchanged after the write attempts');

select * from finish();
rollback;
