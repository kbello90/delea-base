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

-- Tenant isolation: an owner of tenant A cannot read or write tenant B rows on any table,
-- and composite foreign keys reject references that cross tenants.
-- Seed tenants: A = 1111…, B = 2222… (supabase/seed.sql).

insert into auth.users (id, email) values
  ('aaaaaaaa-0000-0000-0000-00000000000a', 'owner-a@test.local'),
  ('bbbbbbbb-0000-0000-0000-00000000000b', 'owner-b@test.local');

insert into public.tenant_members (user_id, tenant_id, app_role) values
  ('aaaaaaaa-0000-0000-0000-00000000000a', '11111111-1111-1111-1111-111111111111', 'owner'),
  ('bbbbbbbb-0000-0000-0000-00000000000b', '22222222-2222-2222-2222-222222222222', 'owner');

create temp table scoped_tables (tbl text primary key);
insert into scoped_tables values
  ('tenant_members'), ('tenant_domains'), ('brand_config'), ('company_settings'),
  ('products'), ('materials'), ('product_materials'),
  ('customers'), ('invoices'), ('invoice_line_items'),
  ('expenses'), ('automation_rules'), ('reorder_list');

-- Sanity: both tenants have rows in every tenant-scoped table, so zero counts below mean
-- "filtered", not "empty".
select cmp_ok(
  pg_temp.count_as('postgres', format(
    'select count(*) from public.%I where tenant_id = %L', tbl, '22222222-2222-2222-2222-222222222222')),
  '>', 0::bigint, tbl || ': tenant B has rows')
from scoped_tables order by tbl;

select cmp_ok(
  pg_temp.count_as('postgres', format(
    'select count(*) from public.%I where tenant_id = %L', tbl, '11111111-1111-1111-1111-111111111111')),
  '>', 0::bigint, tbl || ': tenant A has rows')
from scoped_tables order by tbl;

-- ---------------------------------------------------------------------------
-- Owner of tenant A
-- ---------------------------------------------------------------------------
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');

select cmp_ok(
  pg_temp.count_as('authenticated', format(
    'select count(*) from public.%I where tenant_id = %L', tbl, '11111111-1111-1111-1111-111111111111')),
  '>', 0::bigint, tbl || ': owner A sees own rows')
from scoped_tables order by tbl;

select is(
  pg_temp.count_as('authenticated', format(
    'select count(*) from public.%I where tenant_id is distinct from %L', tbl, '11111111-1111-1111-1111-111111111111')),
  0::bigint, tbl || ': owner A sees no other tenant rows')
from scoped_tables order by tbl;

select is(
  pg_temp.count_as('authenticated', $$select count(*) from public.tenants where id <> '11111111-1111-1111-1111-111111111111'$$),
  0::bigint, 'tenants: owner A sees no other tenant');

select is(
  pg_temp.write_as('authenticated', format(
    'update public.%I set tenant_id = tenant_id where tenant_id = %L', tbl, '22222222-2222-2222-2222-222222222222')),
  0, tbl || ': owner A updates zero tenant B rows')
from scoped_tables order by tbl;

select is(
  pg_temp.write_as('authenticated', format(
    'delete from public.%I where tenant_id = %L', tbl, '22222222-2222-2222-2222-222222222222')),
  0, tbl || ': owner A deletes zero tenant B rows')
from scoped_tables order by tbl;

select is(
  pg_temp.write_as('authenticated', $$update public.tenants set name = name where id = '22222222-2222-2222-2222-222222222222'$$),
  0, 'tenants: owner A updates zero tenant B rows');

select is(
  pg_temp.write_as('authenticated', $$delete from public.tenants where id = '22222222-2222-2222-2222-222222222222'$$),
  0, 'tenants: owner A deletes zero tenant B rows');

-- Inserting rows stamped with tenant B fails the RLS check on every table.
create temp table foreign_inserts (tbl text primary key, sql text not null);
insert into foreign_inserts values
  ('tenants', $q$insert into public.tenants (id, name) values ('33333333-3333-3333-3333-333333333333', 'X')$q$),
  ('tenant_members', $q$insert into public.tenant_members (user_id, tenant_id, app_role) values ('aaaaaaaa-0000-0000-0000-00000000000a', '22222222-2222-2222-2222-222222222222', 'owner')$q$),
  ('tenant_domains', $q$insert into public.tenant_domains (domain, tenant_id, kind) values ('evil.localhost', '22222222-2222-2222-2222-222222222222', 'admin')$q$),
  ('brand_config', $q$insert into public.brand_config (tenant_id, business_name) values ('22222222-2222-2222-2222-222222222222', 'X')$q$),
  ('company_settings', $q$insert into public.company_settings (tenant_id) values ('22222222-2222-2222-2222-222222222222')$q$),
  ('products', $q$insert into public.products (tenant_id, sku, name, metal, price_cents) values ('22222222-2222-2222-2222-222222222222', 'X-1', 'X', 'other', 100)$q$),
  ('materials', $q$insert into public.materials (tenant_id, name, unit, unit_cost_cents) values ('22222222-2222-2222-2222-222222222222', 'X', 'pcs', 1)$q$),
  ('product_materials', $q$insert into public.product_materials (product_id, material_id, tenant_id) values ('b2000000-0000-0000-0000-000000000002', 'b4000000-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222')$q$),
  ('customers', $q$insert into public.customers (tenant_id, first_name) values ('22222222-2222-2222-2222-222222222222', 'X')$q$),
  ('invoices', $q$insert into public.invoices (tenant_id, customer_name) values ('22222222-2222-2222-2222-222222222222', 'X')$q$),
  ('invoice_line_items', $q$insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents) values ('b8000000-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', 'X', 1, 1, 1)$q$),
  ('expenses', $q$insert into public.expenses (tenant_id, expense_date, description, amount_cents) values ('22222222-2222-2222-2222-222222222222', '2026-10-20', 'X', 1)$q$),
  ('automation_rules', $q$insert into public.automation_rules (tenant_id, name, trigger, action) values ('22222222-2222-2222-2222-222222222222', 'X', 'order_placed', 'notify_owner')$q$),
  ('reorder_list', $q$insert into public.reorder_list (tenant_id, material_id) values ('22222222-2222-2222-2222-222222222222', 'b4000000-0000-0000-0000-000000000001')$q$),
  ('automation_events', $q$insert into public.automation_events (tenant_id, event_type) values ('22222222-2222-2222-2222-222222222222', 'order_placed')$q$),
  ('stripe_events', $q$insert into public.stripe_events (event_id, tenant_id) values ('evt_x', '22222222-2222-2222-2222-222222222222')$q$);

select throws_ok(
  format('select pg_temp.write_as(%L, %L)', 'authenticated', sql),
  '42501', null, tbl || ': owner A cannot insert a tenant B row')
from foreign_inserts order by tbl;

-- Moving an own row into tenant B fails the WITH CHECK clause.
select throws_ok(
  format('select pg_temp.write_as(%L, %L)', 'authenticated', format(
    'update public.%I set tenant_id = %L where tenant_id = %L', tbl,
    '22222222-2222-2222-2222-222222222222', '11111111-1111-1111-1111-111111111111')),
  '42501', null, tbl || ': owner A cannot move a row into tenant B')
from (values ('products'), ('customers'), ('invoices'), ('expenses'), ('automation_rules')) v (tbl);

-- Service-role tables are closed to tenant users entirely.
select throws_ok(
  format('select pg_temp.count_as(%L, %L)', 'authenticated', format('select count(*) from public.%I', tbl)),
  '42501', null, tbl || ': owner A cannot read the table at all')
from (values ('automation_events'), ('stripe_events')) v (tbl);

-- ---------------------------------------------------------------------------
-- Owner of tenant B sees nothing of tenant A
-- ---------------------------------------------------------------------------
select pg_temp.set_claims('22222222-2222-2222-2222-222222222222', 'owner');

select is(
  pg_temp.count_as('authenticated', format(
    'select count(*) from public.%I where tenant_id is distinct from %L', tbl, '22222222-2222-2222-2222-222222222222')),
  0::bigint, tbl || ': owner B sees no other tenant rows')
from scoped_tables order by tbl;

-- ---------------------------------------------------------------------------
-- Composite foreign keys: references cannot cross tenants, even for the service role
-- ---------------------------------------------------------------------------
select throws_ok(
  $$insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents)
    values ('b8000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'X', 1, 1, 1)$$,
  '23503', null, 'line item in tenant A cannot reference a tenant B invoice');

select throws_ok(
  $$insert into public.invoice_line_items (invoice_id, tenant_id, product_id, description, qty, unit_price_cents, line_total_cents)
    values ('a7000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'b2000000-0000-0000-0000-000000000001', 'X', 1, 1, 1)$$,
  '23503', null, 'line item in tenant A cannot reference a tenant B product');

select throws_ok(
  $$insert into public.invoices (tenant_id, customer_id, customer_name)
    values ('11111111-1111-1111-1111-111111111111', 'b6000000-0000-0000-0000-000000000001', 'X')$$,
  '23503', null, 'invoice in tenant A cannot reference a tenant B customer');

select throws_ok(
  $$insert into public.product_materials (product_id, material_id, tenant_id)
    values ('b2000000-0000-0000-0000-000000000001', 'a3000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111')$$,
  '23503', null, 'product_materials in tenant A cannot reference a tenant B product');

select throws_ok(
  $$insert into public.reorder_list (tenant_id, material_id)
    values ('11111111-1111-1111-1111-111111111111', 'b4000000-0000-0000-0000-000000000001')$$,
  '23503', null, 'reorder_list in tenant A cannot reference a tenant B material');

select throws_ok(
  $$insert into public.reorder_list (tenant_id, created_by_rule)
    values ('11111111-1111-1111-1111-111111111111', 'b9000000-0000-0000-0000-000000000001')$$,
  '23503', null, 'reorder_list in tenant A cannot reference a tenant B automation rule');

select * from finish();
rollback;
