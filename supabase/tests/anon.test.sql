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

-- anon has no access to any table (Schema §6 and §8 item 1: storefront reads go through
-- FastAPI public endpoints).

create temp table public_tables as
  select c.relname::text as tbl,
         (select a.attname::text from pg_attribute a where a.attrelid = c.oid and a.attnum = 1) as col
  from pg_class c
  where c.relnamespace = 'public'::regnamespace and c.relkind = 'r';

select is((select count(*)::int from public_tables), 28, 'checking all 28 public tables');

-- Even with tenant claims present, anon is anon.
select set_config('request.jwt.claims', json_build_object(
  'role', 'anon', 'tenant_id', '11111111-1111-1111-1111-111111111111', 'app_role', 'owner')::text, true);

select throws_ok(format('select pg_temp.count_as(%L, %L)', 'anon',
                        format('select count(*) from public.%I', tbl)),
                 '42501', null, tbl || ': anon cannot select')
from public_tables order by tbl;

select throws_ok(format('select pg_temp.write_as(%L, %L)', 'anon',
                        format('insert into public.%I default values', tbl)),
                 '42501', null, tbl || ': anon cannot insert')
from public_tables order by tbl;

select throws_ok(format('select pg_temp.write_as(%L, %L)', 'anon',
                        format('update public.%I set %I = %I', tbl, col, col)),
                 '42501', null, tbl || ': anon cannot update')
from public_tables order by tbl;

select throws_ok(format('select pg_temp.write_as(%L, %L)', 'anon',
                        format('delete from public.%I', tbl)),
                 '42501', null, tbl || ': anon cannot delete')
from public_tables order by tbl;

select throws_ok(format('select pg_temp.count_as(%L, %L)', 'anon', 'select count(*) from ai.inventory_status'),
                 '42501', null, 'anon cannot read the ai views');

select is(
  (select array_agg(p.oid::regprocedure::text order by 1)
     from pg_proc p
    where p.pronamespace = 'public'::regnamespace
      and has_function_privilege('anon', p.oid, 'execute')),
  null::text[],
  'anon cannot execute any public function');

select * from finish();
rollback;
