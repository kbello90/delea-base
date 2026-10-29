# DELEA — Technical Requirements Document (v1)

**Status:** Locked — ready for Claude Code scaffolding
**Owner:** Karen
**Scope of this doc:** Architecture, infrastructure, and repo structure for the golden-image base product described in the DELEA PRD (v1, all Open Questions resolved). This doc is the scaffolding spec for Claude Code.

**Locked TRD decisions:**
1. **Two frontend apps** in one monorepo — `apps/admin` and `apps/storefront` — sharing `packages/ui` and `packages/api-client` (§1.1).
2. **Enterprise dedicated DB = a separate Supabase project** on dedicated compute, not self-hosted Postgres. One auth system and one RLS pattern across all tiers; the backend never branches by tier (§2.3).
3. **Monorepo tooling:** pnpm workspaces + Turborepo (§4).
4. **Migrations:** Supabase CLI, SQL-first, in a root `supabase/` directory — RLS policies are versioned in the same migration stream as the schema (§4).
5. **Storefront data access:** public FastAPI endpoints keyed by hostname; no direct Supabase access from the storefront (§1.3). Stripe Connect, guest checkout with a tokenized tracking link, and the other data-model decisions are recorded in `DELEA-Schema.md` §8. Connected accounts are the Standard equivalent (Stripe now calls the `standard` type legacy for new platforms): created with the four controller settings (the shop gets the full Stripe Dashboard, the shop pays Stripe's fees, Stripe covers losses, Stripe collects the shop's details) and onboarded through Stripe-hosted Account Links rather than OAuth (decision K1, Slice B).
6. **Backend database access:** the FastAPI backend talks to Postgres directly through a psycopg connection pool, not through PostgREST. Each authenticated request runs in one transaction that does `set local role authenticated` and sets the caller's verified JWT claims, so RLS is the enforcement. Public storefront endpoints use a separate service connection; the Stripe webhook and Connect onboarding switch to `payments_writer`, a role that may only execute the Stripe functions (decision K4, Slice B2); the email worker switches to `email_worker`, which may only execute the outbox functions (Slice D); the AI assistant uses `set local role ai_readonly` with `app.tenant_id`. `supabase-py` is used only for Auth admin actions such as CPA invites.

---

## 1. Architecture Overview

### 1.1 Why two frontend apps, not one

| | Two apps (`admin` + `storefront`) | Single app, route-gated |
|---|---|---|
| Bundle sent to anonymous shoppers | Storefront-only JS — no admin/CRUD/AI code | Full app bundle, auth-gated by route |
| SEO / initial load on storefront | Cleaner, smaller, easier to optimize independently | Coupled to admin app's build/perf profile |
| Deploy cadence | Can ship admin changes without redeploying every tenant's public site, or vice versa | One deploy affects both |
| Shared code | Needs explicit `packages/ui` + `packages/api-client` discipline | Naturally shared, no package boundary to maintain |
| Complexity | Two Vercel projects per environment, one monorepo | One Vercel project |

Given the PRD's 2-second storefront load NFR and the fact that the storefront is the thing end customers (not owners) touch, I'm recommending the split. The "Website Editor" panel in delea-demo.html stays inside `apps/admin` — it's an authenticated owner tool that edits the same underlying product/brand records `apps/storefront` reads. No UI logic is being re-derived here; both apps consume delea-demo.html's visual/interaction patterns (theming via CSS custom properties, the item-array-driven product grid, the contenteditable-style inline editing) — see §1.4.

### 1.2 System diagram (text)

```
                         ┌─────────────────────────┐
                         │   Vercel (frontend)     │
                         │                         │
                         │  apps/admin (per-tenant │
                         │  subdomain, JWT auth)   │
                         │                         │
                         │  apps/storefront (per-  │
                         │  tenant domain, public) │
                         └───────────┬─────────────┘
                                     │ HTTPS / REST (JSON)
                                     │ Supabase JS client (auth + direct
                                     │ reads where RLS allows it)
                                     ▼
                         ┌─────────────────────────┐
                         │  Azure App Service       │
                         │  FastAPI backend         │
                         │  - REST API (CRUD)       │
                         │  - Stripe webhooks       │
                         │  - AI assistant endpoint │
                         │    (read-only, scoped)   │
                         └───────────┬─────────────┘
                                     │ Postgres protocol,
                                     │ tenant-scoped JWT claims
                                     ▼
                         ┌─────────────────────────┐
                         │  Supabase (Postgres +    │
                         │  Auth + RLS)             │
                         │  - shared DB, Essential/ │
                         │    Professional tiers    │
                         │  - dedicated project,    │
                         │    Enterprise tier       │
                         └─────────────────────────┘
```

Redis (Enterprise-tier only, or shared/namespaced on lower tiers if/when caching is actually needed) sits alongside the backend for session/rate-limit/cache concerns — not required for v1 launch unless load testing says otherwise.

### 1.3 Request patterns

- **Admin app → backend:** all writes (product CRUD, invoices, expenses, automation rules, brand config) go through FastAPI, which validates against the tenant's JWT claims and RLS-scoped Postgres role. This is where business logic (e.g., "can't delete a material referenced by a paid invoice") lives — not in the DB alone.
- **Admin → FastAPI only (locked; reverses the earlier "admin may read Supabase directly" decision):** `apps/admin` reads and writes only through FastAPI. The Supabase JS client is used for **Auth only** (sign-in, token refresh), never for table access.
  - **Why:** the Supabase Data API (REST and GraphQL) is closed for `public`, so a signed-in user can't bypass FastAPI's rules. Those rules include server-computed totals, owner-only refund/cancel/tax override and quote-only edits. With the Data API open, RLS alone would have allowed those writes.
  - **Cost:** one extra hop for reads. FastAPI is in the same region as the database.
- **Storefront → FastAPI public endpoints only (locked):** `apps/storefront` never queries Supabase directly. It calls public, unauthenticated FastAPI endpoints (`GET /api/v1/public/store`, `/api/v1/public/products`, `/api/v1/public/pages/{slug}`, and order tracking by `public_token` from Slice A2) that resolve the tenant from the request hostname via `tenant_domains`. The storefront and the API are on different hosts, so "the request hostname" is the host of the browser's `Origin` header (§6.11). The `anon` Postgres role has no table grants, so one tenant's catalog can never be read by asking for another tenant's id. The storefront's `supabaseClient.ts` stays in the tree but is unused in v1.
- **AI assistant:** `apps/admin` only (never exposed on the public storefront in v1). Frontend sends a natural-language query to a FastAPI endpoint (`/api/v1/ai/query`; all v1 routes mount under `/api/v1`), which calls the LLM with a **read-only, pre-scoped** data context — see §3.2 for exactly how "read-only" is enforced. Demo-stage: this is a direct API call from FastAPI to the LLM provider with seeded/live data stuffed into context. Post-first-customer (per PRD, deferred): this becomes a separate FastAPI + LangChain microservice with LangSmith tracing, same read-only contract, just with tool-calling and observability instead of a single stuffed prompt.

### 1.4 UI reference

**Storefront (approved 2026-10-26):** the five screens in `docs/design/storefront-preview/` refine delea-demo.html for the public storefront, its checkout and the related admin screens; where they differ, the preview wins. Agreed differences: a full-width page (sticky header with the logo or a monogram, dark footer) instead of a panel in the admin; a split hero (left-aligned copy on the brand colour with an eyebrow line, a darker right panel with line art); four-column product cards (square tile, serif name, price on the right, metal underneath, full-width outline button); a trust strip whose items show only when true for the shop; and per-shop font and corner presets. Not adopted: category navigation (no categories in v1) and the admin polish screens (planned with later slices).

For anything storefront-, editor-, or AI-chat-UI related, delea-demo.html is the reference — theming via CSS custom properties (`--brand`, `--brand-gold`, etc.) driven by a config object, the shared `items` array rendering both the live storefront grid and the editable grid, and the chat bubble/quick-action pattern for the AI assistant. `apps/storefront` and the storefront-preview inside `apps/admin` should be built as React components implementing that same visual system, not a new one. Brand config (name, tagline, primary/accent color, logo) is stored per-tenant in the DB (§3), not hardcoded, matching the PRD's theming NFR.

---

## 2. Shared-RLS vs. Dedicated-Infra Split

### 2.1 What's config vs. what's a separate deployment

| Layer | Essential / Professional (shared) | Enterprise (dedicated) |
|---|---|---|
| **Postgres** | One Supabase project, all tenants in the same tables, isolated by RLS policies keyed on `tenant_id` | Separate Supabase project per tenant on dedicated compute (§2.3) — same schema, same migrations, same RLS policies |
| **Backend (FastAPI)** | One Azure App Service instance serving all shared-tier tenants, tenant resolved per-request from JWT/subdomain | Separate Azure App Service instance per Enterprise tenant, same container image, tenant-specific environment/config |
| **Redis** | Shared instance (if used), keys namespaced `tenant:{id}:*` | Dedicated Redis instance per tenant |
| **Frontend hosting** | Both apps on shared Vercel projects; tenant resolved by domain/subdomain at request time, brand config fetched at runtime | Same Vercel projects can still serve Enterprise tenants (frontend has no meaningful "isolation" need) — isolation is a backend/data concern, not a frontend one |
| **Deploy artifact** | Single backend image, single set of frontend builds | **Same** container image and frontend build — Enterprise isolation is about *where it runs and what data it touches*, not a different codebase |
| **Bicep** | One parameter set provisions the shared App Service (and Redis, if used). The shared Supabase project is created once, by hand or via the Supabase CLI | The same `main.bicep`, parameterized per tenant, provisions a new App Service + Redis. The dedicated Supabase project is created via the Supabase CLI/Management API (Bicep doesn't manage Supabase), then its connection string and keys are passed to Bicep as secure parameters |

The important invariant: **there is one codebase.** Enterprise doesn't get a fork — it gets its own copy of the same deployable artifact pointed at its own database and its own app service, provisioned by a parameterized Bicep template rather than by hand.

### 2.2 RLS policy shape (shared tier)

Every tenant-scoped table (`products`, `materials`, `invoices`, `expenses`, `automation_rules`, `brand_config`, etc.) carries a `tenant_id` column. Postgres RLS policies restrict all `SELECT`/`INSERT`/`UPDATE`/`DELETE` to rows where `tenant_id = auth.jwt() ->> 'tenant_id'`. Supabase Auth issues the JWT (ES256, per PRD NFR) with `tenant_id` and `app_role` (owner / staff / cpa) as custom claims, added by a custom access token hook that reads the `tenant_members` table. The claim is deliberately **not** named `role`: Supabase reserves the top-level `role` claim for the Postgres role (`authenticated` / `anon`).

### 2.3 Decision: Enterprise "dedicated DB" is a separate Supabase project

The PRD promises Enterprise a dedicated App Service, DB, and Redis. For the DB, that means **a separate Supabase project on dedicated compute**, not self-hosted Postgres on Azure. The alternative would have meant a second auth implementation (custom JWT issuance) alongside Supabase Auth, for a tier with no customers yet.

Consequences:
- **No tier branching in backend code.** The FastAPI app reads `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, and the DB connection string from environment config. Enterprise differs only in those values.
- **Same migrations everywhere.** `supabase/migrations/` is applied to the shared project and to each Enterprise project (`supabase db push --project-ref <ref>`). RLS stays enabled on Enterprise projects too — a single-tenant database still carries `tenant_id` and the same policies, so schema and tests never fork.
- **Provisioning runbook for an Enterprise customer:** (1) create the Supabase project, (2) apply migrations and seed brand config, (3) run `infra/bicep` with `enterprise.<customer>.bicepparam` to create the App Service (+ Redis), (4) set the App Service environment variables from the new project's keys, (5) point the customer's Vercel domains at the shared frontend projects.
- **Azure Postgres is not provisioned in v1.** If the Enterprise requirement ever changes to hard Azure-resident data, that's a new TRD decision, not a config change.

---

## 3. How the Four Resolved PRD Decisions Land in the Architecture

### 3.1 Order status enum → lives on the `invoices` table

```sql
create type order_status as enum (
  'quote', 'paid', 'in_production', 'shipped', 'delivered'
);

alter table invoices add column order_status order_status not null default 'quote';
```

No separate `orders` table. Automation rules (already in PRD scope — "order placed → email," "stock low → alert") can add a new trigger type keyed on `order_status` transitions (e.g., `in_production → shipped` fires a "your order shipped" email) without any new entity. The invoice list/detail UI in `apps/admin` replaces the flat `tag-ok "Paid"` styling in the demo with a status badge reflecting the enum's five states.

### 3.2 AI assistant read-only enforcement → enforced at the Postgres role level, not just in the prompt

Prompt-level instructions ("only answer, don't act") are not a security boundary — they're a suggestion to the model. Read-only is enforced structurally:

- The AI backend service authenticates to Postgres using a **dedicated Postgres role** (`ai_readonly`) that has `GRANT SELECT` only, on a restricted set of views scoped to the requesting tenant — no `INSERT`/`UPDATE`/`DELETE`/`EXECUTE` grants exist for that role at all. Even if a prompt injection or model error produced a "write" intent, there is no code path capable of executing it.
- The `/ai/query` FastAPI endpoint exposes **no tool/function-calling definitions that map to write operations** — in the demo-stage direct-call implementation, the LLM only ever receives a stuffed, pre-fetched read-only data context and returns text; there's nothing for it to invoke. In the post-launch LangChain microservice version, the tool registry passed to the agent simply never includes write-capable tools — this is a registration-time decision, not a runtime filter.
- This satisfies the PRD's v1 decision without needing confirmation UX, audit trails, or undo logic (all explicitly deferred) — because there's nothing to confirm or undo.

### 3.3 SKU-to-materials reference → many-to-many join table, no deduction logic

Two independent tables, `products` (finished SKUs) and `materials` (raw materials), each with their own stock/qty columns updated only by direct owner action. A join table captures the "typically consumes" relationship for reference only:

```sql
create table product_materials (
  product_id uuid references products(id) on delete cascade,
  material_id uuid references materials(id) on delete cascade,
  note text,  -- e.g. "1 blank, ~2g plating solution" — free text, not a quantity contract
  primary key (product_id, material_id)
);
```

No trigger, no application logic deducts `materials.qty_on_hand` when a `products` row's stock changes or an invoice is marked paid. This table exists purely so the admin UI can show "this SKU typically uses: 925 Silver blanks, packaging boxes" — informational, per the PRD's resolution. Full BOM auto-deduction (v2, per PRD) would later add quantity fields to this join table and a trigger/service that decrements `materials.qty_on_hand` on production events — that's out of scope now and this schema doesn't block adding it later.

### 3.4 Self-serve CPA invite → Supabase Auth invite flow + a `cpa` role scoped by RLS

- Owner-initiated: an "Invite CPA" action in `apps/admin` Settings calls a FastAPI endpoint that uses Supabase's admin API to send an invite email, creating a `tenant_members` row with `app_role = 'cpa'` for the resulting user, which the access token hook turns into the `tenant_id` and `app_role` JWT claims.
- RLS policies for CPA-role JWTs restrict access to read-only `SELECT` on `invoices`, `expenses`, and their CSV/PDF export endpoints only — no access to `products`, `materials`, `automation_rules`, or the AI assistant endpoint. This is a narrower grant than `staff`, not a copy of it.
- No separate onboarding flow or setup-fee service item is implied by this — it's a self-service action any owner can take from Settings, consistent with "self-serve" in your resolution.
- As built (§6.10): Settings → Team invites staff or a CPA; the invite email links to the admin's own accept page.

---

## 4. Project Folder Structure

This is the literal tree to scaffold — precise, not illustrative. Replaces the original `/delea-base/` tree: FastAPI backend (not NestJS), React + Vite (not the original frontend framework assumed in the uploaded docs), no `/mobile`, Bicep only (no Terraform).

```
delea-base/
├── apps/
│   ├── admin/                        # Authenticated owner/staff/CPA dashboard
│   │   ├── src/
│   │   │   ├── components/
│   │   │   │   ├── ai-assistant/     # Chat UI, quick-action cards (per delea-demo.html)
│   │   │   │   ├── storefront-editor/# Inline hero/product editor (per delea-demo.html)
│   │   │   │   ├── inventory/
│   │   │   │   ├── invoices/
│   │   │   │   ├── expenses/
│   │   │   │   ├── marketing/
│   │   │   │   ├── analytics/
│   │   │   │   ├── automation/
│   │   │   │   └── settings/
│   │   │   ├── routes/               # React Router route definitions per panel
│   │   │   ├── hooks/
│   │   │   ├── lib/
│   │   │   │   ├── supabaseClient.ts
│   │   │   │   └── apiClient.ts      # thin wrapper around packages/api-client
│   │   │   ├── App.tsx
│   │   │   └── main.tsx
│   │   ├── index.html
│   │   ├── vite.config.ts
│   │   ├── package.json
│   │   └── tsconfig.json
│   │
│   └── storefront/                   # Public, per-tenant branded storefront
│       ├── src/
│       │   ├── components/
│       │   │   ├── hero/
│       │   │   ├── product-grid/
│       │   │   ├── cart/
│       │   │   └── checkout/         # Stripe Checkout redirect/embed
│       │   ├── routes/
│       │   ├── lib/
│       │   │   ├── supabaseClient.ts
│       │   │   └── apiClient.ts
│       │   ├── App.tsx
│       │   └── main.tsx
│       ├── index.html
│       ├── vite.config.ts
│       ├── package.json
│       └── tsconfig.json
│
├── packages/
│   ├── ui/                           # Shared component library + design tokens
│   │   ├── src/
│   │   │   ├── tokens/               # CSS custom properties per delea-demo.html theming system
│   │   │   ├── components/           # Button, Card, Table, Tag, Metric, etc.
│   │   │   └── index.ts
│   │   ├── package.json
│   │   └── tsconfig.json
│   │
│   ├── api-client/                   # Typed client for the FastAPI backend
│   │   ├── src/
│   │   │   ├── endpoints/            # products.ts, invoices.ts, materials.ts, ai.ts, etc.
│   │   │   ├── types.ts              # Mirrors backend Pydantic schemas
│   │   │   └── index.ts
│   │   ├── package.json
│   │   └── tsconfig.json
│   │
│   └── config/                       # Shared tsconfig/eslint/prettier bases
│       ├── tsconfig.base.json
│       ├── eslint-preset.js
│       └── package.json
│
├── backend/
│   ├── app/
│   │   ├── main.py                   # FastAPI app entrypoint
│   │   ├── core/
│   │   │   ├── config.py             # env/settings (per-tenant + per-environment)
│   │   │   ├── security.py           # JWT validation (ES256), role/claim checks
│   │   │   └── supabase.py           # Supabase client factory (tenant-scoped role)
│   │   ├── api/
│   │   │   ├── deps.py               # shared FastAPI dependencies (auth, tenant context)
│   │   │   └── v1/
│   │   │       ├── products.py
│   │   │       ├── materials.py
│   │   │       ├── invoices.py       # includes order_status transitions
│   │   │       ├── expenses.py
│   │   │       ├── automation.py
│   │   │       ├── settings.py       # brand config, company/tax info, CPA invites
│   │   │       ├── ai.py             # /ai/query — read-only enforcement lives here
│   │   │       ├── public.py         # unauthenticated storefront endpoints; tenant resolved from hostname (added with the storefront endpoints task)
│   │   │       ├── stripe_webhooks.py
│   │   │       └── router.py         # aggregates v1 routers
│   │   ├── models/                   # Pydantic schemas, mirrored by packages/api-client/types.ts
│   │   ├── services/
│   │   │   ├── ai_service.py         # demo-stage: direct LLM call w/ read-only scoped context
│   │   │   ├── invoice_service.py
│   │   │   ├── automation_service.py
│   │   │   └── stripe_service.py
│   │   └── db/
│   │       └── session.py            # DB session/connection helpers (tenant-scoped, ai_readonly role)
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
│
├── ai-service/                       # Post-launch: LangChain + LangSmith microservice
│   ├── (scaffolded but not built in v1 — placeholder per PRD's deferred scope;
│   │   demo/v1 AI logic lives in backend/app/services/ai_service.py instead)
│   └── README.md                     # notes the deferral + what moves here later
│
├── infra/
│   ├── bicep/
│   │   ├── main.bicep                # top-level module, parameterized by tier
│   │   ├── modules/
│   │   │   ├── appService.bicep
│   │   │   └── redis.bicep           # conditional/optional per tier
│   │   ├── parameters/
│   │   │   ├── shared.bicepparam     # Essential + Professional (one shared deployment)
│   │   │   └── enterprise.template.bicepparam  # copy to enterprise.<customer>.bicepparam per Enterprise tenant
│   │   └── README.md                 # includes the Enterprise provisioning runbook (TRD §2.3)
│   └── scripts/
│       └── provision-enterprise.md   # step-by-step: Supabase project → migrations → Bicep → env vars → domains
│
├── supabase/                         # Supabase CLI project root (applies to shared AND Enterprise projects)
│   ├── config.toml
│   ├── migrations/                   # timestamped SQL: schema, order_status enum, product_materials, RLS policies, ai_readonly role + views
│   ├── seed.sql                      # demo/seed data (brand config, sample products/materials/invoices)
│   └── tests/                        # pgTAP tests: tenant isolation, cpa/staff/owner policies, ai_readonly has SELECT only
│
├── docs/
│   ├── DELEA-PRD.md
│   ├── DELEA-TRD.md                  # this document
│   └── delea-demo.html               # UI/UX reference, not re-derived elsewhere
│
├── .github/
│   └── workflows/                    # CI: lint/test/build per app, deploy triggers
│
├── package.json                      # workspace root
├── pnpm-workspace.yaml               # packages: ["apps/*", "packages/*"]
├── turbo.json                        # build/lint/test/dev pipeline across apps + packages
└── README.md
```

**Tooling notes for scaffolding:**
- **pnpm workspaces + Turborepo** cover `apps/*` and `packages/*` only. `backend/` is a Python project (`pyproject.toml`) and is not a pnpm workspace member; CI builds it separately.
- **Migrations live only in `supabase/migrations/`.** `backend/` never owns schema. The `order_status` enum (§3.1), `product_materials` join table (§3.3), RLS policies (§2.2), and the `ai_readonly` role and views (§3.2) all land there as migrations. `supabase/tests/` verifies tenant isolation and that `ai_readonly` holds `SELECT` only.
- **`ai-service/`** stays a documented placeholder (README only) until the first paying customer, per the PRD.

---

## 5. Open Items

None. All TRD decisions are locked (see the list at the top). Anything new that surfaces during scaffolding gets flagged and decided before the spec changes.


---

## 6. As built: backend

What the FastAPI backend implements so far, and the rules it follows. Details live in `backend/README.md`.

### 6.1 Request model
Every authenticated request runs in **one transaction** as `authenticated`, with the caller's verified JWT claims set, so **RLS is the enforcement** (decision 6).
- **Commit timing:** the transaction commits when the endpoint returns, before the response is sent, and rolls back on any error.
- **`tenant_id`:** comes only from the verified token. Request bodies reject a `tenant_id` field with 422, and every query also filters on the caller's tenant.
- **`require_role`:** mirrors the RLS policies to give clear 403s. It never grants more than RLS does.

### 6.2 Routes (all under `/api/v1`)
| Route | owner | staff | cpa |
|---|---|---|---|
| `products` (list with `q`, `is_published`, `metal`, `low_stock`; get · create/update/delete) | ✓ · ✓ | read | — |
| `products/{id}/images` (uploads, register, primary, order, delete; images are read with the product) | ✓ | read | — |
| `materials` (list with `q`, `low_stock`; get · writes) | ✓ · ✓ | read | — |
| `customers` (list/get · create/update · delete) | ✓ · ✓ · ✓ | ✓ · ✓ · — | — |
| `expenses` (list and `summary` with date range / category / deductible / `q`; get; `export.csv` · writes) | ✓ · ✓ | — | read |
| `expenses/{id}/receipt` (view: a 60-second signed link · `uploads`, register, remove) | ✓ · ✓ | — | view |
| `settings/company` (get · patch; the currency is never writable) | ✓ · ✓ | — | — |
| `settings/brand` (get · patch) | ✓ · ✓ | read | — |
| `settings/brand/logo` (`uploads`, register, remove) | ✓ | — | — |
| `settings/currency` (get; via `current_tenant_currency()`) | ✓ | read | read |
| `invoices` (list/get/`export.csv`) | ✓ | ✓ | read (`items` null) |
| `invoices` create (`pay_now`), `POST /preview`, `PUT /{id}/items`, `POST /{id}/items/preview`, `POST /{id}/status`, `PATCH /{id}` (notes, due date) | ✓ | ✓ | — |
| `invoices` tax-rate override, `/{id}/refund`, `/{id}/cancel`, delete (quotes only), correcting a payment method (`PATCH /{id}`) | ✓ | — | — |
| `invoices/{id}/payments` (get) | ✓ | ✓ | read |
| `invoices/{id}/payments` (record), `/{id}/start-production`, `/{id}/settle` | ✓ | ✓ | — |
| `invoices/{id}/payments/{payment_id}/void`, `/{id}/refund` of a deposit | ✓ | — | — |
| `settings/payments` (get) · `settings/payments/stripe/onboarding-link` (post; not for demo shops) | ✓ | — | — |
| `public/orders/{token}/pay` (Stripe Checkout for the balance; 10/h per address) | public | public | public |
| `stripe/webhook` (Stripe-signed; no token) | Stripe | Stripe | Stripe |
| `dashboard/summary`, `dashboard/monthly` | ✓ | — | — |
| `members` (list · invite staff/cpa · `/{user_id}/resend-invite` · `PATCH /{user_id}` role · `DELETE /{user_id}`) | ✓ | — | — |
| `auth/password-reset` (public, no token; always 202) | public | public | public |
| `settings/storefront` (get · patch: store online, contact email, pages, shipping, pickup, manual payment) | ✓ · ✓ | — | — |
| `products/{id}/personalization` (put: replace the fields) | ✓ | — | — |
| `invoices/counts` (website orders awaiting payment) | ✓ | ✓ | — |
| `public/store`, `public/products`, `public/products/{id}`, `public/pages/{slug}` (no token; the shop comes from the `Origin` host, §6.11) | public | public | public |
| `public/checkout/quote`, `public/orders`, `public/orders/{token}` (no token; §6.12) | public | public | public |

**Also:**
- **Health:** `GET /health` (liveness) and `GET /health/ready` (database check) sit outside `/api/v1`.
- **Invoices:**
  - **Client input:** the client sends only `customer_id` (optional for walk-in), `items [{product_id, qty}]`, notes, due date, an owner-only tax-rate override, `pay_now` and, with `pay_now`, a `payment_method`. Everything else is computed by the server; money, status or number fields are a 422.
  - **Preview:** `POST /preview` (and `POST /{id}/items/preview` for a quote's new lines) prices the lines exactly as create (or `PUT /items`) would and lists stock shortages. It writes nothing and uses no document number; the tax override is owner-only here too. The admin shows only these server totals, never a total of its own.
  - **Quote-only changes:** `PUT /items` is quote-only (409 "only quotes can be edited").
  - **Payment method** (`cash`, `card`, `bank_transfer`, `other`): required when an invoice leaves quote (`pay_now`, or a status move from quote; otherwise 422) and refused on any other move. It's null on quotes (a check constraint) and on invoices paid before it existed. Staff can't change it once set (database guard); the owner can correct it with `PATCH /{id}`. It's a CSV column. Partial payments will be a separate payments table later.
  - **Notes and due date:** `PATCH /{id}` edits notes at any status and the due date on quotes only (409 otherwise).
  - **Status moves:** they pass the database's messages through: backward → 422 ("This invoice can't move back to paid."), insufficient stock → 409 ("Not enough stock for SKU …: N available, M needed."), with nothing changed and no number used.
  - **Cancelled and refunded invoices are closed:** their status can't move, a cancelled invoice can't be refunded and a refunded one can't be cancelled (409), in the API and in a database trigger for every caller.
  - **`pay_now`:** atomic. An in-store sale is `pay_now` followed by a move to delivered; if that second step fails the sale stays paid and the admin offers a retry.
  - **Line snapshots:** the product name and unit price. There's no SKU column on line items.
- **Deletes that would damage history** (a product on invoice lines, a customer with invoices, a material used by products) return 409 with a clear message.

### 6.3 Error mapping
Every error response is `{"detail": "..."}`, never with SQL text or stack traces.

| Cause | Status |
|---|---|
| missing, invalid or expired token; a valid token whose membership changed since it was issued ("Membership no longer valid", `WWW-Authenticate: Bearer error="invalid_token"`) | 401 |
| no membership (with `X-Delea-Access: no-membership`), wrong role, RLS violation, insufficient privilege | 403 |
| password-reset requests over the per-IP limit | 429 |
| no row, or a row hidden by RLS | 404 |
| unique, foreign-key or `P0001`; a delete that would damage history; editing a non-quote | 409 |
| check, not-null, invalid value, request validation | 422 |
| JWKS or database unreachable | 503 |

Messages raised by our own triggers (`RAISE`) are passed through; Postgres-generated messages stay generic.

### 6.4 CORS
Two rule sets (`app/core/cors.py`). **Public storefront paths** (`/api/v1/public/...`): the allowed origins are each tenant's storefront domains, looked up per request (§6.11); `GET` and `POST`, the `Content-Type` header only, no credentials; an origin on the admin list is not thereby allowed here. **Every other path:** the rules below.

Allowed origins come from `CORS_ORIGINS`: a comma-separated list of exact origins, defaulting to `http://localhost:5173,http://localhost:5174`. A wildcard is rejected at startup.
- **Methods:** `GET, POST, PATCH, DELETE, OPTIONS`.
- **Headers:** `Authorization, Content-Type`.
- **Exposed response headers:** `X-Delea-Access` (the admin reads it to tell "no business membership" from other 403s).
- **Credentials:** none.

### 6.5 Money rules
All amounts are integer cents. v1 supports two-decimal currencies only: every amount is hundredths of the major unit, and `company_settings.currency` must be one of USD, CAD, EUR, GBP, MXN, AUD, NZD or CHF (validated by the backend). Zero-decimal (e.g. JPY) and three-decimal (e.g. KWD) currencies are not supported. Rounding is half-up, in integer arithmetic: `(a × bps + 5000) // 10000`. These rules are implemented in `backend/app/services/pricing.py`, and will be used by the invoices endpoints.
- **Unit price:** the sale price while a sale is active (`sale_price_cents` set and `sale_ends_at` in the future, or no `sale_ends_at`), otherwise `price_cents`.
- **Wholesale customers:** the lower of that price and `round_half_up(price_cents × (10000 − discount_bps) / 10000)`. Discounts never stack.
- **Totals:**
  - `line_total = unit × qty`;
  - `subtotal = Σ line_total`;
  - `tax = round_half_up(subtotal × rate_bps / 10000)`, once on the subtotal;
  - `total = subtotal + tax`.
- **Tax rate:** 0 for a tax-exempt customer; otherwise an owner-only override if given, else the tenant's `default_tax_rate_bps`. The rate used is stored on the invoice as a snapshot.
- **Client input:** a client never sends or influences subtotal, tax or total.
- **CSV exports:** amounts are written as exact decimal strings (`12.50`). Any cell starting with `= + - @`, tab or CR is prefixed with `'`.
- **Dashboard:**
  - **Revenue (net of refunds, decision R1):** the subtotal of invoices paid in the period (cancelled ones excluded), minus the revenue share of every refund given in the period, whatever month the invoice was paid in. A refund's share is `amount × subtotal / total`, rounded half away from zero, so a full refund removes exactly the subtotal; the month of the sale keeps it. Only refunds of paid invoices given on or after they were paid count: returning a deposit is not a sale undone.
  - **Tax reserve:** their tax, minus the tax share of the same refunds (`amount × tax / total`).
  - **Customer deposits held:** money received on open quotes (not cancelled). A liability, not revenue; current, not per period.
  - **A refund never restocks**, whether recorded in DELEA or (Slice B2) given in Stripe.
  - **Net profit:** revenue − expenses (all expenses dated in the period, up to today).
  - **Periods:** UTC calendar month and year to date.

### 6.6 Known limitations
- **Token claims are re-checked on every request (done, slice 9).** Claims are fixed when a token is issued (about an hour). `user_session` and `ai_session` run `public.is_current_member(user, tenant, role)` first (two primary-key lookups, in the statement that sets the claims, so no extra round trip) and answer 401 "Membership no longer valid" when the membership was removed, its role changed, or the tenant was deactivated. `current_tax_rate_bps()` and `current_tenant_currency()` run the same check, and the access token hook gives no claims for an inactive tenant. Code that calls `apply_user_scope` directly (tests only) has no re-check.
- **One account holds one membership.** The access token hook reads one membership per user, so an email that already has an account in another business can't be invited (the owner sees a neutral refusal that never says where it belongs). A CPA who serves several businesses needs a separate address for each (for example `name+business@gmail.com`). The later fix is a tenant switcher (a selected tenant in `app_metadata`, read by the hook).
- **Auth emails and demo tenants.** The API sends no invite or reset email for a demo tenant, and the database refuses team changes in one. But a browser holding a demo account's session, or just the publishable key, can still ask Auth directly for a password reset or an email change, and Auth sends those itself; Postgres can't stop it. This is only a risk on a project with a real email sender. **Rule:**
  - The public demo runs in its **own Supabase project with no custom SMTP**. Supabase's built-in mailer delivers only to the project's team members (the members of its Supabase organization), at about 2 messages an hour, and isn't for production ("Send messages only to pre-authorized addresses", [Send emails with custom SMTP](https://supabase.com/docs/guides/auth/auth-smtp)). So invites, resets and email-change confirmations sent from the demo project can't reach third parties. Keep that organization's members to our own team.
  - **Launch blocker:** any project that mixes demo tenants with real-customer tenants, or has a custom SMTP sender and demo tenants, needs a Send Email Hook first (Auth calls it instead of sending; it refuses demo accounts and sends everything else).
- **A removed member's Auth account can be left behind.** Removing a member commits, then deletes their Auth user when they have no other membership. If that Auth call fails, it's logged and the account stays without a membership: it can't use the API (no claims at sign-in), but inviting that email again gets the neutral refusal until the account is deleted by hand.
- **Password-reset limit behind a proxy.** **Required before the public demo is deployed (not done yet):** trusted proxy headers configured and checked on the deployed App Service (§6.10, "Client address behind a proxy").
- **Orphaned Storage objects.** **Milestone B (not implemented yet):** one scheduled orphan sweep that deletes:
  - abandoned raw uploads in `product-uploads` older than 24 hours (uploaded through a signed URL but never registered; registration always deletes them);
  - abandoned receipt uploads (`{tenant}/{expense}/pending-*` in `expense-receipts`) older than 24 hours, same cause;
  - receipt files no expense points to: a failed cleanup after an expense is deleted, or after its receipt is replaced or removed (these deletes run after the commit and are only logged when they fail);
  - abandoned logo uploads (`{tenant}/brand/*` in `product-uploads`, covered by the first item) and logo files in `brand-assets` that no `brand_config.logo_path` points to (a failed cleanup after a logo is replaced or removed).
- **Error responses have no machine-readable code:** an error body is only `{"detail": "<message>"}`, so the admin app tells a duplicate value apart from other 409s by matching the message text ("A record with these values already exists"). **Milestone B (not implemented yet):** add a stable `code` field to every error response (for example `duplicate_value`, `in_use`), and have the frontend switch on `code` instead of message text.
- **Multi-tenant users:** a user in several tenants gets their oldest membership of an active tenant (see "One account holds one membership").
- **Direct database API: closed.** `public` isn't exposed over REST or GraphQL (Schema §9 "No direct data API"), so the API-only rules can't be bypassed with a user's JWT. Hosted projects need the same Data API settings; see the provisioning runbook.
- **No restock on refund or cancel (v1):** a refund records its payment row and, for a paid invoice, `refunded_at`; a cancel sets `cancelled_at`. Neither puts stock back. Restock by hand if the piece comes back.
- **The API's database login is an administrative one:** the backend connects as `postgres` and switches to `authenticated` (or `ai_readonly`) for user and AI sessions, but `service_session` (public endpoints, Stripe webhooks, the future worker) keeps that login, which bypasses RLS and could write anything, `tenants.is_demo` included. The `is_demo` guard covers every app role (`authenticated`, `anon`, `ai_readonly`, `service_role`) but not that login. **Required before the public demo is deployed (not implemented yet):** a least-privilege runtime login for the API, separate from the administrative login used by migrations and the showcase loader. It must be granted `storefront_reader`, `storefront_writer`, `payments_writer` and `email_worker` (public storefront reads, the checkout, the Stripe webhook and the email worker switch into them; migrations 20261027000100, 20261027000400, 20261028000700 and 20261029000200 grant them to `postgres` today).
- **Storefront launch blockers:** before any real shop is served, the least-privilege login above and the trusted proxy headers (§6.10, "Client address behind a proxy"): the public storefront's per-address limit keys on the same client address.

### 6.7 Tenant provisioning and demo tenants
- **Provisioning** lives in `backend/app/services/provisioning.py`: `provision_tenant` creates `tenants`, `company_settings`, `brand_config`, `storefront_settings` (switched off), any `tenant_domains` and the members (`tenant_members`) in one transaction; the member users are created first through `backend/app/core/auth_admin.py` (confirmed, so Supabase sends no email). It runs on an administrative connection. Local and hosted differ only in the connection and keys the caller passes. Today the showcase loader is its only caller; a platform-admin onboarding endpoint would call the same function.
- **Demo tenants** (`tenants.is_demo`, migration `20261022000100`): only an administrative connection can set or change it (a trigger refuses every other role, `service_role` included; pgTAP covers it). `delete_demo_tenant` and `remove_tenant_storage` refuse a tenant that isn't a demo tenant.
- **Worker contract (built in Slice D, §6.16):** a demo tenant's email goes to that tenant's own owners only, never to anyone else (enforced by `email_enqueue`), and every event that already has `processed_at` is skipped. Stripe (Slice B2, decision D16): a demo tenant may use Stripe test mode only; a live key, or a live-mode event for a demo tenant, is refused. The showcase loader marks every event it causes as processed, creates no automation rules, and uses `example.com` customer addresses with marketing opt-in off.
- **The DELEA Showcase** (`showcase/README.md`): loaded by `backend/scripts/load_showcase.py` from `showcase/products.yaml` and photos outside the repository. It refuses non-local targets unless `--allow-remote` is given, and then needs its own `SHOWCASE_DATABASE_URL` and `SHOWCASE_PASSWORD`. Its only trigger bypass (`session_replication_role = replica`) is one tenant-scoped update of the invoices' timestamps and numbers, followed by integrity checks that re-validate every foreign key and check constraint.
- **Demo deployment — Milestone B (not implemented yet):** a public demo lets visitors edit and upload. The hosted demo needs (1) a scheduled reset that re-runs the loader (for example nightly), and (2) upload and rate limits for demo tenants (a small per-tenant image and request budget), so the demo can't be used as free file hosting or filled with junk between resets, and (3) a check of the storefront's framing header on the real Vercel deployment (`frame-ancestors 'self'` plus the admin origin, from the middleware, §6.18), with a fallback if the middleware doesn't run there: a static `vercel.json` header per deployment. Also required before it is deployed: the least-privilege runtime login for the API (§6.6).

### 6.8 Expenses and receipts
- **Categories** are a fixed list (enum `expense_category`, migration `20261023000100`): materials, packaging, shipping, marketing, platform_fees, software, equipment, rent, utilities, phone, insurance, professional_services, taxes_licenses, travel, meals, other. Required; the default is `other`. Legacy text was mapped by `expense_category_from_legacy()`: `services` → professional_services; null, empty and unknown values → other. Adding a category is a one-line migration (`alter type ... add value`).
- **Filters:** the list, `GET /expenses/summary` (count, total, deductible total over every page) and the CSV share one parser: date range (inclusive), category, deductible, and `q` on the description.
- **Receipts:** one per expense, in the private `expense-receipts` bucket (10 MB; JPEG, PNG, WebP, PDF). No client can read or write the bucket (RESTRICTIVE policies); uploads go through a signed upload URL the API issues, to `{tenant}/{expense}/pending-{upload}.{ext}`. Registration rebuilds that path from the token, checks the real content, re-encodes images in their own format (JPEG quality 90, PNG, WebP quality 90) after applying the EXIF orientation, with all metadata dropped (25 MP guard), keeps PDFs unchanged (they must start with `%PDF-`), and stores `{tenant}/{expense}/{receipt}.{ext}`; the pending upload is always deleted. The path never leaves the server: owner and CPA view a receipt through a signed link valid for 60 seconds (`Cache-Control: no-store`). The CSV has `has_receipt`, never a link. Expense bodies never accept receipt fields. Replacing or removing a receipt, or deleting the expense, deletes the file after the commit.
- **Roles:** owner reads and writes everything, CPA reads (list, summary, CSV, view receipts), staff have no access.

### 6.9 Settings
- **Company settings** (`GET/PATCH /settings/company`, owner only in the API and RLS): legal name, tax ID (`ein`, free text for any country: EIN, RUC, VAT…), address (one text field), default tax rate, storefront languages. Read-only: currency, document counters. The Stripe account is under `GET /settings/payments` (Slice B2, decision K2).
- **Currency:** chosen at provisioning (`TenantSpec.currency`, one of the two-decimal currencies). The API never accepts it, and a trigger (`company_settings_10_currency_guard`, migration `20261023000200`) refuses a change for every caller once the tenant has products, materials, invoices or expenses.
- **Tax rate:** a new default applies to new quotes only. Every invoice stores the rate it was created with (`invoices.tax_rate_bps`); replacing a quote's lines, and its preview, reuse that rate. Nothing rewrites existing invoices.
- **Brand** (`GET/PATCH /settings/brand`, owner writes, staff read for admin theming): store name, tagline, colours, hero title and subtitle. The logo is uploaded (owner only) through the product-image pipeline: a signed upload to `product-uploads` at `{tenant}/brand/{upload}.{ext}`, then registration rebuilds that path from the token, checks the real content (JPEG, PNG or WebP, 2 MB, 25 MP; no SVG), strips metadata, keeps transparency and writes one WebP of at most 512 px to the public `brand-assets` bucket at `{tenant}/logo-{uuid}.webp` (1 MiB, WebP only, no client access; a check keeps `brand_config.logo_path` under its tenant). The URL is `BRAND_ASSETS_BASE_URL` (or the local stack's public URL) plus the path. A replaced or removed logo is deleted after the commit.
- **Storefront look** (part of the brand, migration 20261027000100): a line above the headline (`hero_eyebrow`, 60 characters), the product list heading (`collection_title`, 80), fonts (`font_preset`: classic Fraunces + Archivo, or modern DM Serif Display + Work Sans), buttons (`corner_style`: square or pill), the mark without a logo (`mark_style`: monogram or name only), and an optional second hero button (`hero_cta_label`, 40; `hero_cta_url`). The link accepts only `https://` with a plain host (so `https://wa.me/…`) or `tel:`; the form, the API and a database check apply the same rule, and the storefront shows the button only when both are set.
- **Storefront** (`GET/PATCH /settings/storefront`, owner only; table `storefront_settings`, owner full and staff read in RLS): "Store is online" (`storefront_enabled`, off when provisioned), the contact email, and four text pages: How custom orders work, Returns, Privacy policy, Terms (20,000 characters each). Everything in this table is public.
- **Team:** a link to Settings → Team (§6.10).
- **Out of scope here:** domains (added by an admin, §6.11), automation rules, Stripe and the website editor.

### 6.10 Team, invites and passwords
- **Members API** (`/members`, owner only in the API, RLS and `tenant_member_list()`): list with email, role, status (`invited` until the invite is accepted, then `active`), `invite_expired` (the 24-hour link lapsed), invited and last sign-in times; invite; resend an invite (new link, the old one stops working); change a role; remove.
- **Rules**, enforced by the API and by the `tenant_members_guard` trigger (migration `20261025000100`, every app role; provisioning and Auth's cascades are trusted):
  - owners invite staff or a CPA; nobody is invited as, or promoted to, owner (owners come from provisioning);
  - a business keeps at least one owner (serialized per tenant with an advisory lock); the owner can't change or remove their own membership (API, 409);
  - only the role can change on a membership row;
  - no team changes in a demo tenant (API 409, database 42501).
- **Existing accounts** (Auth's `email_exists`): already on this team → 409 "already on your team" (or "already invited, use Resend invite"); any other account → 409 "This email can't be invited. Ask them for a different address (for example name+yourbusiness@gmail.com)." It never says where the account belongs.
- **Invite:** the API asks Auth (`/invite`, service-role key) to create the user and send the email, then inserts the membership in the owner's session; if that insert fails, the new Auth user is deleted. The email (`supabase/templates/invite.html`) links to `{SiteURL}/accept-invite?token_hash=…&type=invite`. That page verifies the token only when the form is submitted (so email link scanners can't use it up), then sets the password (at least 10 characters) and signs in. An expired or used link says so and asks for a new invite.
- **Remove:** the membership is deleted and committed; if the account has no other membership, its Auth user is deleted too, which ends its sessions and refresh tokens. Its old access token is refused on the next request by the re-check (§6.6).
- **Password reset:** "Forgot password?" on the sign-in page → `POST /auth/password-reset` (public). It always answers 202 with the same message and does the same work for every address (abuse checks and one insert, on SHA-256 hashes); the account lookup and Auth's `/recover` call run in a background task. Limits (`password_reset_attempts`): 5 requests per IP per 15 minutes (then 429), and one email per address per 60 seconds (later requests get the same 202 and send nothing). Nothing is sent for a demo account. The email links to `{SiteURL}/reset-password?token_hash=…&type=recovery`, which works like the accept page.
- **Client address behind a proxy. Required before the public demo is deployed (not done yet).** The per-IP limit keys on the client address uvicorn reports (`request.client`). uvicorn reads `X-Forwarded-For` only when the TCP peer is in `FORWARDED_ALLOW_IPS` (default `127.0.0.1,::1`; proxy headers are on by default). Behind Azure App Service the API's peer is the platform's front end, so:
  - with the defaults, every client shares the front end's address: one budget for everyone, and one abuser blocks all resets;
  - with `FORWARDED_ALLOW_IPS="*"`, uvicorn takes the leftmost `X-Forwarded-For` entry, which the client writes itself, so the limit can be dodged with a spoofed header. Never use `*`;
  - the deployment must set `FORWARDED_ALLOW_IPS` to the front end's addresses (or range), so uvicorn takes the rightmost untrusted entry, the one the platform appends. Which header and format the front end sends (for example a port after the address) must be confirmed on the deployed service.
  - **Tests:** `backend/tests/integration/test_password_reset.py` runs the API behind uvicorn's `ProxyHeadersMiddleware` trusting one proxy and proves that the limit keys on the real client (not the proxy; another client behind the same proxy keeps its own budget), that a client-supplied `X-Forwarded-For` with a fresh fake address each time still hits 429, and that the header is ignored from a peer that isn't the proxy. **Also required before the public demo:** the same check against the deployed App Service (six requests from one machine, each with a different spoofed `X-Forwarded-For`, must end in 429).
  - Locally, uvicorn trusts `127.0.0.1`, so a client on the same machine can set its own address; that's acceptable for development only.
- **Signals the admin acts on** (status and header, never message text): a 401 makes the API client refresh the session once and retry, so a role change takes effect on the retry; a 403 with `X-Delea-Access: no-membership` shows the no-access page.
- **Auth config** (`supabase/config.toml`, and the same on every hosted project, see `infra/scripts/provision-enterprise.md`): public sign-up off (`[auth] enable_signup = false`; accounts come only from provisioning and invites), the email provider on (`[auth.email] enable_signup`, which is that switch), passwords of at least 10 characters, one-time links valid 24 hours (`otp_expiry`, shared by invites and resets), the two templates. `backend/tests/test_auth_config.py` fails if sign-up is switched back on; `backend/scripts/check_auth_settings.py` checks a hosted project.

### 6.11 Storefront (Slice A1: read-only)
- **Which shop:** the tenant is the one whose storefront domain sent the request's `Origin` (`app/core/storefront_hosts.py`). Its `host[:port]` is looked up in `storefront.hosts`: kind `storefront`, an active tenant, and "Store is online". Anything else (no Origin, an unknown, inactive or switched-off shop, an admin domain) is 404 "Store not found". Forging `Origin` outside a browser only selects which shop's public data is read. Lookups are cached in-process for 60 seconds, unknown hosts included, so a new or removed domain or switch takes effect within a minute.
- **Development shortcuts:** `localhost`, `*.localhost` and plain `http://` origins are accepted only when `ENVIRONMENT` is `local` or `test`. The seed maps `localhost:5174` (tenant A) and `aurora.localhost:5174` (tenant B); the local showcase loader maps `showcase.localhost:5174`. No override variable exists.
- **Read path (migration 20261027000100):** public reads run in `storefront_session`, a read-only transaction as `storefront_reader`. That role can select only from the `storefront` views (`hosts`, `store`, `products`, `product_images`), which are owned by the migration role, filter on `app.tenant_id` (except `hosts`), and list public columns only. It holds no grant on any `public` table or function. pgTAP checks the grants, the exact column lists and the tenant scoping.
- **Public fields:** the store (name, tagline, colours, logo URL, the storefront look, the currency, the contact email, which pages have text, whether any made-to-order product is published, `checkout_enabled` false until A2) and published products (name, description, metal, price, the sale price and its end only while the sale is active, the price to pay now, availability, lead-time note, images). Never public: SKU, stock count, low-stock threshold, costs, the tax ID, the Stripe account, document counters, customers, invoices.
- **Availability:** `made_to_order`, else `out_of_stock` (stock 0 or less), else `low` (below the product's threshold), else `in_stock`. The count is never sent.
- **Made to order** (`products.made_to_order`, `lead_time_note` up to 60 characters): made after the sale. The stock trigger neither checks nor decrements such lines, the invoice preview never reports them short, and the storefront shows them as orderable with the note.
- **Limits:** 120 public requests per minute per client address (in-process sliding window, counted before any database work; 429 with `Retry-After`, readable by a storefront origin the host cache already knows so the shop can ask the shopper to wait). It keys on the address uvicorn reports, so the proxy settings in §6.10 apply.
- **Responses:** `Cache-Control: public, max-age=60` and `Vary: Origin`.
- **Domains:** added by a DELEA admin with `backend/scripts/add_tenant_domain.py` (normalizes to lowercase punycode, refuses ports and `localhost` names on a hosted database and a domain owned by another tenant). The full procedure (Vercel, Cloudflare DNS, the script, the switch, a smoke test) is `infra/scripts/storefront-domain.md`. No self-serve domain screen.
- **The app** (`apps/storefront`): header, hero, collection, a Made to order page, product pages and the text pages, per the preview (§1.4). Fonts are bundled with the build (`@fontsource`), never requested from Google. Light only. Text pages are shown as plain text, never HTML. The bag and checkout screens come with the second half of Slice A2 (the API is in §6.12).
- **Sales tax on online orders (accepted limitation, v1 and the demo):** every order uses the tenant's single default rate (Schema §8 item 3). That is not correct for a shop shipping to buyers in other US states, where destination rates and economic-nexus rules can apply. Destination tax (for example Stripe Tax) is not built; owners selling across states must account for it outside DELEA until it is.

### 6.12 Storefront checkout (Slice A2: manual payment)
- **Who writes:** the public checkout runs as `storefront_writer`, a role with no table grants that may execute four SECURITY DEFINER functions only (migration 20261027000400): `storefront_price_cart`, `storefront_find_order`, `storefront_count_attempt`, `storefront_place_order`. Each pins `search_path = ''`, re-checks the tenant itself (active, store online), and has EXECUTE revoked from `public`, `anon`, `authenticated` and `service_role`; pgTAP proves an anonymous caller and another tenant's signed-in owner get 42501. `storefront_reader` may execute the pricing function (for quotes) and nothing else.
- **One pricing rule:** `storefront_price_cart` prices every storefront cart: retail prices (the sale price while active), shipping from the shop's settings (flat rate, free when the subtotal reaches the threshold, 0 for pickup), tax at the shop's single default rate on the subtotal (plus shipping when "tax shipping" is on), rounded half up in integer arithmetic as `pricing.py`. It also checks stock (summed per product; made-to-order is never short) and personalization. The quote and the order call the same function, so they can't disagree. Admin invoices keep using `pricing.py` (no shipping); `backend/tests/integration/test_pricing_parity.py` runs the same carts through both (half-cent rounding, sale prices, the free-shipping threshold, tax on shipping on and off, and 300 seeded random carts) and requires identical subtotal, tax and total.
- **Endpoints:**
  - `POST /public/checkout/quote`: prices a cart without writing; each line says `unavailable` (unpublished, unknown or not enough stock, no counts shown) or `options` (personalization to fix). 60 per minute per address.
  - `POST /public/orders`: the hidden `website` field and a minimum fill time of 2 seconds, measured from when the checkout page loaded, catch simple bots: both get the same 422 (`reason: "try_again"`, a friendly "place it again"), nothing is stored, and placing it again reuses the same checkout key; a repeat of a checkout key with the same request returns its order (200) before Turnstile, a different request with that key is 409; 20 attempts per address per hour; Turnstile; then `storefront_place_order` writes the customer (an existing retail customer with that email, never updated and never a wholesale one; else a new retail customer), an unpaid quote (`source = 'storefront'`, a `W-` reference of eight Crockford base32 characters, the shopper's payment choice), its lines with personalization snapshots, and the contact, in one transaction. Created 201. A total that differs from the shopper's `expected_total_cents`, or a line that can't be ordered, is 409 with the new quote; nothing is written. Created orders are limited to 5 per address, 3 per email and 30 per shop per hour.
  - `GET /public/orders/{public_token}`: one order, only in its own shop (views filter on the tenant and the token); first name and city/state only; the payment instructions while unpaid; `Cache-Control: no-store`, `Referrer-Policy: no-referrer`. 30 per minute per address.
- **Turnstile (fails closed):** outside `local`/`test` a missing `TURNSTILE_SECRET_KEY` turns checkout off (`/public/store` says `checkout_enabled: false`, orders 503); Cloudflare unreachable or unreadable refuses the order (503); a token passes only for this shop's hostname and the `checkout` action. In `local`/`test` with no key the check is skipped (logged). The site key is the storefront's `VITE_TURNSTILE_SITE_KEY`. Cloudflare's published test keys answer without an action and for another hostname; such an answer (`metadata.result_with_testing_key`) is accepted in `local`/`test` only and refused everywhere else.
- **When checkout is on:** the store is online, it ships or offers pickup, it takes bank transfer or payment on pickup (pickup only), and Turnstile is configured.
- **Stock:** nothing is reserved. A storefront order is a quote; stock drops when the owner confirms payment (the existing move to paid), which refuses a shortage as for any sale (D4).
- **In the admin (API):** invoices carry shipping, fulfillment, the payment choice and the order reference; their lines carry the personalization answers; owner and staff get the shopper's contact (`order_contacts`, never the CPA). `GET /invoices/counts` (owner, staff) counts website orders waiting for payment; the list and CSV filter on `source` and `awaiting_payment`, and `q` matches the order reference. The CSV has a `shipping` column as its last column, so every earlier column keeps its position. A website order's lines can't be replaced (409; Slice C revisits the answers only). The owner confirms payment with the existing status move.
- **Personalization:** `PUT /products/{id}/personalization` (owner) replaces a product's fields (at most five; label, required, free text up to 200 characters or 1-30 choices). Fields sent with their id keep it, so carts still match. Each order line keeps a copy of the label and answer, so editing a field never changes an order.
- **Storefront screens:** the bag lives in the browser (`localStorage`, per host; product ids, quantities and answers only, never a price). The bag and checkout show the server's quote; checkout sends `expected_total_cents`, and on a 409 shows the new total and asks the shopper to confirm it before placing the order again. One checkout key per attempt: a double click or a retry after a lost connection reuses it; changing the order makes a new one. Required personalization blocks adding to the bag. When `checkout_enabled` is false, product pages and checkout say ordering is temporarily unavailable instead of showing the form, and no order request can be sent. The confirmation and the tracking page are one page (`/orders/{token}`, the `W-` reference, never `INV-`), with the shop's payment instructions while unpaid and the city only; it sets `<meta name="referrer" content="no-referrer">`, and `vercel.json` sends `Referrer-Policy: no-referrer` and `no-store` for `/orders/*`. 422, 409 and 429 have plain messages.
- **Admin screens:** Invoices has a Source filter and column (a `Website` tag with the `W-` reference), an "Awaiting payment" card and tag, and the Invoices nav badge (owner, staff; refreshed every minute). A website order's page shows the shopper card (contact and ship-to), the answers under each line, no line editing or deleting, and "Confirm payment" with the shopper's method preselected. Products has the personalization editor (owner; at most five questions); Settings › Storefront has the checkout fields (shipping, flat rate, free-over threshold, tax on shipping, pickup, payment methods and instructions).
- **End-to-end run (local only):** `e2e/` (Playwright, the installed Edge) starts its own API, storefront and admin on 8001/5175/5176 with Turnstile's test keys, makes a throwaway demo shop (`backend/scripts/e2e_shop.py`) and deletes it afterwards: browse, add a personalized ring, check out, confirmation, tracking page, the order and badge in the admin, confirm payment. It stays out of CI until hosting exists (it needs the local Supabase stack); run it locally before pushing storefront or checkout changes.

### 6.13 Payments and deposits (Slice B1: the ledger, no Stripe yet)
- **The ledger:** `public.payments` holds one row per money movement on an invoice: a `payment` received or a `refund` given, with its method, amount, status and time. Only `succeeded` rows count. What an invoice has received is `invoice_amount_paid()` (payments minus refunds) and what is still due `invoice_balance()`; both are computed, never stored. Manual rows (cash, card on the shop's own terminal, bank transfer, other) are recorded in DELEA; Stripe rows arrive in Slice B2.
- **Who:** owner and staff record payments; the owner records refunds and voids a row recorded by mistake while the invoice is still a quote (it stays, marked voided, and counts for nothing); the CPA reads (D15). Nobody deletes a payment. An app user's row is always `succeeded`, received now, in the shop's currency, and recorded under their own name, whatever the request says. A payment can't exceed what is still due, nor a refund what was received; two payments at once serialize on the invoice, so neither can overpay. Amounts never change after insert, for any caller.
- **Rule 1, paid in full:** an invoice leaves quote only when its payments cover its total (`invoices_09_paid_in_full_guard`, every caller, the service role included). "Pay now" and a status move out of quote therefore first record a payment for whatever is still due, in the same transaction: if stock is short the whole move is refused (409) and no payment is kept, as before.
- **Deposits:** a payment smaller than the balance is a deposit. The invoice stays a quote (no number, no stock taken); the deposit is money held for the customer, not revenue.
  - **Rule 2, production:** `POST /invoices/{id}/start-production` (owner, staff) stamps `production_started_at` on an open quote that holds a deposit. It can't be changed or cleared.
  - **Rule 3, completion:** when a payment brings the balance to zero (`POST /invoices/{id}/payments`), `invoice_settle()` makes the quote paid, or in production if production had started; it gets its invoice number and stock goes down once. If stock is short, the payment is kept and the invoice stays a quote with `stock_short` true; the admin shows "Paid in full, but there isn't enough stock" and `POST /invoices/{id}/settle` completes it after restocking.
  - **Rule 5, locks:** a quote holding money can't be cancelled (refund the deposit first: `POST /invoices/{id}/refund` on such a quote records a refund of everything held and leaves it open) or deleted (any payment row, voided ones too, keeps it; cancel it instead). Its total can't drop below what has been paid, and its lines lock once production has started.
  - No deposit is kept when an order is abandoned in v1: the owner refunds it.
- **Invoice method:** an invoice's `payment_method` is a summary: the method all its payments used, or `other` when they differ. The owner's correction of it also corrects the payment when there is exactly one.
- **Backfill (migration 20261028000200):** every invoice paid before the ledger got one payment of its total (its method, or `other`), received when it was paid, and every refunded one a refund of its total; the migration fails if any paid invoice then disagrees with its payments. The seed and the showcase loader record their payments the same way.
- **API:** `GET /invoices/{id}/payments` (owner, staff, CPA); `POST /invoices/{id}/payments` (owner, staff; amount, method, optional note); `POST /invoices/{id}/payments/{payment_id}/void` (owner); `POST /invoices/{id}/start-production` and `/settle` (owner, staff). Every invoice read carries `amount_paid_cents`, `balance_cents`, `production_started_at` and `stock_short`; the single-invoice read carries its `payments`.
- **Tracking page:** a deposit shows as "Deposit received" (or "In production") with what has been paid and the balance due, and the shop's instructions for the rest. A balance under 50 cents (Stripe's minimum card charge) says "Pay the rest with the shop" and will never offer a card Pay button (Slice B2).
- **Admin screens:** the invoice page has a Payments section (total, paid so far, balance due, the list of payments with voided rows crossed out), Record payment (the amount starts at the balance), Start production, Void (owner) and Refund deposit; the list and header show "Deposit paid · In production · $X due". The dashboard shows revenue net of refunds and "Customer deposits held" apart from it.

### 6.14 Card payments with Stripe (Slice B2: test mode only)
- **Model:** Stripe Connect. Each shop has its own connected account, created by DELEA with the four Standard-equivalent controller settings (full Stripe Dashboard, fees paid by the shop, losses covered by Stripe, requirements collected by Stripe; decision K1) and onboarded on Stripe-hosted Account Links. Shoppers pay on Stripe's hosted Checkout, card only, as a **direct charge on the shop's account with no platform fee** (D20). Card data never reaches DELEA. The account is in `payment_accounts` (K2); DELEA stores no shop key.
- **Test mode only:** the API refuses to start with a live key (`sk_live_`/`rk_live_`; the message never repeats the key), and live-mode events are refused by the database functions. Demo shops may use test mode (D16) but can't run onboarding; the showcase gets a test account from `SHOWCASE_STRIPE_ACCOUNT_ID`.
- **Versions:** `stripe==16.0.0`, which pins API version `2026-09-30.endive`; DELEA sends it on every request and the sandbox's default matches. On this version a Checkout Session refuses `payment_method_types` (found in the sandbox check on 2026-10-29; the fake gateway can't catch a refused parameter, see the manual checks and §6.15).
- **Card only (migration 20261029000400):** payment methods come from a payment method configuration on the shop's own account. On the shop's first card checkout DELEA makes one there named "DELEA card only", with card on and every other method the account lists switched off (wallets such as Apple Pay and Link included), stores its `pmc_…` id in `payment_accounts.card_config_id` (compare-and-set, so two checkouts at once keep one), and names it on every session (`payment_method_configuration`). The idempotency key is `delea:card-config:<account>:first`. If Stripe refuses a session, DELEA reads the configuration back: when it is gone, inactive or has card off, a new one replaces it (key `delea:card-config:<account>:<old pmc>`) and the session is tried once more under its own key (`delea:checkout:<payment id>:<new pmc>`, since Stripe may keep the refusal under the first); otherwise the attempt fails as before (Pay now). A new connected account starts without a configuration.
- **If the owner changes the configuration in their Stripe Dashboard (decided 2026-10-29; nothing built for it):** the shop's account has a full Dashboard, so the owner can turn other methods on in "DELEA card only". DELEA doesn't watch or undo that. Methods that are paid at once (wallets, for example) still arrive as `checkout.session.completed` with `payment_status = paid` and are recorded like card. Methods that confirm later are **unsupported**: their `checkout.session.completed` has `payment_status = unpaid` and is recorded as `ignored` (the payment stays pending), and `checkout.session.async_payment_succeeded` / `_failed` are recorded with the outcome `unsupported` and change nothing (`stripe_record_unsupported`; the API logs a warning). The money is then in the shop's Stripe account but not in DELEA: the owner refunds it in Stripe, or switches the method off again. A manual payment can't be recorded meanwhile, because the completed session can't be expired (the 409 of "Manual payments and card sessions" below). Handling those methods is future work.
- **Settings:** `GET /settings/payments` (owner) says whether Stripe is set up on the server, the account and whether card payments are on; while the account can't take charges it asks Stripe again (the owner may just be back from onboarding). `POST /settings/payments/stripe/onboarding-link` creates the account on the first call (idempotency key per shop) and returns the onboarding link; Stripe returns the owner to `ADMIN_APP_URL/settings?stripe=return`.
- **Checkout:** card is a payment choice when the server has Stripe and the shop's account can take charges (the database re-checks when the order is placed). A card order is placed first (a quote, as any website order), then `storefront_card_payment_start` writes a pending payment for the balance and DELEA creates a Checkout Session on the shop's account with the idempotency key `delea:checkout:<payment id>`, metadata (shop, invoice, payment, order reference) and a 1-hour expiry; the session's id, link and expiry are attached to the payment. A double submit gets the same open session. If Stripe can't be reached the attempt is failed, the order stays, and the order page offers **Pay now** (`POST /public/orders/{token}/pay`, 10 per address per hour), which reuses an open session for the same balance or starts a new one (older open sessions are expired). A balance under Stripe's minimum (50 cents; 30 pence; 10 MXN) is paid with the shop. Only `https://checkout.stripe.com/` and `https://connect.stripe.com/` links are ever followed by the apps.
- **Webhook:** `POST /stripe/webhook` takes connected-account events. The raw body (at most 1 MB) is verified against `STRIPE_WEBHOOK_SECRET` (Stripe's HMAC scheme, 5-minute tolerance; 400 otherwise, 503 without a secret). Each event is applied by one SECURITY DEFINER function, as `payments_writer`, in one transaction:
  - `checkout.session.completed` (paid): the pending payment succeeds with its PaymentIntent and `invoice_settle()` runs **in the same transaction** as the payment and the event row: paid, in production if production had started, or (stock short) the payment is kept and the invoice stays a quote with `stock_short`.
  - `checkout.session.expired`: pending → expired (never a succeeded one).
  - `refund.created` / `.updated` / `.failed`: one refund row per Stripe refund id; states never move back; everything refunded marks a paid invoice refunded. A refund never restocks.
  - `account.updated` / `account.application.deauthorized`: the account's state; a disconnected account turns card off.
  - Anything else is recorded as ignored.
  - `checkout.session.async_payment_succeeded` / `_failed`: recorded as `unsupported`, nothing else (card only; see "If the owner changes the configuration" above).
- **Idempotency:** the event id is inserted into `stripe_events` first (`ON CONFLICT DO NOTHING`) in the same transaction as the effects: a replay is a no-op, and a failure rolls the event back so Stripe's retry applies it. Payment rows are unique per Stripe id. Every effect is checked first: the event's account is the payment's shop's account, the payment and its session match, and the amount and currency are the pending payment's; otherwise the event is recorded as `no_match` and answered 200 (a retry would never help). Each event's outcome is kept (`applied`, `stock_short`, `unchanged`, `ignored`, `no_match`, `live_refused`, `unsupported`).
- **Manual payments and card sessions:** recording a payment by hand (or Mark paid) first expires the order's open Checkout Sessions at Stripe; if one can't be expired (for example it was just paid) the payment is refused (409), so a customer can't pay twice. A payment that still lands twice shows as **Overpaid by $X** in the admin.
- **Refunds:** card payments taken online are refunded in the shop's Stripe Dashboard (D13); DELEA records them from the webhook and refuses its own Refund for such an invoice.
- **Cancel waits for the Stripe refund:** an invoice whose net card payments taken online are above 0 can't be cancelled ("Refund this payment in Stripe first.", every caller: `invoices_24_card_cancel_guard`, migration 20261028000800, before the deposit guard). Once the refund webhook has recorded the refund, cancel works as before. The admin doesn't offer Cancel meanwhile and says why.
- **Admin screens:** Settings › Card payments (Connect Stripe / Continue Stripe setup, the account, test mode); on an invoice, card payments read "Card (online)" with a note that refunds are made in Stripe.
- **Tests without Stripe:** CI and the integration tests use an in-memory fake gateway and sign webhook payloads locally with Stripe's scheme; nothing talks to Stripe. The local end-to-end run uses the same fake (`STRIPE_GATEWAY=fake`, refused outside `ENVIRONMENT=local/test`), intercepts Stripe's pages in the browser, and sends a locally signed "paid" event (`backend/scripts/e2e_shop.py card-paid`). The fake refuses a session without a usable card-only configuration, but it never sees Stripe's parameters, so `backend/tests/test_stripe_params.py` checks exactly what is sent (no `payment_method_types`; the configuration with card on and everything else off). What needs the owner's sandbox is in the README ("Stripe sandbox"), including the first session created against real Stripe on the pinned API version.

### 6.15 Go-live for the first real customer (roadmap; not built)
Live mode is blocked in all of Slice B: the API refuses live keys and the database refuses live-mode events. Taking real money needs this checklist, done once, as its own reviewed step. Nothing here is built yet.
1. **Code change, reviewed on its own:** allow live keys only where `ENVIRONMENT` is the production one (never `local`/`test`, never a demo shop: D16 keeps demo shops on test mode); accept live-mode events only for shops whose account is live; keep refusing live keys everywhere else. Tests and a deliberate break, as for every slice.
2. **Live Connect platform profile:** in the Stripe Dashboard (live mode), complete the Connect platform profile and the platform's own verification; set Connect branding (name, colour, icon) for live; confirm the Standard-equivalent setup (shops use their own Dashboard and pay their own fees).
3. **Live webhook endpoint for connected accounts:** add an endpoint for "Events on connected accounts" pointing at the hosted API's `/api/v1/stripe/webhook`, with the events DELEA handles (`checkout.session.completed`, `checkout.session.expired`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `refund.created`, `refund.updated`, `refund.failed`, `account.updated`, `account.application.deauthorized`), on the pinned API version; its signing secret goes into the hosted environment only.
4. **Live keys in the hosted environment only:** `STRIPE_SECRET_KEY` (live, preferably a restricted key with only what DELEA calls: accounts, account links, payment method configurations, Checkout Sessions) and `STRIPE_WEBHOOK_SECRET` in the hosting provider's secret store. Never in a repo, a `.env` committed anywhere, a chat or a ticket; the test keys stay in local and test environments.
5. **Pre-flight:** the least-privilege API login (§6.6) with `storefront_reader`, `storefront_writer` and `payments_writer`; trusted proxy headers; the real customer's shop onboarded through Stripe's live onboarding and showing "Card payments on".
6. **First session against real Stripe on the pinned API version:** before any shopper can pay, start one card checkout on the live shop and confirm Stripe accepts it and the hosted page shows **card only** (the shop's "DELEA card only" configuration is made then). The fake gateway in CI can't catch a parameter Stripe refuses (in the sandbox, 2026-10-29, `payment_method_types` was refused on `2026-09-30.endive`). Repeat this check whenever the Stripe library or the pinned API version changes.
7. **The first real payment and refund:** with the owner, place one small real card order (a real card, a real amount), check the invoice is paid by card and stock went down once, then refund it in the shop's Stripe Dashboard and check DELEA records the refund (and that the card is credited). Keep the Stripe event ids in the go-live notes.
8. **Watch the first week:** the `stripe_events` outcomes (`no_match` or `live_refused` mean something is misconfigured; `unsupported` means the owner turned on a payment method that confirms later in "DELEA card only"), Stripe's webhook delivery log, and any invoice showing "Overpaid".

### 6.16 Email: outbox, worker, owner alerts (Slice D1)
- **Provider:** Resend (free while building, Pro from the first real shop), behind a small sender interface so it can change. Locally and in tests: the Supabase stack's mail catcher (Mailpit); nothing leaves the machine and no key is needed. `EMAIL_PROVIDER=auto` (default): Resend when `RESEND_API_KEY` is set, the mail catcher in `local`/`test`, off elsewhere (emails then wait in the outbox). `mailpit` is refused outside `local`/`test`.
- **Sender identity:** every email comes from `Shop Name <notifications@mail.deleajewelry.com>` (the authenticated sending domain: SPF, DKIM, DMARC, the From domain aligned as Gmail requires). A shop's own address (for example a Gmail address) is never the From; it can be the **Reply-To**, used only once confirmed through a one-time link (24 hours) emailed to it. The confirmation needs a click (link scanners open links by themselves).
- **Shared sending domain: reputation risk.** All shops send from one domain, so one shop's bad behaviour (bounces, spam complaints) hurts every shop's deliverability. Mitigations in D: transactional email only (no marketing), per-shop daily limits, bounced and complained addresses suppressed for every shop, reserved test domains never sent, DMARC monitoring from `p=none`. **Upgrade (not built):** a shop authenticates its own domain (it adds 3-4 DNS records DELEA shows: SPF/return path, DKIM, DMARC; DELEA verifies them through the provider) and sends as `orders@its-domain`, isolating its reputation. Each such domain uses one of the plan's domains (Resend Pro has 10; Scale up to 1,000, from $90 a month).
- **Outbox:** `email_messages`, one row per email, unique per shop and source key, rendered once (subject, HTML, plain text), with its status (`queued`, `sending`, `sent`, `failed`, `suppressed`), attempts and last error. The owner reads the shop's log; nobody with an app role writes it.
- **Rules (in `email_enqueue`, for every caller):** owner alerts go to the shop's owners only; a demo shop emails its own owners only (owner alerts, tests), never customers or anyone else; suppressed addresses get nothing; per shop: 300 customer emails a day, 5 a day to one customer, 10 tests a day. A refused email is kept as `suppressed` with the reason.
- **Worker (`python -m app.worker`, as `email_worker`):** claims unprocessed automation events (`FOR UPDATE SKIP LOCKED`), writes the owner alerts they call for and marks them processed in one transaction (events older than two days are closed without email); once an hour, a daily balance-due digest; then claims due emails (marked `sending` and committed), sends each with its row id as the provider's idempotency key, and records sent, retry (1 min, 5 min, 30 min, 2 h, 6 h) or failed. A row left `sending` for 10 minutes (a worker died) is retried with the same key, which Resend deduplicates (24 hours). Provider down: emails wait; the admin shows "Emails are delayed" after 15 minutes. **Hosting:** the worker needs to run next to the API once hosted (a WebJob or a small container); until then, no real email.
- **Owner alerts (D1):** new website order (reference, customer name, item count, total, how they pay; never a street address or phone), low stock (at most once per product per day), balances due (a daily digest of open quotes holding a deposit past their due date). Each can be switched off in Settings › Emails.
- **New automation events:** `production_started` (a quote's production starts) and `deposit_received` (a payment leaves part of the total due). Customer emails using them: §6.17 (D2).
- **Provider webhook:** `POST /email/webhook`, signed with Svix's scheme (as Resend signs), 5-minute tolerance; each event recorded once; a bounce or complaint suppresses the address for every shop.
- **Admin:** Settings › Emails: who sends and from which address, the reply-to address (and its confirmation), the alerts, "Send a test to myself" (the signed-in owner's own sign-in email only), the latest 20 emails with their status and why one wasn't sent.
- **Email content:** single column, mobile-friendly, the shop's colour (a value that isn't a hex colour is ignored), always with a plain-text version; every value escaped.

### 6.17 Customer emails (Slice D2: website orders only)
- **The emails:**
  - **Order received:** the W- reference, the items with the shopper's answers, the amounts, how to pay (the shop's instructions, or a Pay now button for card) and the tracking link.
  - **Deposit received:** the amount, paid so far, left to pay.
  - **In production:** once per order, whether production started on a deposit or the status moved.
  - **Payment received:** paid in full.
  - **Shipped:** the carrier and tracking number when entered; "ready for pickup" for a pickup order.
  - Pay now opens the tracking page, whose own button starts the payment; a link in an email never charges.
  - Under Stripe's 50-cent minimum, the email says to pay the rest with the shop, as the tracking page does.
- **Which event sends which:**
  - `order_placed` (website orders) → order received;
  - `deposit_received` → deposit received (one per payment);
  - `production_started`, or the status moving to `in_production` → in production;
  - leaving `quote` → payment received;
  - moving to `shipped` → shipped.
  - Each is unique per shop, order and email (`customer:<template>:<order>`, per payment for a deposit), so a replay, or a second route to the same email, queues nothing new. Admin invoices and cancelled orders get none.
- **What they never say:** the street address, phone number, city, ZIP code or the shopper's note. `email_customer_context` doesn't carry them, and the email model has no field for them.
- **No promotion:** these are transactional (CAN-SPAM), so they have no unsubscribe link. Marketing remains a separate slice.
- **When they are written:** when the worker takes the event, from the order as it is then. A deposit email uses the amounts recorded in its event, so a worker that runs late still describes the deposit, not the balance paid since.
- **Footer:**
  - "You're receiving this because you placed an order with <shop>.";
  - "Questions? Write to <contact>" (the storefront contact email, otherwise the confirmed reply-to);
  - the shop's website.
  - The shop's postal address isn't shown: it isn't required for transactional email, and many makers work from home.
- **Website address:** the shop's first storefront domain (alphabetically), with `https://` (plain `http://` only in `local`/`test`). A shop with no storefront domain gets no customer emails.
- **Settings › Emails:**
  - each customer email on or off;
  - an intro of the owner's own (up to 300 characters, shown first, escaped);
  - a preview with a made-up order, in a sandboxed frame at phone width (nothing queued; unsaved intros can be previewed);
  - "Send this one to myself": the owner's own sign-in email, within the 10 tests a day.
  - Demo shops: the preview works; customers never get email (enforced by `email_enqueue`).
- **Shipping details:** "Mark shipped" takes an optional carrier (USPS, UPS, FedEx and DHL suggested) and tracking number. The API refuses them with any other move, or once the order is already shipped. The invoice page and the tracking page show them.
- **No rules page yet:** the admin has no automation rules page (the API's `/automation` is a stub, and the demo page only mocks one). Settings › Emails lists each email with the trigger that sends it, including `production_started` and `deposit_received`.

### 6.18 Website page and framing (Website slice)
- **Website page (owner only, admin menu):**
  - a live preview of the shop's own storefront in a frame, with a Phone (390px) and Desktop (1280px, scaled to fit) toggle;
  - "Open my website" (a new tab);
  - the existing Brand and Storefront settings beside the preview (the same cards as Settings); the preview reloads after each Save, including the logo.
  - No layout editor.
  - The page says that an order placed in the preview is a real order.
- **Which address:** `GET /settings/website` (owner only) returns the shop's first storefront domain, alphabetically (the one customer emails link to), with `https://` (plain `http://` only in `local`/`test`), and whether the storefront is switched on.
- **When the preview can't load:**
  - **No domain yet:** a message says so; there is no frame and nothing to open.
  - **Storefront switched off:** a notice above the preview.
  - **Blocked or unreachable:** the storefront tells its parent window it loaded (`postMessage` of `delea:storefront-ready`, carrying no data), and only a message from the shop's own origin counts. Without it within 10 seconds, the page says the preview couldn't load, and "Open my website" keeps working.
- **Who may frame the storefront:** every page gets `Content-Security-Policy: frame-ancestors 'self' <admin origin>`. Other sites can't frame it (clickjacking).
  - The admin origin comes from the `ADMIN_ORIGIN` setting, never from code. It must be exactly one origin: https, or http for a local host only; no wildcard, list, path or credentials.
  - Missing or invalid, it fails closed to `'self'`: the preview is blocked and the shop stays safe.
  - **Local and e2e:** the Vite dev and preview servers send the header (`vite.config.ts`, `ADMIN_ORIGIN` from the environment or `apps/storefront/.env.local`).
  - **Hosted (Vercel):** Routing Middleware (`apps/storefront/middleware.ts`, `@vercel/functions` `next()`), on pages only (not `/assets/*`). Set `ADMIN_ORIGIN` in the Vercel project. The middleware runs on each page request (fluid compute pricing).
- **Frame sandbox:** scripts, same-origin, forms and popups are allowed (the shop needs its own scripts and storage). Top navigation isn't, so the shop can never navigate the admin. `referrerpolicy="no-referrer"`.

### 6.19 Automations page (read only)
- **Automations (owner only, admin menu):** every automatic email (the three owner alerts and the five customer emails), who it goes to, the trigger that sends it, and whether it is on, read from `GET /settings/emails`. There is no rule editing: the switches and intros stay in Settings › Emails, which the page links to (`/settings#emails-heading`). The API's `/automation` stays a stub.
