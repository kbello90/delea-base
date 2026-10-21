# infra/bicep

One parameterized template provisions the FastAPI backend for every tier (TRD §2.1):

| File | Purpose |
|---|---|
| `main.bicep` | Top-level template, parameterized by `tier` |
| `modules/appService.bicep` | Linux App Service plan + container web app |
| `modules/redis.bicep` | Azure Cache for Redis, deployed only when `deployRedis = true` |
| `parameters/shared.bicepparam` | Essential + Professional (one shared deployment) |
| `parameters/enterprise.template.bicepparam` | Copy to `enterprise.<customer>.bicepparam` per Enterprise tenant |

Supabase is not managed by Bicep. Its URL, keys, and connection string are passed in as
secure parameters. The `.bicepparam` files read them from environment variables, so no secret
is ever committed.

## Validate locally (offline)

```sh
bicep build infra/bicep/main.bicep --stdout > /dev/null
bicep build-params infra/bicep/parameters/shared.bicepparam --stdout > /dev/null
bicep build-params infra/bicep/parameters/enterprise.template.bicepparam --stdout > /dev/null
```

## Deploy

Set `DELEA_BACKEND_IMAGE`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
and `DATABASE_URL` in your shell, then:

```sh
az deployment group create \
  --resource-group "<resource-group>" \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/parameters/shared.bicepparam
```

## Enterprise provisioning runbook (TRD §2.3)

1. Create the customer's Supabase project.
2. In the project's Data API settings, remove `public` from the exposed schemas **and** from the extra search path (or disable the Data API). Then apply migrations (`supabase db push --project-ref <ref>`) and seed brand config.
3. Run this template with `enterprise.<customer>.bicepparam` to create the App Service (+ Redis).
4. Set the App Service environment variables from the new project's keys.
5. Point the customer's Vercel domains at the shared frontend projects.

Step-by-step detail: [`infra/scripts/provision-enterprise.md`](../scripts/provision-enterprise.md).
