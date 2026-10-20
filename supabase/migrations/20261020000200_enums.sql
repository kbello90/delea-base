-- Enums (Schema §2).

create type public.tier as enum ('essential', 'professional', 'enterprise');
create type public.app_role as enum ('owner', 'staff', 'cpa');
-- Locked (PRD Q1). Declaration order is the forward order enforced on invoices.
create type public.order_status as enum ('quote', 'paid', 'in_production', 'shipped', 'delivered');
create type public.customer_type as enum ('retail', 'wholesale');
create type public.payment_terms as enum ('due_on_receipt', 'net_15', 'net_30', 'net_60');
create type public.metal_type as enum (
  'gold_14k', 'gold_18k', 'rose_gold', 'white_gold', 'silver_925', 'platinum', 'other'
);
create type public.automation_trigger as enum (
  'order_placed', 'order_status_changed', 'stock_low', 'material_low'
);
create type public.automation_action as enum ('send_email', 'notify_owner', 'add_to_reorder_list');
