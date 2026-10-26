begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migration 20261025000100 (Team slice). Everything here is rolled back.

create function pg_temp.set_claims(p_sub uuid, p_tenant uuid, p_app_role text)
returns void
language plpgsql
as $$
begin
  perform set_config('request.jwt.claims', jsonb_strip_nulls(jsonb_build_object(
    'sub', p_sub, 'role', 'authenticated',
    'tenant_id', p_tenant, 'app_role', p_app_role))::text, true);
end;
$$;

-- Runs one statement as `authenticated`; the role is always restored.
create function pg_temp.exec_as_authenticated(p_sql text)
returns void
language plpgsql
as $$
begin
  perform set_config('role', 'authenticated', true);
  begin
    execute p_sql;
  exception when others then
    perform set_config('role', 'none', true);
    raise;
  end;
  perform set_config('role', 'none', true);
end;
$$;

-- Runs a single-value query as `authenticated`.
create function pg_temp.value_as_authenticated(p_sql text)
returns text
language plpgsql
as $$
declare
  v text;
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

-- Tenant T (not a demo) and demo tenant D, each with company settings.
insert into public.tenants (id, name) values ('eeeeeeee-0000-0000-0000-0000000000a1', 'Team T');
insert into public.tenants (id, name, is_demo) values ('eeeeeeee-0000-0000-0000-0000000000d1', 'Team D', true);
insert into public.company_settings (tenant_id, default_tax_rate_bps) values
  ('eeeeeeee-0000-0000-0000-0000000000a1', 825),
  ('eeeeeeee-0000-0000-0000-0000000000d1', 0);

insert into auth.users (id, email, email_confirmed_at, invited_at) values
  ('eeeeeeee-0000-0000-0000-000000000001', 'team-owner@test.local', now(), null),
  ('eeeeeeee-0000-0000-0000-000000000002', 'team-staff@test.local', now(), null),
  ('eeeeeeee-0000-0000-0000-000000000003', 'team-invited@test.local', null, now() - interval '2 days'),
  ('eeeeeeee-0000-0000-0000-000000000004', 'team-new@test.local', null, now()),
  ('eeeeeeee-0000-0000-0000-000000000005', 'team-demo-owner@test.local', now(), null),
  ('eeeeeeee-0000-0000-0000-000000000006', 'team-elsewhere@test.local', now(), null),
  ('eeeeeeee-0000-0000-0000-000000000007', 'team-orphan@test.local', now(), null);

-- Provisioning-style setup as postgres (the guard trusts it).
insert into public.tenant_members (user_id, tenant_id, app_role, invited_by) values
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', 'owner', null),
  ('eeeeeeee-0000-0000-0000-000000000002', 'eeeeeeee-0000-0000-0000-0000000000a1', 'staff', 'eeeeeeee-0000-0000-0000-000000000001'),
  ('eeeeeeee-0000-0000-0000-000000000003', 'eeeeeeee-0000-0000-0000-0000000000a1', 'cpa', 'eeeeeeee-0000-0000-0000-000000000001'),
  ('eeeeeeee-0000-0000-0000-000000000005', 'eeeeeeee-0000-0000-0000-0000000000d1', 'owner', null),
  ('eeeeeeee-0000-0000-0000-000000000006', '22222222-2222-2222-2222-222222222222', 'staff', null);
select pass('the guard lets an administrative connection add an owner');

-- ===========================================================================
-- is_current_member
-- ===========================================================================
select ok(public.is_current_member('eeeeeeee-0000-0000-0000-000000000002', 'eeeeeeee-0000-0000-0000-0000000000a1', 'staff'),
          'a member with that role is current');
select ok(not public.is_current_member('eeeeeeee-0000-0000-0000-000000000002', 'eeeeeeee-0000-0000-0000-0000000000a1', 'owner'),
          'the same member with another role is not current');
select ok(not public.is_current_member('eeeeeeee-0000-0000-0000-000000000007', 'eeeeeeee-0000-0000-0000-0000000000a1', 'staff'),
          'a user without a membership is not current');
select ok(not has_function_privilege('authenticated', 'public.is_current_member(uuid, uuid, text)', 'execute'),
          'authenticated can''t call is_current_member directly');
select ok(not has_function_privilege('anon', 'public.is_current_member(uuid, uuid, text)', 'execute'),
          'anon can''t call is_current_member');

-- ===========================================================================
-- current_tax_rate_bps / current_tenant_currency refuse stale claims
-- ===========================================================================
select pg_temp.set_claims('eeeeeeee-0000-0000-0000-000000000002', 'eeeeeeee-0000-0000-0000-0000000000a1', 'staff');
select is(pg_temp.value_as_authenticated('select public.current_tax_rate_bps()'), '825',
          'a current staff member reads the tax rate');
select pg_temp.set_claims('eeeeeeee-0000-0000-0000-000000000002', 'eeeeeeee-0000-0000-0000-0000000000a1', 'owner');
select throws_ok($$select pg_temp.value_as_authenticated('select public.current_tax_rate_bps()')$$, '42501',
                 'not a member of this tenant with this role', 'tax rate: a token with a changed role is refused');
select throws_ok($$select pg_temp.value_as_authenticated('select public.current_tenant_currency()')$$, '42501',
                 'not a member of this tenant with this role', 'currency: a token with a changed role is refused');
select pg_temp.set_claims('eeeeeeee-0000-0000-0000-000000000007', 'eeeeeeee-0000-0000-0000-0000000000a1', 'staff');
select throws_ok($$select pg_temp.value_as_authenticated('select public.current_tax_rate_bps()')$$, '42501',
                 'not a member of this tenant with this role', 'tax rate: a removed member''s token is refused');

-- ===========================================================================
-- Inactive tenant: refused everywhere, and the hook issues no claims
-- ===========================================================================
update public.tenants set is_active = false where id = 'eeeeeeee-0000-0000-0000-0000000000a1';
select ok(not public.is_current_member('eeeeeeee-0000-0000-0000-000000000002', 'eeeeeeee-0000-0000-0000-0000000000a1', 'staff'),
          'a member of an inactive tenant is not current');
select pg_temp.set_claims('eeeeeeee-0000-0000-0000-000000000002', 'eeeeeeee-0000-0000-0000-0000000000a1', 'staff');
select throws_ok($$select pg_temp.value_as_authenticated('select public.current_tax_rate_bps()')$$, '42501',
                 'not a member of this tenant with this role', 'tax rate: an inactive tenant is refused');
select throws_ok($$select pg_temp.value_as_authenticated('select public.current_tenant_currency()')$$, '42501',
                 'not a member of this tenant with this role', 'currency: an inactive tenant is refused');
select is(public.custom_access_token_hook(jsonb_build_object(
            'user_id', 'eeeeeeee-0000-0000-0000-000000000002', 'claims', '{"tenant_id": "x", "app_role": "staff"}'::jsonb))
          -> 'claims', '{}'::jsonb,
          'the hook gives a member of an inactive tenant no tenant_id or app_role');
update public.tenants set is_active = true where id = 'eeeeeeee-0000-0000-0000-0000000000a1';
select is(public.custom_access_token_hook(jsonb_build_object(
            'user_id', 'eeeeeeee-0000-0000-0000-000000000002', 'claims', '{}'::jsonb)) -> 'claims' ->> 'app_role', 'staff',
          'the hook gives the claims again once the tenant is active');

-- ===========================================================================
-- tenant_member_list / account_memberships
-- ===========================================================================
select pg_temp.set_claims('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', 'owner');
select is(pg_temp.value_as_authenticated('select count(*) from public.tenant_member_list()'), '3',
          'the owner lists the three members of their tenant');
select is(pg_temp.value_as_authenticated(
            $$select string_agg(email || ':' || app_role || ':' || status || ':' || invite_expired, ',' order by email)
                from public.tenant_member_list()$$),
          'team-invited@test.local:cpa:invited:true,team-owner@test.local:owner:active:false,team-staff@test.local:staff:active:false',
          'the list has each member''s email, role, status and whether the invite expired');
select is(pg_temp.value_as_authenticated(
            $$select member_here::text || member_elsewhere::text from public.account_memberships(' Team-Staff@test.local ')$$),
          'truefalse', 'account_memberships: a member here (email trimmed, any case)');
select is(pg_temp.value_as_authenticated(
            $$select member_here::text || member_elsewhere::text from public.account_memberships('team-elsewhere@test.local')$$),
          'falsetrue', 'account_memberships: a member of another tenant, without naming it');
select is(pg_temp.value_as_authenticated(
            $$select confirmed::text || member_here::text || member_elsewhere::text from public.account_memberships('team-orphan@test.local')$$),
          'truefalsefalse', 'account_memberships: an account without a membership');
select is(pg_temp.value_as_authenticated(
            $$select count(*) from public.account_memberships('nobody@test.local')$$),
          '0', 'account_memberships: no account, no row');

select pg_temp.set_claims('eeeeeeee-0000-0000-0000-000000000002', 'eeeeeeee-0000-0000-0000-0000000000a1', 'staff');
select throws_ok($$select pg_temp.value_as_authenticated('select count(*) from public.tenant_member_list()')$$, '42501',
                 'only the owner can manage the team', 'staff can''t list the team');
select throws_ok($$select pg_temp.value_as_authenticated($q$select count(*) from public.account_memberships('a@b.c')$q$)$$, '42501',
                 'only the owner can manage the team', 'staff can''t look up accounts');
select pg_temp.set_claims('eeeeeeee-0000-0000-0000-000000000002', 'eeeeeeee-0000-0000-0000-0000000000a1', 'owner');
select throws_ok($$select pg_temp.value_as_authenticated('select count(*) from public.tenant_member_list()')$$, '42501',
                 'not a member of this tenant with this role', 'a token claiming owner without the membership is refused');

-- ===========================================================================
-- Guard: owner of T as `authenticated`
-- ===========================================================================
select pg_temp.set_claims('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', 'owner');

select lives_ok($$select pg_temp.exec_as_authenticated($q$
  insert into public.tenant_members (user_id, tenant_id, app_role, invited_by)
  values ('eeeeeeee-0000-0000-0000-000000000004', 'eeeeeeee-0000-0000-0000-0000000000a1', 'staff',
          'eeeeeeee-0000-0000-0000-000000000001')$q$)$$, 'the owner adds a staff member');
select throws_ok($$select pg_temp.exec_as_authenticated($q$
  insert into public.tenant_members (user_id, tenant_id, app_role)
  values ('eeeeeeee-0000-0000-0000-000000000007', 'eeeeeeee-0000-0000-0000-0000000000a1', 'owner')$q$)$$,
  '42501', 'Owners can''t be invited. Invite staff or an accountant.', 'the owner can''t add an owner');
select throws_ok($$select pg_temp.exec_as_authenticated($q$
  insert into public.tenant_members (user_id, tenant_id, app_role)
  values ('eeeeeeee-0000-0000-0000-000000000007', '22222222-2222-2222-2222-222222222222', 'staff')$q$)$$,
  '42501', 'Not allowed.', 'the owner can''t add a member to another tenant');
select lives_ok($$select pg_temp.exec_as_authenticated($q$
  update public.tenant_members set app_role = 'cpa'
   where user_id = 'eeeeeeee-0000-0000-0000-000000000004'$q$)$$, 'the owner changes staff to cpa');
select throws_ok($$select pg_temp.exec_as_authenticated($q$
  update public.tenant_members set app_role = 'owner'
   where user_id = 'eeeeeeee-0000-0000-0000-000000000004'$q$)$$,
  '42501', 'Members can''t be made owners.', 'the owner can''t promote a member to owner');
select throws_ok($$select pg_temp.exec_as_authenticated($q$
  update public.tenant_members set user_id = 'eeeeeeee-0000-0000-0000-000000000007'
   where user_id = 'eeeeeeee-0000-0000-0000-000000000004'$q$)$$,
  '42501', 'Only a member''s role can change.', 'only the role can change');
select throws_ok($$select pg_temp.exec_as_authenticated($q$
  delete from public.tenant_members where user_id = 'eeeeeeee-0000-0000-0000-000000000001'$q$)$$,
  'P0001', 'A business must keep at least one owner.', 'the last owner can''t be removed');
select throws_ok($$select pg_temp.exec_as_authenticated($q$
  update public.tenant_members set app_role = 'staff' where user_id = 'eeeeeeee-0000-0000-0000-000000000001'$q$)$$,
  'P0001', 'A business must keep at least one owner.', 'the last owner can''t be demoted');
select lives_ok($$select pg_temp.exec_as_authenticated($q$
  delete from public.tenant_members where user_id = 'eeeeeeee-0000-0000-0000-000000000004'$q$)$$,
  'the owner removes a member');

-- With a second owner (provisioning only), one owner may be demoted.
insert into public.tenant_members (user_id, tenant_id, app_role)
values ('eeeeeeee-0000-0000-0000-000000000007', 'eeeeeeee-0000-0000-0000-0000000000a1', 'owner');
select lives_ok($$select pg_temp.exec_as_authenticated($q$
  update public.tenant_members set app_role = 'staff' where user_id = 'eeeeeeee-0000-0000-0000-000000000007'$q$)$$,
  'an owner can be demoted while another owner remains');

-- ===========================================================================
-- Guard: demo tenant
-- ===========================================================================
select pg_temp.set_claims('eeeeeeee-0000-0000-0000-000000000005', 'eeeeeeee-0000-0000-0000-0000000000d1', 'owner');
select throws_ok($$select pg_temp.exec_as_authenticated($q$
  insert into public.tenant_members (user_id, tenant_id, app_role)
  values ('eeeeeeee-0000-0000-0000-000000000004', 'eeeeeeee-0000-0000-0000-0000000000d1', 'staff')$q$)$$,
  '42501', 'Team changes are turned off in the demo.', 'nobody can be added to a demo tenant');

-- ===========================================================================
-- invited_by doesn't block deleting the inviter's Auth user
-- ===========================================================================
delete from public.tenant_members where user_id = 'eeeeeeee-0000-0000-0000-000000000001';
select lives_ok($$delete from auth.users where id = 'eeeeeeee-0000-0000-0000-000000000001'$$,
                'the inviter''s Auth user can be deleted');
select is((select invited_by from public.tenant_members where user_id = 'eeeeeeee-0000-0000-0000-000000000002'),
          null, 'the invited member''s invited_by becomes null');

-- ===========================================================================
-- password_reset_attempts: no app access
-- ===========================================================================
select ok(not has_table_privilege('anon', 'public.password_reset_attempts', 'select'), 'anon can''t read reset attempts');
select ok(not has_table_privilege('authenticated', 'public.password_reset_attempts', 'select'), 'authenticated can''t read reset attempts');
select ok(not has_table_privilege('authenticated', 'public.password_reset_attempts', 'insert'), 'authenticated can''t write reset attempts');

select * from finish();
rollback;
