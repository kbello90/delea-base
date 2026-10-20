# DELEA — Technical Requirements Document (v1)

**Status:** Locked — ready for Claude Code scaffolding
**Owner:** Karen
**Scope of this doc:** Architecture, infrastructure, and repo structure for the golden-image base product described in the DELEA PRD (v1, all Open Questions resolved). This doc is the scaffolding spec for Claude Code.

**Locked TRD decisions:**
1. **Two frontend apps** in one monorepo — `apps/admin` and `apps/storefront` — sharing `packages/ui` and `packages/api-client` (§1.1).
2. **Enterprise dedicated DB = a separate Supabase project** on dedicated compute, not self-hosted Postgres. One auth system and one RLS pattern across all tiers; the backend never branches by tier (§2.3).
3. **Monorepo tooling:** pnpm workspaces + Turborepo (§4).
4. **Migrations:** Supabase CLI, SQL-first, in a root `supabase/` directory — RLS policies are versioned in the same migration stream as the schema (§4).
5. **Storefront data access:** public FastAPI endpoints keyed by hostname; no direct Supabase access from the storefront (§1.3). Stripe Connect Standard, guest checkout with a tokenized tracking link, and the other data-model decisions are recorded in `DELEA-Schema.md` §8.
6. **Backend database access:** the FastAPI backend talks to Postgres directly through a psycopg connection pool, not through PostgREST. Each authenticated request runs in one transaction that does `set local role authenticated` and sets the caller's verified JWT claims, so RLS is the enforcement. Public storefront endpoints, Stripe webhooks and the automation worker use a separate service connection; the AI assistant uses `set local role ai_readonly` with `app.tenant_id`. `supabase-py` is used only for Auth admin actions such as CPA invites.

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
- **Admin → Supabase directly (authenticated reads only):** simple reads in `apps/admin` (e.g., dashboard listing views) can go through the Supabase JS client with RLS enforcing tenant isolation, skipping FastAPI for latency. Anything that mutates data or triggers business logic (Stripe checkout, invoice creation, automation triggers) always routes through FastAPI.
- **Storefront → FastAPI public endpoints only (locked):** `apps/storefront` never queries Supabase directly. It calls public, unauthenticated FastAPI endpoints (`GET /api/v1/public/products`, `/api/v1/public/brand`, order tracking by `public_token`) that resolve the tenant from the request hostname via `tenant_domains`. The `anon` Postgres role has no table grants, so one tenant's catalog can never be read by asking for another tenant's id. The storefront's `supabaseClient.ts` stays in the tree but is unused in v1.
- **AI assistant:** `apps/admin` only (never exposed on the public storefront in v1). Frontend sends a natural-language query to a FastAPI endpoint (`/api/v1/ai/query`; all v1 routes mount under `/api/v1`), which calls the LLM with a **read-only, pre-scoped** data context — see §3.2 for exactly how "read-only" is enforced. Demo-stage: this is a direct API call from FastAPI to the LLM provider with seeded/live data stuffed into context. Post-first-customer (per PRD, deferred): this becomes a separate FastAPI + LangChain microservice with LangSmith tracing, same read-only contract, just with tool-calling and observability instead of a single stuffed prompt.

### 1.4 UI reference

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
| `products` (list/get · create/update/delete) | ✓ · ✓ | read | — |
| `materials` (list with `q`, `low_stock`; get · writes) | ✓ · ✓ | read | — |
| `customers` (list/get · create/update · delete) | ✓ · ✓ · ✓ | ✓ · ✓ · — | — |
| `expenses` (list with date range / category / deductible; get; `export.csv` · writes) | ✓ · ✓ | — | read |
| `settings/company` (get · patch) | ✓ · ✓ | — | — |
| `settings/brand` (get · patch) | ✓ · ✓ | read | — |
| `dashboard/summary` | ✓ | — | — |

**Also:**
- **Health:** `GET /health` (liveness) and `GET /health/ready` (database check) sit outside `/api/v1`.
- **Invoices:** waiting on the migration that lets staff read the tax rate and delete quote line items.

### 6.3 Error mapping
Every error response is `{"detail": "..."}`, never with SQL text or stack traces.

| Cause | Status |
|---|---|
| missing, invalid or expired token | 401 |
| no membership, wrong role, RLS violation, insufficient privilege | 403 |
| no row, or a row hidden by RLS | 404 |
| unique, foreign-key or `P0001` | 409 |
| check, not-null, invalid value, request validation | 422 |
| JWKS or database unreachable | 503 |

Messages raised by our own triggers (`RAISE`) are passed through; Postgres-generated messages stay generic.

### 6.4 CORS
Allowed origins come from `CORS_ORIGINS`: a comma-separated list of exact origins, defaulting to `http://localhost:5173,http://localhost:5174`. A wildcard is rejected at startup.
- **Methods:** `GET, POST, PATCH, DELETE, OPTIONS`.
- **Headers:** `Authorization, Content-Type`.
- **Credentials:** none.

### 6.5 Money rules
All amounts are integer cents. Rounding is half-up, in integer arithmetic: `(a × bps + 5000) // 10000`. These rules are implemented in `backend/app/services/pricing.py`, and will be used by the invoices endpoints.
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
  - **Revenue:** the subtotal of invoices paid in the period, excluding refunded and cancelled ones.
  - **Tax reserve:** their tax.
  - **Net profit:** revenue − expenses (all expenses dated in the period, up to today).
  - **Periods:** UTC calendar month and year to date.

### 6.6 Known limitations
- **Token expiry:** an access token stays valid until it expires (`jwt_expiry`, 3600 s locally). Removing or changing someone's membership takes effect at their next token refresh. We'll revisit this with a shorter expiry, or a membership re-check on sensitive requests.
- **Multi-tenant users:** a user in several tenants gets their oldest membership.
- **Direct database API:** until the follow-up migration closes it, the Supabase REST API still exposes `public` to signed-in users. The API-only rules (owner-only refund, cancel and tax override; server-computed totals) are enforced only through FastAPI.
