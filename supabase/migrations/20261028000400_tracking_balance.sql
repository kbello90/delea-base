-- Slice B1: the order tracking page shows a deposit (TRD §6.13). Appended to storefront.orders:
-- what has been received, what is still due, and whether production has started. The sums are
-- written out here rather than calling invoice_amount_paid(): storefront_reader may read this
-- view and nothing else.
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
    i.production_started_at is not null as production_started
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
