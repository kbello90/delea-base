-- Settings slice (TRD §6.9):
--   1. The currency can't change once the tenant has products, materials, invoices or
--      expenses (every caller). Provisioning sets it when the tenant is created.
--   2. The brand logo is an uploaded file in the public brand-assets bucket: brand_config
--      stores its path (free-text logo_url, null everywhere, is dropped).
--   3. brand-assets: public reads (the storefront shows the logo), WebP only, 1 MiB, written
--      only by the backend.

-- ---------------------------------------------------------------------------
-- 1. Currency guard
-- ---------------------------------------------------------------------------
create function public.company_settings_currency_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.currency is distinct from old.currency and (
       exists (select 1 from public.products where tenant_id = new.tenant_id)
    or exists (select 1 from public.materials where tenant_id = new.tenant_id)
    or exists (select 1 from public.invoices where tenant_id = new.tenant_id)
    or exists (select 1 from public.expenses where tenant_id = new.tenant_id)) then
    raise exception 'The currency can''t change once you have products, materials, invoices or expenses.'
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger company_settings_10_currency_guard
  before update of currency on public.company_settings
  for each row execute function public.company_settings_currency_guard();

revoke all on function public.company_settings_currency_guard() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. Logo path
-- ---------------------------------------------------------------------------
alter table public.brand_config drop column logo_url;
alter table public.brand_config add column logo_path text;
alter table public.brand_config add constraint brand_config_logo_path_own check (
  logo_path is null
  or logo_path ~ ('^' || tenant_id::text
                  || '/logo-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.webp$'));

-- ---------------------------------------------------------------------------
-- 3. The bucket: public reads through the public object endpoint, no client writes
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types) values
  ('brand-assets', 'brand-assets', true, 1048576, '{image/webp}');

-- RESTRICTIVE, as for the product buckets: no client can list, write, update or delete
-- objects; public reads of a known path don't use these grants. The service role bypasses RLS.
create policy brand_assets_no_client_insert on storage.objects
  as restrictive for insert to anon, authenticated
  with check (bucket_id <> 'brand-assets');
create policy brand_assets_no_client_update on storage.objects
  as restrictive for update to anon, authenticated
  using (bucket_id <> 'brand-assets')
  with check (bucket_id <> 'brand-assets');
create policy brand_assets_no_client_delete on storage.objects
  as restrictive for delete to anon, authenticated
  using (bucket_id <> 'brand-assets');
create policy brand_assets_no_client_select on storage.objects
  as restrictive for select to anon, authenticated
  using (bucket_id <> 'brand-assets');
