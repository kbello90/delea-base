-- A shop's primary website address (hosted-demo plan step 2; TRD §6.11, Schema §2).
--
-- Customer emails, the Website page and "Open my website" used the shop's first storefront
-- domain alphabetically, which picks the wrong one once a shop has several (for example
-- deleajewelry.com and www.deleajewelry.com). Now exactly one storefront domain per shop is
-- primary, and every reader uses it. Every domain still serves the store (host lookups are
-- unchanged); redirecting the others to the primary is done in Vercel, not here.
--
-- Rules (enforced here for every caller, the administrative login included):
--   * only a storefront domain can be primary, and a shop has at most one primary;
--   * a shop's first storefront domain becomes primary by itself;
--   * while a shop has any storefront domain, one of them is primary: removing the primary,
--     turning it into an admin domain or unsetting it is refused while other storefront
--     domains exist (move the primary first). Removing a shop's last storefront domain, or
--     the whole shop, is allowed.
-- Moving the primary is one statement, so the one-primary rule is checked at its end:
--   update public.tenant_domains set is_primary = (domain = '<new>')
--    where tenant_id = '<shop>' and kind = 'storefront';
-- (backend/scripts/add_tenant_domain.py --primary does this.)

alter table public.tenant_domains
  add column is_primary boolean not null default false;

-- Backfill: every shop with a storefront domain gets the one its emails already linked to
-- (the first alphabetically), so nothing a shopper or owner sees changes.
update public.tenant_domains d
   set is_primary = true
 where d.kind = 'storefront'
   and d.domain = (select min(o.domain) from public.tenant_domains o
                    where o.tenant_id = d.tenant_id and o.kind = 'storefront');

alter table public.tenant_domains
  add constraint tenant_domains_primary_is_storefront
    check (not is_primary or kind = 'storefront'),
  -- Checked at the end of each statement, so a one-statement move never trips it.
  add constraint tenant_domains_one_primary
    exclude using btree (tenant_id with =) where (is_primary)
    deferrable initially immediate;

comment on column public.tenant_domains.is_primary is
  'The shop''s website address: customer emails and the Website page link to it. Exactly one '
  'storefront domain per shop while it has any (triggers tenant_domains_primary_*).';

-- ---------------------------------------------------------------------------
-- The first storefront domain becomes primary; a primary can't be turned into an admin
-- domain while the shop has other storefront domains.
-- ---------------------------------------------------------------------------
create function public.tenant_domains_primary_before()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.kind = 'storefront' and not new.is_primary and not exists (
         select 1 from public.tenant_domains d
          where d.tenant_id = new.tenant_id and d.is_primary) then
      new.is_primary := true;
    end if;
    return new;
  end if;

  -- UPDATE: a primary that stops being a storefront domain.
  if old.is_primary and new.is_primary and new.kind <> 'storefront' then
    if exists (select 1 from public.tenant_domains d
                where d.tenant_id = old.tenant_id and d.kind = 'storefront'
                  and d.domain <> old.domain) then
      raise exception '% is this shop''s primary website address. Make another storefront domain primary first (add_tenant_domain.py --primary).', old.domain
        using errcode = 'check_violation';
    end if;
    new.is_primary := false;  -- its last storefront domain: the shop has no website address
  end if;
  return new;
end;
$$;

create trigger tenant_domains_primary_before
  before insert or update on public.tenant_domains
  for each row execute function public.tenant_domains_primary_before();

-- ---------------------------------------------------------------------------
-- After each statement's rows: a shop that still has storefront domains has a primary.
-- (A row trigger fired after the statement sees its final state, so a move in one
-- statement passes, and so does deleting a whole shop: its domains are all gone.)
-- ---------------------------------------------------------------------------
create function public.tenant_domains_primary_after()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if old.is_primary
     and exists (select 1 from public.tenant_domains d
                  where d.tenant_id = old.tenant_id and d.kind = 'storefront')
     and not exists (select 1 from public.tenant_domains d
                      where d.tenant_id = old.tenant_id and d.is_primary) then
    raise exception '% is this shop''s primary website address. Make another storefront domain primary first (add_tenant_domain.py --primary).', old.domain
      using errcode = 'check_violation';
  end if;
  return null;
end;
$$;

create trigger tenant_domains_primary_after
  after update or delete on public.tenant_domains
  for each row execute function public.tenant_domains_primary_after();

revoke all on function public.tenant_domains_primary_before() from public, anon, authenticated, service_role;
revoke all on function public.tenant_domains_primary_after() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Customer emails link to the primary domain (was: the first alphabetically).
-- Unchanged otherwise; create or replace keeps the grant to email_worker only.
-- ---------------------------------------------------------------------------
create or replace function public.email_customer_context(p_tenant_id uuid, p_invoice_id uuid)
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
                           where d.tenant_id = i.tenant_id and d.is_primary),
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
