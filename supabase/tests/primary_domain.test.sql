begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migration 20261103000200: a shop's primary website address (hosted-demo plan step 2).
-- Everything here is rolled back.

-- Backfill: every shop with a storefront domain has exactly one primary (seed tenants A, B
-- and the showcase when loaded), and only storefront domains are primary.
select is((select count(*)::int from public.tenants t
            where exists (select 1 from public.tenant_domains d
                           where d.tenant_id = t.id and d.kind = 'storefront')
              and (select count(*) from public.tenant_domains d
                    where d.tenant_id = t.id and d.is_primary) <> 1),
          0, 'every shop with a storefront domain has exactly one primary');
select is((select count(*)::int from public.tenant_domains where is_primary and kind <> 'storefront'),
          0, 'no admin domain is primary');
select is((select domain from public.tenant_domains
            where tenant_id = '11111111-1111-1111-1111-111111111111' and is_primary),
          'localhost:5174', 'tenant A: its storefront domain');
select is((select domain from public.tenant_domains
            where tenant_id = '22222222-2222-2222-2222-222222222222' and is_primary),
          'aurora.localhost:5174', 'tenant B: its storefront domain');

-- A throwaway shop for the rules.
insert into public.tenants (id, name, tier)
values ('99999999-0000-4000-8000-000000000201', 'Primary Domain Test', 'essential');

insert into public.tenant_domains (domain, tenant_id, kind) values
  ('pd-admin.localhost:5173', '99999999-0000-4000-8000-000000000201', 'admin');
select is((select count(*)::int from public.tenant_domains
            where tenant_id = '99999999-0000-4000-8000-000000000201' and is_primary),
          0, 'an admin domain is never made primary');

insert into public.tenant_domains (domain, tenant_id, kind) values
  ('pd-m.localhost:5174', '99999999-0000-4000-8000-000000000201', 'storefront'),
  ('pd-a.localhost:5174', '99999999-0000-4000-8000-000000000201', 'storefront');
select is((select domain from public.tenant_domains
            where tenant_id = '99999999-0000-4000-8000-000000000201' and is_primary),
          'pd-m.localhost:5174', 'the first storefront domain is primary, not the first alphabetically');

select throws_ok(
  $q$delete from public.tenant_domains where domain = 'pd-m.localhost:5174'$q$,
  '23514', 'pd-m.localhost:5174 is this shop''s primary website address. Make another storefront domain primary first (add_tenant_domain.py --primary).',
  'removing the primary is refused while another storefront domain exists');
select throws_ok(
  $q$update public.tenant_domains set kind = 'admin' where domain = 'pd-m.localhost:5174'$q$,
  '23514', null, 'turning the primary into an admin domain is refused');
select throws_ok(
  $q$update public.tenant_domains set is_primary = false where domain = 'pd-m.localhost:5174'$q$,
  '23514', null, 'unsetting the primary is refused');
select throws_ok(
  $q$update public.tenant_domains set is_primary = true where domain = 'pd-a.localhost:5174'$q$,
  '23P01', null, 'a second primary is refused');
select throws_ok(
  $q$insert into public.tenant_domains (domain, tenant_id, kind, is_primary)
     values ('pd-x.localhost:5173', '99999999-0000-4000-8000-000000000201', 'admin', true)$q$,
  '23514', null, 'an admin domain can''t be primary');

select lives_ok(
  $q$update public.tenant_domains set is_primary = (domain = 'pd-a.localhost:5174')
      where tenant_id = '99999999-0000-4000-8000-000000000201' and kind = 'storefront'$q$,
  'the primary moves in one statement');
select is((select domain from public.tenant_domains
            where tenant_id = '99999999-0000-4000-8000-000000000201' and is_primary),
          'pd-a.localhost:5174', 'the new primary');
select lives_ok($q$delete from public.tenant_domains where domain = 'pd-m.localhost:5174'$q$,
                'a domain that is not primary can go');
select lives_ok($q$delete from public.tenant_domains where domain = 'pd-a.localhost:5174'$q$,
                'the last storefront domain can go: the shop has no website address');
select is((select count(*)::int from public.tenant_domains
            where tenant_id = '99999999-0000-4000-8000-000000000201' and is_primary),
          0, 'no primary without a storefront domain');

-- A customer email for a shop with no website address is kept as not sent, with the reason
-- (migration 20261103000300); an owner's test email is not affected.
create temp table nw1 as select public.email_enqueue('99999999-0000-4000-8000-000000000201',
  'customer', 'order_received', 'pd-test:customer', 'shopper@example.com', 'Order received',
  '<p>x</p>', 'x') as id;
select is((select m.status || ': ' || m.last_error from public.email_messages m join nw1 using (id)),
          'suppressed: no website address', 'a customer email without a website address is suppressed');
insert into public.tenant_domains (domain, tenant_id, kind) values
  ('pd-w.localhost:5174', '99999999-0000-4000-8000-000000000201', 'storefront');
create temp table nw2 as select public.email_enqueue('99999999-0000-4000-8000-000000000201',
  'customer', 'order_received', 'pd-test:customer-2', 'shopper@example.com', 'Order received',
  '<p>x</p>', 'x') as id;
select is((select m.status from public.email_messages m join nw2 using (id)),
          'queued', 'with a primary domain it is queued');
delete from public.tenant_domains where domain = 'pd-w.localhost:5174';

insert into public.tenant_domains (domain, tenant_id, kind) values
  ('pd-b.localhost:5174', '99999999-0000-4000-8000-000000000201', 'storefront'),
  ('pd-c.localhost:5174', '99999999-0000-4000-8000-000000000201', 'storefront');
select lives_ok($q$delete from public.tenants where id = '99999999-0000-4000-8000-000000000201'$q$,
                'deleting the shop takes its domains, primary included');
select is((select count(*)::int from public.tenant_domains
            where tenant_id = '99999999-0000-4000-8000-000000000201'),
          0, 'no domain left');

-- Nobody but the administrative login writes domains (unchanged), and the trigger
-- functions are not callable by app roles.
select ok(not has_function_privilege('authenticated', 'public.tenant_domains_primary_before()', 'execute'),
          'authenticated can''t execute the trigger function');
select ok(not has_function_privilege('delea_api', 'public.tenant_domains_primary_after()', 'execute'),
          'delea_api can''t execute the trigger function');

select * from finish();
rollback;
