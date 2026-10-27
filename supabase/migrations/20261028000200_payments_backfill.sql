-- Slice B1: every invoice that left quote before the ledger existed gets its payment, and every
-- refunded one its refund, so the ledger and the invoices agree from the start.
--   One succeeded manual payment per paid invoice: its method (or 'other' when none was
--   recorded), its total, received when it was paid.
--   One succeeded manual refund of the whole total per refunded invoice, given when it was
--   refunded.
-- Invoices with a zero total need no row. The migration fails if any paid invoice's net
-- payments don't then equal its total (or zero, when refunded).

insert into public.payments
  (tenant_id, invoice_id, kind, provider, method, status, amount_cents, currency, received_at, note)
select i.tenant_id, i.id, 'payment', 'manual', coalesce(i.payment_method, 'other'), 'succeeded',
       i.total_cents, cs.currency, i.paid_at, 'Recorded before the payments ledger.'
  from public.invoices i
  join public.company_settings cs on cs.tenant_id = i.tenant_id
 where i.paid_at is not null
   and i.total_cents > 0
   and not exists (select 1 from public.payments p where p.invoice_id = i.id);

insert into public.payments
  (tenant_id, invoice_id, kind, provider, method, status, amount_cents, currency, received_at,
   refund_of, note)
select i.tenant_id, i.id, 'refund', 'manual', p.method, 'succeeded', i.total_cents, p.currency,
       i.refunded_at, p.id, 'Recorded before the payments ledger.'
  from public.invoices i
  join public.payments p on p.invoice_id = i.id and p.kind = 'payment'
 where i.refunded_at is not null
   and i.paid_at is not null
   and i.total_cents > 0
   and not exists (select 1 from public.payments r where r.invoice_id = i.id and r.kind = 'refund');

do $$
declare
  n integer;
begin
  select count(*) into n
    from public.invoices i
   where i.paid_at is not null
     and public.invoice_amount_paid(i.id)
         <> case when i.refunded_at is null then i.total_cents else 0 end;
  if n > 0 then
    raise exception 'payments backfill: % paid invoice(s) don''t match their payments', n;
  end if;
end
$$;
