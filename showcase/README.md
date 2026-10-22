# DELEA Showcase

A demo tenant, **DELEA Showcase** (brand "DELEA Jewelry"), built from real product photos plus a
generated six-month history, so the dashboard and product pages look like a working shop.

- `products.yaml` (committed) lists the products: SKU, name, description, metal, prices,
  stock, and the photo filenames in display order. Edit it by hand; the loader picks up the
  edits on its next run.
- The photos never enter this repository. The loader reads them from the folder in
  `SHOWCASE_DIR` and uploads only the processed WebP variants to Storage. Only
  `products.yaml` and this README can be committed here (`.gitignore`, and a test that fails
  if anything else under `showcase/` is tracked or if any tracked file contains a path from
  a user's machine).

## What the loader does

`backend/scripts/load_showcase.py` (code in `backend/showcase/`):

1. Validates `products.yaml`, checks every listed photo exists, and processes every photo
   with the API's own `process_product_image` (sRGB, no metadata, WebP 1200/400) before it
   writes anything. `--dry-run` stops here.
2. Creates or updates the three logins (owner, staff, cpa), already confirmed: Supabase sends
   no email.
3. Removes the previous showcase (its Storage objects, then its rows). It refuses any tenant
   that isn't a demo tenant, so it can never remove a real business.
4. In one transaction: provisions the tenant (`is_demo = true`, 8% sales tax) through
   `app/services/provisioning.py`, then products and images, six materials, fourteen
   fictional customers (three wholesale, one tax-exempt; `example.com` addresses only), the
   invoices (through the same path as the API: quote, lines, status changes; the database
   numbers them and decrements stock), one refund, one cancellation, open quotes, and
   expenses. Dates are relative to the run date; the random seed is fixed, so every run
   looks the same.
5. Backdates the invoices (the one step that runs with `session_replication_role = replica`,
   scoped to this tenant), then runs integrity checks: every foreign key and check
   constraint re-validated, document counters, invoice totals, stock equal to
   `products.yaml`, images, no pending automation events. Any failure rolls everything back.

Stock in `products.yaml` is the stock on hand **today**: the loader starts each product at
that number plus the units sold in the loaded history.

### No email, ever

Demo tenants send nothing: the showcase has no automation rules, every automation event the
triggers emit is marked processed as it is created, customer emails are `example.com`, and
nobody is opted in to marketing. A future worker, and Stripe, must also skip any tenant with
`tenants.is_demo` (see the TRD).

## Run it locally

From the repository root, with the local stack running and `backend/.env` filled in:

```sh
SHOWCASE_DIR="<the folder with the product photo folders>" \
  backend/.venv/Scripts/python backend/scripts/load_showcase.py --dry-run
SHOWCASE_DIR="<same>" backend/.venv/Scripts/python backend/scripts/load_showcase.py
```

Sign in at the admin app as `owner@showcase.delea.test`, `staff@showcase.delea.test` or
`cpa@showcase.delea.test` with the local dev password (the one `create_dev_users.py` prints).
Re-running replaces the showcase (and only the showcase).

## Environment

| Variable | Meaning |
|---|---|
| `SHOWCASE_DIR` | Required. The photo folder; read only. |
| `SHOWCASE_DATABASE_URL` | The loader's own administrative database connection. Locally it defaults to `DATABASE_URL`; on a hosted target it is required. |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Storage and the Auth admin API (from `backend/.env` locally). |
| `SHOWCASE_PASSWORD` | The three logins' password. Required on a hosted target; locally the dev password. Never printed. |
| `SHOWCASE_OWNER_EMAIL`, `SHOWCASE_STAFF_EMAIL`, `SHOWCASE_CPA_EMAIL` | The logins (defaults `*@showcase.delea.test`, which can't receive mail). |

## Hosted

The loader refuses any database or Storage URL that isn't on this machine unless
`--allow-remote` is passed, and then it also needs `SHOWCASE_DATABASE_URL` and
`SHOWCASE_PASSWORD`. Run it from a workstation with your own environment variables:

- **On its own administrative database connection, never the API's runtime role**, and never
  from inside the deployed API (the app never imports `backend/showcase/`, which a test checks;
  the package isn't in the app's wheel).
- The service-role key gives full Storage and Auth access: keep it in your environment only.
- A public demo lets visitors edit and upload; see "Demo deployment" in the TRD (scheduled
  reset, upload and rate limits for demo tenants).
