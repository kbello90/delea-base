# infra/bicep

One parameterized template provisions the backend for every tier (TRD §2.1): the API and the
email worker, the same image, on one Linux App Service plan. Supabase and the Vercel frontends
are not managed here.

| File | Purpose |
|---|---|
| `main.bicep` | Log Analytics workspace, container registry, Key Vault, role assignments; calls the modules |
| `modules/appService.bicep` | The plan and two apps (API, worker), their settings, logs, the API's custom domain |
| `modules/hostNameTls.bicep` | Turns on TLS (SNI) for the API's custom domain with the managed certificate |
| `modules/redis.bicep` | Azure Cache for Redis. **Unused:** the app doesn't use Redis; `deployRedis` stays false |
| `parameters/demo.bicepparam` | The hosted demo (East US, `ENVIRONMENT=demo`) |
| `parameters/shared.bicepparam` | Essential + Professional production (not deployed yet) |
| `parameters/enterprise.template.bicepparam` | Copy to `enterprise.<customer>.bicepparam` per Enterprise tenant |

## What gets created (demo, about $18–20 a month plus Supabase Pro)

East US list prices (Azure Retail Prices API, checked 2026-11-06): the B1 Linux plan
$0.017/hour (about $12.41 a month), the Basic registry $0.1666/day (about $5.07), Log
Analytics free for the first 5 GB a month and then $2.30/GB, Key Vault $0.03 per 10,000
operations, the managed certificate free. Image builds (`az acr build`) cost $0.0001 per vCPU
second.

| Resource | Name | Notes |
|---|---|---|
| Log Analytics workspace | `delea-demo-logs` | 30 days; both apps' HTTP, console, app and platform logs |
| Container registry (Basic) | `acrdeleademo<suffix>` | No admin user: the apps pull with their managed identities |
| Key Vault | `kv-demo-<suffix>` | Access by Azure roles; soft delete 90 days; holds every secret |
| App Service plan (B1, Linux) | `delea-demo-plan` | Both apps share it |
| Web app: API | `delea-demo-api` | uvicorn on 8000; `/health` health check; optional custom domain |
| Web app: worker | `delea-demo-worker` | `python -m app.worker`; answers `GET /health` on 8000; every public request refused |

`<suffix>` is derived from the resource group, so names stay the same on every redeploy. Each
app has a system-assigned identity with exactly two roles: **AcrPull** on the registry and **Key
Vault Secrets User** on the vault. Neither app has FTP or basic-auth deployment credentials.

## Settings

The API refuses to start if a secret's Key Vault reference didn't resolve (missing secret, no
permission), or if `FORWARDED_ALLOW_IPS` contains `*`.
`backend/tests/test_bicep_settings.py` fails when the code reads a setting that is neither
set here nor listed there with a reason.

| Setting | API | Worker | Source |
|---|---|---|---|
| `ENVIRONMENT` | yes | yes | `demo` (the demo) or `production`; never `local`/`test` (refused by the template) |
| `SUPABASE_URL` | yes | | parameter (public) |
| `SUPABASE_ANON_KEY` | yes | | parameter (public: shipped to every browser) |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | | **Key Vault** `supabase-service-role-key` |
| `DATABASE_URL` | yes | yes | **Key Vault** `database-url`: the `delea_api` login, pooler in session mode |
| `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` | 1 / 5 | 1 / 2 | fixed: the two maximums total 7, under half the Supabase session pooler's pool of 15 (Micro compute), so an old and a new instance of both apps fit during a restart |
| `STRIPE_SECRET_KEY` | yes | | **Key Vault** `stripe-secret-key` (test key: a live key stops the API) |
| `STRIPE_WEBHOOK_SECRET` | yes | | **Key Vault** `stripe-webhook-secret` |
| `STRIPE_GATEWAY` | `stripe` | | fixed |
| `ADMIN_APP_URL` | yes | yes | parameter |
| `EMAIL_PROVIDER` | `resend` | `resend` | fixed |
| `EMAIL_FROM_DOMAIN` | yes | yes | parameter (`mail.deleajewelry.com`) |
| `RESEND_API_KEY` | yes | yes | **Key Vault** `resend-api-key` |
| `RESEND_WEBHOOK_SECRET` | yes | | **Key Vault** `resend-webhook-secret` |
| `TURNSTILE_SECRET_KEY` | yes | | **Key Vault** `turnstile-secret-key` |
| `SEND_EMAIL_HOOK_SECRET` | yes | | **Key Vault** `send-email-hook-secret`; added only with `DELEA_EMAIL_HOOK=true` (step 8) |
| `CORS_ORIGINS` | yes | | parameter (the admin's exact origin) |
| `FORWARDED_ALLOW_IPS` | yes | | parameter, required, never `*` (TRD §6.10); confirm on the deployed service |
| `WORKER_HEALTH_PORT` | | `8000` | fixed |
| `WEBSITES_PORT` | `8000` | `8000` | fixed (App Service) |

Not set hosted, on purpose: `ADMIN_DATABASE_URL` (scripts and test setup only), `MAILPIT_URL`
(local only), `LLM_API_KEY` (read by nothing yet), `JWT_AUDIENCE` (the default is Supabase's),
`PRODUCT_IMAGES_BASE_URL` and `BRAND_ASSETS_BASE_URL` (derived from `SUPABASE_URL`).

### Why `ENVIRONMENT=demo`

The code treats every value other than `local` and `test` the same way today: no
`*.localhost` or `http://` storefront origins, Turnstile required (no secret means no checkout),
Mailpit and the fake Stripe gateway refused. Live Stripe keys are refused whatever the value, and
demo shops are governed by `tenants.is_demo`, not by `ENVIRONMENT`. `demo` keeps the hosted
demo apart from `production`, the value the go-live step (TRD §6.15) will require before live
keys are ever allowed.

## Secrets

Never in a file, a parameter, a chat or a ticket. After the first deploy (phase 1 below), the
owner sets each one straight into the vault. The loop below asks for each value without
echoing it, so it never appears on screen or in shell history:

```sh
VAULT="<keyVaultName output>"
for name in database-url supabase-service-role-key stripe-secret-key stripe-webhook-secret \
            resend-api-key resend-webhook-secret turnstile-secret-key send-email-hook-secret; do
  read -rsp "$name: " value; echo
  az keyvault secret set --vault-name "$VAULT" --name "$name" --value "$value" --output none
  unset value
done
```

PowerShell: `$v = Read-Host -AsSecureString "<name>"`, then
`az keyvault secret set --vault-name $VAULT --name <name> --value ([System.Net.NetworkCredential]::new('', $v).Password) --output none`.

Setting a secret needs the **Key Vault Secrets Officer** role on the vault for your own account
(the template grants roles to the apps only).

## Validate locally (offline, no Azure)

```sh
export SUPABASE_URL=https://placeholder.supabase.co SUPABASE_ANON_KEY=placeholder \
       DELEA_ADMIN_APP_URL=https://admin.placeholder.example DELEA_FORWARDED_ALLOW_IPS=192.0.2.0/24
bicep build infra/bicep/main.bicep --stdout > /dev/null
bicep lint infra/bicep/main.bicep
for p in infra/bicep/parameters/*.bicepparam; do bicep build-params "$p" --stdout > /dev/null; done
```

CI runs the same (job "Infrastructure"), with Bicep pinned, and fails on any warning.

## Deploy (the hosted demo)

Not done yet: each command is run with the owner, one at a time. Resource group
`rg-delea-demo` in East US, in a subscription the owner chooses.

1. **Phase 1 (no apps):** `DELEA_DEPLOY_APPS=false`. Run `az deployment group what-if`, then
   `create`, with `--parameters infra/bicep/parameters/demo.bicepparam`. Creates the logs,
   registry and vault.
2. **Secrets:** give yourself Key Vault Secrets Officer, then set the first seven secrets (above).
   The eighth, `send-email-hook-secret`, can only exist once the hook is configured in Supabase
   (step 8); until then `DELEA_EMAIL_HOOK` stays false, so the API doesn't reference it.
3. **Image:** `az acr build --registry <registry> --image delea-backend:<tag> backend` (builds
   in Azure from the repo's Dockerfile; no local Docker push).
4. **Phase 2 (apps):** `DELEA_DEPLOY_APPS=true DELEA_IMAGE_TAG=<tag>`; `what-if`, then
   `create`. The first image pull can fail until the role assignments propagate; the apps
   retry.
5. **Smoke test:** `https://<apiDefaultHostName>/health/ready` is `{"status":"ready"}`; the
   worker's log stream shows `email worker: resend`. Confirm the front end's addresses in the
   API's request logs and set `DELEA_FORWARDED_ALLOW_IPS` to them (TRD §6.10).
6. **Custom domain:** in Cloudflare, add `CNAME api -> <apiDefaultHostName>` (DNS only) and
   `TXT asuid.api -> <customDomainVerificationId>`. Then redeploy with
   `DELEA_API_HOST_NAME=api.deleajewelry.com` and, on this deploy only,
   `DELEA_API_HOST_FIRST_BIND=true`: the host name is bound, a free managed certificate is
   issued, and TLS is turned on. Every later deploy keeps `DELEA_API_HOST_NAME` and leaves
   `DELEA_API_HOST_FIRST_BIND` unset (false): the binding is then never declared without TLS,
   so a redeploy doesn't drop TLS on the domain. Set by mistake later, it drops TLS briefly
   (until the same deploy's TLS step); left unset on the first bind, the certificate step fails
   because the host name isn't bound, and nothing changes: rerun with it set.
7. **Webhooks:** the Stripe endpoint for connected accounts at
   `https://api.deleajewelry.com/api/v1/stripe/webhook` with the nine events (TRD §6.14), and
   the Resend webhook; their signing secrets go into the vault.
8. **Send Email Hook (TRD §6.6), right after the API is live.** Until now the Supabase project
   uses its built-in mailer (team addresses only) with the hook off.
   1. Supabase dashboard › Authentication › Hooks › Send Email: type HTTPS, URL
      `https://api.deleajewelry.com/api/v1/auth/email-hook`, generate the secret and copy it.
      From the moment the hook is enabled until the redeploy below, Auth emails fail (the API
      answers 404 without the secret): do this when nobody is signing up or resetting.
   2. Set `send-email-hook-secret` in the vault to that value (the loop above, one name).
   3. Redeploy with `DELEA_EMAIL_HOOK=true` (`what-if`, then `create`).
   4. Prove the full path: a password reset for a test account arrives; a reset for a demo
      account and a magic-link request send nothing; the API's log stream shows both refusals
      (`Auth email not sent: ... reason=demo` and `reason=type not used`).
   With the hook on, an API that is down means no Auth emails (acceptable for the private demo;
   a production consideration, TRD §6.6). Turning the hook off in Supabase falls back to the
   built-in mailer.

Health checks: App Service calls `/health` on both apps. The worker answers 503 when no pass has
finished for ten minutes. On a single instance App Service replaces an instance that stays
unhealthy for an hour; a worker that crashes is restarted at once.

## Enterprise provisioning runbook (TRD §2.3)

1. Create the customer's Supabase project.
2. In its Data API settings, remove `public` from the exposed schemas **and** from the extra
   search path (or disable the Data API). Apply migrations (`supabase db push --project-ref
   <ref>`), set the `delea_api` password (TRD §6.6) and seed brand config.
3. Copy `enterprise.template.bicepparam` to `enterprise.<customer>.bicepparam`, set
   `tenantSlug`, and deploy in the two phases above, in the customer's own resource group.
4. Point the customer's Vercel domains at the shared frontend projects.

Step-by-step detail: [`infra/scripts/provision-enterprise.md`](../scripts/provision-enterprise.md).
