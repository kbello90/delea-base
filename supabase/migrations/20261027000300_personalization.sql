-- Storefront A2: personalization fields (TRD §6.11).
--   1. product_personalization_fields: up to five optional fields per product (label,
--      required, free text with a maximum length or a list of choices), e.g. Ring size,
--      Initial, Birth month. No per-option stock or price.
--   2. invoice_line_items.personalization: the shopper's answers as a snapshot,
--      [{"label": ..., "value": ...}], so editing or deleting a field later never changes an
--      existing order. Written only by the storefront checkout in A2.
--   3. storefront.product_personalization: the fields of published products, for the shop.

-- ---------------------------------------------------------------------------
-- 1. Fields
-- ---------------------------------------------------------------------------
-- Each choice 1..40 characters after trimming, no duplicates (case-insensitive), 1..30 of them.
create function public.personalization_choices_ok(p_choices text[])
returns boolean
language sql
immutable
set search_path = ''
as $$
  select p_choices is not null
     and cardinality(p_choices) between 1 and 30
     and not exists (select 1 from unnest(p_choices) c
                      where c is null or c <> btrim(c) or char_length(c) not between 1 and 40)
     and (select count(distinct lower(c)) from unnest(p_choices) c) = cardinality(p_choices);
$$;

create table public.product_personalization_fields (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  product_id uuid not null,
  position integer not null,
  label text not null,
  kind text not null,
  required boolean not null default false,
  max_length integer,
  choices text[],
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (tenant_id, product_id) references public.products (tenant_id, id) on delete cascade,
  -- Positions 1..5 and unique per product: at most five fields.
  constraint product_personalization_fields_position check (position between 1 and 5),
  constraint product_personalization_fields_position_unique unique (product_id, position),
  constraint product_personalization_fields_label
    check (label = btrim(label) and char_length(label) between 1 and 40),
  constraint product_personalization_fields_kind check (
    (kind = 'text' and choices is null and max_length between 1 and 200)
    or (kind = 'choice' and max_length is null and public.personalization_choices_ok(choices)))
);
create unique index product_personalization_fields_label_unique
  on public.product_personalization_fields (product_id, lower(label));
create index product_personalization_fields_tenant_id
  on public.product_personalization_fields (tenant_id);

create trigger product_personalization_fields_90_updated_at
  before update on public.product_personalization_fields
  for each row execute function public.set_updated_at();

alter table public.product_personalization_fields enable row level security;

create policy product_personalization_fields_owner_all on public.product_personalization_fields
  for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');
create policy product_personalization_fields_staff_select on public.product_personalization_fields
  for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');

revoke all on public.product_personalization_fields from anon, authenticated;
grant select, insert, update, delete on public.product_personalization_fields to authenticated;

-- ---------------------------------------------------------------------------
-- 2. Answers on order lines
-- ---------------------------------------------------------------------------
-- 1..5 objects with exactly a label (1..40 characters) and a value (1..200 characters).
create function public.personalization_snapshot_ok(p_answers jsonb)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select jsonb_typeof(p_answers) = 'array'
     and jsonb_array_length(p_answers) between 1 and 5
     and not exists (
       select 1 from jsonb_array_elements(p_answers) a
        where jsonb_typeof(a) <> 'object'
           or (select array_agg(k order by k) from jsonb_object_keys(a) k) <> array['label', 'value']
           or jsonb_typeof(a -> 'label') <> 'string'
           or jsonb_typeof(a -> 'value') <> 'string'
           or char_length(a ->> 'label') not between 1 and 40
           or char_length(a ->> 'value') not between 1 and 200);
$$;

alter table public.invoice_line_items add column personalization jsonb;
alter table public.invoice_line_items add constraint invoice_line_items_personalization
  check (personalization is null or public.personalization_snapshot_ok(personalization));

-- ---------------------------------------------------------------------------
-- 3. The fields of published products, for the storefront
-- ---------------------------------------------------------------------------
create view storefront.product_personalization as
  select f.id, f.product_id, f.position, f.label, f.kind, f.required, f.max_length, f.choices
    from public.product_personalization_fields f
    join storefront.products p on p.id = f.product_id
   where f.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid;

revoke all on storefront.product_personalization from public, anon, authenticated;
grant select on storefront.product_personalization to storefront_reader;
