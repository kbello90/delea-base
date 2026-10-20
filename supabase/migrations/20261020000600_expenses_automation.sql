-- Expenses, automation, and Stripe idempotency (Schema §5).

create table public.expenses (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  expense_date date not null,
  description text not null,
  category text,
  amount_cents bigint not null check (amount_cents >= 0),
  is_tax_deductible boolean not null default true,
  receipt_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.automation_rules (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  name text not null,
  trigger public.automation_trigger not null,
  trigger_config jsonb not null default '{}',
  action public.automation_action not null,
  action_config jsonb not null default '{}',
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, id)
);

create table public.reorder_list (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  material_id uuid,
  product_id uuid,
  created_by_rule uuid,
  status text not null default 'open' check (status in ('open', 'ordered', 'dismissed')),
  created_at timestamptz not null default now(),
  foreign key (tenant_id, material_id)
    references public.materials (tenant_id, id) on delete cascade,
  foreign key (tenant_id, product_id)
    references public.products (tenant_id, id) on delete cascade,
  foreign key (tenant_id, created_by_rule)
    references public.automation_rules (tenant_id, id) on delete set null (created_by_rule)
);

-- Queue drained by the FastAPI automation service. Triggers insert rows; the database never
-- sends email itself.
create table public.automation_events (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  event_type public.automation_trigger not null,
  payload jsonb not null default '{}',
  created_at timestamptz not null default now(),
  processed_at timestamptz
);

-- Idempotency for Stripe webhooks.
create table public.stripe_events (
  event_id text primary key,
  tenant_id uuid references public.tenants (id) on delete cascade,
  processed_at timestamptz not null default now()
);

create trigger expenses_90_updated_at
  before update on public.expenses
  for each row execute function public.set_updated_at();

create trigger automation_rules_90_updated_at
  before update on public.automation_rules
  for each row execute function public.set_updated_at();
