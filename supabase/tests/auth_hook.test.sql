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

-- Custom access token hook (TRD §2.2): adds tenant_id and app_role, never touches role.

insert into auth.users (id, email) values
  ('cccccccc-0000-0000-0000-000000000001', 'multi@test.local'),
  ('cccccccc-0000-0000-0000-000000000002', 'cpa-b@test.local'),
  ('cccccccc-0000-0000-0000-000000000003', 'nobody@test.local');

-- multi@ joined tenant A first (owner), then tenant B (staff).
insert into public.tenant_members (user_id, tenant_id, app_role, created_at) values
  ('cccccccc-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', 'staff', now() - interval '1 day'),
  ('cccccccc-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'owner', now() - interval '2 days'),
  ('cccccccc-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222', 'cpa',   now());

create function pg_temp.hook_claims(p_user uuid)
returns jsonb
language sql
as $$
  select public.custom_access_token_hook(jsonb_build_object(
    'user_id', p_user,
    'authentication_method', 'password',
    'claims', jsonb_build_object(
      'sub', p_user, 'role', 'authenticated', 'aud', 'authenticated',
      'tenant_id', 'stale-tenant', 'app_role', 'stale-role'))) -> 'claims';
$$;

select is(pg_temp.hook_claims('cccccccc-0000-0000-0000-000000000002') ->> 'tenant_id',
          '22222222-2222-2222-2222-222222222222', 'hook sets tenant_id from tenant_members');
select is(pg_temp.hook_claims('cccccccc-0000-0000-0000-000000000002') ->> 'app_role',
          'cpa', 'hook sets app_role from tenant_members');
select is(pg_temp.hook_claims('cccccccc-0000-0000-0000-000000000002') ->> 'role',
          'authenticated', 'hook leaves the Postgres role claim untouched');
select is(pg_temp.hook_claims('cccccccc-0000-0000-0000-000000000002') ->> 'sub',
          'cccccccc-0000-0000-0000-000000000002', 'hook keeps the other claims');

select is(pg_temp.hook_claims('cccccccc-0000-0000-0000-000000000001') ->> 'tenant_id',
          '11111111-1111-1111-1111-111111111111', 'multi-tenant user gets the oldest membership''s tenant');
select is(pg_temp.hook_claims('cccccccc-0000-0000-0000-000000000001') ->> 'app_role',
          'owner', 'multi-tenant user gets the oldest membership''s role');

select ok(not (pg_temp.hook_claims('cccccccc-0000-0000-0000-000000000003') ? 'tenant_id'),
          'user without membership gets no tenant_id claim (stale value removed)');
select ok(not (pg_temp.hook_claims('cccccccc-0000-0000-0000-000000000003') ? 'app_role'),
          'user without membership gets no app_role claim (stale value removed)');

-- Function properties and grants.
select ok((select prosecdef from pg_proc where oid = 'public.custom_access_token_hook(jsonb)'::regprocedure),
          'hook is SECURITY DEFINER');
select ok((select 'search_path=""' = any (proconfig) from pg_proc
            where oid = 'public.custom_access_token_hook(jsonb)'::regprocedure),
          'hook pins an empty search_path');
select ok(has_function_privilege('supabase_auth_admin', 'public.custom_access_token_hook(jsonb)', 'execute'),
          'supabase_auth_admin can execute the hook');
select ok(not has_function_privilege('authenticated', 'public.custom_access_token_hook(jsonb)', 'execute'),
          'authenticated cannot execute the hook');
select ok(not has_function_privilege('anon', 'public.custom_access_token_hook(jsonb)', 'execute'),
          'anon cannot execute the hook');
select is(
  (select count(*)::int
     from pg_proc p, aclexplode(p.proacl) a
    where p.oid = 'public.custom_access_token_hook(jsonb)'::regprocedure
      and a.grantee = 0),
  0, 'PUBLIC has no EXECUTE on the hook');

select * from finish();
rollback;
