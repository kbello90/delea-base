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

-- Catch-all guards over the whole public schema.

-- Fails if any table in public has RLS disabled, and names the offenders.
select is(
  (select array_agg(c.relname::text order by c.relname)
     from pg_class c
    where c.relnamespace = 'public'::regnamespace
      and c.relkind in ('r', 'p')
      and not c.relrowsecurity),
  null::text[],
  'every table in public has RLS enabled'
);

select tables_are(
  'public',
  array[
    'tenants', 'tenant_members', 'tenant_domains', 'brand_config', 'company_settings',
    'products', 'product_images', 'materials', 'product_materials',
    'customers', 'invoices', 'invoice_line_items',
    'expenses', 'automation_rules', 'reorder_list', 'automation_events', 'stripe_events',
    'password_reset_attempts', 'storefront_settings', 'order_contacts',
    'product_personalization_fields', 'public_rate_hits'
  ],
  'public contains exactly the 22 Schema tables'
);

-- Every function in public pins search_path (covers every SECURITY DEFINER function).
select is(
  (select array_agg(p.oid::regprocedure::text order by 1)
     from pg_proc p
    where p.pronamespace = 'public'::regnamespace
      and not exists (
        select 1 from unnest(coalesce(p.proconfig, '{}'::text[])) cfg
        where cfg like 'search_path=%')),
  null::text[],
  'every function in public sets search_path'
);

select cmp_ok(
  (select count(*)::int from pg_proc p
    where p.pronamespace = 'public'::regnamespace and p.prosecdef),
  '>', 0,
  'SECURITY DEFINER functions exist (so the search_path check above is meaningful)'
);

select is(
  (select count(*)::int from pg_policies
    where schemaname = 'public' and tablename in ('stripe_events', 'automation_events')),
  0,
  'stripe_events and automation_events have no policies (service role only)'
);

select is(
  (select array_agg(policyname::text order by policyname) from pg_policies
    where schemaname = 'public' and roles <> '{authenticated}'::name[]),
  null::text[],
  'every policy targets authenticated only (none for anon or public)'
);

select is(
  (select array_agg(c.relname::text order by c.relname)
     from pg_class c
    where c.relnamespace = 'public'::regnamespace
      and c.relkind in ('r', 'p', 'v', 'm')
      and has_table_privilege('anon', c.oid,
            'select, insert, update, delete, truncate, references, trigger')),
  null::text[],
  'anon holds no privilege on any public table'
);

select is(
  (select array_agg(c.relname::text order by c.relname)
     from pg_class c
    where c.relnamespace = 'public'::regnamespace
      and c.relkind = 'r'
      and has_table_privilege('authenticated', c.oid, 'truncate, references, trigger')),
  null::text[],
  'authenticated holds no TRUNCATE/REFERENCES/TRIGGER on public tables'
);

select is(
  (select array_agg(c.relname::text order by c.relname)
     from pg_class c
    where c.relnamespace = 'public'::regnamespace
      and c.relname in ('automation_events', 'stripe_events')
      and has_table_privilege('authenticated', c.oid, 'select, insert, update, delete')),
  null::text[],
  'authenticated holds no privilege on automation_events or stripe_events'
);

select * from finish();
rollback;
