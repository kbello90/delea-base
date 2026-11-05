# Provision an Enterprise customer

Stub runbook. Restates TRD §2.3. Commands are filled in as each step is automated.

An Enterprise customer gets its own copy of the same deployable artifact: a dedicated
Supabase project, App Service, and Redis. There is no code fork and no tier branching in the
backend; only environment values differ.

## Steps

1. **Create the Supabase project.**
   Create a new Supabase project on dedicated compute for the customer (Supabase CLI or
   Management API), on the hosted Postgres major version (17, as `supabase/config.toml`).
   Record its project ref and URL. Create a **publishable key** and a **secret key** (Settings >
   API Keys); the legacy `anon` and `service_role` keys are retired by Supabase by the end of
   2026. The publishable key is `SUPABASE_ANON_KEY` (and the admin app's
   `VITE_SUPABASE_ANON_KEY`); the secret key is `SUPABASE_SERVICE_ROLE_KEY`, sent on the `apikey`
   header only (`backend/app/core/supabase_keys.py`). The API refuses to start with the two
   swapped.
   **Signing key:** the API accepts only ES256 tokens. Under Settings > JWT Signing Keys, if
   the current key isn't ECC (P-256): **Migrate JWT secret**, check the standby key is ECC
   P-256, then **Rotate keys**.

2. **Close the Data API, then apply migrations and seed brand config.**
   In the new project's dashboard, Data API settings:
   - remove `public` from the **exposed schemas**, so only `graphql_public` is left, or disable the Data API entirely;
   - remove `public` from the **extra search path**.

   Both are needed: `pg_graphql`, which is enabled by default on hosted projects, exposes every schema on the search path through `/graphql/v1`. Never expose `ai`. Then check with a real user token: `GET /rest/v1/products` must fail, and a GraphQL `productsCollection` query must return no data. The app only uses Auth directly; everything else goes through FastAPI (TRD §1.3).

   Apply the same migration stream used by the shared project:
   `supabase db push --project-ref <ref>`.
   Then seed the customer's brand config. RLS stays enabled even on a single-tenant project.

3. **Run Bicep to create the API, the worker, the registry and the Key Vault.**
   Copy `infra/bicep/parameters/enterprise.template.bicepparam` to
   `enterprise.<customer>.bicepparam`, set `tenantSlug`, then deploy `infra/bicep/main.bicep`
   with that parameter file in two phases (first without the apps). See
   `infra/bicep/README.md` ("Deploy").

4. **Put the new project's secrets into the deployment's Key Vault** (between the two phases).
   `database-url` (the project's `delea_api` login, after `alter role delea_api with login
   password '…'`), `supabase-service-role-key`, the Stripe, Resend and Turnstile secrets: the
   seven names in `infra/bicep/README.md` ("Secrets"). Secrets are never Bicep parameters;
   `SUPABASE_URL` and the public anon key are ordinary parameters.

5. **Point the customer's Vercel domains at the shared frontend projects.**
   `apps/admin` and `apps/storefront` are not deployed per customer; add the customer's
   domains to the shared Vercel projects.

6. **Auth settings (every hosted project: production, each Enterprise one, the public demo).**
   `config.toml` only configures the local stack. In the dashboard, Authentication:
   - **Sign-up off:** "Allow new users to sign up" off. Keep the **Email** provider on (it
     carries password sign-in, invites and resets).
   - **Password minimum 10 characters** (Providers → Email).
   - **Site URL** = the admin app's URL for this project (invite and reset links go to
     `{Site URL}/accept-invite` and `{Site URL}/reset-password`).
   - **Email templates:** paste `supabase/templates/invite.html` and `recovery.html` (subjects
     as in `config.toml`). Email OTP expiry: 86400 s (24 hours).
   - **Custom SMTP on the production project only.** You arrange: an email provider account
     (Postmark, Resend, SES…), a verified sending domain with SPF, DKIM and DMARC, then enter
     the SMTP host, port, user and password yourself in Authentication → SMTP. Never put them
     in the repo or send them to anyone. Then raise the email rate limit.
   - **The public demo project gets no custom SMTP.** Its built-in mailer only reaches the
     project's team members (https://supabase.com/docs/guides/auth/auth-smtp), so demo
     visitors can't use invites, resets or email changes to mail third parties. Keep that
     organization's members to our own team. A project that has custom SMTP and demo tenants
     needs a Send Email Hook first (TRD §6.6).
   - **Check it:** `python backend/scripts/check_auth_settings.py --url <project URL> --key
     <publishable key>` must print `ok` for each project (repeat `--url`/`--key` for several).
     It reads the public settings: sign-up off, email provider on. Check the password minimum
     and SMTP in the dashboard.
   - **Check the whole project:** after the migrations are pushed, run
     `python backend/scripts/check_hosted_project.py --url <project URL> --sign-in` in your own
     terminal. It asks for the publishable and secret keys with hidden input and prints only
     `ok`/`FAIL` lines: Auth settings, an ES256 signing key, the Data API closed (REST and
     GraphQL, for the browser key and for a signed-in user), the secret key accepted by Auth
     admin and Storage as the API sends it, the four buckets, and a real sign-in whose token is
     ES256 and verifies (a key that is only on standby fails). `--sign-in` creates a temporary
     confirmed user (no email) and deletes it; without it the script only reads.

7. **Trusted proxy headers for the API. Required before the public demo is deployed.**
   The password-reset limit counts requests per client address (TRD §6.10). Behind App Service
   the API sees the platform's front end, not the visitor.
   - Set `FORWARDED_ALLOW_IPS` on the App Service to the front end's addresses or range (uvicorn
     then takes the rightmost untrusted `X-Forwarded-For` entry, the one the platform appends).
     Never `*`: uvicorn would take the leftmost entry, which a client can forge. Left at the
     default, every visitor shares one budget.
   - Confirm on the deployed service which header the front end sets and its format (for
     example an address with a port).
   - Check it: from one machine, send six requests to `POST /api/v1/auth/password-reset`, each
     with a different made-up `X-Forwarded-For`; the sixth must get 429. From a second machine,
     a request must still get 202.
