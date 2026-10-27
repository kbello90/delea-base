-- Slice B2: card at checkout (TRD §6.14).
--   1. payments: a Stripe payment row keeps its Checkout Session's link and expiry, so "Pay now"
--      can reuse an open session. The update guard lets the service attach a session to a
--      pending row once, and lets a Stripe refund fail after it succeeded; nothing else changes.
--   2. invoices.payment_choice also accepts 'card'.
--   3. storefront.store says whether card is available; storefront_place_order accepts card
--      only then.
--   4. Three SECURITY DEFINER functions for storefront_writer: start a card payment (a pending
--      row for the balance), attach its session, mark a failed attempt. Each re-checks the
--      shop and the order itself.

-- ---------------------------------------------------------------------------
-- 1. payments
-- ---------------------------------------------------------------------------
alter table public.payments
  add column checkout_url text,
  add column checkout_expires_at timestamptz,
  add constraint payments_checkout_fields_stripe_only
    check (provider = 'stripe' or (checkout_url is null and checkout_expires_at is null)),
  add constraint payments_checkout_url_https
    check (checkout_url is null or checkout_url ~ '^https://');

-- Same as migration 20261028000100, apart from the session link and the late refund failure.
create or replace function public.payments_update_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role text := (select auth.jwt() ->> 'app_role');
  v_status public.order_status;
begin
  if new.tenant_id is distinct from old.tenant_id
     or new.invoice_id is distinct from old.invoice_id
     or new.kind is distinct from old.kind
     or new.provider is distinct from old.provider
     or new.amount_cents is distinct from old.amount_cents
     or new.currency is distinct from old.currency
     or (old.external_ref is not null and new.external_ref is distinct from old.external_ref)
     or new.refund_of is distinct from old.refund_of
     or new.recorded_by is distinct from old.recorded_by
     or new.created_at is distinct from old.created_at
     or (old.external_id is not null and new.external_id is distinct from old.external_id) then
    raise exception 'a recorded payment can''t be changed; void it and record it again'
      using errcode = 'insufficient_privilege';
  end if;

  if new.status is distinct from old.status then
    -- A Stripe refund may still fail after it succeeded (the bank returns it).
    if old.status in ('voided', 'failed', 'expired')
       or (old.status = 'succeeded' and new.status <> 'voided'
           and not (old.provider = 'stripe' and old.kind = 'refund' and new.status = 'failed'))
       or new.status = 'pending' then
      raise exception 'a payment can''t move from % to %', old.status, new.status
        using errcode = 'check_violation';
    end if;
  end if;

  if v_role is not null then
    if v_role <> 'owner' then
      raise exception 'only the owner can change a recorded payment'
        using errcode = 'insufficient_privilege';
    end if;
    if old.provider <> 'manual' then
      raise exception 'Card payments are managed in Stripe.' using errcode = 'insufficient_privilege';
    end if;
    if new.received_at is distinct from old.received_at
       or new.note is distinct from old.note
       or new.external_id is distinct from old.external_id
       or new.checkout_url is distinct from old.checkout_url
       or new.checkout_expires_at is distinct from old.checkout_expires_at
       or (new.voided_at is distinct from old.voided_at and new.status = old.status)
       or (new.voided_by is distinct from old.voided_by and new.status = old.status) then
      raise exception 'only the payment method can be corrected, or the payment voided'
        using errcode = 'insufficient_privilege';
    end if;
    if new.status = 'voided' and old.status <> 'voided' then
      select i.order_status into v_status
        from public.invoices i where i.id = old.invoice_id and i.tenant_id = old.tenant_id
         for update;
      if v_status <> 'quote' then
        raise exception 'This invoice is paid, so its payments can''t be voided. Refund it instead.';
      end if;
      new.voided_at := now();
      new.voided_by := (select auth.uid());
    end if;
  end if;

  -- Voiding a payment must not leave more refunded than received.
  if new.status = 'voided' and old.status = 'succeeded' and old.kind = 'payment'
     and public.invoice_amount_paid(old.invoice_id) - old.amount_cents < 0 then
    raise exception 'Void the refund first: this payment has been refunded.';
  end if;
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- 2. payment_choice
-- ---------------------------------------------------------------------------
alter table public.invoices drop constraint invoices_payment_choice;
alter table public.invoices add constraint invoices_payment_choice
  check (payment_choice in ('bank_transfer', 'pay_on_pickup', 'card'));

-- ---------------------------------------------------------------------------
-- 3. Card availability, the store, placing an order
-- ---------------------------------------------------------------------------
create function public.storefront_card_enabled(p_tenant_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from public.payment_accounts a
                  where a.tenant_id = p_tenant_id and a.provider = 'stripe' and a.charges_enabled
                    and a.disconnected_at is null and not a.livemode);
$$;

-- Same as migration 20261027000400, plus card_enabled.
create or replace view storefront.store as
  select
    b.business_name,
    b.tagline,
    b.primary_color,
    b.accent_color,
    b.logo_path,
    b.hero_eyebrow,
    b.hero_title,
    b.hero_subtitle,
    b.collection_title,
    b.font_preset,
    b.corner_style,
    b.mark_style,
    b.hero_cta_label,
    b.hero_cta_url,
    c.currency::text as currency,
    s.contact_email,
    s.return_policy,
    s.privacy_policy,
    s.terms,
    s.custom_orders_info,
    exists (select 1 from public.products p
             where p.tenant_id = b.tenant_id and p.is_published and p.made_to_order)
      as has_made_to_order,
    s.ship_enabled,
    s.ship_flat_cents,
    s.free_ship_over_cents,
    s.tax_shipping,
    s.pickup_enabled,
    s.pickup_note,
    s.bank_transfer_enabled,
    s.pay_on_pickup_enabled,
    c.default_tax_rate_bps,
    -- Card at checkout (Slice B2): the shop's connected account can take charges. The API
    -- also needs Stripe configured on its side.
    exists (select 1 from public.payment_accounts a
             where a.tenant_id = b.tenant_id and a.provider = 'stripe' and a.charges_enabled
               and a.disconnected_at is null and not a.livemode) as card_enabled
  from public.brand_config b
  join public.company_settings c on c.tenant_id = b.tenant_id
  join public.storefront_settings s on s.tenant_id = b.tenant_id
  join public.tenants t on t.id = b.tenant_id
  where b.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
    and t.is_active
    and s.storefront_enabled;

revoke all on storefront.store from public, anon, authenticated;
grant select on storefront.store to storefront_reader;

-- Same as migration 20261027000400, plus card when the shop can take it.
create or replace function public.storefront_place_order(
  p_tenant_id uuid,
  p_checkout_key uuid,
  p_fingerprint text,
  p_order jsonb,
  p_ip_hash text,
  p_email_hash text
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  shop record;
  previous jsonb;
  priced jsonb;
  contact jsonb := p_order -> 'contact';
  ship_to jsonb := p_order -> 'ship_to';
  fulfillment text := p_order ->> 'fulfillment';
  payment_choice text := p_order ->> 'payment_choice';
  tenant_hash text := encode(extensions.digest(p_tenant_id::text, 'sha256'), 'hex');
  customer_id uuid;
  alphabet constant text := '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
  random_bytes bytea;
  ref text;
  new_invoice record;
  i integer;
begin
  if p_fingerprint !~ '^[0-9a-f]{64}$' or p_ip_hash !~ '^[0-9a-f]{64}$'
     or p_email_hash !~ '^[0-9a-f]{64}$' or p_checkout_key is null then
    raise exception 'invalid checkout key or hashes' using errcode = 'invalid_parameter_value';
  end if;

  -- The tenant is re-checked here, whatever the caller resolved.
  select s.bank_transfer_enabled, s.pay_on_pickup_enabled
    into shop
    from public.storefront_settings s
    join public.tenants t on t.id = s.tenant_id
   where s.tenant_id = p_tenant_id and t.is_active and s.storefront_enabled;
  if not found then
    return jsonb_build_object('status', 'closed');
  end if;

  -- One order per checkout key: a double submit waits here, then finds the first order.
  perform pg_advisory_xact_lock(hashtextextended('checkout:' || p_tenant_id || ':' || p_checkout_key, 0));
  previous := public.storefront_find_order(p_tenant_id, p_checkout_key, p_fingerprint);
  if previous ->> 'status' <> 'none' then
    return previous;
  end if;

  -- Created orders per address, per email and per shop, in the last hour.
  perform pg_advisory_xact_lock(hashtextextended('order_tenant:' || tenant_hash, 0));
  if (select count(*) from public.public_rate_hits
       where scope = 'order_ip' and key_hash = p_ip_hash and created_at > now() - interval '1 hour') >= 5 then
    return jsonb_build_object('status', 'rate_limited', 'scope', 'ip');
  end if;
  if (select count(*) from public.public_rate_hits
       where scope = 'order_email' and key_hash = p_email_hash and created_at > now() - interval '1 hour') >= 3 then
    return jsonb_build_object('status', 'rate_limited', 'scope', 'email');
  end if;
  if (select count(*) from public.public_rate_hits
       where scope = 'order_tenant' and key_hash = tenant_hash and created_at > now() - interval '1 hour') >= 30 then
    return jsonb_build_object('status', 'rate_limited', 'scope', 'shop');
  end if;

  if fulfillment is null then
    return jsonb_build_object('status', 'invalid', 'reason', 'fulfillment');
  end if;
  priced := public.storefront_price_cart(p_tenant_id, p_order -> 'items', fulfillment);
  if not (priced ->> 'ok')::boolean then
    return case priced ->> 'error'
      when 'closed' then jsonb_build_object('status', 'closed')
      else jsonb_build_object('status', 'invalid', 'reason', priced ->> 'error') end;
  end if;
  if (priced ->> 'has_problems')::boolean then
    return jsonb_build_object('status', 'unavailable', 'quote', priced);
  end if;
  if jsonb_typeof(p_order -> 'expected_total_cents') is distinct from 'number'
     or (p_order ->> 'expected_total_cents')::numeric <> (priced ->> 'total_cents')::numeric then
    return jsonb_build_object('status', 'price_changed', 'quote', priced);
  end if;

  if not ((payment_choice = 'bank_transfer' and shop.bank_transfer_enabled)
          or (payment_choice = 'pay_on_pickup' and shop.pay_on_pickup_enabled and fulfillment = 'pickup')
          or (payment_choice = 'card' and public.storefront_card_enabled(p_tenant_id))) then
    return jsonb_build_object('status', 'invalid', 'reason', 'payment_choice');
  end if;
  -- coalesce: a missing address or contact must count as "absent", never as NULL.
  if (fulfillment = 'ship') <> coalesce(jsonb_typeof(ship_to) = 'object', false)
     or not coalesce(jsonb_typeof(contact) = 'object', false) then
    return jsonb_build_object('status', 'invalid', 'reason', 'address');
  end if;

  -- The customer: an existing retail customer with this email (never updated, and never a
  -- wholesale one: the storefront always sells at retail), else a new retail customer.
  select c.id into customer_id
    from public.customers c
   where c.tenant_id = p_tenant_id and c.customer_type = 'retail'
     and lower(c.email) = lower(contact ->> 'email')
   order by c.created_at, c.id
   limit 1;
  if customer_id is null then
    insert into public.customers (tenant_id, customer_type, first_name, last_name, email, phone,
                                  marketing_opt_in)
    values (p_tenant_id, 'retail', contact ->> 'first_name', contact ->> 'last_name',
            contact ->> 'email', contact ->> 'phone', false)
    returning id into customer_id;
  end if;

  loop
    random_bytes := extensions.gen_random_bytes(8);
    ref := 'W-';
    for i in 0..7 loop
      ref := ref || substr(alphabet, get_byte(random_bytes, i) % 32 + 1, 1);
    end loop;
    exit when not exists (select 1 from public.invoices
                           where tenant_id = p_tenant_id and order_ref = ref);
  end loop;

  insert into public.invoices (tenant_id, customer_id, customer_name, order_status, source,
                               subtotal_cents, tax_rate_bps, tax_cents, shipping_cents,
                               total_cents, notes, fulfillment, payment_choice, checkout_key,
                               checkout_fingerprint, order_ref)
  values (p_tenant_id, customer_id,
          (contact ->> 'first_name') || ' ' || (contact ->> 'last_name'), 'quote', 'storefront',
          (priced ->> 'subtotal_cents')::bigint, (priced ->> 'tax_rate_bps')::integer,
          (priced ->> 'tax_cents')::bigint, (priced ->> 'shipping_cents')::bigint,
          (priced ->> 'total_cents')::bigint, null, fulfillment, payment_choice, p_checkout_key,
          p_fingerprint, ref)
  returning id, public_token into new_invoice;

  insert into public.invoice_line_items (invoice_id, tenant_id, product_id, description, qty,
                                         unit_price_cents, line_total_cents, position,
                                         personalization)
  select new_invoice.id, p_tenant_id, (l ->> 'product_id')::uuid, l ->> 'name',
         (l ->> 'qty')::integer, (l ->> 'unit_price_cents')::bigint,
         (l ->> 'line_total_cents')::bigint, (l ->> 'position')::integer,
         nullif(l -> 'personalization', 'null'::jsonb)
    from jsonb_array_elements(priced -> 'lines') l;

  insert into public.order_contacts (invoice_id, tenant_id, first_name, last_name, email, phone,
                                     ship_line1, ship_line2, ship_city, ship_state,
                                     ship_postal_code, ship_country, note)
  values (new_invoice.id, p_tenant_id, contact ->> 'first_name', contact ->> 'last_name',
          contact ->> 'email', contact ->> 'phone',
          ship_to ->> 'line1', ship_to ->> 'line2', ship_to ->> 'city', ship_to ->> 'state',
          ship_to ->> 'postal_code', case when ship_to is not null then 'US' end,
          p_order ->> 'note');

  insert into public.public_rate_hits (scope, key_hash) values
    ('order_ip', p_ip_hash), ('order_email', p_email_hash), ('order_tenant', tenant_hash);

  return jsonb_build_object('status', 'created', 'public_token', new_invoice.public_token,
                            'order_ref', ref);
end;
$$;

-- ---------------------------------------------------------------------------
-- 4. Card attempts
-- ---------------------------------------------------------------------------
-- Stripe's smallest card charge in the shop's currency (two-decimal currencies: 50 cents, or
-- the local equivalent Stripe documents).
create function public.stripe_minimum_cents(p_currency text)
returns bigint
language sql
immutable
set search_path = ''
as $$
  select case upper(p_currency)
           when 'GBP' then 30 when 'MXN' then 1000 when 'CHF' then 50
           else 50 end::bigint;
$$;

-- Starts a card payment for an order's balance. Returns one of:
--   {"status": "reuse", "checkout_url": ...}       an open session for this exact balance
--   {"status": "start", "payment_id", "amount_cents", "currency", "account_id", "order_ref",
--     "open_sessions": [...]}                      a new pending row; open older sessions to expire
--   {"status": "not_found" | "closed" | "card_off" | "paid" | "too_small"}
create function public.storefront_card_payment_start(p_tenant_id uuid, p_token uuid)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  inv record;
  acct text;
  v_currency text;
  v_balance bigint;
  existing record;
  new_id uuid;
begin
  if not exists (select 1 from public.storefront_settings s join public.tenants t on t.id = s.tenant_id
                  where s.tenant_id = p_tenant_id and t.is_active and s.storefront_enabled) then
    return jsonb_build_object('status', 'closed');
  end if;
  select i.id, i.order_status, i.cancelled_at, i.refunded_at, i.total_cents, i.order_ref
    into inv
    from public.invoices i
   where i.tenant_id = p_tenant_id and i.public_token = p_token and i.source = 'storefront'
     for update;
  if not found then
    return jsonb_build_object('status', 'not_found');
  end if;
  if inv.cancelled_at is not null or inv.refunded_at is not null then
    return jsonb_build_object('status', 'closed');
  end if;
  select a.external_account_id into acct
    from public.payment_accounts a
   where a.tenant_id = p_tenant_id and a.provider = 'stripe' and a.charges_enabled
     and a.disconnected_at is null and not a.livemode;
  if acct is null then
    return jsonb_build_object('status', 'card_off');
  end if;
  v_balance := inv.total_cents - public.invoice_amount_paid(inv.id);
  if inv.order_status <> 'quote' or v_balance <= 0 then
    return jsonb_build_object('status', 'paid');
  end if;
  select cs.currency into v_currency from public.company_settings cs where cs.tenant_id = p_tenant_id;
  if v_balance < public.stripe_minimum_cents(v_currency) then
    return jsonb_build_object('status', 'too_small');
  end if;

  select p.checkout_url into existing
    from public.payments p
   where p.invoice_id = inv.id and p.provider = 'stripe' and p.status = 'pending'
     and p.amount_cents = v_balance and p.checkout_url is not null
     and p.checkout_expires_at > now() + interval '10 minutes'
   order by p.created_at desc
   limit 1;
  if found then
    return jsonb_build_object('status', 'reuse', 'checkout_url', existing.checkout_url);
  end if;

  insert into public.payments (tenant_id, invoice_id, kind, provider, method, status,
                               amount_cents, currency)
  values (p_tenant_id, inv.id, 'payment', 'stripe', 'card', 'pending', v_balance, v_currency)
  returning id into new_id;

  return jsonb_build_object(
    'status', 'start', 'payment_id', new_id, 'invoice_id', inv.id, 'amount_cents', v_balance,
    'currency', v_currency, 'account_id', acct, 'order_ref', inv.order_ref,
    'open_sessions', coalesce((select jsonb_agg(p.external_ref)
                                 from public.payments p
                                where p.invoice_id = inv.id and p.provider = 'stripe'
                                  and p.status = 'pending' and p.external_ref is not null), '[]'::jsonb));
end;
$$;

-- The session Stripe created for a pending row: its id, link and expiry, set once.
create function public.storefront_card_payment_attach(
  p_tenant_id uuid, p_payment_id uuid, p_session_id text, p_url text, p_expires_at timestamptz)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
begin
  if p_session_id !~ '^cs_[A-Za-z0-9_]{1,250}$' then
    raise exception 'invalid session id' using errcode = 'invalid_parameter_value';
  end if;
  update public.payments
     set external_ref = p_session_id, checkout_url = p_url, checkout_expires_at = p_expires_at
   where id = p_payment_id and tenant_id = p_tenant_id and provider = 'stripe'
     and status = 'pending' and external_ref is null;
  if not found then
    raise exception 'no pending card payment to attach' using errcode = 'no_data_found';
  end if;
end;
$$;

-- Stripe refused or couldn't be reached: the attempt is failed (the order stays; Pay now retries).
create function public.storefront_card_payment_failed(p_tenant_id uuid, p_payment_id uuid)
returns void
language sql
volatile
security definer
set search_path = ''
as $$
  update public.payments set status = 'failed'
   where id = p_payment_id and tenant_id = p_tenant_id and provider = 'stripe'
     and status = 'pending' and external_ref is null;
$$;

revoke all on function public.storefront_card_enabled(uuid) from public, anon, authenticated, service_role;
revoke all on function public.stripe_minimum_cents(text) from public, anon, authenticated, service_role;
revoke all on function public.storefront_card_payment_start(uuid, uuid) from public, anon, authenticated, service_role;
revoke all on function public.storefront_card_payment_attach(uuid, uuid, text, text, timestamptz) from public, anon, authenticated, service_role;
revoke all on function public.storefront_card_payment_failed(uuid, uuid) from public, anon, authenticated, service_role;
grant execute on function public.storefront_card_payment_start(uuid, uuid) to storefront_writer;
grant execute on function public.storefront_card_payment_attach(uuid, uuid, text, text, timestamptz) to storefront_writer;
grant execute on function public.storefront_card_payment_failed(uuid, uuid) to storefront_writer;
