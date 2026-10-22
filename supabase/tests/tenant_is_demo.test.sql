begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migration 20261022000100: tenants.is_demo. Only an administrative connection (postgres)
-- may set or change it; no app role can, owner included. Everything is rolled back.

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

-- A write as p_role: returns the row count and rolls the write back. Errors propagate.
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
-- The column
-- ===========================================================================
select has_column('public', 'tenants', 'is_demo', 'tenants.is_demo exists');
select col_not_null('public', 'tenants', 'is_demo', 'is_demo is not null');
select col_default_is('public', 'tenants', 'is_demo', 'false', 'is_demo defaults to false');
select is((select count(*) from public.tenants where is_demo
            and id in ('11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222')),
          0::bigint, 'the seed tenants are not demo tenants');

-- ===========================================================================
-- No app role can set it
-- ===========================================================================
create temp table demo_sql (name text primary key, sql text not null);
grant select on demo_sql to authenticated, anon, ai_readonly, service_role;
insert into demo_sql values
  ('set A', $q$update public.tenants set is_demo = true where id = '11111111-1111-1111-1111-111111111111'$q$),
  ('insert demo', $q$insert into public.tenants (name, is_demo) values ('Sneaky demo', true)$q$),
  ('rename A', $q$update public.tenants set name = 'Renamed' where id = '11111111-1111-1111-1111-111111111111'$q$);

-- authenticated: tenants has a select policy only, so updates match no row and inserts fail.
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select is(pg_temp.write_as('authenticated', (select sql from demo_sql where name = 'set A')), 0,
          'owner of A cannot set is_demo on their own tenant (no row is updatable)');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', (select sql from demo_sql where name = 'insert demo')),
                 '42501', null, 'owner cannot insert a demo tenant');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.write_as('authenticated', (select sql from demo_sql where name = 'set A')), 0,
          'staff cannot set is_demo');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'cpa');
select is(pg_temp.write_as('authenticated', (select sql from demo_sql where name = 'set A')), 0,
          'cpa cannot set is_demo');

-- Even if a tenants update policy is added later (here a permissive one, rolled back with
-- the test), the trigger still refuses is_demo for the owner while allowing other columns.
create policy tmp_tenants_owner_update on public.tenants for update to authenticated
  using (id = (select (auth.jwt() ->> 'tenant_id')::uuid))
  with check (id = (select (auth.jwt() ->> 'tenant_id')::uuid));
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', (select sql from demo_sql where name = 'set A')),
                 '42501', 'is_demo can only be set by an administrative connection',
                 'with an update policy in place, the trigger still refuses is_demo for the owner');
select is(pg_temp.write_as('authenticated', (select sql from demo_sql where name = 'rename A')), 1,
          'with an update policy in place, the owner can change other columns');
drop policy tmp_tenants_owner_update on public.tenants;

select throws_ok(format('select pg_temp.write_as(%L, %L)', 'anon', (select sql from demo_sql where name = 'set A')),
                 '42501', null, 'anon cannot set is_demo');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'ai_readonly', (select sql from demo_sql where name = 'set A')),
                 '42501', null, 'ai_readonly cannot set is_demo');

-- service_role bypasses RLS and holds UPDATE on tenants: the trigger is what stops it.
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'service_role', (select sql from demo_sql where name = 'set A')),
                 '42501', 'is_demo can only be set by an administrative connection',
                 'service_role cannot set is_demo (the trigger refuses)');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'service_role', (select sql from demo_sql where name = 'insert demo')),
                 '42501', 'is_demo can only be set by an administrative connection',
                 'service_role cannot insert a demo tenant');
select is(pg_temp.write_as('service_role', (select sql from demo_sql where name = 'rename A')), 1,
          'the trigger is narrow: service_role can still update other tenant columns');

-- ===========================================================================
-- An administrative connection can
-- ===========================================================================
select lives_ok($q$update public.tenants set is_demo = true where id = '22222222-2222-2222-2222-222222222222'$q$,
                'postgres can set is_demo');
select is((select is_demo from public.tenants where id = '22222222-2222-2222-2222-222222222222'), true,
          'is_demo was set');
select lives_ok($q$insert into public.tenants (id, name, is_demo) values ('33333333-3333-3333-3333-333333333333', 'Demo', true)$q$,
                'postgres can insert a demo tenant');

select * from finish();
rollback;
