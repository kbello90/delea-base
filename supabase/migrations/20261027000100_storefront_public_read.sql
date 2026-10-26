-- Storefront A1: the public, read-only storefront (TRD §1.3, §6.11).
--   1. products: made_to_order and a short lead-time note. A made-to-order product is never
--      short of stock: the stock trigger skips both its check and its decrement.
--   2. brand_config: the storefront's look (hero eyebrow, collection title, font and corner
--      presets, monogram or wordmark) and an optional second hero button whose link may only
--      be https:// or tel:.
--   3. storefront_settings: per-tenant storefront switches and pages. Everything in this
--      table is public by design; company_settings (tax ID, Stripe, counters) never is.
--   4. storefront_reader and the storefront schema: public reads run as this role, which can
--      select from the storefront views only. The views expose an exact list of columns, so a
--      bug in a public endpoint can't reach costs, stock counts, customers or another tenant.

-- ---------------------------------------------------------------------------
-- 1. Products
-- ---------------------------------------------------------------------------
alter table public.products add column made_to_order boolean not null default false;
alter table public.products add column lead_time_note text;
alter table public.products add constraint products_lead_time_note_length
  check (lead_time_note is null or char_length(lead_time_note) between 1 and 60);

-- Same as migration 20261022000200 apart from the made-to-order filter: such lines are
-- neither checked nor decremented (they are made after the sale).
create or replace function public.invoices_decrement_stock()
returns trigger
language plpgsql
security definer  -- staff may advance an invoice but can't update products
set search_path = ''
as $$
declare
  r record;
begin
  -- Lock this invoice's products, in id order (no deadlocks), before reading stock: two
  -- simultaneous sales of the same product serialize here, and the second one sees the
  -- first one's decrement.
  perform 1
     from public.products p
    where p.tenant_id = new.tenant_id
      and p.id in (select li.product_id
                     from public.invoice_line_items li
                    where li.invoice_id = new.id
                      and li.tenant_id = new.tenant_id
                      and li.product_id is not null)
    order by p.id
      for update;

  -- One check and one decrement per stocked product, summing qty across that product's
  -- lines. Lines without a product (e.g. a custom piece) and made-to-order products are
  -- skipped.
  for r in
    select p.id, p.sku, p.stock_qty, n.needed
      from (select li.product_id, sum(li.qty)::bigint as needed
              from public.invoice_line_items li
             where li.invoice_id = new.id
               and li.tenant_id = new.tenant_id
               and li.product_id is not null
             group by li.product_id) n
      join public.products p on p.id = n.product_id and p.tenant_id = new.tenant_id
     where not p.made_to_order
     order by p.sku
  loop
    if r.stock_qty < r.needed then
      raise exception 'Not enough stock for SKU %: % available, % needed.',
        r.sku, r.stock_qty, r.needed;  -- P0001 -> 409 with this message
    end if;
    update public.products
       set stock_qty = stock_qty - r.needed::integer
     where id = r.id and tenant_id = new.tenant_id;
  end loop;
  return null;
end;
$$;

-- ---------------------------------------------------------------------------
-- 2. Brand: the storefront's look
-- ---------------------------------------------------------------------------
alter table public.brand_config
  add column hero_eyebrow text,
  add column collection_title text,
  add column font_preset text not null default 'classic',
  add column corner_style text not null default 'square',
  add column mark_style text not null default 'monogram',
  add column hero_cta_label text,
  add column hero_cta_url text;

alter table public.brand_config
  add constraint brand_config_hero_eyebrow_length
    check (hero_eyebrow is null or char_length(hero_eyebrow) between 1 and 60),
  add constraint brand_config_collection_title_length
    check (collection_title is null or char_length(collection_title) between 1 and 80),
  add constraint brand_config_font_preset check (font_preset in ('classic', 'modern')),
  add constraint brand_config_corner_style check (corner_style in ('square', 'pill')),
  add constraint brand_config_mark_style check (mark_style in ('monogram', 'wordmark')),
  add constraint brand_config_hero_cta_label_length
    check (hero_cta_label is null or char_length(hero_cta_label) between 1 and 40),
  -- An https:// link with a plain host (https://wa.me/... included) or a tel: number. No
  -- other scheme (javascript:, http:, data:), no user@host, no spaces. The API checks the same.
  add constraint brand_config_hero_cta_url check (
    hero_cta_url is null
    or (char_length(hero_cta_url) <= 500
        and (hero_cta_url ~* '^https://[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(:[0-9]{1,5})?([/?#][^[:space:]<>"]*)?$'
             or hero_cta_url ~ '^tel:\+?[0-9][0-9().-]{2,29}$')));

-- ---------------------------------------------------------------------------
-- 3. Storefront settings (public by design)
-- ---------------------------------------------------------------------------
create table public.storefront_settings (
  tenant_id uuid primary key references public.tenants (id) on delete cascade,
  -- Off: the storefront answers "Store not found" on every one of the tenant's domains.
  storefront_enabled boolean not null default false,
  contact_email text,
  return_policy text,
  privacy_policy text,
  terms text,
  custom_orders_info text,  -- the "How custom orders work" page; its hero button shows only with text
  updated_at timestamptz not null default now(),
  constraint storefront_settings_contact_email
    check (contact_email is null
           or (char_length(contact_email) <= 254 and contact_email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')),
  constraint storefront_settings_page_length
    check (coalesce(char_length(return_policy), 0) <= 20000
           and coalesce(char_length(privacy_policy), 0) <= 20000
           and coalesce(char_length(terms), 0) <= 20000
           and coalesce(char_length(custom_orders_info), 0) <= 20000)
);

create trigger storefront_settings_90_updated_at
  before update on public.storefront_settings
  for each row execute function public.set_updated_at();

-- Existing tenants get a row, switched off (provisioning creates one for new tenants).
insert into public.storefront_settings (tenant_id)
  select id from public.tenants
  on conflict (tenant_id) do nothing;

alter table public.storefront_settings enable row level security;

create policy storefront_settings_owner_all on public.storefront_settings for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');
create policy storefront_settings_staff_select on public.storefront_settings for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');

revoke all on public.storefront_settings from anon, authenticated;
grant select, insert, update, delete on public.storefront_settings to authenticated;

-- ---------------------------------------------------------------------------
-- 4. storefront_reader and the storefront views
-- ---------------------------------------------------------------------------
-- Roles are cluster-wide and can outlive a database reset, so create it only if missing.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'storefront_reader') then
    create role storefront_reader nologin noinherit;
  end if;
end
$$;

-- The API's database login switches into storefront_reader for every public read
-- (`set local role storefront_reader`), so it must be a member. Locally and today that login
-- is postgres; the least-privilege runtime login (TRD §6.6) gets the same grant.
grant storefront_reader to postgres;

create schema storefront;
revoke all on schema storefront from public;
grant usage on schema storefront to storefront_reader;

-- Views are owned by the migration role and are NOT security_invoker: they read the base
-- tables with the owner's rights, so storefront_reader has (and needs) no grants on public.
-- All but `hosts` filter on the tenant set for the transaction:
--   set local role storefront_reader;
--   select set_config('app.tenant_id', '<tenant resolved from the request host>', true);
-- An unset app.tenant_id matches nothing.

-- Which storefront host belongs to which tenant: only active tenants with the storefront on.
create view storefront.hosts as
  select d.domain, d.tenant_id
    from public.tenant_domains d
    join public.tenants t on t.id = d.tenant_id
    join public.storefront_settings s on s.tenant_id = d.tenant_id
   where d.kind = 'storefront'
     and t.is_active
     and s.storefront_enabled;

-- The shop's public face. company_settings contributes the currency only.
create view storefront.store as
  select
    b.business_name,
    b.tagline,
    b.primary_color,
    b.accent_color,
    b.logo_path,
    b.hero_eyebrow,
    b.hero_title,
    b.hero_subtitle,
    b.collection_title,
    b.font_preset,
    b.corner_style,
    b.mark_style,
    b.hero_cta_label,
    b.hero_cta_url,
    c.currency::text as currency,
    s.contact_email,
    s.return_policy,
    s.privacy_policy,
    s.terms,
    s.custom_orders_info,
    exists (select 1 from public.products p
             where p.tenant_id = b.tenant_id and p.is_published and p.made_to_order)
      as has_made_to_order
  from public.brand_config b
  join public.company_settings c on c.tenant_id = b.tenant_id
  join public.storefront_settings s on s.tenant_id = b.tenant_id
  join public.tenants t on t.id = b.tenant_id
  where b.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
    and t.is_active
    and s.storefront_enabled;

-- Published products. No SKU, no stock count, no threshold: availability is computed here.
-- The sale price appears only while the sale is active.
create view storefront.products as
  select
    p.id,
    p.name,
    p.description,
    p.metal::text as metal,
    p.price_cents,
    case when p.sale_price_cents is not null and (p.sale_ends_at is null or p.sale_ends_at > now())
         then p.sale_price_cents end as sale_price_cents,
    case when p.sale_price_cents is not null and (p.sale_ends_at is null or p.sale_ends_at > now())
         then p.sale_ends_at end as sale_ends_at,
    case when p.made_to_order then 'made_to_order'
         when p.stock_qty <= 0 then 'out_of_stock'
         when p.stock_qty < p.low_stock_threshold then 'low'
         else 'in_stock' end as availability,
    p.made_to_order,
    p.lead_time_note,
    p.sort_order
  from public.products p
  join public.storefront_settings s on s.tenant_id = p.tenant_id
  join public.tenants t on t.id = p.tenant_id
  where p.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
    and p.is_published
    and t.is_active
    and s.storefront_enabled;

-- Images of published products only.
create view storefront.product_images as
  select i.id, i.product_id, i.position, i.is_primary, i.large_path, i.thumb_path,
         i.width, i.height
    from public.product_images i
    join storefront.products p on p.id = i.product_id
   where i.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid;

revoke all on all tables in schema storefront from public, anon, authenticated;
grant select on all tables in schema storefront to storefront_reader;
