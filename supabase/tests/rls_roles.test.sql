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

-- Role matrix (Schema §6) for owner / staff / cpa within tenant A, plus the staff financial
-- guard on invoices past 'quote'.

insert into auth.users (id, email) values
  ('aaaaaaaa-0000-0000-0000-00000000000a', 'owner-a@test.local'),
  ('aaaaaaaa-0000-0000-0000-00000000000b', 'staff-a@test.local'),
  ('aaaaaaaa-0000-0000-0000-00000000000c', 'cpa-a@test.local'),
  ('aaaaaaaa-0000-0000-0000-00000000000d', 'new-a@test.local');

insert into public.tenant_members (user_id, tenant_id, app_role) values
  ('aaaaaaaa-0000-0000-0000-00000000000a', '11111111-1111-1111-1111-111111111111', 'owner'),
  ('aaaaaaaa-0000-0000-0000-00000000000b', '11111111-1111-1111-1111-111111111111', 'staff'),
  ('aaaaaaaa-0000-0000-0000-00000000000c', '11111111-1111-1111-1111-111111111111', 'cpa');

-- One insert / update / delete statement per table, all scoped to tenant A.
-- brand_config and company_settings already have A's row, so their insert is an upsert.
-- Line-item statements target the quote invoice so the staff guard does not interfere.
create temp table cases (tbl text primary key, ins text not null, upd text not null, del text not null);
insert into cases values
  ('tenants',
   $q$insert into public.tenants (name) values ('X')$q$,
   $q$update public.tenants set name = name where id = '11111111-1111-1111-1111-111111111111'$q$,
   $q$delete from public.tenants where id = '11111111-1111-1111-1111-111111111111'$q$),
  ('tenant_members',
   $q$insert into public.tenant_members (user_id, tenant_id, app_role) values ('aaaaaaaa-0000-0000-0000-00000000000d', '11111111-1111-1111-1111-111111111111', 'staff')$q$,
   $q$update public.tenant_members set app_role = app_role where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
   -- Not the owner's row: a tenant keeps at least one owner (20261025000100).
   $q$delete from public.tenant_members where tenant_id = '11111111-1111-1111-1111-111111111111' and app_role <> 'owner'$q$),
  ('tenant_domains',
   $q$insert into public.tenant_domains (domain, tenant_id, kind) values ('new.localhost', '11111111-1111-1111-1111-111111111111', 'admin')$q$,
   $q$update public.tenant_domains set kind = kind where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
   $q$delete from public.tenant_domains where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
  ('brand_config',
   $q$insert into public.brand_config (tenant_id, business_name) values ('11111111-1111-1111-1111-111111111111', 'X') on conflict (tenant_id) do update set business_name = excluded.business_name$q$,
   $q$update public.brand_config set tagline = 'X' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
   $q$delete from public.brand_config where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
  ('company_settings',
   $q$insert into public.company_settings (tenant_id, legal_name) values ('11111111-1111-1111-1111-111111111111', 'X') on conflict (tenant_id) do update set legal_name = excluded.legal_name$q$,
   $q$update public.company_settings set legal_name = 'X' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
   $q$delete from public.company_settings where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
  ('products',
   $q$insert into public.products (tenant_id, sku, name, metal, price_cents) values ('11111111-1111-1111-1111-111111111111', 'T-1', 'T', 'other', 100)$q$,
   $q$update public.products set name = name where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
   -- Products that appear on an invoice past quote can't be deleted by app users (see the
   -- line-item guard tests below), so the matrix deletes the unsold ones.
   $q$delete from public.products where tenant_id = '11111111-1111-1111-1111-111111111111' and id not in (select li.product_id from public.invoice_line_items li join public.invoices i on i.id = li.invoice_id where li.product_id is not null and i.order_status <> 'quote')$q$),
  ('materials',
   $q$insert into public.materials (tenant_id, name, unit, unit_cost_cents) values ('11111111-1111-1111-1111-111111111111', 'T', 'pcs', 1)$q$,
   $q$update public.materials set name = name where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
   $q$delete from public.materials where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
  ('product_materials',
   $q$insert into public.product_materials (product_id, material_id, tenant_id) values ('a1000000-0000-0000-0000-000000000002', 'a3000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111')$q$,
   $q$update public.product_materials set note = note where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
   $q$delete from public.product_materials where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
  ('customers',
   $q$insert into public.customers (tenant_id, first_name) values ('11111111-1111-1111-1111-111111111111', 'T')$q$,
   $q$update public.customers set notes = 'X' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
   $q$delete from public.customers where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
  ('invoices',
   $q$insert into public.invoices (tenant_id, customer_name) values ('11111111-1111-1111-1111-111111111111', 'T')$q$,
   $q$update public.invoices set notes = 'X' where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
   $q$delete from public.invoices where tenant_id = '11111111-1111-1111-1111-111111111111' and order_status = 'quote'$q$),  -- app users may delete only quotes (20261020001300)
  ('invoice_line_items',
   $q$insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents) values ('a7000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'T', 1, 1, 1)$q$,
   $q$update public.invoice_line_items set description = description where invoice_id = 'a7000000-0000-0000-0000-000000000002'$q$,
   $q$delete from public.invoice_line_items where invoice_id = 'a7000000-0000-0000-0000-000000000002'$q$),
  ('expenses',
   $q$insert into public.expenses (tenant_id, expense_date, description, amount_cents) values ('11111111-1111-1111-1111-111111111111', '2026-10-20', 'T', 1)$q$,
   $q$update public.expenses set description = description where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
   $q$delete from public.expenses where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
  ('automation_rules',
   $q$insert into public.automation_rules (tenant_id, name, trigger, action) values ('11111111-1111-1111-1111-111111111111', 'T', 'order_placed', 'notify_owner')$q$,
   $q$update public.automation_rules set name = name where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
   $q$delete from public.automation_rules where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
  ('reorder_list',
   $q$insert into public.reorder_list (tenant_id, material_id) values ('11111111-1111-1111-1111-111111111111', 'a3000000-0000-0000-0000-000000000001')$q$,
   $q$update public.reorder_list set status = status where tenant_id = '11111111-1111-1111-1111-111111111111'$q$,
   $q$delete from public.reorder_list where tenant_id = '11111111-1111-1111-1111-111111111111'$q$);

-- Expected access, straight from the Schema §6 matrix.
create temp table expected (
  app_role text, tbl text, can_select boolean, can_insert boolean, can_update boolean, can_delete boolean,
  primary key (app_role, tbl)
);
insert into expected values
  -- owner
  ('owner', 'tenants',            true,  false, false, false),
  ('owner', 'tenant_domains',     true,  false, false, false),
  ('owner', 'tenant_members',     true,  true,  true,  true),
  ('owner', 'brand_config',       true,  true,  true,  true),
  ('owner', 'company_settings',   true,  true,  true,  true),
  ('owner', 'products',           true,  true,  true,  true),
  ('owner', 'materials',          true,  true,  true,  true),
  ('owner', 'product_materials',  true,  true,  true,  true),
  ('owner', 'customers',          true,  true,  true,  true),
  ('owner', 'invoices',           true,  true,  true,  true),
  ('owner', 'invoice_line_items', true,  true,  true,  true),
  ('owner', 'expenses',           true,  true,  true,  true),
  ('owner', 'automation_rules',   true,  true,  true,  true),
  ('owner', 'reorder_list',       true,  true,  true,  true),
  -- staff
  ('staff', 'tenants',            true,  false, false, false),
  ('staff', 'tenant_domains',     true,  false, false, false),
  ('staff', 'tenant_members',     false, false, false, false),
  ('staff', 'brand_config',       true,  false, false, false),
  ('staff', 'company_settings',   false, false, false, false),
  ('staff', 'products',           true,  false, false, false),
  ('staff', 'materials',          true,  false, false, false),
  ('staff', 'product_materials',  true,  false, false, false),
  ('staff', 'customers',          true,  true,  true,  false),
  ('staff', 'invoices',           true,  true,  true,  false),
  ('staff', 'invoice_line_items', true,  true,  true,  true),   -- quote lines only (20261020001200)
  ('staff', 'expenses',           false, false, false, false),
  ('staff', 'automation_rules',   true,  false, false, false),
  ('staff', 'reorder_list',       true,  false, false, false),
  -- cpa
  ('cpa',   'tenants',            false, false, false, false),
  ('cpa',   'tenant_domains',     false, false, false, false),
  ('cpa',   'tenant_members',     false, false, false, false),
  ('cpa',   'brand_config',       false, false, false, false),
  ('cpa',   'company_settings',   false, false, false, false),
  ('cpa',   'products',           false, false, false, false),
  ('cpa',   'materials',          false, false, false, false),
  ('cpa',   'product_materials',  false, false, false, false),
  ('cpa',   'customers',          false, false, false, false),
  ('cpa',   'invoices',           true,  false, false, false),
  ('cpa',   'invoice_line_items', false, false, false, false),
  ('cpa',   'expenses',           true,  false, false, false),
  ('cpa',   'automation_rules',   false, false, false, false),
  ('cpa',   'reorder_list',       false, false, false, false);

select is((select count(*)::int from expected), 42, 'matrix covers 14 tables x 3 roles');

-- Runs every matrix check for one role. Denied select/update/delete are filtered to zero
-- rows by RLS; denied insert fails the RLS check (42501).
create function pg_temp.check_matrix(p_app_role text)
returns setof text
language plpgsql
as $$
declare
  r record;
  who text := p_app_role;
begin
  perform pg_temp.set_claims('11111111-1111-1111-1111-111111111111', p_app_role);
  for r in
    select e.*, c.ins, c.upd, c.del from expected e join cases c using (tbl)
    where e.app_role = p_app_role order by e.tbl
  loop
    if r.can_select then
      return next cmp_ok(pg_temp.count_as('authenticated', format('select count(*) from public.%I', r.tbl)),
                         '>', 0::bigint, format('%s can read %s', who, r.tbl));
    else
      return next is(pg_temp.count_as('authenticated', format('select count(*) from public.%I', r.tbl)),
                     0::bigint, format('%s cannot read %s', who, r.tbl));
    end if;

    if r.can_insert then
      return next is(pg_temp.write_as('authenticated', r.ins), 1, format('%s can insert into %s', who, r.tbl));
    else
      return next throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', r.ins),
                            '42501', null, format('%s cannot insert into %s', who, r.tbl));
    end if;

    if r.can_update then
      return next cmp_ok(pg_temp.write_as('authenticated', r.upd), '>', 0, format('%s can update %s', who, r.tbl));
    else
      return next is(pg_temp.write_as('authenticated', r.upd), 0, format('%s cannot update %s', who, r.tbl));
    end if;

    if r.can_delete then
      return next cmp_ok(pg_temp.write_as('authenticated', r.del), '>', 0, format('%s can delete from %s', who, r.tbl));
    else
      return next is(pg_temp.write_as('authenticated', r.del), 0, format('%s cannot delete from %s', who, r.tbl));
    end if;
  end loop;

  -- Service-role-only tables: no access at all for any tenant role.
  return next throws_ok(format('select pg_temp.count_as(%L, %L)', 'authenticated', 'select count(*) from public.automation_events'),
                        '42501', null, format('%s cannot read automation_events', who));
  return next throws_ok(format('select pg_temp.count_as(%L, %L)', 'authenticated', 'select count(*) from public.stripe_events'),
                        '42501', null, format('%s cannot read stripe_events', who));
  return next throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
                          $q$insert into public.automation_events (tenant_id, event_type) values ('11111111-1111-1111-1111-111111111111', 'order_placed')$q$),
                        '42501', null, format('%s cannot insert into automation_events', who));
  return next throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
                          $q$insert into public.stripe_events (event_id, tenant_id) values ('evt_t', '11111111-1111-1111-1111-111111111111')$q$),
                        '42501', null, format('%s cannot insert into stripe_events', who));
end;
$$;

select * from pg_temp.check_matrix('owner');
select * from pg_temp.check_matrix('staff');
select * from pg_temp.check_matrix('cpa');

-- ---------------------------------------------------------------------------
-- Staff financial guard. Seed: a7…01 is paid (INV-2026-089), a7…02 is a quote.
-- ---------------------------------------------------------------------------
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');

-- On a quote, staff can still edit money and line items.
select is(pg_temp.write_as('authenticated',
  $$update public.invoices set subtotal_cents = 20000, tax_cents = 1600, total_cents = 21600 where id = 'a7000000-0000-0000-0000-000000000002'$$),
  1, 'staff can change amounts on a quote');
select is(pg_temp.write_as('authenticated',
  $$update public.invoice_line_items set qty = 2, line_total_cents = 35000 where invoice_id = 'a7000000-0000-0000-0000-000000000002'$$),
  1, 'staff can edit line items on a quote');

-- Past 'quote': money columns are locked for staff.
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', col_sql), '42501', null,
                 'staff cannot change ' || col || ' on a paid invoice')
from (values
  ('subtotal_cents', $q$update public.invoices set subtotal_cents = 1 where id = 'a7000000-0000-0000-0000-000000000001'$q$),
  ('tax_rate_bps',   $q$update public.invoices set tax_rate_bps = 0 where id = 'a7000000-0000-0000-0000-000000000001'$q$),
  ('tax_cents',      $q$update public.invoices set tax_cents = 0 where id = 'a7000000-0000-0000-0000-000000000001'$q$),
  ('total_cents',    $q$update public.invoices set total_cents = 1 where id = 'a7000000-0000-0000-0000-000000000001'$q$)
) v (col, col_sql);

-- Past 'quote': line items are locked (insert, update, move in, delete). Inserts and moves
-- hit the guard trigger (42501); updates and deletes are filtered to 0 rows by the policies.
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
  $q$insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents) values ('a7000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Extra', 1, 100, 100)$q$),
  '42501', null, 'staff cannot add a line item to a paid invoice');
select is(pg_temp.write_as('authenticated',
  $$update public.invoice_line_items set qty = 5 where invoice_id = 'a7000000-0000-0000-0000-000000000001'$$),
  0, 'staff cannot edit a line item on a paid invoice');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
  $q$update public.invoice_line_items set invoice_id = 'a7000000-0000-0000-0000-000000000001' where invoice_id = 'a7000000-0000-0000-0000-000000000002'$q$),
  '42501', null, 'staff cannot move a line item onto a paid invoice');
select is(pg_temp.write_as('authenticated',
  $$delete from public.invoice_line_items where invoice_id = 'a7000000-0000-0000-0000-000000000001'$$),
  0, 'staff cannot delete a line item on a paid invoice');

-- Past 'quote': staff can still move status forward and edit non-financial fields.
select is(pg_temp.write_as('authenticated',
  $$update public.invoices set order_status = 'in_production' where id = 'a7000000-0000-0000-0000-000000000001'$$),
  1, 'staff can move a paid invoice forward');
select is(pg_temp.write_as('authenticated',
  $$update public.invoices set order_status = 'delivered' where id = 'a7000000-0000-0000-0000-000000000001'$$),
  1, 'staff can move a paid invoice to delivered');
select is(pg_temp.write_as('authenticated',
  $$update public.invoices set notes = 'Gift wrap', due_date = '2026-11-01' where id = 'a7000000-0000-0000-0000-000000000001'$$),
  1, 'staff can edit notes on a paid invoice');
select is(pg_temp.write_as('authenticated',
  $$update public.invoices set total_cents = total_cents, notes = 'same total' where id = 'a7000000-0000-0000-0000-000000000001'$$),
  1, 'staff can write an unchanged amount alongside a notes edit');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
  $q$update public.invoices set order_status = 'quote' where id = 'a7000000-0000-0000-0000-000000000001'$q$),
  '23514', null, 'staff cannot move a paid invoice backward');

-- The owner may still change amounts on a paid invoice (the staff financial guard), but not
-- its line items: the quote-only rule covers every app_role (20261020001200).
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select is(pg_temp.write_as('authenticated',
  $$update public.invoices set total_cents = 10000 where id = 'a7000000-0000-0000-0000-000000000001'$$),
  1, 'owner can change amounts on a paid invoice');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
  $q$insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents) values ('a7000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Extra', 1, 100, 100)$q$),
  '42501', 'line items can only change while the invoice is a quote',
  'owner cannot add a line item to a paid invoice');
select is(pg_temp.write_as('authenticated',
  $$delete from public.invoice_line_items where invoice_id = 'a7000000-0000-0000-0000-000000000001'$$),
  0, 'owner cannot delete a line item on a paid invoice');
select is(pg_temp.write_as('authenticated',
  $$update public.invoice_line_items set qty = 9 where invoice_id = 'a7000000-0000-0000-0000-000000000001'$$),
  0, 'owner cannot edit a line item on a paid invoice');
-- Consequence of the rule: deleting a product sold on a paid invoice would null that line's
-- product_id (FK on delete set null), which is a line change past quote, so it is refused.
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
  $q$delete from public.products where id = 'a1000000-0000-0000-0000-000000000001'$q$),
  '42501', null, 'owner cannot delete a product that appears on a paid invoice');

-- The service role (no app_role claim) is not restricted either.
select set_config('request.jwt.claims', '{"role": "service_role"}', true);
select is(pg_temp.write_as('service_role',
  $$update public.invoices set total_cents = 10000 where id = 'a7000000-0000-0000-0000-000000000001'$$),
  1, 'service role can change amounts on a paid invoice');
select is(pg_temp.write_as('service_role',
  $$insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents) values ('a7000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Adjustment', 1, 100, 100)$$),
  1, 'service role can still add a line item to a paid invoice');

select * from finish();
rollback;
