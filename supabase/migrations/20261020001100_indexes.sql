-- Tenant-leading indexes for common lookups. Unique constraints already index
-- products (tenant_id, sku), materials (tenant_id, name), invoices (tenant_id, quote_number /
-- invoice_number), and (tenant_id, id) on every composite-FK parent.

create index tenant_members_tenant_id on public.tenant_members (tenant_id);
create index tenant_domains_tenant_id on public.tenant_domains (tenant_id);

create index products_storefront on public.products (tenant_id, is_published, sort_order);

create index product_materials_material on public.product_materials (tenant_id, material_id);
create index product_materials_product on public.product_materials (tenant_id, product_id);

create index customers_name on public.customers (tenant_id, last_name, first_name);
create index customers_email on public.customers (tenant_id, email);

create index invoices_status on public.invoices (tenant_id, order_status);
create index invoices_created on public.invoices (tenant_id, created_at desc);
create index invoices_paid on public.invoices (tenant_id, paid_at) where paid_at is not null;
create index invoices_customer on public.invoices (tenant_id, customer_id);

create index invoice_line_items_invoice on public.invoice_line_items (tenant_id, invoice_id);
create index invoice_line_items_product on public.invoice_line_items (tenant_id, product_id);

create index expenses_date on public.expenses (tenant_id, expense_date);

create index automation_rules_active on public.automation_rules (tenant_id, trigger) where is_active;

create index reorder_list_status on public.reorder_list (tenant_id, status);
create index reorder_list_material on public.reorder_list (tenant_id, material_id);
create index reorder_list_product on public.reorder_list (tenant_id, product_id);
create index reorder_list_rule on public.reorder_list (tenant_id, created_by_rule);

create index automation_events_pending on public.automation_events (created_at)
  where processed_at is null;
create index automation_events_tenant on public.automation_events (tenant_id, created_at);

create index stripe_events_tenant on public.stripe_events (tenant_id);
