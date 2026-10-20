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

## Access model in one paragraph

- **RLS:** every table in `public` has RLS enabled. Policies read
  `(auth.jwt() ->> 'tenant_id')::uuid` and `auth.jwt() ->> 'app_role'`. The custom access token
  hook sets both claims from `tenant_members`.
- **anon:** has no grants on any table. The storefront reads through public FastAPI endpoints.
- **Service-role-only tables:** `automation_events` and `stripe_events` have no policies and no
  `authenticated` grants.
- **AI assistant:** reads only the `ai` views, as the `ai_readonly` role.

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

**2. In-store order entry by staff has to follow a fixed order.** Once an invoice is past
`quote`, staff can't change its money columns (`subtotal_cents`, `tax_rate_bps`, `tax_cents`,
`total_cents`), and can't add, edit or delete its line items. Only the owner or the service role
can. Staff can still move `order_status` forward and edit non-financial fields such as `notes`.

So a staff member taking an in-store order must:

1. create the invoice as a `quote`,
2. add the line items and set the totals,
3. then mark it `paid`.

The alternative is to go through a FastAPI endpoint that does the whole thing with the service
role. Inserting an invoice directly as `paid` and then adding line items fails for staff.

## Other rules enforced in the database

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
