# Provision an Enterprise customer

Stub runbook. Restates TRD §2.3. Commands are filled in as each step is automated.

An Enterprise customer gets its own copy of the same deployable artifact: a dedicated
Supabase project, App Service, and Redis. There is no code fork and no tier branching in the
backend; only environment values differ.

## Steps

1. **Create the Supabase project.**
   Create a new Supabase project on dedicated compute for the customer (Supabase CLI or
   Management API). Record its project ref, URL, anon key, service role key, and DB
   connection string.

2. **Apply migrations and seed brand config.**
   Apply the same migration stream used by the shared project:
   `supabase db push --project-ref <ref>`.
   Then seed the customer's brand config. RLS stays enabled even on a single-tenant project.

3. **Run Bicep to create the App Service (+ Redis).**
   Copy `infra/bicep/parameters/enterprise.template.bicepparam` to
   `enterprise.<customer>.bicepparam`, set `tenantSlug`, then deploy `infra/bicep/main.bicep`
   with that parameter file. See `infra/bicep/README.md`.

4. **Set the App Service environment variables from the new project's keys.**
   `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`
   (and Stripe / LLM keys). These are passed to Bicep as secure parameters in step 3.

5. **Point the customer's Vercel domains at the shared frontend projects.**
   `apps/admin` and `apps/storefront` are not deployed per customer; add the customer's
   domains to the shared Vercel projects.
