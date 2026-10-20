-- Business-rule triggers (Schema §5 "Invoice rules enforced in the database").
-- BEFORE triggers on invoices fire in name order: 10 status, 20 staff guard, 30 numbering,
-- 90 updated_at.

-- ---------------------------------------------------------------------------
-- order_status moves forward only. Forward skips (paid -> shipped) are allowed.
-- ---------------------------------------------------------------------------
create function public.invoices_enforce_forward_status()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.order_status < old.order_status then
    raise exception 'order_status cannot move backward (% -> %)', old.order_status, new.order_status
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger invoices_10_status_forward
  before update of order_status on public.invoices
  for each row execute function public.invoices_enforce_forward_status();

-- ---------------------------------------------------------------------------
-- Staff financial guard. Once an invoice is past 'quote', staff cannot change its money
-- columns or add/edit/delete its line items; only the owner or the service role can.
-- Callers without an app_role claim (service role, migrations) are not restricted.
-- ---------------------------------------------------------------------------
create function public.invoices_staff_financial_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (select auth.jwt() ->> 'app_role') = 'staff'
     and old.order_status <> 'quote'
     and (new.subtotal_cents is distinct from old.subtotal_cents
          or new.tax_rate_bps is distinct from old.tax_rate_bps
          or new.tax_cents is distinct from old.tax_cents
          or new.total_cents is distinct from old.total_cents) then
    raise exception 'staff cannot change amounts on an invoice past quote'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

create trigger invoices_20_staff_financial_guard
  before update on public.invoices
  for each row execute function public.invoices_staff_financial_guard();

create function public.invoice_line_items_staff_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  parent_ids uuid[];
begin
  if (select auth.jwt() ->> 'app_role') is distinct from 'staff' then
    return coalesce(new, old);
  end if;

  if tg_op = 'INSERT' then
    parent_ids := array[new.invoice_id];
  elsif tg_op = 'UPDATE' then
    parent_ids := array[old.invoice_id, new.invoice_id];
  else
    parent_ids := array[old.invoice_id];
  end if;

  if exists (
    select 1 from public.invoices i
    where i.id = any (parent_ids) and i.order_status <> 'quote'
  ) then
    raise exception 'staff cannot change line items on an invoice past quote'
      using errcode = 'insufficient_privilege';
  end if;

  return coalesce(new, old);
end;
$$;

create trigger invoice_line_items_20_staff_guard
  before insert or update or delete on public.invoice_line_items
  for each row execute function public.invoice_line_items_staff_guard();

-- ---------------------------------------------------------------------------
-- Numbering. quote_number on insert; invoice_number + paid_at when the status first moves
-- past 'quote' (including an insert directly as paid). Counters live in company_settings and
-- are incremented with a single UPDATE, which row-locks the tenant's settings row.
-- SECURITY DEFINER because staff cannot read company_settings.
-- ---------------------------------------------------------------------------
create function public.next_document_number(p_tenant_id uuid, p_kind text, p_at timestamptz)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  n integer;
  prefix text;
begin
  if p_kind = 'quote' then
    update public.company_settings
      set next_quote_number = next_quote_number + 1
      where tenant_id = p_tenant_id
      returning next_quote_number - 1 into n;
    prefix := 'QTE';
  elsif p_kind = 'invoice' then
    update public.company_settings
      set next_invoice_number = next_invoice_number + 1
      where tenant_id = p_tenant_id
      returning next_invoice_number - 1 into n;
    prefix := 'INV';
  else
    raise exception 'unknown document kind %', p_kind;
  end if;

  if n is null then
    raise exception 'company_settings row missing for tenant %', p_tenant_id
      using errcode = 'foreign_key_violation';
  end if;

  return prefix || '-' || to_char(p_at at time zone 'UTC', 'YYYY') || '-'
    || lpad(n::text, greatest(3, length(n::text)), '0');
end;
$$;

-- Definer so the caller never needs EXECUTE on next_document_number (which nobody but the
-- owner role holds, so counters cannot be advanced outside an invoice write).
create function public.invoices_assign_numbers()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    new.quote_number := public.next_document_number(new.tenant_id, 'quote', now());
    new.invoice_number := null;
    new.paid_at := null;
    if new.order_status > 'quote' then
      new.paid_at := now();
      new.invoice_number := public.next_document_number(new.tenant_id, 'invoice', new.paid_at);
    end if;
  else
    -- Numbers and paid_at are immutable once assigned.
    new.quote_number := old.quote_number;
    new.invoice_number := old.invoice_number;
    new.paid_at := old.paid_at;
    if old.order_status = 'quote' and new.order_status > 'quote' then
      new.paid_at := now();
      new.invoice_number := public.next_document_number(new.tenant_id, 'invoice', new.paid_at);
    end if;
  end if;
  return new;
end;
$$;

create trigger invoices_30_numbering
  before insert or update on public.invoices
  for each row execute function public.invoices_assign_numbers();

-- ---------------------------------------------------------------------------
-- Automation events. SECURITY DEFINER because automation_events has no policies.
-- ---------------------------------------------------------------------------
create function public.invoices_emit_events()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    insert into public.automation_events (tenant_id, event_type, payload)
    values (new.tenant_id, 'order_placed', jsonb_build_object(
      'invoice_id', new.id, 'order_status', new.order_status, 'source', new.source));
  elsif new.order_status is distinct from old.order_status then
    insert into public.automation_events (tenant_id, event_type, payload)
    values (new.tenant_id, 'order_status_changed', jsonb_build_object(
      'invoice_id', new.id, 'from_status', old.order_status, 'to_status', new.order_status));
  end if;
  return null;
end;
$$;

create trigger invoices_emit_order_placed
  after insert on public.invoices
  for each row execute function public.invoices_emit_events();

create trigger invoices_emit_status_changed
  after update of order_status on public.invoices
  for each row execute function public.invoices_emit_events();

-- Stock crossing below threshold ("low" means strictly below, as in "stock below 3 units").
create function public.products_emit_stock_low()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if old.stock_qty >= new.low_stock_threshold and new.stock_qty < new.low_stock_threshold then
    insert into public.automation_events (tenant_id, event_type, payload)
    values (new.tenant_id, 'stock_low', jsonb_build_object(
      'product_id', new.id, 'sku', new.sku,
      'stock_qty', new.stock_qty, 'threshold', new.low_stock_threshold));
  end if;
  return null;
end;
$$;

create trigger products_emit_stock_low
  after update of stock_qty on public.products
  for each row execute function public.products_emit_stock_low();

create function public.materials_emit_material_low()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if old.qty_on_hand >= new.reorder_threshold and new.qty_on_hand < new.reorder_threshold then
    insert into public.automation_events (tenant_id, event_type, payload)
    values (new.tenant_id, 'material_low', jsonb_build_object(
      'material_id', new.id, 'name', new.name,
      'qty_on_hand', new.qty_on_hand, 'threshold', new.reorder_threshold));
  end if;
  return null;
end;
$$;

create trigger materials_emit_material_low
  after update of qty_on_hand on public.materials
  for each row execute function public.materials_emit_material_low();
