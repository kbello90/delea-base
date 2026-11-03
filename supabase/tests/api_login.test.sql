begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migration 20261103000100: the API's least-privilege login, delea_api (TRD §6.6).
-- Everything here is rolled back.

-- Runs a statement as delea_api and returns its single value as text; the role is restored.
create function pg_temp.as_api(p_sql text)
returns text
language plpgsql
as $$
declare
  v text;
begin
  perform set_config('role', 'delea_api', true);
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

-- The test session (postgres) may act as delea_api for these checks only.
grant delea_api to postgres;

-- ===========================================================================
-- 1. What the login is
-- ===========================================================================
select is(
  (select row(rolsuper, rolinherit, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication)::text
     from pg_roles where rolname = 'delea_api'),
  row(false, false, false, false, false, false)::text,
  'delea_api: no superuser, inherits nothing, no RLS bypass, no role or database creation, no replication');

select set_eq(
  $$select r.rolname::text from pg_auth_members am
      join pg_roles r on r.oid = am.roleid
      join pg_roles m on m.oid = am.member
     where m.rolname = 'delea_api'$$,
  array['authenticated', 'ai_readonly', 'storefront_reader', 'storefront_writer',
        'payments_writer', 'email_worker'],
  'delea_api may switch into exactly the six roles the API uses');

select is(
  (select count(*)::integer from information_schema.role_table_grants where grantee = 'delea_api'),
  0, 'delea_api holds no table privilege of its own');

-- ===========================================================================
-- 2. On the bare login: no data at all
-- ===========================================================================
select throws_ok($$select pg_temp.as_api('select count(*) from public.tenants')$$,
                 '42501', null, 'can''t read tenants');
select throws_ok($$select pg_temp.as_api('select count(*) from public.tenant_members')$$,
                 '42501', null, 'can''t read memberships');
select throws_ok($$select pg_temp.as_api('select count(*) from auth.users')$$,
                 '42501', null, 'can''t read Auth users');
select throws_ok($$select pg_temp.as_api('select count(*) from public.payments')$$,
                 '42501', null, 'can''t read payments');
select throws_ok($$select pg_temp.as_api('select count(*) from public.password_reset_attempts')$$,
                 '42501', null, 'can''t read the password-reset attempts');
select throws_ok($$select pg_temp.as_api('update public.tenants set is_demo = false')$$,
                 '42501', null, 'can''t touch tenants.is_demo');
select throws_ok($$select pg_temp.as_api('select count(*) from public.invoices')$$,
                 '42501', null, 'can''t read invoices without switching to authenticated');

-- Only the six roles: never postgres, service_role or the platform's own. (SET ROLE is decided
-- by the session user, which is postgres in this test, so membership is checked directly here;
-- backend/tests/integration/test_api_login.py signs in as delea_api and tries the switches.)
select ok(not pg_has_role('delea_api', r, 'member'), format('delea_api is not a member of %s', r))
  from unnest(array['postgres', 'service_role', 'supabase_admin', 'authenticator', 'anon',
                    'pgsodium_keyholder', 'supabase_storage_admin', 'supabase_auth_admin']) r
 where exists (select 1 from pg_roles where rolname = r);

-- Functions: the membership re-check and its own api_* functions; nothing that writes money.
select is(pg_temp.as_api(
  'select public.is_current_member(''00000000-0000-0000-0000-000000000000'', ''11111111-1111-1111-1111-111111111111'', ''owner'')'),
  'false', 'runs the membership re-check');
select set_eq(
  $$select p.oid::regprocedure::text from pg_proc p
     where p.prosecdef
       and p.pronamespace in ('public'::regnamespace, 'storefront'::regnamespace, 'ai'::regnamespace)
       and has_function_privilege('delea_api', p.oid, 'execute')$$,
  array['is_current_member(uuid,uuid,text)',
        'api_password_reset_attempt(text,text,integer,interval,interval,interval)',
        'api_account_is_demo(text)', 'api_owner_sign_in_email(uuid,uuid)',
        'api_open_card_sessions(uuid,uuid)', 'api_card_session_for_link(uuid,text)'],
  'on its own it runs exactly these SECURITY DEFINER functions: no Stripe, checkout or email ones');
select ok(not has_function_privilege('delea_api', 'public.stripe_apply_refund(text,text,text,boolean,text,text,bigint,text,text,timestamptz)', 'execute'),
          'the Stripe refund function needs payments_writer');
select throws_ok(
  $$select pg_temp.as_api('select public.storefront_place_order(null, null, null, null, null, null, null, null, null, null, null)')$$,
  null, null, 'can''t run the checkout functions without switching to storefront_writer');

-- ===========================================================================
-- 3. The api_* functions: what they return, and only delea_api may run them
-- ===========================================================================
insert into auth.users (id, email) values
  ('eeeeeeee-0000-0000-0000-00000000000a', 'API-Owner-A@test.local'),
  ('eeeeeeee-0000-0000-0000-00000000000b', 'api-staff-a@test.local'),
  ('eeeeeeee-0000-0000-0000-00000000000c', 'api-demo@test.local');
insert into public.tenant_members (user_id, tenant_id, app_role) values
  ('eeeeeeee-0000-0000-0000-00000000000a', '11111111-1111-1111-1111-111111111111', 'owner'),
  ('eeeeeeee-0000-0000-0000-00000000000b', '11111111-1111-1111-1111-111111111111', 'staff');
insert into public.tenants (id, name, tier, is_demo) values
  ('eeeeeeee-1111-0000-0000-000000000001', 'API demo shop', 'essential', true);
insert into public.tenant_members (user_id, tenant_id, app_role) values
  ('eeeeeeee-0000-0000-0000-00000000000c', 'eeeeeeee-1111-0000-0000-000000000001', 'owner');

select is(pg_temp.as_api('select public.api_owner_sign_in_email(''eeeeeeee-0000-0000-0000-00000000000a'', ''11111111-1111-1111-1111-111111111111'')'),
          'API-Owner-A@test.local', 'an owner''s own sign-in email');
select is(pg_temp.as_api('select public.api_owner_sign_in_email(''eeeeeeee-0000-0000-0000-00000000000b'', ''11111111-1111-1111-1111-111111111111'')'),
          null, 'nothing for staff');
select is(pg_temp.as_api('select public.api_owner_sign_in_email(''eeeeeeee-0000-0000-0000-00000000000a'', ''22222222-2222-2222-2222-222222222222'')'),
          null, 'nothing for another tenant');

select is(pg_temp.as_api('select public.api_account_is_demo(''api-owner-a@test.local'')'), 'false',
          'a real account (any case) may get a reset email');
select is(pg_temp.as_api('select public.api_account_is_demo(''api-demo@test.local'')'), 'true',
          'a demo account may not');
select is(pg_temp.as_api('select public.api_account_is_demo(''nobody@test.local'')'), null,
          'no account: null, nothing else said');

-- Password reset: send, then cooling down for the same email, then too many from one address.
create function pg_temp.attempt(p_email text, p_ip text)
returns text
language sql
as $$
  select pg_temp.as_api(format(
    'select public.api_password_reset_attempt(%L, %L, 3, ''15 minutes'', ''60 seconds'', ''1 day'')',
    encode(extensions.digest(p_email, 'sha256'), 'hex'), encode(extensions.digest(p_ip, 'sha256'), 'hex')));
$$;
select is(pg_temp.attempt('a@test.local', '203.0.113.7'), 'send', 'the first request sends');
select is(pg_temp.attempt('a@test.local', '203.0.113.7'), 'cooling_down', 'the same email again: nothing sent');
select is(pg_temp.attempt('b@test.local', '203.0.113.7'), 'send', 'another email from that address sends');
select is(pg_temp.attempt('c@test.local', '203.0.113.7'), 'too_many', 'the fourth from one address: too many');
select is(pg_temp.attempt('c@test.local', '198.51.100.9'), 'send', 'another address isn''t limited');
select is((select count(*)::integer from public.password_reset_attempts
            where ip_hash = encode(extensions.digest('203.0.113.7', 'sha256'), 'hex')),
          3, 'a refused request records nothing');
select throws_ok(
  $$select pg_temp.as_api('select public.api_password_reset_attempt(''not-a-hash'', ''x'', 5, ''1 minute'', ''1 minute'', ''1 day'')')$$,
  '22023', null, 'only hashes are accepted, never an address');

select is(pg_temp.as_api('select count(*) from public.api_open_card_sessions(''11111111-1111-1111-1111-111111111111'', gen_random_uuid())'),
          '0', 'open card sessions: runs');
select is(pg_temp.as_api('select count(*) from public.api_card_session_for_link(''11111111-1111-1111-1111-111111111111'', ''https://checkout.stripe.com/x'')'),
          '0', 'session for a link: runs');

-- Nobody else runs them.
select ok(not has_function_privilege(r, f, 'execute'), format('%s can''t run %s', r, f))
  from unnest(array['anon', 'authenticated', 'service_role']) r,
       unnest(array[
         'public.api_password_reset_attempt(text, text, integer, interval, interval, interval)',
         'public.api_account_is_demo(text)',
         'public.api_owner_sign_in_email(uuid, uuid)',
         'public.api_open_card_sessions(uuid, uuid)',
         'public.api_card_session_for_link(uuid, text)']) f;
select ok((select bool_and(prosecdef and proconfig @> array['search_path=""'])
             from pg_proc where proname like 'api\_%' and pronamespace = 'public'::regnamespace),
          'every api_* function is SECURITY DEFINER with an empty search_path');

select * from finish();
rollback;
