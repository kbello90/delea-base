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

Each app reads `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` and `VITE_API_BASE_URL`.
Copy `apps/<app>/.env.example` to `apps/<app>/.env.local` to set them. The placeholder pages
render without them.

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
