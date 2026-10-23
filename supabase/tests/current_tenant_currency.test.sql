begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migration 20261020001400: current_tenant_currency(). Everything here is rolled back.

-- Claims as the access token hook would issue them, for a given user.
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

-- Runs the function as `authenticated` and returns its value; the role is always restored.
create function pg_temp.currency_as_authenticated()
returns text
language plpgsql
as $$
declare
  v text;
begin
  perform set_config('role', 'authenticated', true);
  begin
    v := public.current_tenant_currency();
  exception when others then
    perform set_config('role', 'none', true);
    raise;
  end;
  perform set_config('role', 'none', true);
  return v;
end;
$$;

-- Members (tenant A: owner, staff, cpa; tenant B: owner) and a user with no membership.
insert into auth.users (id, email) values
  ('dddddddd-0000-0000-0000-00000000000a', 'cur-owner-a@test.local'),
  ('dddddddd-0000-0000-0000-00000000000b', 'cur-staff-a@test.local'),
  ('dddddddd-0000-0000-0000-00000000000c', 'cur-cpa-a@test.local'),
  ('dddddddd-0000-0000-0000-00000000000d', 'cur-owner-b@test.local'),
  ('dddddddd-0000-0000-0000-00000000000e', 'cur-nobody@test.local');
insert into public.tenant_members (user_id, tenant_id, app_role) values
  ('dddddddd-0000-0000-0000-00000000000a', '11111111-1111-1111-1111-111111111111', 'owner'),
  ('dddddddd-0000-0000-0000-00000000000b', '11111111-1111-1111-1111-111111111111', 'staff'),
  ('dddddddd-0000-0000-0000-00000000000c', '11111111-1111-1111-1111-111111111111', 'cpa'),
  ('dddddddd-0000-0000-0000-00000000000d', '22222222-2222-2222-2222-222222222222', 'owner');

-- Tenant B gets a different currency so a leak between tenants would show. B has data, so
-- the currency guard (migration 20261023000200, tested in settings_currency_logo.test.sql)
-- would refuse; triggers are switched off for this one setup statement only.
set local session_replication_role = replica;
update public.company_settings set currency = 'EUR'
 where tenant_id = '22222222-2222-2222-2222-222222222222';
set local session_replication_role = origin;

-- ===========================================================================
-- Readers get their own tenant's currency
-- ===========================================================================
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000a', '11111111-1111-1111-1111-111111111111', 'owner');
select is(pg_temp.currency_as_authenticated(), 'USD', 'owner of A reads USD');
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000b', '11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.currency_as_authenticated(), 'USD', 'staff of A reads USD');
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000c', '11111111-1111-1111-1111-111111111111', 'cpa');
select is(pg_temp.currency_as_authenticated(), 'USD', 'cpa of A reads USD');
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000d', '22222222-2222-2222-2222-222222222222', 'owner');
select is(pg_temp.currency_as_authenticated(), 'EUR', 'owner of B reads tenant B''s EUR, not A''s USD');

-- ===========================================================================
-- Refusals: 42501
-- ===========================================================================
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000a', '11111111-1111-1111-1111-111111111111', null);
select throws_ok('select pg_temp.currency_as_authenticated()', '42501',
                 'only owner, staff or cpa can read the currency', 'a token without app_role is refused');
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000a', '11111111-1111-1111-1111-111111111111', 'admin');
select throws_ok('select pg_temp.currency_as_authenticated()', '42501',
                 'only owner, staff or cpa can read the currency', 'an unknown app_role is refused');
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000a', null, 'owner');
select throws_ok('select pg_temp.currency_as_authenticated()', '42501',
                 'no tenant in the token', 'a token without tenant_id is refused');
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000a', '', 'owner');
select throws_ok('select pg_temp.currency_as_authenticated()', '42501',
                 'no tenant in the token', 'an empty tenant_id is refused');
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000a', 'not-a-uuid', 'owner');
select throws_ok('select pg_temp.currency_as_authenticated()', '42501',
                 'the token''s tenant is not valid', 'a malformed tenant_id is 42501, not 22P02');
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000e', '11111111-1111-1111-1111-111111111111', 'owner');
select throws_ok('select pg_temp.currency_as_authenticated()', '42501',
                 'not a member of this tenant with this role', 'a non-member token is refused');
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000a', '22222222-2222-2222-2222-222222222222', 'owner');
select throws_ok('select pg_temp.currency_as_authenticated()', '42501',
                 'not a member of this tenant with this role', 'a member of A presenting tenant B is refused');
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000b', '11111111-1111-1111-1111-111111111111', 'owner');
select throws_ok('select pg_temp.currency_as_authenticated()', '42501',
                 'not a member of this tenant with this role', 'staff presenting app_role owner is refused');

-- A membership removed after the token was issued.
delete from public.tenant_members where user_id = 'dddddddd-0000-0000-0000-00000000000c';
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000c', '11111111-1111-1111-1111-111111111111', 'cpa');
select throws_ok('select pg_temp.currency_as_authenticated()', '42501',
                 'not a member of this tenant with this role', 'a removed member''s still-valid token is refused');

-- ===========================================================================
-- Missing settings: P0001
-- ===========================================================================
insert into public.tenants (id, name) values ('33333333-3333-3333-3333-333333333333', 'No Settings');
insert into public.tenant_members (user_id, tenant_id, app_role) values
  ('dddddddd-0000-0000-0000-00000000000e', '33333333-3333-3333-3333-333333333333', 'owner');
select pg_temp.set_claims('dddddddd-0000-0000-0000-00000000000e', '33333333-3333-3333-3333-333333333333', 'owner');
select throws_ok('select pg_temp.currency_as_authenticated()', 'P0001',
                 'company settings are missing for this tenant', 'a tenant without settings gets P0001');

-- ===========================================================================
-- Privileges and definition
-- ===========================================================================
select ok(has_function_privilege('authenticated', 'public.current_tenant_currency()', 'execute'),
          'authenticated can execute current_tenant_currency');
select ok(not has_function_privilege('anon', 'public.current_tenant_currency()', 'execute'),
          'anon cannot execute current_tenant_currency');
select ok(not has_function_privilege('service_role', 'public.current_tenant_currency()', 'execute'),
          'service_role cannot execute current_tenant_currency');
select ok(not has_function_privilege('ai_readonly', 'public.current_tenant_currency()', 'execute'),
          'ai_readonly cannot execute current_tenant_currency');
select is((select count(*)::int from pg_proc p, aclexplode(p.proacl) a
            where p.oid = 'public.current_tenant_currency()'::regprocedure and a.grantee = 0),
          0, 'PUBLIC has no EXECUTE on current_tenant_currency');
select ok((select prosecdef and 'search_path=""' = any (proconfig) from pg_proc
            where oid = 'public.current_tenant_currency()'::regprocedure),
          'current_tenant_currency is SECURITY DEFINER with an empty search_path');

select * from finish();
rollback;
