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

2. **Close the Data API, then apply migrations and seed brand config.**
   In the new project's dashboard, Data API settings:
   - remove `public` from the **exposed schemas**, so only `graphql_public` is left, or disable the Data API entirely;
   - remove `public` from the **extra search path**.

   Both are needed: `pg_graphql`, which is enabled by default on hosted projects, exposes every schema on the search path through `/graphql/v1`. Never expose `ai`. Then check with a real user token: `GET /rest/v1/products` must fail, and a GraphQL `productsCollection` query must return no data. The app only uses Auth directly; everything else goes through FastAPI (TRD §1.3).

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
