-- Local/demo seed data. Loaded by `supabase db reset` after the migrations.
-- No auth users or tenant_members are seeded (tests create their own inside a transaction).
--
-- Tenant A "DELEA Demo"  11111111-1111-1111-1111-111111111111  (data from docs/delea-demo.html)
-- Tenant B "Aurora Gems" 22222222-2222-2222-2222-222222222222  (small data set for isolation tests)
--
-- Invoice numbers come from the numbering trigger. Tenant A's counters start at quote 41 and
-- invoice 89 so the demo's QTE-2026-042 and INV-2026-089 come out exactly.

-- ---------------------------------------------------------------------------
-- Tenants and settings
-- ---------------------------------------------------------------------------
insert into public.tenants (id, name, tier) values
  ('11111111-1111-1111-1111-111111111111', 'DELEA Demo', 'professional'),
  ('22222222-2222-2222-2222-222222222222', 'Aurora Gems', 'essential');

insert into public.tenant_domains (domain, tenant_id, kind) values
  ('localhost:5173',        '11111111-1111-1111-1111-111111111111', 'admin'),
  ('localhost:5174',        '11111111-1111-1111-1111-111111111111', 'storefront'),
  ('aurora.localhost:5173', '22222222-2222-2222-2222-222222222222', 'admin'),
  ('aurora.localhost:5174', '22222222-2222-2222-2222-222222222222', 'storefront');

insert into public.brand_config
  (tenant_id, business_name, tagline, primary_color, accent_color, hero_title, hero_subtitle)
values
  ('11111111-1111-1111-1111-111111111111', 'DELEA', 'Jewelry Operations', '#6B2737', '#B8935B',
   'DELEA Fine Jewelry',
   'Handcrafted pieces in 14K gold, rose gold, and 925 sterling silver — made to order.'),
  ('22222222-2222-2222-2222-222222222222', 'Aurora Gems', 'Fine gemstones', '#1F3A5F', '#C9A227',
   'Aurora Gems', 'Ethically sourced gemstones, set by hand.');

insert into public.company_settings
  (tenant_id, legal_name, ein, address, currency, default_language, enabled_languages,
   default_tax_rate_bps, next_quote_number, next_invoice_number)
values
  ('11111111-1111-1111-1111-111111111111', 'DELEA Jewelry LLC', '12-3456789',
   '5th Avenue, Jewelry District, NY', 'USD', 'en', '{en,es,fr}', 800, 41, 89),
  ('22222222-2222-2222-2222-222222222222', 'Aurora Gems Inc.', '98-7654321',
   '12 Harbor Street, Portland, ME', 'USD', 'en', '{en}', 550, 1, 1);

-- ---------------------------------------------------------------------------
-- Catalog
-- ---------------------------------------------------------------------------
insert into public.products
  (id, tenant_id, sku, name, metal, price_cents, stock_qty, icon, is_published, sort_order)
values
  ('a1000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
   'DL-NK-01', 'Custom Initial Necklace', 'gold_14k',   9800, 25, '◆', true, 1),  -- 24 after Sarah's sale
  ('a1000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111',
   'DL-FL-02', 'Birth Flower Pendant',    'rose_gold', 11000,  5, '✿', true, 2),
  ('a1000000-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111',
   'DL-RG-01', 'Engagement Ring',         'silver_925', 14500,  2, '◇', true, 3),
  ('a1000000-0000-0000-0000-000000000004', '11111111-1111-1111-1111-111111111111',
   'DL-CH-01', 'Celestial Charm',         'gold_14k',   9500, 12, '✦', true, 4),
  ('a1000000-0000-0000-0000-000000000005', '11111111-1111-1111-1111-111111111111',
   'DL-RG-02', 'Diamond Halo Ring',       'white_gold', 18500,  4, '◆', true, 5),
  ('a1000000-0000-0000-0000-000000000006', '11111111-1111-1111-1111-111111111111',
   'DL-NK-02', 'Monogram Necklace',       'gold_18k',  12500,  9, '◈', true, 6),
  ('b2000000-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222',
   'AG-SP-01', 'Sapphire Pendant',        'white_gold', 32000,  6, null, true, 1),
  ('b2000000-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222',
   'AG-EM-01', 'Emerald Studs',           'gold_18k',  27500,  3, null, false, 2);

insert into public.materials
  (id, tenant_id, name, unit, unit_cost_cents, qty_on_hand, reorder_threshold)
values
  ('a3000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
   '925 Silver blanks', 'pcs', 2250, 18, 10),
  ('a3000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111',
   '14K gold plating solution', 'vials', 6500, 3, 5),
  ('a3000000-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111',
   'Packaging boxes', 'pcs', 290, 212, 50),
  ('b4000000-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222',
   'Pendant bails', 'pcs', 150, 40, 10);

insert into public.product_materials (product_id, material_id, tenant_id, note) values
  ('a1000000-0000-0000-0000-000000000001', 'a3000000-0000-0000-0000-000000000002',
   '11111111-1111-1111-1111-111111111111', '~2g plating solution'),
  ('a1000000-0000-0000-0000-000000000001', 'a3000000-0000-0000-0000-000000000003',
   '11111111-1111-1111-1111-111111111111', '1 box'),
  ('a1000000-0000-0000-0000-000000000003', 'a3000000-0000-0000-0000-000000000001',
   '11111111-1111-1111-1111-111111111111', '1 blank'),
  ('b2000000-0000-0000-0000-000000000001', 'b4000000-0000-0000-0000-000000000001',
   '22222222-2222-2222-2222-222222222222', '1 bail');

-- ---------------------------------------------------------------------------
-- Customers
-- ---------------------------------------------------------------------------
insert into public.customers
  (id, tenant_id, customer_type, first_name, last_name, email, city, state,
   company_name, tax_exempt, resale_certificate_number, wholesale_discount_bps, payment_terms)
values
  ('a5000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
   'retail', 'Sarah', 'Jenkins', 'sarah.jenkins@example.com', 'New York', 'NY',
   null, false, null, 0, 'due_on_receipt'),
  ('a5000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111',
   'retail', 'Michael', 'Chang', 'michael.chang@example.com', 'Brooklyn', 'NY',
   null, false, null, 0, 'due_on_receipt'),
  ('a5000000-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111',
   'wholesale', 'Elena', 'Rossi', 'orders@bellaboutique.example.com', 'Hoboken', 'NJ',
   'Bella Boutique', true, 'NJ-RS-448812', 1500, 'net_30'),
  ('b6000000-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222',
   'retail', 'Priya', 'Natarajan', 'priya@example.com', 'Portland', 'ME',
   null, false, null, 0, 'due_on_receipt');

-- ---------------------------------------------------------------------------
-- Invoices (8% tax for tenant A, 5.5% for tenant B)
-- ---------------------------------------------------------------------------
-- Sarah's order: created as a quote (QTE-2026-041), then paid (INV-2026-089). Becoming
-- paid decrements stock (migration 20261020001200): the necklace goes 25 -> 24, the demo's figure.
insert into public.invoices
  (id, tenant_id, customer_id, customer_name, subtotal_cents, tax_rate_bps, tax_cents, total_cents)
values
  ('a7000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
   'a5000000-0000-0000-0000-000000000001', 'Sarah Jenkins', 9800, 800, 784, 10584);

insert into public.invoice_line_items
  (invoice_id, tenant_id, product_id, description, qty, unit_price_cents, line_total_cents)
values
  ('a7000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
   'a1000000-0000-0000-0000-000000000001', 'Custom Initial Necklace', 1, 9800, 9800);

update public.invoices set order_status = 'paid'
  where id = 'a7000000-0000-0000-0000-000000000001';

-- Michael's quote (QTE-2026-042). "Silver Ring" is not a catalog product in the demo.
insert into public.invoices
  (id, tenant_id, customer_id, customer_name, subtotal_cents, tax_rate_bps, tax_cents, total_cents)
values
  ('a7000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111',
   'a5000000-0000-0000-0000-000000000002', 'Michael Chang', 17500, 800, 1400, 18900);

insert into public.invoice_line_items
  (invoice_id, tenant_id, product_id, description, qty, unit_price_cents, line_total_cents)
values
  ('a7000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111',
   null, 'Silver Ring', 1, 17500, 17500);

-- Tenant B: one paid invoice.
insert into public.invoices
  (id, tenant_id, customer_id, customer_name, order_status,
   subtotal_cents, tax_rate_bps, tax_cents, total_cents)
values
  ('b8000000-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222',
   'b6000000-0000-0000-0000-000000000001', 'Priya Natarajan', 'paid', 32000, 550, 1760, 33760);

insert into public.invoice_line_items
  (invoice_id, tenant_id, product_id, description, qty, unit_price_cents, line_total_cents)
values
  ('b8000000-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222',
   'b2000000-0000-0000-0000-000000000001', 'Sapphire Pendant', 1, 32000, 32000);

-- ---------------------------------------------------------------------------
-- Expenses
-- ---------------------------------------------------------------------------
insert into public.expenses
  (tenant_id, expense_date, description, category, amount_cents, is_tax_deductible)
values
  ('11111111-1111-1111-1111-111111111111', '2026-10-02', '925 silver blanks (bulk order)',
   'materials', 45000, true),
  ('11111111-1111-1111-1111-111111111111', '2026-10-05', 'Packaging boxes', 'materials', 14500, true),
  ('11111111-1111-1111-1111-111111111111', '2026-10-10', 'Booth fee, holiday market',
   'marketing', 25000, true),
  ('11111111-1111-1111-1111-111111111111', '2026-10-12', 'Team lunch', 'meals', 6800, false),
  ('22222222-2222-2222-2222-222222222222', '2026-10-08', 'Gem certification', 'professional_services', 18000, true);

-- ---------------------------------------------------------------------------
-- Automation (the demo's two rules) and reorder list
-- ---------------------------------------------------------------------------
insert into public.automation_rules
  (id, tenant_id, name, trigger, trigger_config, action, action_config)
values
  ('a9000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
   'Order placed: confirmation email', 'order_placed', '{}',
   'send_email', '{"template": "order_confirmation"}'),
  ('a9000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111',
   'Stock below 3 units: alert owner + reorder list', 'stock_low', '{"threshold": 3}',
   'add_to_reorder_list', '{"notify_owner": true}'),
  ('b9000000-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222',
   'Order placed: confirmation email', 'order_placed', '{}',
   'send_email', '{"template": "order_confirmation"}');

insert into public.reorder_list (tenant_id, material_id, product_id, created_by_rule, status) values
  ('11111111-1111-1111-1111-111111111111', 'a3000000-0000-0000-0000-000000000002', null,
   'a9000000-0000-0000-0000-000000000002', 'open'),
  ('22222222-2222-2222-2222-222222222222', null, 'b2000000-0000-0000-0000-000000000002',
   null, 'open');

-- ---------------------------------------------------------------------------
-- Stripe idempotency rows (service-role table; one per tenant for isolation tests)
-- ---------------------------------------------------------------------------
insert into public.stripe_events (event_id, tenant_id) values
  ('evt_seed_demo_0001', '11111111-1111-1111-1111-111111111111'),
  ('evt_seed_aurora_0001', '22222222-2222-2222-2222-222222222222');
