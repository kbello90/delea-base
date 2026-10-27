-- Slice B2: where a shop's Stripe account is kept (TRD §6.14, decisions K2 and K5).
--   1. payment_accounts: one connected account per shop and provider. The owner reads it; only
--      payments_writer (migration 20261028000700) writes it. Moved from
--      company_settings.stripe_account_id, which is dropped.
--   2. invoices.stripe_payment_intent_id is dropped: payments.external_id replaces it.
--   3. stripe_events records what each event was and what it did.

-- ---------------------------------------------------------------------------
-- 1. payment_accounts
-- ---------------------------------------------------------------------------
create table public.payment_accounts (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  provider text not null default 'stripe' check (provider in ('stripe')),
  external_account_id text not null check (external_account_id ~ '^acct_[A-Za-z0-9]{1,64}$'),
  charges_enabled boolean not null default false,
  details_submitted boolean not null default false,
  livemode boolean not null default false,
  connected_at timestamptz not null default now(),
  disconnected_at timestamptz,
  updated_at timestamptz not null default now(),
  constraint payment_accounts_one_per_shop unique (tenant_id, provider),
  constraint payment_accounts_account_unique unique (provider, external_account_id)
);

create trigger payment_accounts_90_updated_at
  before update on public.payment_accounts
  for each row execute function public.set_updated_at();

alter table public.payment_accounts enable row level security;
create policy payment_accounts_owner_select on public.payment_accounts for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner');
revoke all on public.payment_accounts from anon, authenticated;
grant select on public.payment_accounts to authenticated;

-- An account id already stored (none in practice: the column was read-only) moves across.
insert into public.payment_accounts (tenant_id, provider, external_account_id)
select tenant_id, 'stripe', stripe_account_id
  from public.company_settings
 where stripe_account_id ~ '^acct_[A-Za-z0-9]{1,64}$';

alter table public.company_settings drop column stripe_account_id;

-- ---------------------------------------------------------------------------
-- 2. The unused PaymentIntent column
-- ---------------------------------------------------------------------------
alter table public.invoices drop column stripe_payment_intent_id;

-- ---------------------------------------------------------------------------
-- 3. stripe_events
-- ---------------------------------------------------------------------------
-- tenant_id stays nullable: an event that matches no shop is still recorded (outcome no_match).
alter table public.stripe_events
  add column event_type text,
  add column account_id text,
  add column livemode boolean,
  add column outcome text not null default 'applied'
    check (outcome in ('applied', 'stock_short', 'unchanged', 'ignored', 'no_match', 'live_refused'));
