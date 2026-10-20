-- Row-Level Security and grants (Schema §6).
-- Claims: (auth.jwt() ->> 'tenant_id')::uuid and auth.jwt() ->> 'app_role'.
-- Wrapped in (select ...) so Postgres evaluates them once per statement, not per row.

-- ---------------------------------------------------------------------------
-- RLS on every table in public. A table without policies is service-role only.
-- ---------------------------------------------------------------------------
alter table public.tenants enable row level security;
alter table public.tenant_members enable row level security;
alter table public.tenant_domains enable row level security;
alter table public.brand_config enable row level security;
alter table public.company_settings enable row level security;
alter table public.products enable row level security;
alter table public.materials enable row level security;
alter table public.product_materials enable row level security;
alter table public.customers enable row level security;
alter table public.invoices enable row level security;
alter table public.invoice_line_items enable row level security;
alter table public.expenses enable row level security;
alter table public.automation_rules enable row level security;
alter table public.reorder_list enable row level security;
alter table public.automation_events enable row level security;
alter table public.stripe_events enable row level security;

-- ---------------------------------------------------------------------------
-- Grants. anon gets nothing (storefront reads go through FastAPI, Schema §8 item 1).
-- authenticated gets DML only; RLS decides which rows. No TRUNCATE/REFERENCES/TRIGGER.
-- ---------------------------------------------------------------------------
revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all functions in schema public from anon, authenticated, public;

alter default privileges for role postgres in schema public revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on functions from anon, authenticated, public;

grant select, insert, update, delete on
  public.tenants,
  public.tenant_members,
  public.tenant_domains,
  public.brand_config,
  public.company_settings,
  public.products,
  public.materials,
  public.product_materials,
  public.customers,
  public.invoices,
  public.invoice_line_items,
  public.expenses,
  public.automation_rules,
  public.reorder_list
to authenticated;
-- automation_events and stripe_events: no grants, no policies (service role only).

-- The auth hook keeps its own grant (migration 08); restate it after the blanket revoke.
grant execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin;

-- ---------------------------------------------------------------------------
-- tenants, tenant_domains: owner read, staff read
-- ---------------------------------------------------------------------------
create policy tenants_select on public.tenants for select to authenticated
  using (id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') in ('owner', 'staff'));

create policy tenant_domains_select on public.tenant_domains for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') in ('owner', 'staff'));

-- ---------------------------------------------------------------------------
-- Owner full access on every tenant-scoped table it owns outright.
-- ---------------------------------------------------------------------------
create policy tenant_members_owner_all on public.tenant_members for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');

create policy brand_config_owner_all on public.brand_config for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');

create policy company_settings_owner_all on public.company_settings for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');

create policy products_owner_all on public.products for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');

create policy materials_owner_all on public.materials for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');

create policy product_materials_owner_all on public.product_materials for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');

create policy customers_owner_all on public.customers for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');

create policy invoices_owner_all on public.invoices for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');

create policy invoice_line_items_owner_all on public.invoice_line_items for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');

create policy expenses_owner_all on public.expenses for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');

create policy automation_rules_owner_all on public.automation_rules for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');

create policy reorder_list_owner_all on public.reorder_list for all to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');

-- ---------------------------------------------------------------------------
-- Staff
-- ---------------------------------------------------------------------------
-- read: brand_config, products, materials, product_materials, automation_rules, reorder_list
create policy brand_config_staff_select on public.brand_config for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');

create policy products_staff_select on public.products for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');

create policy materials_staff_select on public.materials for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');

create policy product_materials_staff_select on public.product_materials for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');

create policy automation_rules_staff_select on public.automation_rules for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');

create policy reorder_list_staff_select on public.reorder_list for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');

-- read, insert, update (no delete): customers, invoices, invoice_line_items.
-- The financial guard on invoices past 'quote' is a trigger (migration 07).
create policy customers_staff_select on public.customers for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');
create policy customers_staff_insert on public.customers for insert to authenticated
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'staff');
create policy customers_staff_update on public.customers for update to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'staff');

create policy invoices_staff_select on public.invoices for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');
create policy invoices_staff_insert on public.invoices for insert to authenticated
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'staff');
create policy invoices_staff_update on public.invoices for update to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'staff');

create policy invoice_line_items_staff_select on public.invoice_line_items for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff');
create policy invoice_line_items_staff_insert on public.invoice_line_items for insert to authenticated
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'staff');
create policy invoice_line_items_staff_update on public.invoice_line_items for update to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'staff')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'staff');

-- ---------------------------------------------------------------------------
-- CPA: read invoices and expenses only (PRD Q4, TRD §3.4). No line items.
-- ---------------------------------------------------------------------------
create policy invoices_cpa_select on public.invoices for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'cpa');

create policy expenses_cpa_select on public.expenses for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'cpa');
