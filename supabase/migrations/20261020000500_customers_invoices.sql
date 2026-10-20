-- Customers, invoices, and line items (Schema §5).

create table public.customers (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  customer_type public.customer_type not null default 'retail',
  first_name text not null,
  last_name text,
  email text,
  phone text,
  address_line1 text,
  address_line2 text,
  city text,
  state text,
  postal_code text,
  country char(2) not null default 'US',
  marketing_opt_in boolean not null default false,
  notes text,
  -- Wholesale-only fields (null / defaults for retail customers)
  company_name text,
  tax_exempt boolean not null default false,
  resale_certificate_number text,
  wholesale_discount_bps integer not null default 0
    check (wholesale_discount_bps between 0 and 10000),
  payment_terms public.payment_terms not null default 'due_on_receipt',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, id),
  constraint customers_retail_has_no_wholesale_fields check (
    customer_type = 'wholesale'
    or (company_name is null and not tax_exempt and wholesale_discount_bps = 0
        and payment_terms = 'due_on_receipt')
  ),
  constraint customers_wholesale_requires_company check (
    customer_type <> 'wholesale' or company_name is not null
  )
);

create table public.invoices (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  customer_id uuid,
  customer_name text not null,          -- snapshot so CPA exports work without access to customers
  quote_number text,                    -- QTE-YYYY-NNN, assigned on insert by trigger
  invoice_number text,                  -- INV-YYYY-NNN, assigned when status first passes 'quote'
  order_status public.order_status not null default 'quote',
  source text not null default 'admin' check (source in ('admin', 'storefront')),
  subtotal_cents bigint not null default 0,
  tax_rate_bps integer not null default 0,
  tax_cents bigint not null default 0,
  total_cents bigint not null default 0,
  stripe_payment_intent_id text,
  due_date date,
  public_token uuid not null default gen_random_uuid(),
  notes text,
  paid_at timestamptz,
  refunded_at timestamptz,
  cancelled_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, quote_number),
  unique (tenant_id, invoice_number),
  unique (public_token),
  unique (tenant_id, id),
  foreign key (tenant_id, customer_id)
    references public.customers (tenant_id, id) on delete set null (customer_id)
);

create table public.invoice_line_items (
  id uuid primary key default gen_random_uuid(),
  invoice_id uuid not null,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  product_id uuid,
  description text not null,            -- snapshot of the product name at sale time
  qty integer not null check (qty > 0),
  unit_price_cents bigint not null check (unit_price_cents >= 0),
  line_total_cents bigint not null,
  foreign key (tenant_id, invoice_id)
    references public.invoices (tenant_id, id) on delete cascade,
  foreign key (tenant_id, product_id)
    references public.products (tenant_id, id) on delete set null (product_id)
);

create trigger customers_90_updated_at
  before update on public.customers
  for each row execute function public.set_updated_at();

create trigger invoices_90_updated_at
  before update on public.invoices
  for each row execute function public.set_updated_at();
