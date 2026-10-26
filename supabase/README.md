# supabase/

Supabase CLI project for DELEA. The same migrations apply to the shared project and to every
Enterprise project (TRD §2.3). The spec is [`docs/DELEA-Schema.md`](../docs/DELEA-Schema.md).

| Path | Contents |
|---|---|
| `migrations/` | Timestamped SQL, one file per step (helpers, enums, tables, triggers, auth hook, RLS, AI read path, indexes) |
| `seed.sql` | Local/demo data: tenant A "DELEA Demo" (from `docs/delea-demo.html`) and tenant B "Aurora Gems" |
| `tests/` | pgTAP tests: schema guards, tenant isolation, role matrix, staff financial guard, `ai_readonly`, `anon`, invoice rules, auth hook |
| `config.toml` | Local stack config, including the custom access token hook |

## Run locally

Requires Docker. The CLI doesn't need to be installed: `npx` downloads it.

```sh
npx supabase@2.121.0 start      # local stack; Studio at http://127.0.0.1:55323
npx supabase@2.121.0 db reset   # re-apply all migrations from scratch + seed.sql
npx supabase@2.121.0 test db    # run every pgTAP file in tests/
npx supabase@2.121.0 stop
```

Local ports are 553xx rather than the CLI's default 543xx, because Windows (Hyper-V/WinNAT) often
reserves the 543xx range. To see the reserved ranges on your machine:
`netsh interface ipv4 show excludedportrange protocol=tcp`.

Never run `supabase link` or `supabase db push` against a hosted project from a dev machine
without going through the provisioning runbook (`infra/scripts/provision-enterprise.md`).

## Hosted projects (shared and Enterprise): close the Data API

`config.toml` only configures the local stack. On every hosted project, the shared one and each
Enterprise one, open the dashboard's **Data API** settings and either disable the Data API, or:

- remove `public` from **Exposed schemas**, so only `graphql_public` remains, and never add `ai`;
- remove `public` from **Extra search path**. `pg_graphql`, enabled by default on hosted
  projects, exposes every schema on the search path through `/graphql/v1`.

Then check with a real user token: `GET /rest/v1/products` must fail, and a GraphQL
`productsCollection` query must return no data. Auth (`/auth/v1`) is unaffected.

## Hosted projects: Auth settings

Also per hosted project, Dashboard → Authentication (the full checklist is in
`infra/scripts/provision-enterprise.md`): sign-up off, email provider on, minimum password
length 10, Site URL = the admin app's URL, the invite and recovery templates from
`templates/`, and custom SMTP on the production project only. Then run
`backend/scripts/check_auth_settings.py --url <project URL> --key <publishable key>`.

## Access model in one paragraph

- **RLS:** every table in `public` has RLS enabled. Policies read
  `(auth.jwt() ->> 'tenant_id')::uuid` and `auth.jwt() ->> 'app_role'`. The custom access token
  hook sets both claims from `tenant_members`.
- **anon:** has no grants on any table. The storefront reads through public FastAPI endpoints.
- **Service-role-only tables:** `automation_events` and `stripe_events` have no policies and no
  `authenticated` grants.
- **AI assistant:** reads only the `ai` views, as the `ai_readonly` role.
- **Membership re-check:** `public.is_current_member()` isn't granted to any app role. The
  backend calls it on its own login before switching role, so a dedicated production login
  needs `execute` on it, and `select, insert, delete` on `password_reset_attempts` (written by
  the public password-reset endpoint through `service_session`).
- **Team:** `tenant_members_guard` refuses, for every app role, team changes in demo tenants,
  new owners, removing or demoting the last owner, and changes other than the role.

## Notes for the backend

**1. The AI read path must not run as `postgres` in production.** The FastAPI backend must
connect as a **dedicated non-superuser login role that is granted `ai_readonly`**. For example:

```sql
create role delea_api login password '...' noinherit;
grant ai_readonly to delea_api;
-- plus whatever the backend's normal (tenant-scoped) path needs
```

For each `/api/v1/ai/query` request, it runs these inside one transaction:

```sql
set local role ai_readonly;
select set_config('app.tenant_id', '<tenant_id from the validated JWT>', true);
-- queries against ai.* only
```

`tenant_id` comes from the caller's validated JWT, never from request input. The migrations do
**not** grant `ai_readonly` to `postgres`. The pgTAP tests make that grant inside their own
rolled-back transaction, for local testing only.

**2. Order entry has to follow a fixed order, for every app user.** Owner and staff (any
`app_role`) can only create an invoice as a `quote`, and can only change its line items while
it is a quote. So an order is:

1. create the invoice as a `quote`;
2. add the line items and set the totals (the tax rate comes from `current_tax_rate_bps()`);
3. move it to `paid` (or further), which decrements stock.

After that, its lines are frozen for everyone except the service role. Corrections go through
cancel or refund plus a new invoice. Staff additionally can't change the money columns past
`quote`.

**3. No direct data API.** `public` isn't exposed over REST or GraphQL (see `config.toml`).
The admin app uses the Supabase client for Auth only; all data goes through FastAPI.

## Other rules enforced in the database

- **Stock:** when an invoice first leaves `quote`, each product's stock drops by the summed
  quantity of its lines. The products are locked `FOR UPDATE`, so concurrent sales can't
  oversell. Insufficient stock is refused, naming the SKU and the quantity available. There's
  no restock on refund or cancel in v1.

- **Numbering:** on insert, `quote_number` = `QTE-YYYY-NNN` from
  `company_settings.next_quote_number`. When the status first moves past `quote`,
  `invoice_number` = `INV-YYYY-NNN` and `paid_at` is set. Each counter is advanced with a
  row-locked `UPDATE`. Numbers and `paid_at` can't be changed afterwards, and values a client
  supplies are ignored.
- **Status:** `order_status` moves forward only. Forward skips are allowed.
- **Same tenant:** composite foreign keys stop a child row from referencing a parent in another
  tenant, even for the service role.
- **Automation events:** triggers write `order_placed`, `order_status_changed`, `stock_low` and
  `material_low` events to `automation_events`. The low-stock events fire only when stock crosses
  below its threshold. The FastAPI automation service drains this queue.
