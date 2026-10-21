begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migration 20261020001500: product_images (RLS, constraints, 10-image limit), products
-- without image_urls, and the two Storage buckets that no client may read or write directly.
-- Everything is rolled back. Seed: a1…01 is a tenant A product, b2…01 a tenant B product.

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

create function pg_temp.no_claims()
returns void
language sql
as $$ select set_config('request.jwt.claims', '', true); $$;

-- Single-value query as p_role; the role is always restored.
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

-- A write as p_role: returns the row count and rolls the write back. Errors propagate.
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

-- Inserts an image row for a product (as the test owner) with correct paths.
create function pg_temp.add_image(p_tenant uuid, p_product uuid, p_position int, p_primary boolean)
returns uuid
language plpgsql
as $$
declare
  v_id uuid := gen_random_uuid();
begin
  insert into public.product_images
    (id, tenant_id, product_id, position, is_primary, large_path, thumb_path, width, height)
  values
    (v_id, p_tenant, p_product, p_position, p_primary,
     p_tenant || '/' || p_product || '/' || v_id || '-1200.webp',
     p_tenant || '/' || p_product || '/' || v_id || '-400.webp', 1200, 800);
  return v_id;
end;
$$;

-- ===========================================================================
-- products.image_urls is gone
-- ===========================================================================
select hasnt_column('public', 'products', 'image_urls', 'products.image_urls was dropped');
select has_table('public', 'product_images', 'product_images exists');

-- ===========================================================================
-- RLS on product_images
-- ===========================================================================
select pg_temp.add_image('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000001', 0, true);
select pg_temp.add_image('22222222-2222-2222-2222-222222222222', 'b2000000-0000-0000-0000-000000000001', 0, true);

create temp table img_sql (name text primary key, sql text not null);
grant select on img_sql to authenticated, anon;
insert into img_sql values
  ('select', 'select count(*) from public.product_images'),
  ('insert', $q$insert into public.product_images (tenant_id, product_id, position, large_path, thumb_path, width, height)
                select tenant_id, product_id, 5, tenant_id || '/' || product_id || '/' || x || '-1200.webp',
                       tenant_id || '/' || product_id || '/' || x || '-400.webp', 10, 10
                  from (select '11111111-1111-1111-1111-111111111111'::uuid as tenant_id,
                               'a1000000-0000-0000-0000-000000000001'::uuid as product_id,
                               gen_random_uuid() as x) s$q$),
  ('update', 'update public.product_images set width = 999'),
  ('delete', 'delete from public.product_images');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select is(pg_temp.count_as('authenticated', (select sql from img_sql where name = 'select')), 1::bigint,
          'owner of A sees only A''s images');
select is(pg_temp.write_as('authenticated', (select sql from img_sql where name = 'update')), 1,
          'owner of A updates only A''s images');
select is(pg_temp.write_as('authenticated', (select sql from img_sql where name = 'delete')), 1,
          'owner of A deletes only A''s images');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.count_as('authenticated', (select sql from img_sql where name = 'select')), 1::bigint,
          'staff of A reads A''s images');
select is(pg_temp.write_as('authenticated', (select sql from img_sql where name = 'update')), 0,
          'staff cannot update images');
select is(pg_temp.write_as('authenticated', (select sql from img_sql where name = 'delete')), 0,
          'staff cannot delete images');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', (select sql from img_sql where name = 'insert')),
                 '42501', null, 'staff cannot insert images');

select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'cpa');
select is(pg_temp.count_as('authenticated', (select sql from img_sql where name = 'select')), 0::bigint,
          'cpa sees no images');

select pg_temp.set_claims('22222222-2222-2222-2222-222222222222', 'owner');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', (select sql from img_sql where name = 'insert')),
                 '42501', null, 'owner of B cannot insert an image for A''s product');
select is(pg_temp.write_as('authenticated', 'update public.product_images set width = 1 where tenant_id = ''11111111-1111-1111-1111-111111111111'''), 0,
          'owner of B cannot update A''s images');

select pg_temp.no_claims();
select ok(not has_table_privilege('anon', 'public.product_images', 'select'), 'anon has no SELECT on product_images');

-- ===========================================================================
-- Constraints
-- ===========================================================================
select throws_ok($q$select pg_temp.add_image('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000001', 1, true)$q$,
                 '23505', null, 'a product has at most one primary image');
select throws_ok($q$insert into public.product_images (tenant_id, product_id, position, large_path, thumb_path, width, height)
                    values ('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000001', 9,
                            '22222222-2222-2222-2222-222222222222/x/y-1200.webp', '22222222-2222-2222-2222-222222222222/x/y-400.webp', 1, 1)$q$,
                 '23514', null, 'a path outside the row''s tenant and product is refused');
-- Paths are consistent with the row, so only the (tenant_id, product_id) foreign key can refuse it.
select throws_ok($q$insert into public.product_images (id, tenant_id, product_id, position, large_path, thumb_path, width, height)
                    values ('eeeeeeee-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
                            'b2000000-0000-0000-0000-000000000001', 0,
                            '11111111-1111-1111-1111-111111111111/b2000000-0000-0000-0000-000000000001/eeeeeeee-0000-0000-0000-000000000001-1200.webp',
                            '11111111-1111-1111-1111-111111111111/b2000000-0000-0000-0000-000000000001/eeeeeeee-0000-0000-0000-000000000001-400.webp', 1, 1)$q$,
                 '23503', null, 'an image cannot point at another tenant''s product');

-- Swapping two positions inside one transaction is allowed (the unique key is deferred).
create temp table swap_ids as
  select pg_temp.add_image('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000001', 1, false) as id
  union all
  select pg_temp.add_image('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000001', 2, false);
select lives_ok($q$
  update public.product_images set position = case position when 1 then 2 else 1 end
   where id in (select id from swap_ids);
  set constraints product_images_position_unique immediate;
$q$, 'two images can swap positions in one transaction');
set constraints product_images_position_unique deferred;
select throws_ok($q$
  update public.product_images set position = 7 where id in (select id from swap_ids);
  set constraints product_images_position_unique immediate;
$q$, '23505', null, 'two images cannot end up in the same position');
set constraints product_images_position_unique deferred;

-- The 10-image limit (the trigger is deferred; checked when constraints run).
select lives_ok($q$
  select pg_temp.add_image('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000001', g, false)
    from generate_series(3, 9) g;
  set constraints product_images_limit immediate;
$q$, 'a product can have 10 images');
set constraints product_images_limit deferred;
select is((select count(*)::int from public.product_images where product_id = 'a1000000-0000-0000-0000-000000000001'), 10,
          'tenant A product now has 10 images');
select throws_ok($q$
  select pg_temp.add_image('11111111-1111-1111-1111-111111111111', 'a1000000-0000-0000-0000-000000000001', 10, false);
  set constraints product_images_limit immediate;
$q$, 'P0001', 'A product can have at most 10 images', 'the 11th image is refused');
set constraints product_images_limit deferred;

-- Deleting the product deletes its images (delete the line items that reference it first).
delete from public.invoice_line_items where product_id = 'b2000000-0000-0000-0000-000000000001';
delete from public.products where id = 'b2000000-0000-0000-0000-000000000001';
select is((select count(*)::int from public.product_images where product_id = 'b2000000-0000-0000-0000-000000000001'), 0,
          'deleting a product deletes its images');

-- ===========================================================================
-- Storage: buckets and no client access
-- ===========================================================================
select results_eq(
  $q$select id, public, file_size_limit::bigint, allowed_mime_types from storage.buckets
      where id in ('product-uploads', 'product-images') order by id$q$,
  $q$values ('product-images'::text, true, 5242880::bigint, '{image/webp}'::text[]),
            ('product-uploads'::text, false, 5242880::bigint, '{image/jpeg,image/png,image/webp}'::text[])$q$,
  'product-images is public (WebP only); product-uploads is private (JPEG/PNG/WebP); both 5 MiB');

select is((select count(*)::int from pg_policies
            where schemaname = 'storage' and tablename = 'objects'
              and policyname like 'product_buckets_no_client_%' and permissive = 'RESTRICTIVE'
              and roles @> '{anon,authenticated}'),
          4, 'four RESTRICTIVE no-client policies (insert, update, delete, select) for anon and authenticated');

-- Existing objects in both buckets (inserted as the test owner, which bypasses RLS).
insert into storage.objects (bucket_id, name) values
  ('product-uploads', '11111111-1111-1111-1111-111111111111/p/raw.jpg'),
  ('product-images', '11111111-1111-1111-1111-111111111111/p/i-1200.webp');

create temp table storage_sql (name text primary key, sql text not null);
grant select on storage_sql to authenticated, anon;
insert into storage_sql values
  ('insert_uploads', $q$insert into storage.objects (bucket_id, name) values ('product-uploads', '11111111-1111-1111-1111-111111111111/p/x.jpg')$q$),
  ('insert_images',  $q$insert into storage.objects (bucket_id, name) values ('product-images', '11111111-1111-1111-1111-111111111111/p/x.webp')$q$),
  ('update',         $q$update storage.objects set metadata = '{}' where bucket_id in ('product-uploads', 'product-images')$q$),
  ('delete',         $q$delete from storage.objects where bucket_id in ('product-uploads', 'product-images')$q$),
  ('select',         $q$select count(*) from storage.objects where bucket_id in ('product-uploads', 'product-images')$q$);

-- Supabase blocks every plain SQL delete on storage.objects; the Storage API sets
-- storage.allow_delete_query for its own deletes. Set it the same way below, so the delete
-- checks exercise the RLS policies on the path the Storage API uses.
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', (select sql from storage_sql where name = 'delete')),
                 '42501', 'Direct deletion from storage tables is not allowed. Use the Storage API instead.',
                 'a plain SQL delete on storage.objects is blocked outright');
select set_config('storage.allow_delete_query', 'true', true);

select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', (select sql from storage_sql where name = 'insert_uploads')),
                 '42501', null, 'an owner cannot write to product-uploads directly');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'authenticated', (select sql from storage_sql where name = 'insert_images')),
                 '42501', null, 'an owner cannot write to product-images directly');
select is(pg_temp.write_as('authenticated', (select sql from storage_sql where name = 'update')), 0,
          'an owner cannot update objects in the product buckets');
select is(pg_temp.write_as('authenticated', (select sql from storage_sql where name = 'delete')), 0,
          'an owner cannot delete objects in the product buckets');
select is(pg_temp.count_as('authenticated', (select sql from storage_sql where name = 'select')), 0::bigint,
          'an owner cannot list objects in the product buckets');

select pg_temp.no_claims();
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'anon', (select sql from storage_sql where name = 'insert_uploads')),
                 '42501', null, 'anon cannot write to product-uploads');
select throws_ok(format('select pg_temp.write_as(%L, %L)', 'anon', (select sql from storage_sql where name = 'insert_images')),
                 '42501', null, 'anon cannot write to product-images');
select is(pg_temp.write_as('anon', (select sql from storage_sql where name = 'delete')), 0,
          'anon cannot delete objects in the product buckets');
select is(pg_temp.count_as('anon', (select sql from storage_sql where name = 'select')), 0::bigint,
          'anon cannot list objects in the product buckets');

select * from finish();
rollback;
