-- Catalog and inventory (Schema §4). Finished SKUs and raw materials are independent (PRD Q3).
-- Child rows reference parents through (tenant_id, id) so a reference can never cross tenants.

create table public.products (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  sku text not null,
  name text not null,
  description text,
  metal public.metal_type not null,
  price_cents bigint not null check (price_cents >= 0),
  sale_price_cents bigint check (sale_price_cents >= 0),
  sale_ends_at timestamptz,
  stock_qty integer not null default 0,
  low_stock_threshold integer not null default 3,
  image_urls text[] not null default '{}',
  icon text,
  is_published boolean not null default false,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, sku),
  unique (tenant_id, id)
);

create table public.materials (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  name text not null,
  unit text not null,
  unit_cost_cents bigint not null check (unit_cost_cents >= 0),
  qty_on_hand numeric(12, 2) not null default 0,
  reorder_threshold numeric(12, 2) not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, name),
  unique (tenant_id, id)
);

-- Reference only. Nothing deducts materials when product stock changes.
create table public.product_materials (
  product_id uuid not null,
  material_id uuid not null,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  note text,
  primary key (product_id, material_id),
  foreign key (tenant_id, product_id) references public.products (tenant_id, id) on delete cascade,
  foreign key (tenant_id, material_id) references public.materials (tenant_id, id) on delete cascade
);

create trigger products_90_updated_at
  before update on public.products
  for each row execute function public.set_updated_at();

create trigger materials_90_updated_at
  before update on public.materials
  for each row execute function public.set_updated_at();
