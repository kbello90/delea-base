-- Tenancy, auth membership, and settings (Schema §3).

create table public.tenants (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  tier public.tier not null default 'essential',
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

-- Source of truth for who belongs to which tenant, and with what role.
-- The custom access token hook reads this table and adds tenant_id + app_role to the JWT.
create table public.tenant_members (
  user_id uuid not null references auth.users (id) on delete cascade,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  app_role public.app_role not null,
  invited_by uuid references auth.users (id),
  created_at timestamptz not null default now(),
  primary key (user_id, tenant_id)
);

-- Maps request hostnames (including port in local development) to tenants.
create table public.tenant_domains (
  domain text primary key,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  kind text not null check (kind in ('storefront', 'admin'))
);

create table public.brand_config (
  tenant_id uuid primary key references public.tenants (id) on delete cascade,
  business_name text not null,
  tagline text,
  primary_color text not null default '#6B2737',
  accent_color text not null default '#B8935B',
  logo_url text,
  hero_title text,
  hero_subtitle text,
  updated_at timestamptz not null default now()
);

create table public.company_settings (
  tenant_id uuid primary key references public.tenants (id) on delete cascade,
  legal_name text,
  ein text,                                          -- sensitive: owner only (Schema §6)
  address text,
  currency char(3) not null default 'USD',
  default_language text not null default 'en' check (default_language in ('en', 'es', 'fr')),
  enabled_languages text[] not null default '{en}',
  default_tax_rate_bps integer not null default 0,   -- basis points (Schema §8 item 3)
  stripe_account_id text,                            -- Stripe Connect account (Schema §8 item 2)
  next_quote_number integer not null default 1,
  next_invoice_number integer not null default 1,
  updated_at timestamptz not null default now()
);

create trigger brand_config_90_updated_at
  before update on public.brand_config
  for each row execute function public.set_updated_at();

create trigger company_settings_90_updated_at
  before update on public.company_settings
  for each row execute function public.set_updated_at();
