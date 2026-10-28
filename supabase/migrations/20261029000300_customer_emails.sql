-- Slice D2: customer emails for website orders (TRD §6.16).
--   1. invoices.carrier and invoices.tracking_number: optional, entered when an order is marked
--      shipped; shown in the Shipped email and on the tracking page.
--   2. email_settings: each customer email on or off, with a short intro of the owner's own.
--   3. storefront.orders: the tracking page also gets the carrier and tracking number.
--   4. email_customer_context: what a customer email may say about an order, for email_worker
--      only. Website orders only. It never carries the street address, the phone number, the
--      city, the ZIP code or the shopper's note: a customer email shows the order reference,
--      the items, the amounts, how to pay and the tracking link, nothing more.

-- ---------------------------------------------------------------------------
-- 1. Carrier and tracking number
-- ---------------------------------------------------------------------------
alter table public.invoices
  add column carrier text,
  add column tracking_number text,
  add constraint invoices_carrier
    check (carrier is null or carrier ~ '^[^[:cntrl:]<>]{1,40}$'),
  add constraint invoices_tracking_number
    check (tracking_number is null or tracking_number ~ '^[A-Za-z0-9 -]{3,40}$'),
  -- Only a shipped (or delivered) order has them.
  add constraint invoices_tracking_when_shipped
    check ((carrier is null and tracking_number is null) or order_status in ('shipped', 'delivered'));

-- ---------------------------------------------------------------------------
-- 2. Customer emails on or off, and their intros
-- ---------------------------------------------------------------------------
alter table public.email_settings
  add column customer_order_received boolean not null default true,
  add column customer_payment_received boolean not null default true,
  add column customer_deposit_received boolean not null default true,
  add column customer_in_production boolean not null default true,
  add column customer_shipped boolean not null default true,
  add column intro_order_received text,
  add column intro_payment_received text,
  add column intro_deposit_received text,
  add column intro_in_production text,
  add column intro_shipped text,
  add constraint email_settings_intros check (
    coalesce(char_length(intro_order_received), 1) between 1 and 300
    and coalesce(char_length(intro_payment_received), 1) between 1 and 300
    and coalesce(char_length(intro_deposit_received), 1) between 1 and 300
    and coalesce(char_length(intro_in_production), 1) between 1 and 300
    and coalesce(char_length(intro_shipped), 1) between 1 and 300);

-- ---------------------------------------------------------------------------
-- 3. The tracking page
-- ---------------------------------------------------------------------------
create or replace view storefront.orders as
  select
    i.order_ref,
    i.created_at,
    i.order_status::text as order_status,
    i.paid_at is not null as paid,
    i.cancelled_at is not null as cancelled,
    i.refunded_at is not null as refunded,
    i.fulfillment,
    i.payment_choice,
    i.subtotal_cents,
    i.shipping_cents,
    i.tax_rate_bps,
    i.tax_cents,
    i.total_cents,
    c.first_name,
    c.ship_city,
    c.ship_state,
    case when i.paid_at is null and i.cancelled_at is null then s.manual_payment_instructions end
      as payment_instructions,
    case when i.fulfillment = 'pickup' then s.pickup_note end as pickup_note,
    coalesce(paid.cents, 0)::bigint as amount_paid_cents,
    case when i.paid_at is null and i.cancelled_at is null
         then greatest(i.total_cents - coalesce(paid.cents, 0), 0) else 0 end::bigint
      as balance_cents,
    i.production_started_at is not null as production_started,
    i.carrier,
    i.tracking_number
  from public.invoices i
  join public.order_contacts c on c.invoice_id = i.id and c.tenant_id = i.tenant_id
  join public.storefront_settings s on s.tenant_id = i.tenant_id
  join public.tenants t on t.id = i.tenant_id
  left join lateral (
    select sum(case when p.kind = 'payment' then p.amount_cents else -p.amount_cents end) as cents
      from public.payments p
     where p.invoice_id = i.id and p.tenant_id = i.tenant_id and p.status = 'succeeded'
  ) paid on true
  where i.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
    and i.public_token = nullif(current_setting('app.order_token', true), '')::uuid
    and i.source = 'storefront'
    and t.is_active
    and s.storefront_enabled;

revoke all on storefront.orders from public, anon, authenticated;
grant select on storefront.orders to storefront_reader;

-- ---------------------------------------------------------------------------
-- 4. What a customer email may say
-- ---------------------------------------------------------------------------
-- Null when the invoice isn't a website order of this shop. The shop's storefront address is
-- its first storefront domain (alphabetically); the worker adds the scheme.
create function public.email_customer_context(p_tenant_id uuid, p_invoice_id uuid)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select jsonb_build_object(
    'to', lower(c.email),
    'first_name', c.first_name,
    'order_ref', i.order_ref,
    'public_token', i.public_token,
    'order_status', i.order_status::text,
    'cancelled', i.cancelled_at is not null,
    'fulfillment', i.fulfillment,
    'payment_choice', i.payment_choice,
    'currency', cs.currency::text,
    'subtotal_cents', i.subtotal_cents,
    'shipping_cents', i.shipping_cents,
    'tax_cents', i.tax_cents,
    'total_cents', i.total_cents,
    'amount_paid_cents', public.invoice_amount_paid(i.id),
    'balance_cents', greatest(public.invoice_balance(i.id), 0),
    'lines', coalesce((select jsonb_agg(jsonb_build_object(
                                 'description', li.description, 'qty', li.qty,
                                 'line_total_cents', li.line_total_cents,
                                 'personalization', coalesce(li.personalization, '[]'::jsonb))
                               order by li.position, li.id)
                         from public.invoice_line_items li
                        where li.invoice_id = i.id and li.tenant_id = i.tenant_id), '[]'::jsonb),
    'payment_instructions', case when i.paid_at is null then s.manual_payment_instructions end,
    'pickup_note', case when i.fulfillment = 'pickup' then s.pickup_note end,
    'carrier', i.carrier,
    'tracking_number', i.tracking_number,
    'storefront_domain', (select d.domain from public.tenant_domains d
                           where d.tenant_id = i.tenant_id and d.kind = 'storefront'
                           order by d.domain limit 1),
    'contact_email', coalesce(s.contact_email,
                              case when es.reply_to_confirmed_at is not null then es.reply_to_email end),
    'switches', jsonb_build_object(
      'order_received', coalesce(es.customer_order_received, true),
      'payment_received', coalesce(es.customer_payment_received, true),
      'deposit_received', coalesce(es.customer_deposit_received, true),
      'in_production', coalesce(es.customer_in_production, true),
      'shipped', coalesce(es.customer_shipped, true)),
    'intros', jsonb_build_object(
      'order_received', es.intro_order_received,
      'payment_received', es.intro_payment_received,
      'deposit_received', es.intro_deposit_received,
      'in_production', es.intro_in_production,
      'shipped', es.intro_shipped))
    from public.invoices i
    join public.order_contacts c on c.invoice_id = i.id and c.tenant_id = i.tenant_id
    join public.company_settings cs on cs.tenant_id = i.tenant_id
    join public.storefront_settings s on s.tenant_id = i.tenant_id
    left join public.email_settings es on es.tenant_id = i.tenant_id
   where i.id = p_invoice_id and i.tenant_id = p_tenant_id and i.source = 'storefront';
$$;

revoke all on function public.email_customer_context(uuid, uuid) from public, anon, authenticated, service_role;
grant execute on function public.email_customer_context(uuid, uuid) to email_worker;
