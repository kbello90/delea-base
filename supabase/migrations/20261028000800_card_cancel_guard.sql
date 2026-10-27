-- Slice B2 follow-up: an invoice holding card money taken online can't be cancelled in DELEA.
-- The money is in the shop's Stripe account, so cancelling here would leave it there unseen.
-- Refund it in the Stripe Dashboard first: once the refund webhook has recorded it (net card
-- payments back to 0), cancel works as before. Every caller.
create function public.invoice_card_net_cents(p_invoice_id uuid)
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(sum(case when p.kind = 'payment' then p.amount_cents else -p.amount_cents end), 0)::bigint
    from public.payments p
   where p.invoice_id = p_invoice_id and p.provider = 'stripe' and p.status = 'succeeded';
$$;

create function public.invoices_card_cancel_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if old.cancelled_at is null and new.cancelled_at is not null
     and public.invoice_card_net_cents(old.id) > 0 then
    raise exception 'Refund this payment in Stripe first.';
  end if;
  return new;
end;
$$;

-- Before the deposit guard (25), so a card deposit gets this message.
create trigger invoices_24_card_cancel_guard
  before update on public.invoices
  for each row execute function public.invoices_card_cancel_guard();

revoke all on function public.invoice_card_net_cents(uuid) from public, anon, authenticated, service_role;
revoke all on function public.invoices_card_cancel_guard() from public, anon, authenticated, service_role;
