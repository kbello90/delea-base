-- Product images (slice 3): a table replaces products.image_urls, plus two Storage buckets
-- that no client can write to. Files are written only by the backend (service key over the
-- Storage HTTP API); clients upload raw files through signed upload URLs it issues.
-- The table stores object paths only; public URLs are built by the backend from a setting.

-- ---------------------------------------------------------------------------
-- 1. Table
-- ---------------------------------------------------------------------------
create table public.product_images (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  product_id uuid not null,
  position integer not null check (position >= 0),
  is_primary boolean not null default false,
  -- Object paths in the public bucket product-images.
  large_path text not null,
  thumb_path text not null,
  width integer not null check (width > 0),     -- of the large variant
  height integer not null check (height > 0),
  created_at timestamptz not null default now(),
  foreign key (tenant_id, product_id) references public.products (tenant_id, id) on delete cascade,
  -- Paths can only live under the row's own tenant and product.
  check (large_path = tenant_id::text || '/' || product_id::text || '/' || id::text || '-1200.webp'),
  check (thumb_path = tenant_id::text || '/' || product_id::text || '/' || id::text || '-400.webp'),
  -- Deferrable so a reorder can swap positions inside one transaction.
  constraint product_images_position_unique unique (product_id, position) deferrable initially deferred
);
create unique index product_images_one_primary on public.product_images (product_id) where is_primary;
create index product_images_tenant_id on public.product_images (tenant_id);

-- At most 10 images per product (the API checks first; this is the backstop).
create function public.product_images_limit()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (select count(*) from public.product_images where product_id = new.product_id) > 10 then
    raise exception 'A product can have at most 10 images';
  end if;
  return new;
end;
$$;
create constraint trigger product_images_limit
  after insert on public.product_images
  deferrable initially deferred
  for each row execute function public.product_images_limit();
revoke all on function public.product_images_limit() from public, anon, authenticated;

alter table public.product_images enable row level security;

create policy product_images_owner_all on public.product_images for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');
create policy product_images_staff_select on public.product_images for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');

revoke all on public.product_images from anon;
grant select, insert, update, delete on public.product_images to authenticated;

-- products.image_urls held arbitrary client-supplied strings; images now live in the table.
-- Nothing else uses it (seed, ai views and the demo checked).
alter table public.products drop column image_urls;

-- ---------------------------------------------------------------------------
-- 2. Storage buckets
-- ---------------------------------------------------------------------------
-- product-uploads: private, raw uploads via signed URL only; deleted after registration.
-- product-images: public read, processed WebP variants written only by the backend.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types) values
  ('product-uploads', 'product-uploads', false, 5242880, '{image/jpeg,image/png,image/webp}'),
  ('product-images',  'product-images',  true,  5242880, '{image/webp}');

-- ---------------------------------------------------------------------------
-- 3. No client access to either bucket's objects
-- ---------------------------------------------------------------------------
-- RESTRICTIVE policies are ANDed with any permissive policy, so a permissive policy added
-- later can't open these buckets to clients. Public reads of product-images go through the
-- public object endpoint, which doesn't use these grants. The service role bypasses RLS.
create policy product_buckets_no_client_insert on storage.objects
  as restrictive for insert to anon, authenticated
  with check (bucket_id not in ('product-uploads', 'product-images'));
create policy product_buckets_no_client_update on storage.objects
  as restrictive for update to anon, authenticated
  using (bucket_id not in ('product-uploads', 'product-images'))
  with check (bucket_id not in ('product-uploads', 'product-images'));
create policy product_buckets_no_client_delete on storage.objects
  as restrictive for delete to anon, authenticated
  using (bucket_id not in ('product-uploads', 'product-images'));
create policy product_buckets_no_client_select on storage.objects
  as restrictive for select to anon, authenticated
  using (bucket_id not in ('product-uploads', 'product-images'));
