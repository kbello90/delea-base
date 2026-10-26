# Adding a storefront domain to a tenant (manual, admin only)

Domain onboarding is done by hand by a DELEA admin. There is no self-serve domain screen. The
storefront finds its tenant from the browser's `Origin` host (TRD §1.3, §6.11), so a shop is
live on a domain once three things are true: the domain reaches the storefront app on Vercel,
`tenant_domains` maps it to the tenant, and the tenant's store is switched on.

Hosted deployment is not built yet. These steps assume the storefront Vercel project and the
hosted API exist; until then, use the local equivalent at the end.

## Before you start

- The tenant exists (provisioned; you have its id).
- The owner has filled in **Settings > Storefront** (contact email, pages) and **Settings >
  Brand** (name, colours, logo, storefront look). The store can stay switched off until the
  last step.
- **Launch blockers for any real shop** (TRD §6.6, §6.10): the API's least-privilege runtime
  login exists and is granted `storefront_reader` and `storefront_writer`,
  `TURNSTILE_SECRET_KEY` is set (without it the API turns checkout off), and
  `FORWARDED_ALLOW_IPS` is set on the
  App Service so the per-address limits see real client addresses.

## 1. Choose the domain

Prefer a subdomain of the customer's own domain, such as `shop.theirjewelry.com`: it needs a
single CNAME record. An apex domain (`theirjewelry.com`) also works but needs the A record
Vercel gives you, plus a redirect for `www`.

## 2. Add the domain in Vercel

1. Open the **storefront** Vercel project (not the admin project) > Settings > Domains.
2. Add the domain. Vercel shows the DNS record it expects (a CNAME target for a subdomain,
   an A record for an apex). Copy exactly what Vercel shows; don't reuse a value from
   another project or from memory.

## 3. Add the DNS record in Cloudflare

1. In the customer's Cloudflare zone > DNS > Records, add the record Vercel showed.
2. Set **Proxy status: DNS only** (grey cloud). Vercel issues and renews the certificate
   itself and recommends against proxying through Cloudflare; check Vercel's current
   guidance if anything looks different.
3. If the zone has CAA records, make sure they allow the certificate authority Vercel uses
   (Vercel's domain page says when they don't).

## 4. Wait for Vercel

The domain page in Vercel must show the configuration as valid and the certificate as issued.
`https://<domain>` then loads the storefront app, which says "This shop isn't available"
until steps 5 and 7 are done.

## 5. Map the domain to the tenant

From a workstation, on an administrative database connection (never the API's own login):

```sh
# First a dry run: checks the domain, the tenant and conflicts; writes nothing.
ADMIN_DATABASE_URL="<admin connection string>" \
  python backend/scripts/add_tenant_domain.py --allow-remote \
  --tenant <tenant uuid> --domain shop.theirjewelry.com --dry-run

# Then for real.
ADMIN_DATABASE_URL="<admin connection string>" \
  python backend/scripts/add_tenant_domain.py --allow-remote \
  --tenant <tenant uuid> --domain shop.theirjewelry.com
```

- The script stores the domain the way browsers send it (lowercase, punycode), refuses
  `https://`, paths, ports and `localhost` names on a hosted database, and refuses a domain
  that belongs to another tenant. Running it twice changes nothing.
- It tells you if the tenant's store is still switched off.
- The API picks the domain up within a minute (host lookups are cached for 60 seconds).
- Never put the connection string in a file in the repository.

## 6. Turnstile

Add the domain to the Turnstile widget's hostname list in Cloudflare (Turnstile > the widget >
Hostname management). The API accepts a token only when Cloudflare reports this shop's
hostname, so a missing hostname means every order on that domain is refused. The widget's
site key is the storefront's `VITE_TURNSTILE_SITE_KEY`; its secret is the API's
`TURNSTILE_SECRET_KEY` (set in the App Service, never in a file in the repository).

## 7. Switch the store on

The owner turns on **Settings > Storefront > Store is online** (or you do it with them).

## 8. Smoke test

1. Open `https://<domain>`: the right business name, colours and logo; products load.
2. Open a product page and the footer pages.
3. From a terminal, the API refuses a request that doesn't come from a storefront:

   ```sh
   curl -s https://<api host>/api/v1/public/store -H "Origin: https://not-a-shop.example"
   # {"detail":"Store not found"}
   ```

4. The same request with `-H "Origin: https://<domain>"` returns this tenant's store.

## Removing a domain

There is no script for removal. On the administrative connection:

```sql
delete from public.tenant_domains where domain = 'shop.theirjewelry.com';
```

Then remove the domain from the Vercel project and the DNS record from Cloudflare. The API
stops serving it within a minute. To take a shop offline without touching DNS, switch its
store off in Settings instead.

## Local equivalent

The seed maps `localhost:5174` (tenant A) and `aurora.localhost:5174` (tenant B), and the
showcase loader maps `showcase.localhost:5174`. To add another local one:

```sh
python backend/scripts/add_tenant_domain.py --tenant <uuid> --domain myshop.localhost:5174
```

Browsers send `*.localhost` to this machine, so `http://myshop.localhost:5174` works with the
storefront dev server. `localhost` names and `http://` origins are accepted only when the API's
`ENVIRONMENT` is `local` or `test`.
