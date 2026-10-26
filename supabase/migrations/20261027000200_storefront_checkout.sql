-- Storefront A2: checkout with manual payment (TRD §6.11).
--   1. storefront_settings: shipping (flat rate, free over a threshold, taxed or not), local
--      pickup, and the manual payment options with the instructions shown after an order.
--   2. invoices: shipping, fulfillment, the shopper's payment choice, the checkout
--      idempotency key and its fingerprint, and the shopper-facing order reference. The total
--      is now subtotal + tax + shipping, as a check. The staff financial guard covers shipping.
--   3. order_contacts: the shopper's contact and ship-to address, one row per storefront
--      order. Owner and staff read it; the CPA never does (RLS), unlike invoices.
--   4. public_rate_hits: per-address, per-email and per-shop counters for the public
--      checkout (hashes only).

-- ---------------------------------------------------------------------------
-- 1. Storefront settings
-- ---------------------------------------------------------------------------
alter table public.storefront_settings
  add column ship_enabled boolean not null default false,
  add column ship_flat_cents bigint not null default 0,
  add column free_ship_over_cents bigint,
  add column tax_shipping boolean not null default false,
  add column pickup_enabled boolean not null default false,
  add column pickup_note text,
  add column bank_transfer_enabled boolean not null default false,
  add column pay_on_pickup_enabled boolean not null default false,
  -- Shown to the shopper only after an order is placed (never in the public store view).
  add column manual_payment_instructions text;

alter table public.storefront_settings
  add constraint storefront_settings_ship_flat check (ship_flat_cents between 0 and 100000000),
  add constraint storefront_settings_free_ship_over
    check (free_ship_over_cents is null or free_ship_over_cents between 0 and 100000000000),
  add constraint storefront_settings_pickup_note_length
    check (pickup_note is null or char_length(pickup_note) between 1 and 500),
  add constraint storefront_settings_instructions_length
    check (manual_payment_instructions is null
           or char_length(manual_payment_instructions) between 1 and 2000),
  -- Paying on pickup needs pickup.
  add constraint storefront_settings_pay_on_pickup check (not pay_on_pickup_enabled or pickup_enabled);

-- ---------------------------------------------------------------------------
-- 2. Invoices
-- ---------------------------------------------------------------------------
alter table public.invoices
  add column shipping_cents bigint not null default 0,
  add column fulfillment text,
  add column payment_choice text,
  add column checkout_key uuid,
  add column checkout_fingerprint text,
  add column order_ref text;

alter table public.invoices
  add constraint invoices_shipping_non_negative check (shipping_cents >= 0),
  add constraint invoices_fulfillment check (fulfillment in ('ship', 'pickup')),
  -- Slice B adds 'card'.
  add constraint invoices_payment_choice check (payment_choice in ('bank_transfer', 'pay_on_pickup')),
  -- W- and 8 Crockford base32 characters (no I, L, O, U).
  add constraint invoices_order_ref_format check (order_ref ~ '^W-[0-9A-HJKMNP-TV-Z]{8}$'),
  add constraint invoices_total_parts check (total_cents = subtotal_cents + tax_cents + shipping_cents),
  add constraint invoices_storefront_fields check (
    source <> 'storefront'
    or (fulfillment is not null and payment_choice is not null and checkout_key is not null
        and checkout_fingerprint is not null and order_ref is not null)),
  add constraint invoices_checkout_key_unique unique (tenant_id, checkout_key),
  add constraint invoices_order_ref_unique unique (tenant_id, order_ref);

-- The admin's "awaiting payment" count: storefront orders still at quote, not cancelled.
create index invoices_storefront_awaiting on public.invoices (tenant_id)
  where source = 'storefront' and order_status = 'quote' and cancelled_at is null;

-- Same as migration 20261022000200, plus shipping among the amounts staff can't change.
create or replace function public.invoices_staff_financial_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (select auth.jwt() ->> 'app_role') = 'staff'
     and old.order_status <> 'quote'
     and (new.subtotal_cents is distinct from old.subtotal_cents
          or new.tax_rate_bps is distinct from old.tax_rate_bps
          or new.tax_cents is distinct from old.tax_cents
          or new.shipping_cents is distinct from old.shipping_cents
          or new.total_cents is distinct from old.total_cents) then
    raise exception 'staff cannot change amounts on an invoice past quote'
      using errcode = 'insufficient_privilege';
  end if;
  if (select auth.jwt() ->> 'app_role') = 'staff'
     and old.payment_method is not null
     and new.payment_method is distinct from old.payment_method then
    raise exception 'staff cannot change the payment method once it is set'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- 3. Order contacts
-- ---------------------------------------------------------------------------
create table public.order_contacts (
  invoice_id uuid primary key,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  first_name text not null,
  last_name text not null,
  email text not null,
  phone text,
  ship_line1 text,
  ship_line2 text,
  ship_city text,
  ship_state text,
  ship_postal_code text,
  ship_country char(2),
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (tenant_id, invoice_id) references public.invoices (tenant_id, id) on delete cascade,
  constraint order_contacts_names
    check (char_length(first_name) between 1 and 100 and char_length(last_name) between 1 and 100),
  constraint order_contacts_email
    check (char_length(email) <= 254 and email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
  constraint order_contacts_phone check (phone is null or char_length(phone) between 1 and 40),
  constraint order_contacts_note check (note is null or char_length(note) between 1 and 500),
  -- A ship-to address is all there or not there; v1 ships within the US only.
  constraint order_contacts_ship_complete
    check (num_nonnulls(ship_line1, ship_city, ship_state, ship_postal_code, ship_country) in (0, 5)
           and (ship_line1 is not null or ship_line2 is null)),
  constraint order_contacts_ship_us check (
    ship_line1 is null
    or (ship_country = 'US'
        and char_length(ship_line1) between 1 and 200
        and (ship_line2 is null or char_length(ship_line2) between 1 and 200)
        and char_length(ship_city) between 1 and 100
        and ship_state ~ '^[A-Z]{2}$'
        and ship_postal_code ~ '^[0-9]{5}(-[0-9]{4})?$'))
);
create index order_contacts_tenant_id on public.order_contacts (tenant_id);

create trigger order_contacts_90_updated_at
  before update on public.order_contacts
  for each row execute function public.set_updated_at();

alter table public.order_contacts enable row level security;

-- Rows are created only by the storefront checkout (public.storefront_place_order). Owner and
-- staff read; the owner may correct them; nobody with an app role inserts or deletes.
create policy order_contacts_owner_select on public.order_contacts for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner');
create policy order_contacts_owner_update on public.order_contacts for update to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');
create policy order_contacts_staff_select on public.order_contacts for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');

revoke all on public.order_contacts from anon, authenticated;
grant select, update on public.order_contacts to authenticated;

-- ---------------------------------------------------------------------------
-- 4. Public checkout counters
-- ---------------------------------------------------------------------------
-- One row per counted event: scope (what was counted) and a SHA-256 hash of the key (client
-- address, email, or tenant id). Written by the checkout functions only; no app role has
-- grants. Rows older than a day are deleted as new ones arrive.
create table public.public_rate_hits (
  id bigint generated by default as identity primary key,
  scope text not null check (scope in ('order_attempt_ip', 'order_ip', 'order_email', 'order_tenant')),
  key_hash text not null check (key_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now()
);
create index public_rate_hits_lookup on public.public_rate_hits (scope, key_hash, created_at);
alter table public.public_rate_hits enable row level security;
revoke all on public.public_rate_hits from anon, authenticated;
