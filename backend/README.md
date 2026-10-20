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

Put them in `backend/.env`. That file is gitignored, so never commit it.

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
    `backend/.env`, they **skip** with a message saying how to start the stack. That's why CI
    passes without a database.

## Error responses

Every error is `{"detail": "..."}`. SQL text, constraint and table names, and stack traces never
reach the client.

| Cause | Status |
|---|---|
| missing, invalid or expired token | 401 |
| valid token without a membership (no `tenant_id` / `app_role`) | 403 |
| role not allowed (`require_role`), RLS violation, insufficient privilege | 403 |
| no row, or a row hidden by RLS | 404 |
| unique violation, foreign-key violation, trigger exception (`P0001`) | 409 |
| check / not-null violation, invalid value, request validation | 422 |
| JWKS or database unreachable | 503 |

**Trigger messages:** when one of our PL/pgSQL triggers raised the error, the trigger's own
message is returned with the mapped status. For example, a backward `order_status` returns
422 "order_status cannot move backward (paid -> quote)". Postgres-generated messages always
stay generic.

## Known limitations

- **Removing access takes effect at the next token refresh.** An access token stays valid until
  it expires (`jwt_expiry`, 3600 s locally), even if the user's `tenant_members` row is deleted
  or their role changes in the meantime. The `tenant_id` and `app_role` claims are fixed when
  the token is issued. We'll revisit this with a shorter expiry, or a membership re-check on
  sensitive requests.
- **Multi-tenant users:** a user in several tenants gets their oldest membership. See the TODO
  in the access token hook.
