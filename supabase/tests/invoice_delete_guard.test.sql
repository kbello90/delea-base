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


-- Migration 20261020001300: app users may delete only quotes; the service role is exempt.
-- Seed: a7…01 is Sarah's paid invoice (1 line), a7…02 is Michael's quote (1 line).

create function pg_temp.exec_as(p_role text, p_sql text)
returns integer
language plpgsql
as $$
declare
  n integer;
begin
  perform set_config('role', p_role, true);
  begin
    execute p_sql;
    get diagnostics n = row_count;
  exception when others then
    perform set_config('role', 'none', true);
    raise;
  end;
  perform set_config('role', 'none', true);
  return n;
end;
$$;

create function pg_temp.lines(p_invoice uuid)
returns integer
language sql
as $$ select count(*)::int from public.invoice_line_items where invoice_id = p_invoice; $$;

-- A shipped invoice with a line, created by the service role (no app_role claim).
select set_config('request.jwt.claims', '', true);
insert into public.invoices (id, tenant_id, customer_name)
values ('f0000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Guard shipped');
insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents)
values ('f0000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Repair', 1, 100, 100);
update public.invoices set order_status = 'shipped' where id = 'f0000000-0000-0000-0000-000000000001';

-- A quote with two lines, for the cascade case.
insert into public.invoices (id, tenant_id, customer_name)
values ('f0000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'Guard quote');
insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents)
values ('f0000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'A', 1, 100, 100),
       ('f0000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'B', 2, 100, 200);

-- ---------------------------------------------------------------------------
-- Owner
-- ---------------------------------------------------------------------------
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
                 $q$delete from public.invoices where id = 'a7000000-0000-0000-0000-000000000001'$q$),
                 '42501', 'only quotes can be deleted; cancel or refund this invoice instead',
                 'owner cannot delete a paid invoice');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated',
                 $q$delete from public.invoices where id = 'f0000000-0000-0000-0000-000000000001'$q$),
                 '42501', 'only quotes can be deleted; cancel or refund this invoice instead',
                 'owner cannot delete a shipped invoice');
select is(pg_temp.lines('a7000000-0000-0000-0000-000000000001'), 1, 'the paid invoice keeps its line');

-- Cascade: deleting a quote removes its lines. The line guard fires on each cascaded
-- delete; the parent is already gone, so it lets them through.
select is(pg_temp.lines('f0000000-0000-0000-0000-000000000002'), 2, 'the quote has 2 lines');
select is(pg_temp.exec_as('authenticated',
          $q$delete from public.invoices where id = 'f0000000-0000-0000-0000-000000000002'$q$),
          1, 'owner can delete a quote');
select is(pg_temp.lines('f0000000-0000-0000-0000-000000000002'), 0, 'its lines cascade away');

-- ---------------------------------------------------------------------------
-- Staff and cpa: no delete at all (no policy; filtered to 0 rows)
-- ---------------------------------------------------------------------------
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.write_as('authenticated',
          $q$delete from public.invoices where id = 'a7000000-0000-0000-0000-000000000002'$q$),
          0, 'staff cannot delete a quote');
select is(pg_temp.write_as('authenticated',
          $q$delete from public.invoices where id = 'a7000000-0000-0000-0000-000000000001'$q$),
          0, 'staff cannot delete a paid invoice');
select is(pg_temp.write_as('authenticated',
          $q$delete from public.invoices where id = 'f0000000-0000-0000-0000-000000000001'$q$),
          0, 'staff cannot delete a shipped invoice');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'cpa');
select is(pg_temp.write_as('authenticated',
          $q$delete from public.invoices where tenant_id = '11111111-1111-1111-1111-111111111111'$q$),
          0, 'cpa cannot delete invoices');
select is((select count(*)::int from public.invoices
            where id in ('a7000000-0000-0000-0000-000000000001', 'a7000000-0000-0000-0000-000000000002',
                         'f0000000-0000-0000-0000-000000000001')),
          3, 'all three invoices are still there');

-- ---------------------------------------------------------------------------
-- Service role (no app_role): exempt
-- ---------------------------------------------------------------------------
select set_config('request.jwt.claims', '{"role":"service_role"}', true);
select is(pg_temp.exec_as('service_role',
          $q$delete from public.invoices where id = 'a7000000-0000-0000-0000-000000000001'$q$),
          1, 'service role can delete a paid invoice');
select is(pg_temp.lines('a7000000-0000-0000-0000-000000000001'), 0, 'its lines cascade away too');

select ok((select 'search_path=""' = any (proconfig) from pg_proc
            where oid = 'public.invoices_delete_quote_only_guard()'::regprocedure),
          'the guard pins an empty search_path');

select * from finish();
rollback;
