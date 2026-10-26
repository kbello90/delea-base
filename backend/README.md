# backend/

FastAPI backend for DELEA. It talks to Postgres directly through a psycopg async pool
(TRD locked decision 6):

| Session | Used for | Runs as |
|---|---|---|
| `user_session` | every authenticated request (via the `DbConn` dependency) | `authenticated`, with the caller's verified JWT claims, so RLS enforces access |
| `storefront_session` | public storefront reads (`/api/v1/public/...`) | `storefront_reader`, read-only, scoped by `app.tenant_id` from the request's Origin host (TRD §6.11) |
| `service_session` | public writes (checkout, from Slice A2), Stripe webhooks, the automation worker | the connection role (no RLS); must be a dedicated non-superuser role in production |
| `ai_session` | the AI assistant | `ai_readonly`, scoped by `app.tenant_id` from the verified token |

Each session is one transaction. It commits when the endpoint returns, before the response is
sent, and rolls back on any error.

## Setup

```sh
cd backend
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"      # Windows: .venv\Scripts\python
cp .env.example .env                              # then fill in (see below)
```

### Local keys

Start the local Supabase stack from the repo root, then print its local demo keys:

```sh
npx supabase@2.121.0 start
npx supabase@2.121.0 status -o env
```

Put them in `backend/.env`. That file is gitignored, so never commit it. Or let the script
write it from the local stack, which is what CI does. It refuses non-local URLs:

```sh
npx supabase@2.121.0 status -o env | python backend/scripts/write_local_env.py
```

| `supabase status` | `backend/.env` |
|---|---|
| `API_URL` (http://127.0.0.1:55321) | `SUPABASE_URL` |
| `ANON_KEY` | `SUPABASE_ANON_KEY` |
| `SERVICE_ROLE_KEY` | `SUPABASE_SERVICE_ROLE_KEY` |
| `DB_URL` (…@127.0.0.1:55322/postgres) | `DATABASE_URL` |

These keys only work against the local stack. Never put a hosted project's keys in a file in
the repo.

## Run

```sh
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

**Windows:** async psycopg can't run on Windows' default Proactor event loop, so add
`--loop asyncio:SelectorEventLoop`:

```sh
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000 --loop asyncio:SelectorEventLoop
```

Linux and Docker use the default loop, and the Dockerfile has no loop flag.

- `GET /health`: liveness. It never touches the database.
- `GET /health/ready`: runs `select 1`. It returns 503 if the database is unreachable or
  `DATABASE_URL` isn't set.

## OpenAPI schema and pinned versions

The frontend's typed client is generated from `packages/api-client/openapi.json`. After any
change to routes or models, regenerate both files and commit them with the change:

```sh
.venv/bin/python scripts/dump_openapi.py          # writes packages/api-client/openapi.json
pnpm --filter @delea/api-client gen               # writes packages/api-client/src/schema.d.ts
```

CI regenerates both and fails on any diff. `fastapi`, `starlette` and `pydantic` are pinned to
exact versions in `pyproject.toml` because they shape the generated schema: with version ranges,
a new release could change `openapi.json` and fail the drift check without any code change.
Bump them on purpose, and regenerate and commit the schema in the same change.

## Tests

```sh
.venv/bin/python -m pytest                  # everything; integration tests skip if the stack is down
.venv/bin/python -m pytest -m integration   # only the integration tests (needs the local stack)
.venv/bin/python -m pytest -m "not integration"
.venv/bin/python -m ruff check .
```

- **Unit tests** need no network or database, and always run:
  - JWT verification against a locally generated EC key, with the JWKS client replaced;
  - error mapping;
  - CORS;
  - health.
- **Integration tests** (`tests/integration/`, marker `integration`) run against the local
  stack.
  - **Setup:** they create real Auth users through the Auth admin API with the local
    service-role key: owner A, staff A, cpa A, owner B, and a user with no membership. They
    insert the `tenant_members` rows, sign each user in through `/auth/v1/token` to get real
    ES256 access tokens, and delete the users afterwards.
  - **Skipping:** if the database or Auth is unreachable, or the keys are missing from
    `backend/.env`, they **skip** with a message saying how to start the stack. That's why the
    plain backend CI job passes without a database. Set `DELEA_REQUIRE_INTEGRATION=1` to make
    them **fail** instead. The CI `database` job does this, after starting the local stack and
    running the pgTAP tests.
  - **Seed guard:** the run fails if any seed row was modified. Tests must create their own rows
    with the `IT-` prefix, which are deleted afterwards.

## Product images

Images live in the `product_images` table (paths only) and in two Storage buckets
(migration 20261020001500):

| Bucket | Public | Holds | Written by |
|---|---|---|---|
| `product-uploads` | no | raw uploads (JPEG/PNG/WebP, 5 MiB), deleted at registration | the browser, only through a signed URL the backend issues |
| `product-images` | read | the 1200 px and 400 px WebP variants | the backend only |

Restrictive policies deny `anon` and `authenticated` every direct read or write in both buckets.

1. `POST /products/{id}/images/uploads` signs `{tenant_id}/{product_id}/{upload_id}.{ext}`.
   The tenant comes from the token; a `tenant_id` or path in the body is a 422.
2. The browser PUTs the file to the signed URL.
3. `POST /products/{id}/images` rebuilds that path from the token, downloads the file, and runs
   `app/services/product_images.py` in the thread pool (at most 2 at a time). The service checks
   it is really a JPEG, PNG or WebP (Pillow, not the content type), at most 5 MiB and
   25 megapixels. It applies the EXIF orientation, converts non-sRGB ICC profiles to sRGB,
   keeps transparency, strips all metadata (EXIF/GPS, XMP, ICC), and writes WebP variants
   (1200 px at quality 85, 400 px at 82). The row is then inserted, and the raw upload is
   always deleted. If the insert fails, the variants are deleted too.
4. Primary, order and delete are owner only, like every product write. Deleting an image or a
   product removes its files after the transaction commits.

**Storage key.** These calls use `SUPABASE_SERVICE_ROLE_KEY` over the Storage HTTP API
(`app/core/storage.py`). It lives in `backend/.env` locally (gitignored) and in an App Service
setting from a `@secure()` Bicep parameter when hosted. It is never a `VITE_*` variable, so it
can't reach the browser. No SQL runs with it, so this isn't a database `service_session`: image
rows are read and written in the caller's RLS user session.

**Public URLs.** The database stores object paths only. URLs are `PRODUCT_IMAGES_BASE_URL` plus
the path. When the setting is empty, it is derived from `SUPABASE_URL`
(`…/storage/v1/object/public/product-images`), which is right for the local stack. Hosted,
set it to the project's (or a CDN's) public URL for the bucket.

The local stack must run the Storage API: start it with
`npx supabase@2.121.0 start -x realtime,imgproxy,edge-runtime,logflare,vector,supavisor`.

## Error responses

Every error is `{"detail": "..."}`. SQL text, constraint and table names, and stack traces never
reach the client.

| Cause | Status |
|---|---|
| missing, invalid or expired token | 401 |
| valid token without a membership (no `tenant_id` / `app_role`) | 403 |
| role not allowed (`require_role`), RLS violation, insufficient privilege | 403 |
| no row, or a row hidden by RLS | 404 |
| unique violation, foreign-key violation, trigger exception (`P0001`), `ConflictError` (e.g. "only quotes can be edited", deletes that would damage history) | 409 |
| check / not-null violation, invalid value, request validation | 422 |
| JWKS or database unreachable | 503 |

**Trigger messages:** when one of our PL/pgSQL triggers raised the error, the trigger's own
message is returned with the mapped status. For example, a backward `order_status` returns
422 "This invoice can't move back to quote.". Postgres-generated messages always
stay generic.

## Known limitations

- **Membership re-check (done).** Token claims are fixed for about an hour, so `user_session`
  and `ai_session` first run `public.is_current_member(user, tenant, role)` (two primary-key
  lookups) and answer 401 "Membership no longer valid" for a removed member, a changed role or
  an inactive tenant. The browser refreshes once on a 401; the new token carries the current
  membership. `apply_user_scope` alone (tests) skips the check. TRD §6.6.
- **One account holds one membership.** An email with an account in another business can't be
  invited; a CPA serving several businesses needs one address per business. A tenant switcher
  is the later fix. TRD §6.6.
- **Auth emails and demo tenants.** The API never emails demo accounts, but Auth itself answers
  a direct reset or email-change request. The public demo therefore runs in its own Supabase
  project without custom SMTP; a Send Email Hook is a launch blocker for any project that has
  a custom sender and demo tenants. TRD §6.6.
- **Password-reset limit behind a proxy. Required before the public demo is deployed.**
  The limit keys on `request.client`, which uvicorn takes from `X-Forwarded-For` only for peers
  in `FORWARDED_ALLOW_IPS`. On App Service set it to the front end's addresses (never `*`,
  which trusts the client-written leftmost entry), and check it on the deployed service. The
  tests prove the behaviour behind a trusted proxy. TRD §6.10.
- **Orphaned Storage objects are not swept.** **Milestone B** (not implemented yet): one
  scheduled orphan sweep that deletes (1) abandoned raw uploads in `product-uploads` and (2)
  abandoned receipt uploads (`pending-*` in `expense-receipts`), both older than 24 hours
  (registration always removes them, but a file uploaded and never registered stays), and (3)
  receipt files no expense points to, left by a failed cleanup after an expense was deleted
  or its receipt replaced or removed, and (4) logo files in `brand-assets` that no
  `brand_config.logo_path` points to, left by a failed cleanup after a logo was replaced or
  removed (those deletes run after the commit and are only logged when they fail). Abandoned
  logo uploads (`{tenant}/brand/*` in `product-uploads`) are covered by (1).
- **Error responses have no machine-readable code.** An error body is only
  `{"detail": "<message>"}`, so the admin app tells a duplicate value apart from other 409s
  by matching the message text. **Milestone B** (not implemented yet): a stable `code` field
  on every error response (for example `duplicate_value`, `in_use`), and the frontend
  switches on `code` instead of message text.
- **The API logs into Postgres as `postgres`.** User and AI sessions switch role, but
  `service_session` keeps that administrative login. **Required before the public demo is
  deployed** (not implemented yet): a least-privilege runtime login, separate from the one
  migrations and the showcase loader use. It needs `grant storefront_reader to <login>` for
  the public storefront reads.
- **Storefront host lookups are cached for 60 seconds** per API instance (unknown hosts too),
  so a new domain, a switched-off store or an inactive tenant takes up to a minute to show.
- **Demo deployment.** A public demo lets visitors edit and upload. **Milestone B** (not
  implemented yet): a scheduled reset that re-runs the showcase loader, and upload and rate
  limits for `is_demo` tenants. The least-privilege runtime login above is required
  before that deployment too. See `showcase/README.md` and TRD §6.7 (provisioning, the demo
  tenant, and the worker contract: skip `is_demo` tenants and processed events).
- **Two-decimal currencies only (v1).** Every amount is integer hundredths of the major unit
  (`*_cents`). `company_settings.currency` accepts only USD, CAD, EUR, GBP, MXN, AUD, NZD and
  CHF (`SUPPORTED_CURRENCIES` in `app/models/settings.py`); zero-decimal (JPY) and
  three-decimal (KWD) currencies are a 422.
  Every role reads the currency from `GET /api/v1/settings/currency` (owner, staff, cpa), which
  calls `current_tenant_currency()`, and the invoice and expense CSV exports carry a `currency`
  column just before the first amount column.
- **Multi-tenant users:** a user in several tenants gets their oldest membership. See the TODO
  in the access token hook.
