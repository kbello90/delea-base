begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migration 20261104000100: the Send Email Hook's decision (TRD §6.6). Only invites for an
-- active non-demo shop and password resets for members of an active shop and of no demo shop
-- may be sent; everything else is refused with a reason. Everything here is rolled back.

create function pg_temp.decide(p_user uuid, p_action text, p_tenant uuid)
returns text
language plpgsql
as $$
declare
  v text;
begin
  perform set_config('role', 'delea_api', true);
  select coalesce(d.decision, '') || '|' || coalesce(d.business_name, '')
    into v from public.api_auth_email_decision(p_user, p_action, p_tenant) d;
  perform set_config('role', 'none', true);
  return v;
end;
$$;
grant delea_api to postgres;

insert into public.tenants (id, name, tier, is_demo, is_active) values
  ('aeaeaeae-1111-0000-0000-000000000001', 'Hook real shop', 'essential', false, true),
  ('aeaeaeae-1111-0000-0000-000000000002', 'Hook demo shop', 'essential', true, true),
  ('aeaeaeae-1111-0000-0000-000000000003', 'Hook closed shop', 'essential', false, false),
  ('aeaeaeae-1111-0000-0000-000000000004', 'Hook unbranded shop', 'essential', false, true);
insert into public.brand_config (tenant_id, business_name) values
  ('aeaeaeae-1111-0000-0000-000000000001', 'Aurora Hook Studio'),
  ('aeaeaeae-1111-0000-0000-000000000002', 'Demo Hook Studio'),
  ('aeaeaeae-1111-0000-0000-000000000003', 'Closed Hook Studio');
insert into auth.users (id, email) values
  ('aeaeaeae-0000-0000-0000-00000000000a', 'hook-member@test.local'),
  ('aeaeaeae-0000-0000-0000-00000000000b', 'hook-demo@test.local'),
  ('aeaeaeae-0000-0000-0000-00000000000c', 'hook-leftover@test.local'),
  ('aeaeaeae-0000-0000-0000-00000000000d', 'hook-closed@test.local'),
  ('aeaeaeae-0000-0000-0000-00000000000e', 'hook-both@test.local'),
  ('aeaeaeae-0000-0000-0000-00000000000f', 'hook-new@test.local');
insert into public.tenant_members (user_id, tenant_id, app_role) values
  ('aeaeaeae-0000-0000-0000-00000000000a', 'aeaeaeae-1111-0000-0000-000000000001', 'staff'),
  ('aeaeaeae-0000-0000-0000-00000000000b', 'aeaeaeae-1111-0000-0000-000000000002', 'owner'),
  ('aeaeaeae-0000-0000-0000-00000000000d', 'aeaeaeae-1111-0000-0000-000000000003', 'owner'),
  ('aeaeaeae-0000-0000-0000-00000000000e', 'aeaeaeae-1111-0000-0000-000000000001', 'cpa'),
  ('aeaeaeae-0000-0000-0000-00000000000e', 'aeaeaeae-1111-0000-0000-000000000002', 'staff');

-- Invites
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000f', 'invite', 'aeaeaeae-1111-0000-0000-000000000001'),
          'send|Aurora Hook Studio', 'invite to an active real shop: sent, with the shop''s name from brand config');
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000f', 'invite', 'aeaeaeae-1111-0000-0000-000000000004'),
          'send|Hook unbranded shop', 'no brand config: the tenant''s own name');
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000f', 'invite', 'aeaeaeae-1111-0000-0000-000000000002'),
          'demo|', 'invite to a demo shop: refused, no name');
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000f', 'invite', 'aeaeaeae-1111-0000-0000-000000000003'),
          'unknown shop|', 'invite to an inactive shop: refused');
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000f', 'invite', null),
          'unknown shop|', 'invite naming no shop: refused');
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000f', 'invite', gen_random_uuid()),
          'unknown shop|', 'invite naming a shop that doesn''t exist: refused');

-- Password resets
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000a', 'recovery', null),
          'send|', 'reset for a member of an active real shop: sent');
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000b', 'recovery', null),
          'demo|', 'reset for a demo account: refused');
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000e', 'recovery', null),
          'demo|', 'a member of a real shop AND a demo shop: refused (demo wins)');
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000c', 'recovery', null),
          'no membership|', 'a leftover account with no membership: refused');
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000d', 'recovery', null),
          'no membership|', 'a member of an inactive shop only: refused');
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000a', 'recovery', 'aeaeaeae-1111-0000-0000-000000000002'),
          'send|', 'a reset ignores any shop named in the user''s metadata');
select is(pg_temp.decide(gen_random_uuid(), 'recovery', null), 'no account|', 'no such account: refused');
select is(pg_temp.decide(null, 'recovery', null), 'no account|', 'no user id: refused');

-- Every other Auth email type, even for a member of a real shop
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000a', t, 'aeaeaeae-1111-0000-0000-000000000001'),
          'type not used|', format('%s: refused', t))
  from unnest(array['magiclink', 'email_change', 'signup', 'email', 'reauthentication',
                    'password_changed_notification', 'email_changed_notification',
                    'identity_linked_notification', 'mfa_factor_enrolled_notification',
                    'INVITE', '', 'recovery ']) t;
select is(pg_temp.decide('aeaeaeae-0000-0000-0000-00000000000a', null, null), 'type not used|', 'no type: refused');

-- Only delea_api runs it
select ok(not has_function_privilege(r, 'public.api_auth_email_decision(uuid, text, uuid)', 'execute'),
          format('%s can''t run the decision', r))
  from unnest(array['anon', 'authenticated', 'service_role', 'storefront_writer', 'email_worker']) r;
select ok(has_function_privilege('delea_api', 'public.api_auth_email_decision(uuid, text, uuid)', 'execute'),
          'delea_api runs it');
select ok((select prosecdef and proconfig @> array['search_path=""'] and provolatile = 's'
             from pg_proc where oid = 'public.api_auth_email_decision(uuid, text, uuid)'::regprocedure),
          'SECURITY DEFINER, empty search_path, stable (reads only)');

select * from finish();
rollback;
