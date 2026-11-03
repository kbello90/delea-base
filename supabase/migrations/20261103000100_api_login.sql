-- The API's least-privilege database login (TRD §6.6; hosted-demo plan step 1).
--
-- Until now the API connected as `postgres`, which bypasses RLS: every request switches to a
-- narrower role first, but the few statements run on the bare connection (service_session)
-- could read or write anything, tenants.is_demo included. From here the API connects as
-- `delea_api`, which:
--   - is not a superuser, can't bypass RLS, create roles or databases, and inherits nothing
--     (NOINHERIT): on its own it holds no table privilege at all;
--   - may switch (`set local role`) into exactly the six roles the API uses:
--     authenticated (signed-in users, RLS), ai_readonly (the AI views), storefront_reader,
--     storefront_writer (checkout), payments_writer (Stripe) and email_worker (outbox);
--   - may execute is_current_member() (the per-request membership re-check, run before the
--     switch) and the five functions below, which replace the statements service_session ran
--     directly (password reset, the owner's test-email address, open card sessions).
-- `postgres` stays the administrative login: migrations, the showcase loader, scripts.
--
-- The role is created NOLOGIN without a password. Locally, supabase/seed.sql gives it the
-- public local password; on a hosted project the owner runs
--   alter role delea_api with login password '<their own secret>';
-- in the SQL editor and stores the connection string as the API's DATABASE_URL. The password
-- never appears in a migration, the repository or a chat.

do
$$
begin
  if not exists (select 1 from pg_roles where rolname = 'delea_api') then
    create role delea_api nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication;
  end if;
end
$$;

grant authenticated, ai_readonly, storefront_reader, storefront_writer, payments_writer, email_worker
  to delea_api;
grant usage on schema public to delea_api;
grant execute on function public.is_current_member(uuid, uuid, text) to delea_api;

-- ---------------------------------------------------------------------------
-- Password reset (POST /auth/password-reset), public: the abuse limits, then the account
-- lookup in the background task.
-- ---------------------------------------------------------------------------
-- One check at a time per address (advisory lock on its hash), then the per-address count, the
-- per-email cooldown, the attempt itself and the clean-up of old attempts, in the caller's
-- transaction. Returns 'too_many' (nothing recorded), 'cooling_down' (recorded, send nothing)
-- or 'send'. The limits are the API's (app/api/v1/password_reset.py) and passed in.
create function public.api_password_reset_attempt(
  p_email_hash text, p_ip_hash text, p_ip_limit integer, p_ip_window interval,
  p_email_cooldown interval, p_keep interval)
returns text
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  ip_count integer;
  cooling boolean;
begin
  if p_email_hash !~ '^[0-9a-f]{64}$' or p_ip_hash !~ '^[0-9a-f]{64}$' then
    raise exception 'hashes are SHA-256 hex digests' using errcode = 'invalid_parameter_value';
  end if;
  perform pg_advisory_xact_lock(hashtext(p_ip_hash));
  select count(*) into ip_count
    from public.password_reset_attempts
   where ip_hash = p_ip_hash and created_at > now() - p_ip_window;
  if ip_count >= p_ip_limit then
    return 'too_many';
  end if;
  select exists (select 1 from public.password_reset_attempts
                  where email_hash = p_email_hash and created_at > now() - p_email_cooldown)
    into cooling;
  insert into public.password_reset_attempts (email_hash, ip_hash) values (p_email_hash, p_ip_hash);
  delete from public.password_reset_attempts where created_at < now() - p_keep;
  return case when cooling then 'cooling_down' else 'send' end;
end;
$$;

-- Whether a reset email may go to this address: null when there is no account, true when the
-- account belongs to a demo tenant (no email), false otherwise. Nothing else about the account.
create function public.api_account_is_demo(p_email text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from public.tenant_members m
                   join public.tenants t on t.id = m.tenant_id
                  where m.user_id = u.id and t.is_demo)
    from auth.users u
   where lower(u.email) = lower(p_email);
$$;

-- ---------------------------------------------------------------------------
-- "Send a test to myself": the signed-in owner's own sign-in email (null if they aren't an
-- owner of that tenant). The API calls it with the verified token's user and tenant only.
-- ---------------------------------------------------------------------------
create function public.api_owner_sign_in_email(p_user uuid, p_tenant uuid)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select u.email::text
    from auth.users u
    join public.tenant_members m on m.user_id = u.id
   where u.id = p_user and m.tenant_id = p_tenant and m.app_role = 'owner';
$$;

-- ---------------------------------------------------------------------------
-- Open card sessions (Slice B2): which Checkout Sessions to expire at Stripe before money is
-- recorded by hand, and which session sits behind a Pay now link. Session and account ids only.
-- ---------------------------------------------------------------------------
create function public.api_open_card_sessions(p_tenant uuid, p_invoice uuid)
returns table (session_id text, account_id text)
language sql
stable
security definer
set search_path = ''
as $$
  select p.external_ref, a.external_account_id
    from public.payments p
    join public.payment_accounts a on a.tenant_id = p.tenant_id and a.provider = 'stripe'
   where p.tenant_id = p_tenant and p.invoice_id = p_invoice and p.provider = 'stripe'
     and p.status = 'pending' and p.external_ref is not null;
$$;

create function public.api_card_session_for_link(p_tenant uuid, p_checkout_url text)
returns table (session_id text, account_id text)
language sql
stable
security definer
set search_path = ''
as $$
  select p.external_ref, a.external_account_id
    from public.payments p
    join public.payment_accounts a on a.tenant_id = p.tenant_id and a.provider = 'stripe'
   where p.tenant_id = p_tenant and p.provider = 'stripe' and p.status = 'pending'
     and p.checkout_url = p_checkout_url and p.external_ref is not null;
$$;

revoke all on function public.api_password_reset_attempt(text, text, integer, interval, interval, interval)
  from public, anon, authenticated, service_role;
revoke all on function public.api_account_is_demo(text) from public, anon, authenticated, service_role;
revoke all on function public.api_owner_sign_in_email(uuid, uuid) from public, anon, authenticated, service_role;
revoke all on function public.api_open_card_sessions(uuid, uuid) from public, anon, authenticated, service_role;
revoke all on function public.api_card_session_for_link(uuid, text) from public, anon, authenticated, service_role;
grant execute on function public.api_password_reset_attempt(text, text, integer, interval, interval, interval)
  to delea_api;
grant execute on function public.api_account_is_demo(text) to delea_api;
grant execute on function public.api_owner_sign_in_email(uuid, uuid) to delea_api;
grant execute on function public.api_open_card_sessions(uuid, uuid) to delea_api;
grant execute on function public.api_card_session_for_link(uuid, text) to delea_api;
