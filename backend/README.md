# backend/

FastAPI backend for DELEA. It talks to Postgres directly through a psycopg async pool
(TRD locked decision 6):

| Session | Used for | Runs as |
|---|---|---|
| `user_session` | every authenticated request (via the `DbConn` dependency) | `authenticated`, with the caller's verified JWT claims, so RLS enforces access |
| `service_session` | public storefront endpoints, Stripe webhooks, the automation worker | the connection role (no RLS); must be a dedicated non-superuser role in production |
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
422 "order_status cannot move backward (paid -> quote)". Postgres-generated messages always
stay generic.

## Known limitations

- **A removed member, or a tenant that is no longer active, keeps API access until their token
  expires (about an hour).** RLS and the backend trust the `tenant_id` and `app_role` claims,
  which are fixed when the token is issued (`jwt_expiry`, 3600 s locally). Deleting the
  `tenant_members` row, changing the role, or setting `tenants.is_active = false` changes
  nothing for tokens already issued. Only `current_tenant_currency()` re-checks the membership
  today. **Milestone B, required before the first paying customer** (not implemented yet):
  in the `user_session` setup, one primary-key lookup per request that checks the
  `tenant_members` row exists for (user, tenant, role) and `tenants.is_active` is true,
  refusing with 403 otherwise; and the same membership check in `current_tax_rate_bps()`,
  in the same change.
- **Abandoned raw uploads stay in `product-uploads`.** A file uploaded through a signed URL
  but never registered is not removed (registration always removes it). **Milestone B**
  (not implemented yet): a scheduled sweep that deletes objects in `product-uploads` older
  than 24 hours.
- **Two-decimal currencies only (v1).** Every amount is integer hundredths of the major unit
  (`*_cents`). `company_settings.currency` accepts only USD, CAD, EUR, GBP, MXN, AUD, NZD and
  CHF (`SUPPORTED_CURRENCIES` in `app/models/settings.py`); zero-decimal (JPY) and
  three-decimal (KWD) currencies are a 422.
  Every role reads the currency from `GET /api/v1/settings/currency` (owner, staff, cpa), which
  calls `current_tenant_currency()`, and the invoice and expense CSV exports carry a `currency`
  column just before the first amount column.
- **Multi-tenant users:** a user in several tenants gets their oldest membership. See the TODO
  in the access token hook.
