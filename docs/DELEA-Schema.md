# DELEA — Backend Schema (v1 draft for review)

**Status:** Draft — review before scaffolding migrations
**Derived from:** DELEA PRD v1 (all Open Questions resolved) and DELEA TRD v1 (locked)
**Lives in the repo as:** `supabase/migrations/*.sql` (this doc is the spec those migrations are written from)

Items marked **[DECISION]** are not settled by the PRD or TRD. Each has a recommendation; they are collected in §8.

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
  logo_url text,
  hero_title text,
  hero_subtitle text,
  updated_at timestamptz not null default now()
);

create table company_settings (
  tenant_id uuid primary key references tenants(id) on delete cascade,
  legal_name text,
  ein text,                               -- sensitive: restricted to owner (see §6)
  address text,
  currency char(3) not null default 'USD',
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
  name text not null,
  email text,
  phone text,
  address text,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
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
  public_token uuid not null default gen_random_uuid(),      -- unguessable link for shopper order tracking; see §8 item 4
  notes text,
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
  line_total_cents bigint not null
);

create table expenses (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  expense_date date not null,
  description text not null,
  category text,
  amount_cents bigint not null check (amount_cents >= 0),
  is_tax_deductible boolean not null default true,
  receipt_url text,
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

-- Idempotency for Stripe webhooks.
create table stripe_events (
  event_id text primary key,
  tenant_id uuid references tenants(id) on delete cascade,
  processed_at timestamptz not null default now()
);
```

**Invoice rules enforced in the database:**
- `order_status` moves forward only along `quote → paid → in_production → shipped → delivered`, enforced by a `before update` trigger. A forward skip such as `paid → shipped` is allowed, since not every piece is made to order. Backward moves are rejected.
- Setting `paid` stamps `paid_at` and assigns `invoice_number` from `company_settings.next_invoice_number` (row-locked increment). `quote_number` is assigned from `next_quote_number` on insert.
- `order_status_changed` and `order_placed` automations fire from a trigger that inserts into a queue the FastAPI automation service drains. The database never sends email itself.

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
| `stripe_events` | none (service role only) | none | none |

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
create view ai.tax_summary as
  select ... from public.invoices i full join public.expenses e on false ... ;  -- shape finalized in the migration; same tenant filter

grant select on all tables in schema ai to ai_readonly;   -- SELECT on views only. No INSERT/UPDATE/DELETE/EXECUTE grants exist.
```

If `app.tenant_id` is unset, the filter matches nothing, so a missing tenant context returns zero rows rather than every tenant's rows.

FastAPI's `/ai/query` handler connects with its normal backend login, opens a transaction, and runs `set local role ai_readonly` followed by `select set_config('app.tenant_id', :tenant_id, true)` before any query. `tenant_id` comes from the caller's validated JWT, never from request input. The view definitions exclude `company_settings`, `tenant_members`, and customer contact details.

`supabase/tests/` includes pgTAP tests for all of the following:
- `ai_readonly` can select from the `ai` views and nothing else (no access to any `public` table).
- An `ai_readonly` insert/update/delete attempt fails on every table and view.
- A session with `app.tenant_id` set to tenant A cannot see tenant B rows, and an unset `app.tenant_id` returns zero rows.

## 8. Decisions needed before migrations are written

Each has a recommendation. Reply with the numbers you want changed, or "all recommended."

1. **Storefront data access [DECISION; requires a TRD change].** TRD §1.3 says the storefront can read the product grid directly from Supabase. In the shared tier that would require an `anon` read policy, and anonymous visitors could then query every tenant's published products, because the client chooses which tenant to ask for. **Recommendation:** the storefront reads through public FastAPI endpoints (`GET /public/products`, `/public/brand`) that resolve the tenant from the request hostname via `tenant_domains`. `anon` gets no table access. Cost: one extra hop. Benefit: no cross-tenant exposure and the same path works for Enterprise.
2. **Stripe account model [DECISION].** Each customer must receive money in their own Stripe account. **Recommendation:** Stripe Connect (Standard), where the owner connects their account via OAuth and `company_settings.stripe_account_id` stores the connected account. DELEA never holds the customer's secret keys, and one platform webhook handles every tenant. The alternative, per-tenant API keys, means storing and rotating secrets per customer.
3. **Sales tax [DECISION].** **Recommendation:** v1 uses one default rate per tenant (`default_tax_rate_bps`), with a per-invoice override and the rate snapshotted on the invoice. Per-state tables or Stripe Tax come later. A single-location boutique rarely needs more.
4. **Shopper order tracking [DECISION].** The PRD lists "track order" for end customers. **Recommendation:** guest checkout with no shopper accounts. The confirmation email carries a link containing `invoices.public_token`, which a public FastAPI endpoint resolves to a read-only order status page. Accounts can come later.
5. **Discount rules [DECISION].** The PRD lists "discount rules" under the catalog without defining them. **Recommendation:** v1 supports a product-level sale price (`sale_price_cents`, optional `sale_ends_at`) only. Promo codes and rule engines are deferred.
6. **Refunds and cancellations [DECISION].** The five-value `order_status` enum is locked and I'm not reopening it. But refunds and cancelled orders will happen once Stripe is live. **Recommendation:** keep the enum as is and add nullable `refunded_at` and `cancelled_at` timestamps on `invoices` (already in the DDL above), set by the Stripe webhook or the owner. The UI shows a Refunded/Cancelled badge over the unchanged status.

Smaller choices I made that you can veto: separate `quote_number` and `invoice_number` series (a quote gets `QTE-…` at creation and an `INV-…` number when paid, matching the demo's two number formats); `reorder_list` as a small table so the `add_to_reorder_list` automation has somewhere to write; and one SKU per metal rather than a variant model.
