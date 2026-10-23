# DELEA — Backend Schema (v1)

**Status:** Locked and implemented — migrations in `supabase/migrations/` are the source of truth; §9 lists where they differ from the DDL below (all §8 decisions accepted as recommended, 2026-10-20)
**Derived from:** DELEA PRD v1 (all Open Questions resolved) and DELEA TRD v1 (locked)
**Lives in the repo as:** `supabase/migrations/*.sql` (this doc is the spec those migrations are written from)

Items marked **[DECISION]** were open when this doc was drafted; all are now settled and recorded in §8.

---

## 1. Conventions

- Every tenant-scoped table has `tenant_id uuid not null references tenants(id)`. On Enterprise (dedicated project) the table has exactly one tenant, but the column and policies stay identical so the schema never forks.
- Primary keys are `uuid default gen_random_uuid()`.
- Money is stored as integer cents (`*_cents bigint`), never floats. Currency comes from `company_settings.currency`.
- Every table has `created_at timestamptz not null default now()`; mutable tables also have `updated_at`, maintained by a shared `set_updated_at()` trigger.
- Names are `snake_case`, tables are plural.
- RLS is enabled on every table in `public`. A table without policies is inaccessible to everything except the service role.

## 2. Enums

```sql
create type tier as enum ('essential', 'professional', 'enterprise');
create type app_role as enum ('owner', 'staff', 'cpa');
create type order_status as enum ('quote', 'paid', 'in_production', 'shipped', 'delivered');  -- locked, PRD Q1
create type customer_type as enum ('retail', 'wholesale');
create type payment_terms as enum ('due_on_receipt', 'net_15', 'net_30', 'net_60');
create type metal_type as enum ('gold_14k', 'gold_18k', 'rose_gold', 'white_gold', 'silver_925', 'platinum', 'other');
create type automation_trigger as enum ('order_placed', 'order_status_changed', 'stock_low', 'material_low');
create type automation_action as enum ('send_email', 'notify_owner', 'add_to_reorder_list');
```

`add_to_reorder_list` is an owner-facing list entry created by an automation rule. It is a system action, not an AI write action, so it does not conflict with PRD Q2.

## 3. Tenancy, auth, and settings

```sql
create table tenants (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  tier tier not null default 'essential',
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

-- Source of truth for who belongs to which tenant, and with what role.
-- A custom access token hook reads this table and adds tenant_id + app_role to the JWT.
create table tenant_members (
  user_id uuid not null references auth.users(id) on delete cascade,
  tenant_id uuid not null references tenants(id) on delete cascade,
  app_role app_role not null,
  invited_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  primary key (user_id, tenant_id)
);

-- Maps request hostnames to tenants (storefront + admin). See §8 item 1.
create table tenant_domains (
  domain text primary key,
  tenant_id uuid not null references tenants(id) on delete cascade,
  kind text not null check (kind in ('storefront', 'admin'))
);

create table brand_config (
  tenant_id uuid primary key references tenants(id) on delete cascade,
  business_name text not null,
  tagline text,
  primary_color text not null default '#6B2737',
  accent_color text not null default '#B8935B',
  logo_path text,                                            -- {tenant_id}/logo-{uuid}.webp in the public brand-assets bucket (migration 20261023000200)
  hero_title text,
  hero_subtitle text,
  updated_at timestamptz not null default now()
);

create table company_settings (
  tenant_id uuid primary key references tenants(id) on delete cascade,
  legal_name text,
  ein text,                               -- sensitive: restricted to owner (see §6)
  address text,
  currency char(3) not null default 'USD',              -- set at provisioning; a trigger refuses a change once the tenant has data
  default_language text not null default 'en' check (default_language in ('en', 'es', 'fr')),
  enabled_languages text[] not null default '{en}',
  default_tax_rate_bps integer not null default 0,   -- basis points; see §8 item 3
  stripe_account_id text,                            -- see §8 item 2
  next_quote_number integer not null default 1,
  next_invoice_number integer not null default 1,
  updated_at timestamptz not null default now()
);
```

`brand_config` fields map one-to-one to the `applyBrand()` inputs in delea-demo.html (name, tagline, primary, accent), plus logo and hero copy from the website editor panel.

## 4. Catalog and inventory (PRD Q3: two independent entities)

```sql
create table products (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  sku text not null,
  name text not null,
  description text,
  metal metal_type not null,
  price_cents bigint not null check (price_cents >= 0),
  sale_price_cents bigint check (sale_price_cents >= 0),     -- see §8 item 5
  sale_ends_at timestamptz,
  stock_qty integer not null default 0,
  low_stock_threshold integer not null default 3,            -- matches the demo's "stock below 3 units" rule
  image_urls text[] not null default '{}',
  icon text,                                                 -- demo-only glyph fallback; optional
  is_published boolean not null default false,
  sort_order integer not null default 0,                     -- storefront order, set by the editor's move arrows
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, sku)
);

create table materials (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  name text not null,
  unit text not null,                                        -- 'pcs', 'vials', 'g' — free text
  unit_cost_cents bigint not null check (unit_cost_cents >= 0),
  qty_on_hand numeric(12,2) not null default 0,
  reorder_threshold numeric(12,2) not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, name)
);

-- Reference only. No trigger or application code deducts materials when product stock changes.
create table product_materials (
  product_id uuid not null references products(id) on delete cascade,
  material_id uuid not null references materials(id) on delete cascade,
  tenant_id uuid not null references tenants(id) on delete cascade,
  note text,
  primary key (product_id, material_id)
);
```

Assumption: one SKU per metal. "Gold ring" and "silver ring" are separate products, not variants of one product, which matches the demo. A real variant model (size, metal as options on one product) is deferred. `product_materials` carries `tenant_id` so its RLS policy is a simple equality check rather than a join.

## 5. Customers, invoices, expenses, automation

```sql
create table customers (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  customer_type customer_type not null default 'retail',
  first_name text not null,
  last_name text,
  email text,
  phone text,
  address_line1 text,
  address_line2 text,
  city text,
  state text,
  postal_code text,
  country char(2) not null default 'US',
  marketing_opt_in boolean not null default false,
  notes text,
  -- Wholesale-only fields (null / defaults for retail customers)
  company_name text,
  tax_exempt boolean not null default false,
  resale_certificate_number text,
  wholesale_discount_bps integer not null default 0 check (wholesale_discount_bps between 0 and 10000),
  payment_terms payment_terms not null default 'due_on_receipt',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (customer_type = 'wholesale' or (company_name is null and not tax_exempt and wholesale_discount_bps = 0 and payment_terms = 'due_on_receipt')),
  check (customer_type <> 'wholesale' or company_name is not null)
);

create table invoices (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  customer_id uuid references customers(id) on delete set null,
  customer_name text not null,                               -- snapshot so CPA exports work without access to customers
  quote_number text,                                         -- e.g. QTE-2026-042, assigned at creation
  invoice_number text,                                       -- e.g. INV-2026-089, assigned when status becomes 'paid'
  order_status order_status not null default 'quote',        -- locked, PRD Q1
  source text not null default 'admin' check (source in ('admin', 'storefront')),
  subtotal_cents bigint not null default 0,
  tax_rate_bps integer not null default 0,                   -- snapshot of the rate used
  tax_cents bigint not null default 0,
  total_cents bigint not null default 0,
  stripe_payment_intent_id text,
  due_date date,                                             -- set from customers.payment_terms; null = due on receipt
  public_token uuid not null default gen_random_uuid(),      -- unguessable link for shopper order tracking; see §8 item 4
  notes text,
  payment_method payment_method,                             -- cash/card/bank_transfer/other; null on quotes (migration 20261022000200)
  paid_at timestamptz,
  refunded_at timestamptz,                                   -- see §8 item 6
  cancelled_at timestamptz,                                  -- see §8 item 6
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, quote_number),
  unique (tenant_id, invoice_number),
  unique (public_token)
);

create table invoice_line_items (
  id uuid primary key default gen_random_uuid(),
  invoice_id uuid not null references invoices(id) on delete cascade,
  tenant_id uuid not null references tenants(id) on delete cascade,
  product_id uuid references products(id) on delete set null,
  description text not null,                                 -- snapshot of the product name at sale time
  qty integer not null check (qty > 0),
  unit_price_cents bigint not null check (unit_price_cents >= 0),
  line_total_cents bigint not null,
  position integer not null check (position >= 1),           -- entry order, 1..n per invoice; unique (invoice_id, position); assigned on insert when omitted (migration 20261022000300)
);

create table expenses (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  expense_date date not null,
  description text not null,
  category expense_category not null default 'other',      -- fixed list (migration 20261023000100); see TRD §6.8
  amount_cents bigint not null check (amount_cents >= 0),
  is_tax_deductible boolean not null default true,
  -- receipt: one private file in the expense-receipts bucket (migration 20261023000100); the path never leaves the API
  receipt_path text,                                         -- {tenant_id}/{expense_id}/{uuid}.{jpg|png|webp|pdf} (check)
  receipt_content_type text,                                 -- image/jpeg, image/png, image/webp or application/pdf
  receipt_size_bytes integer,                                -- 1..10 MiB; all four receipt columns set or null together
  receipt_uploaded_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table automation_rules (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  name text not null,
  trigger automation_trigger not null,
  trigger_config jsonb not null default '{}',                -- e.g. {"to_status": "shipped"} or {"threshold": 3}
  action automation_action not null,
  action_config jsonb not null default '{}',                 -- e.g. {"template": "order_shipped"}
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table reorder_list (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  material_id uuid references materials(id) on delete cascade,
  product_id uuid references products(id) on delete cascade,
  created_by_rule uuid references automation_rules(id) on delete set null,
  status text not null default 'open' check (status in ('open', 'ordered', 'dismissed')),
  created_at timestamptz not null default now()
);

-- Queue drained by the FastAPI automation service. Database triggers insert rows; the database never sends email itself.
create table automation_events (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  event_type automation_trigger not null,
  payload jsonb not null default '{}',                       -- e.g. {"invoice_id": "...", "from_status": "paid", "to_status": "shipped"}
  created_at timestamptz not null default now(),
  processed_at timestamptz
);
create index automation_events_pending on automation_events (created_at) where processed_at is null;

-- Idempotency for Stripe webhooks.
create table stripe_events (
  event_id text primary key,
  tenant_id uuid references tenants(id) on delete cascade,
  processed_at timestamptz not null default now()
);
```

**Invoice rules enforced in the database:**
- `order_status` moves forward only along `quote → paid → in_production → shipped → delivered`, enforced by a `before update` trigger. A forward skip such as `paid → shipped` is allowed, since not every piece is made to order. Backward moves are rejected ("This invoice can't move back to paid.").
- **Closed invoices** (migration 20261022000200, `invoices_08_closed_guard`, every caller): a cancelled or refunded invoice keeps its status, a cancelled one can't be refunded and a refunded one can't be cancelled (P0001).
- **`payment_method`** (enum `cash`, `card`, `bank_transfer`, `other`; same migration): null while the invoice is a quote (check `invoices_payment_method_only_when_paid`), recorded when it's paid, null on invoices paid before the column existed. The staff financial guard also stops staff changing a method that is set.
- Setting `paid` stamps `paid_at` and assigns `invoice_number` from `company_settings.next_invoice_number` (row-locked increment). `quote_number` is assigned from `next_quote_number` on insert.
- `order_placed` and `order_status_changed` events are inserted into `automation_events` by a trigger on `invoices`. `stock_low` and `material_low` events are inserted by triggers on `products` and `materials` when a stock update crosses below its threshold. The FastAPI automation service drains the queue; the database never sends email itself.

## 6. Row-Level Security

Policies read the JWT claims: `(auth.jwt() ->> 'tenant_id')::uuid` and `auth.jwt() ->> 'app_role'`. The `app_role` claim is deliberately not named `role`, which Supabase reserves for the Postgres role. Example:

```sql
create policy products_select on products for select to authenticated
  using (tenant_id = (auth.jwt() ->> 'tenant_id')::uuid
         and (auth.jwt() ->> 'app_role') in ('owner', 'staff'));
```

| Table | owner | staff | cpa |
|---|---|---|---|
| `tenants`, `tenant_domains` | read | read | none |
| `tenant_members` | full | none | none |
| `brand_config` | full | read | none |
| `company_settings` | full | none | none |
| `products`, `materials`, `product_materials` | full | read | none |
| `customers` | full | read, insert, update | none |
| `invoices`, `invoice_line_items` | full | read, insert, update status up to `shipped`/`delivered`; no delete | invoices: read only; line items: none |
| `expenses` | full | none | read |
| `automation_rules`, `reorder_list` | full | read | none |
| `stripe_events`, `automation_events` | none (service role only) | none | none |

Notes:
- `company_settings` is owner-only because it holds the EIN and Stripe account reference.
- `delete` on `invoices` is owner-only, and a paid invoice cannot be deleted by anyone through the API (enforced in FastAPI, per TRD §1.3).
- CPA gets `invoices` and `expenses` only, as locked in PRD Q4 and TRD §3.4. Invoice rows carry a `customer_name` snapshot so exports are complete without access to `customers`.
- **Anonymous (storefront) access:** the `anon` role gets no table grants. See §8 item 1.

## 7. AI assistant read path (PRD Q2: read-only)

```sql
create role ai_readonly nologin noinherit;
create schema ai;
grant usage on schema ai to ai_readonly;

-- Views live in a separate schema, are owned by the migration role, and filter on the
-- tenant set for the current transaction. ai_readonly has NO grants on public tables.
create view ai.inventory_status as
  select ... from public.products  where tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid;
create view ai.materials_status as
  select ... from public.materials where tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid;
create view ai.sales_summary as    -- MTD/YTD revenue by status
  select ... from public.invoices  where tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid;
create view ai.expense_summary as  -- expenses by month and category, with deductible totals
  select ... from public.expenses  where tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid;
create view ai.tax_summary as      -- tax collected on paid invoices by month (the "tax reserve" figure in the demo)
  select ... from public.invoices  where tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
                                     and paid_at is not null and refunded_at is null and cancelled_at is null;

grant select on all tables in schema ai to ai_readonly;   -- SELECT on views only. No INSERT/UPDATE/DELETE/EXECUTE grants exist.
```

If `app.tenant_id` is unset, the filter matches nothing, so a missing tenant context returns zero rows rather than every tenant's rows.

FastAPI's `/ai/query` handler connects with its normal backend login, opens a transaction, and runs `set local role ai_readonly` followed by `select set_config('app.tenant_id', :tenant_id, true)` before any query. `tenant_id` comes from the caller's validated JWT, never from request input. The view definitions exclude `company_settings`, `tenant_members`, and customer contact details.

`supabase/tests/` includes pgTAP tests for all of the following:
- `ai_readonly` can select from the `ai` views and nothing else (no access to any `public` table).
- An `ai_readonly` insert/update/delete attempt fails on every table and view.
- A session with `app.tenant_id` set to tenant A cannot see tenant B rows, and an unset `app.tenant_id` returns zero rows.

## 8. Locked decisions

All accepted as recommended on 2026-10-20.

1. **Storefront data access [DECISION; requires a TRD change].** TRD §1.3 says the storefront can read the product grid directly from Supabase. In the shared tier that would require an `anon` read policy, and anonymous visitors could then query every tenant's published products, because the client chooses which tenant to ask for. **Recommendation:** the storefront reads through public FastAPI endpoints (`GET /public/products`, `/public/brand`) that resolve the tenant from the request hostname via `tenant_domains`. `anon` gets no table access. Cost: one extra hop. Benefit: no cross-tenant exposure and the same path works for Enterprise.
2. **Stripe account model [DECISION].** Each customer must receive money in their own Stripe account. **Recommendation:** Stripe Connect (Standard), where the owner connects their account via OAuth and `company_settings.stripe_account_id` stores the connected account. DELEA never holds the customer's secret keys, and one platform webhook handles every tenant. The alternative, per-tenant API keys, means storing and rotating secrets per customer.
3. **Sales tax [DECISION].** **Recommendation:** v1 uses one default rate per tenant (`default_tax_rate_bps`), with a per-invoice override and the rate snapshotted on the invoice. Per-state tables or Stripe Tax come later. A single-location boutique rarely needs more.
4. **Shopper order tracking [DECISION].** The PRD lists "track order" for end customers. **Recommendation:** guest checkout with no shopper accounts. The confirmation email carries a link containing `invoices.public_token`, which a public FastAPI endpoint resolves to a read-only order status page. Accounts can come later.
5. **Discount rules [DECISION].** The PRD lists "discount rules" under the catalog without defining them. **Recommendation:** v1 supports a product-level sale price (`sale_price_cents`, optional `sale_ends_at`) only. Promo codes and rule engines are deferred.
6. **Refunds and cancellations [DECISION].** The five-value `order_status` enum is locked and I'm not reopening it. But refunds and cancelled orders will happen once Stripe is live. **Recommendation:** keep the enum as is and add nullable `refunded_at` and `cancelled_at` timestamps on `invoices` (already in the DDL above), set by the Stripe webhook or the owner. The UI shows a Refunded/Cancelled badge over the unchanged status.

**Wholesale behavior (added after review).** `customers.customer_type` is `retail` or `wholesale`. Wholesale customers carry a company name, tax-exempt flag with resale certificate number, a percentage discount, and payment terms. FastAPI applies these when it builds an invoice: the discount reduces unit prices, `tax_exempt` sets `tax_rate_bps` to 0, and `payment_terms` sets `invoices.due_date`. Storefront shoppers are always created as `retail`. Wholesale accounts are created by the owner or staff in the admin app. Decision (accepted): wholesale pricing is a per-customer discount percent. A separate wholesale price on each product is deferred, since it would add a second price to maintain on every SKU.

Smaller choices I made that you can veto: separate `quote_number` and `invoice_number` series (a quote gets `QTE-…` at creation and an `INV-…` number when paid, matching the demo's two number formats); `reorder_list` as a small table so the `add_to_reorder_list` automation has somewhere to write; and one SKU per metal rather than a variant model.

## 9. As built (supabase/migrations; last updated with migration 20261020001500)

The migrations in `supabase/migrations/` are now the source of truth. Where the SQL in §3–§7 differs from them, the migrations win, and the differences are:

- **Same-tenant references.** Every parent/child reference uses a composite `(tenant_id, id)` foreign key, so a child row can never point at a parent in another tenant (foreign-key checks bypass RLS, so RLS alone cannot stop this). Parents (`products`, `materials`, `customers`, `invoices`, `automation_rules`) carry `unique (tenant_id, id)`. "Set null" actions use column lists so `tenant_id` is never nulled.
- **Staff financial guard.** Once an invoice's `order_status` is past `quote`, staff cannot change `subtotal_cents`, `tax_rate_bps`, `tax_cents` or `total_cents`. The owner and the service role can. Staff can still advance the status and edit non-financial fields such as notes. The guard identifies staff from the `app_role` claim, so the service role and migration role are never blocked.
- **Line items are quote-only (migration 20261020001200).** Once an invoice is past `quote`, nobody with an `app_role` (owner, staff or cpa) can insert, update or delete its line items. Only the service role (no `app_role`: webhooks, seed, maintenance) can. This replaces the earlier rule that let the owner edit lines past quote. **Why:** stock is decremented when an invoice becomes paid, so editing lines afterwards would desync stock and totals. A paid invoice is corrected by cancel or refund plus a new invoice.
  - **Policies:** owner and staff get INSERT, UPDATE and DELETE only on lines whose invoice (same tenant) is a quote. Reads are unchanged: owner and staff read all of the tenant's lines; cpa still reads none.
  - **Trigger:** `invoice_line_items_quote_only_guard` blocks the same writes for any app_role. It's a backstop if a policy is ever widened.
  - **What callers see:** an insert on a non-quote invoice fails with 42501 ("line items can only change while the invoice is a quote"). An update or delete is filtered to 0 rows by the policy.
  - **Consequence:** an app user can't delete a product that appears on a paid invoice. Its `on delete set null (product_id)` would change that invoice's line. Unpublish the product instead.
- **Invoices start as quotes.** A caller with an `app_role` can only insert an invoice with `order_status = 'quote'`, and gets 23514 otherwise. The service role is exempt: seed, storefront checkout, webhooks. App users therefore always reach "paid" through the status transition, which is where stock moves.
- **Stock decrement.** When an invoice first leaves `quote` (`old = 'quote' and new > 'quote'`, including a skip straight to shipped), each product's `stock_qty` drops by the sum of its lines' `qty`.
  - **Concurrency:** the product rows are locked `FOR UPDATE` in id order first, so two simultaneous sales of the same product serialize. The second sale sees the first one's decrement.
  - **Lines:** lines without a product are skipped.
  - **Insufficient stock:** raises P0001 "Not enough stock for SKU ...: N available, M needed.", and the whole transition rolls back.
  - **Later updates:** they never decrement again, and there's no restock on refund or cancel in v1.
  - **Invoices inserted directly as paid** (service role only) have no lines at insert time, so they never decrement stock. Those paths must create the quote, add the lines, then advance it.
- **Tax rate for staff.** `current_tax_rate_bps()` (SECURITY DEFINER, `search_path = ''`) returns `default_tax_rate_bps` for the caller's tenant (from the JWT) when `app_role` is owner or staff. It raises 42501 for anyone else, cpa included. `EXECUTE` is granted to `authenticated` only. Staff get the rate without access to `company_settings`.
- **Currency for every role (migration 20261020001400).** `current_tenant_currency()` (SECURITY DEFINER, `search_path = ''`) returns `company_settings.currency` for the caller's tenant to owner, staff and cpa. It also checks that a `tenant_members` row exists for (token user, tenant, role), so a removed member's still-valid token is refused (42501), as are a missing or malformed `tenant_id`. `EXECUTE` is granted to `authenticated` only.
- **Product images (migration 20261020001500).** `products.image_urls` is dropped; images live in `product_images` (tenant, product, position, `is_primary`, the two object paths and the large variant's size).
  - **Paths only:** the table stores object paths in the public `product-images` bucket, constrained to `{tenant_id}/{product_id}/{id}-1200.webp` and `-400.webp`. Public URLs are built by the backend from a setting.
  - **Rules:** one primary image per product; positions unique per product (deferrable, so a reorder can swap); at most 10 images per product (deferred constraint trigger, P0001); composite FK to `products (tenant_id, id)` with cascade.
  - **RLS:** owner all, staff read, cpa nothing, like `products`.
  - **Storage:** private bucket `product-uploads` (raw uploads, JPEG/PNG/WebP) and public bucket `product-images` (WebP), both 5 MiB. Four RESTRICTIVE policies on `storage.objects` deny anon and authenticated any insert, update, delete or select in either bucket, so no permissive policy can open them later. Clients upload raw files only through signed URLs the backend issues; the backend (service key) writes the variants.
- **Only quotes can be deleted (migration 20261020001300).** A before-delete trigger on `invoices` refuses callers with an `app_role` when the invoice is past `quote`, with 42501 "only quotes can be deleted; cancel or refund this invoice instead". The owner's DELETE policy is deliberately unchanged, so this message is what users see rather than a silent 0 rows. The service role is exempt. Deleting a quote still cascades its lines; the line guard lets the cascaded deletes through because the parent is already gone.
- **Deletes that would damage history are refused by the API, not the database.** The FK actions themselves are unchanged. FastAPI checks first and returns 409 with a clear message for:
  - a product on any invoice line ("unpublish it instead"). Deleting it would empty the line's `product_id`, so a quote would never decrement stock;
  - a customer with invoices, which would be detached from their history;
  - a material used by products, whose `product_materials` rows would cascade away.
- **Document numbering.**
  - Format is `QTE-YYYY-NNN` / `INV-YYYY-NNN`, zero-padded to at least 3 digits and never truncated.
  - Numbers are always assigned by trigger; client-supplied values are ignored.
  - `quote_number`, `invoice_number` and `paid_at` are immutable once set.
  - An invoice inserted directly as paid, or moved from `quote` straight past `paid`, still receives its invoice number.
  - Inserting an invoice fails if the tenant has no `company_settings` row.
- **Stock events.** "Low" means strictly below the threshold. `stock_low` and `material_low` fire only when an update crosses the threshold, never on insert.
- **Domains.** `tenant_domains.domain` includes the port (for example `localhost:5173`), because the Host header carries it.
- **Functions.** `EXECUTE` is revoked from `public`, `anon` and `authenticated` on every function in `public`, so `ai_readonly` holds no execute grants. The `grant ai_readonly to postgres` needed by the local tests is made inside each test transaction and is not in any migration, so it cannot reach production.
- **No direct data API.** Neither `public` nor `ai` is exposed through the Supabase Data API. Locally, `supabase/config.toml` sets `[api] schemas = ["graphql_public"]` and `extra_search_path = ["extensions"]`. Every read and write goes through FastAPI (TRD §1.3).
  - **REST:** `/rest/v1` returns 404 for every table.
  - **GraphQL:** `pg_graphql` reflects the schemas on the search path, so `public` must stay off the search path too. Verified with `pg_graphql` enabled.
  - **Hosted projects:** the same settings are made in the Data API dashboard (see the provisioning runbook).
- **Local ports.** `supabase/config.toml` uses 553xx instead of the CLI defaults (543xx), because Windows reserves 54269–54368. The local API is on 55321, the database on 55322, and Studio on 55323.
- **Seed.** Tenant A "DELEA Demo" uses 800 bps (8%) tax to match the demo; tenant B "Aurora Gems" is a small second tenant for isolation tests.

**Provisioning note.** Because invoice insert requires a `company_settings` row, creating a tenant must insert `tenants`, `company_settings` and `brand_config` together. A tenant-creation procedure belongs in the provisioning task.
