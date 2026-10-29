# delea-base

Golden-image base for DELEA, an omnichannel CRM and e-commerce platform for fine jewelry
businesses. Specs live in [`docs/`](docs/):

- [`docs/DELEA-PRD.md`](docs/DELEA-PRD.md): product requirements
- [`docs/DELEA-TRD.md`](docs/DELEA-TRD.md): architecture and repo structure (source of truth for this layout)
- [`docs/DELEA-Schema.md`](docs/DELEA-Schema.md): draft data model, pending review
- [`docs/delea-demo.html`](docs/delea-demo.html): UI/UX reference

## Repo map

| Path | What it is |
|---|---|
| `apps/admin` | Owner/staff/CPA dashboard (React + Vite + TS) |
| `apps/storefront` | Public, per-tenant branded storefront (React + Vite + TS) |
| `packages/ui` | Shared components + design tokens (CSS custom properties from the demo) |
| `packages/api-client` | Typed client for the FastAPI backend |
| `packages/config` | Shared tsconfig and ESLint preset |
| `backend/` | FastAPI backend (Python, not a pnpm workspace member) |
| `ai-service/` | Deferred LangChain microservice (README only) |
| `supabase/` | Supabase CLI project: migrations, seed, pgTAP tests |
| `infra/bicep` | Azure App Service + Redis, one template for all tiers |
| `infra/scripts` | Runbooks (Enterprise provisioning) |
| `.github/workflows` | CI |

## Prerequisites

- Node.js 22+ (24 recommended) and pnpm 10 (`npm install -g pnpm@10`, or `corepack enable pnpm`)
- Python 3.11+
- Optional: Supabase CLI, Azure CLI / Bicep, Docker

## Frontend

```sh
pnpm install
pnpm build        # type-check + build all apps and packages (Turborepo)
pnpm lint
pnpm dev          # admin on http://localhost:5173, storefront on http://localhost:5174
pnpm --filter @delea/admin dev        # one app only
```

Each app reads `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`, plus the backend URL:
`VITE_API_URL` in the admin app and `VITE_API_BASE_URL` in the storefront. Copy
`apps/<app>/.env.example` to `apps/<app>/.env.local` (gitignored, never commit it) and fill it
from the LOCAL stack only: `API_URL` and `ANON_KEY` from `npx supabase@2.121.0 status -o env`.
The admin app shows a "not configured" message without them.

The storefront also reads `ADMIN_ORIGIN` (not `VITE_`: the dev server only): the admin's origin,
the one other site allowed to frame the storefront, for the admin's Website preview
(`frame-ancestors`, TRD §6.18). Locally `ADMIN_ORIGIN=http://localhost:5173` in
`apps/storefront/.env.local`; hosted, set it in the storefront's Vercel project. Without it the
preview says it couldn't load and "Open my website" still works.

```sh
pnpm test         # Vitest (admin, storefront, api-client)
pnpm typecheck
```

### End-to-end run (local only)

One Playwright test (`e2e/tests/checkout.spec.ts`) walks the whole website order: browse,
personalize a ring, check out, the confirmation, the tracking page, then the order in the
admin with its badge, and confirms the payment. It needs the local stack and `backend/.env`;
it starts its own API (8001), storefront (5175) and admin (5176), creates a throwaway demo
shop and deletes it afterwards, and uses Cloudflare's published Turnstile test keys (which the
API accepts in `local`/`test` only). It drives the installed Microsoft Edge, so nothing is
downloaded. It is not part of CI.

```sh
pnpm --filter @delea/e2e e2e       # screenshots in e2e/screenshots/ (gitignored)
```

### Stripe sandbox (card payments, test mode only)

Card payments (Slice B2) run against **your** Stripe sandbox. You enter the keys in
`backend/.env` yourself; never paste one into chat, a ticket or the repo. The API refuses to
start with a live key (`sk_live_`/`rk_live_`). The library is pinned (`stripe==16.0.0`), which
pins Stripe API version **2026-09-30.endive**.

**One-time setup (Dashboard, in a sandbox):**

1. Create a Stripe account (or sign in), then **Sandboxes** (account menu) → create one and
   switch into it.
2. **Connect → Get started**: choose that the businesses you onboard use their own Stripe
   Dashboard and pay their own fees (the Standard-equivalent setup).
3. **Settings → Connect → Branding**: business name `DELEA`, a colour and an icon (Stripe's
   onboarding needs them).
4. **Developers → API version**: set the default to `2026-09-30.endive`.
5. **Developers → API keys**: copy the **test** secret key (`sk_test_…`) into
   `backend/.env` as `STRIPE_SECRET_KEY`. No publishable key is needed.
6. Install the Stripe CLI (free; Stripe's install page), then sign it in: `stripe login`.
7. `stripe listen --print-secret`, and put the `whsec_…` it prints into `backend/.env` as
   `STRIPE_WEBHOOK_SECRET` (it stays the same between `listen` restarts).
8. Optional, for the showcase shop: **Connect → Accounts → Create** a test connected account,
   finish its onboarding with Stripe's test data, and set its `acct_…` id as the Windows user
   environment variable `SHOWCASE_STRIPE_ACCOUNT_ID`; then reload the showcase.

**Running locally:** start the API on 8000 as usual, and in another terminal forward the
connected accounts' events to it:

```sh
stripe listen --forward-connect-to localhost:8000/api/v1/stripe/webhook
```

**Manual checks (they need your sandbox; CI never talks to Stripe):**

A non-demo shop with an owner login for them, on the local stack only (never the seed tenants;
the password is printed once): `cd backend` then
`.venv/Scripts/python scripts/e2e_shop.py create --port 5174` (macOS/Linux: `.venv/bin/python`);
remove it afterwards with `e2e_shop.py delete <tenant id>`.

1. *Connect.* Sign in to the admin as a non-demo shop's owner → Settings → Card payments →
   **Connect Stripe** → finish Stripe's form with test data → you return to Settings, which
   should say **Card payments on**. `stripe get /v1/accounts/<acct_…>` shows
   `controller.stripe_dashboard.type` = `full`.
2. *First session on the pinned API version.* Start one card checkout on that shop's storefront:
   Stripe accepts it (no "The card payment couldn't start" message) and its page shows
   **Pay with card** with card as the only payment method. The shop's account now has a
   payment method configuration named "DELEA card only". The fake gateway in CI can't catch a
   parameter Stripe refuses, so repeat this whenever the Stripe library or the API version
   changes (TRD §6.14, §6.15).
3. *Pay by card.* On that shop's storefront, check out with **Card**; on Stripe's page pay with
   `4242 4242 4242 4242`, any future date, any CVC. You return to the order page, which says the
   payment is being confirmed, then **Paid**. In the admin the invoice is paid, method Card, with
   one "Card (online)" payment; stock went down once. `stripe listen` shows
   `checkout.session.completed` answered `200`.
4. *Pay now.* Place another card order and press back on Stripe's page: the order page offers
   **Pay … by card**; it opens the same session again.
5. *Refund.* In the Stripe Dashboard (switch to the connected account), refund part of the
   payment, then the rest. The admin shows a refund row each time; after the second the invoice
   is Refunded. The dashboard's revenue goes down in this month. Stock is not put back.
6. *Replay.* `stripe events resend <evt_… of step 3> --account <acct_…>`: `200`, and nothing in
   the admin changes (no second payment, stock unchanged).
7. *Expiry.* Start a card checkout and leave it; after it expires (1 hour), the admin shows
   that payment as Expired and the order page offers Pay now again.

### Email (Slice D)

Locally nothing is needed: emails go to the Supabase stack's mail catcher, at
`http://127.0.0.1:55324` (Mailpit). Run the worker next to the API:

```sh
cd backend
.venv/bin/python -m app.worker          # keeps running; --once for a single pass
```

**Your setup for real sending (Resend; you enter the key in `backend/.env` yourself):**

1. In Resend: **Domains → Add domain** `mail.deleajewelry.com`. Resend shows the DNS records
   to add (typically SPF and MX records for a sending subdomain, and a DKIM TXT record); copy
   them exactly as Resend shows them.
2. In Cloudflare (deleajewelry.com → DNS): add each record exactly as shown. Set them to
   **DNS only** (grey cloud), not proxied. Back in Resend, press **Verify**.
3. In Cloudflare, add DMARC: a TXT record named `_dmarc.mail` with
   `v=DMARC1; p=none; rua=mailto:<a mailbox you read>` (start at `p=none` and tighten later).
4. Resend → **API Keys → Create**: permission **Sending access**, domain
   `mail.deleajewelry.com`. Put it in `backend/.env` as `RESEND_API_KEY`. That switches
   `EMAIL_PROVIDER=auto` to Resend; set `EMAIL_PROVIDER=mailpit` to keep testing locally.
5. Later, once hosted: Resend → **Webhooks** → the API's `/api/v1/email/webhook` with
   `email.bounced` and `email.complained`; its signing secret goes into the hosting
   environment as `RESEND_WEBHOOK_SECRET`.

Resend's free plan sends at most 100 emails a day (3,000 a month); move to Pro ($20 a month)
before the first real shop.

### The storefront locally

The storefront finds its shop from the address it is opened on (the browser's `Origin`), so open
it through a mapped name: `http://localhost:5174` is tenant A, `http://aurora.localhost:5174`
tenant B, and `http://showcase.localhost:5174` the showcase (after `load_showcase.py`). Browsers
send `*.localhost` to this machine. Any other name shows "This shop isn't available". To map
another one, see `infra/scripts/storefront-domain.md` (local equivalent).

### Local dev users (manual testing)

With the local stack running and `backend/.env` written (see Backend), create three users in
tenant A ("DELEA Demo"), one per role:

```sh
backend/.venv/bin/python backend/scripts/create_dev_users.py   # Windows: backend\.venv\Scripts\python
```

| Email | Role | Password |
|---|---|---|
| `owner@delea.local` | owner | `delea-local-dev` |
| `staff@delea.local` | staff | `delea-local-dev` |
| `cpa@delea.local` | cpa | `delea-local-dev` |

The password is public and works only against the local stack. The script is idempotent
(re-running resets the password and role) and refuses any non-local URL. Integration tests
don't use these users; they create and delete their own.

## Backend

Run these in `backend/`. On Windows, the venv's scripts live in `.venv\Scripts\` instead of `.venv/bin/`.

```sh
cd backend
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest                  # unit tests; integration tests skip without the stack
.venv/bin/python -m pytest -m integration   # against the local Supabase stack
.venv/bin/python -m ruff check .
.venv/bin/python -m uvicorn app.main:app --reload --port 8000   # Windows: add --loop asyncio:SelectorEventLoop
```

Settings come from environment variables or `backend/.env` (gitignored). Copy
[`backend/.env.example`](backend/.env.example). Local keys come from
`npx supabase@2.121.0 status -o env`. Never commit real values. See
[`backend/README.md`](backend/README.md) for the DB session model, the error mapping, the
integration test setup and known limitations.

Docker:

```sh
docker build -t delea-backend backend
docker run --rm -p 8000:8000 --env-file .env delea-backend
```

## Database

Schema, RLS policies and the `ai_readonly` role live only in `supabase/migrations/`, and
pgTAP tests in `supabase/tests/`. Both are empty until the schema is approved.

```sh
supabase start         # local stack
supabase db reset      # apply migrations + seed.sql locally
supabase db push --project-ref <ref>   # apply to a hosted project
```

## Infrastructure

See [`infra/bicep/README.md`](infra/bicep/README.md) for validation and deployment, and
[`infra/scripts/provision-enterprise.md`](infra/scripts/provision-enterprise.md) for the
Enterprise runbook.
