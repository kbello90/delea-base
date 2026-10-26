-- Storefront A2: pricing and placing orders, and order tracking (TRD §6.11).
--
-- The public checkout never runs as an administrative login. It runs as storefront_writer,
-- a role with no table grants that may execute four SECURITY DEFINER functions only:
--   storefront_price_cart    the one pricing rule for the storefront: retail prices (the sale
--                            price while active), shipping from the shop's settings, tax at
--                            the shop's single default rate (on shipping too when the shop
--                            says so), stock and personalization checks. The quote endpoint
--                            and the order use the same function, so they can't disagree.
--   storefront_find_order    the idempotency lookup for a checkout key (before Turnstile).
--   storefront_count_attempt the per-address attempt limit.
--   storefront_place_order   validates the shop, the limits, prices the cart again, and
--                            creates the customer (or reuses a retail one), the quote, its
--                            lines and the contact, all in one transaction.
-- Each function pins search_path, re-checks the tenant itself (active, store online), and
-- has EXECUTE revoked from public, anon, authenticated and service_role.
-- storefront_reader may execute storefront_price_cart (the quote) and nothing else.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'storefront_writer') then
    create role storefront_writer nologin noinherit;
  end if;
end
$$;
-- The API's login switches into it for checkout; the least-privilege login gets the same grant.
grant storefront_writer to postgres;

-- ---------------------------------------------------------------------------
-- 1. What the shop shows before checkout (appended to storefront.store)
-- ---------------------------------------------------------------------------
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
    c.default_tax_rate_bps
  from public.brand_config b
  join public.company_settings c on c.tenant_id = b.tenant_id
  join public.storefront_settings s on s.tenant_id = b.tenant_id
  join public.tenants t on t.id = b.tenant_id
  where b.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
    and t.is_active
    and s.storefront_enabled;

-- ---------------------------------------------------------------------------
-- 2. Order tracking: one order, by its public token, in the resolved shop
-- ---------------------------------------------------------------------------
-- Both views filter on the tenant AND on app.order_token, so a read can only ever see the one
-- order whose token the shopper holds. First name and city/state only: a forwarded link must
-- not reveal the whole address.
create view storefront.orders as
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
    case when i.fulfillment = 'pickup' then s.pickup_note end as pickup_note
  from public.invoices i
  join public.order_contacts c on c.invoice_id = i.id and c.tenant_id = i.tenant_id
  join public.storefront_settings s on s.tenant_id = i.tenant_id
  join public.tenants t on t.id = i.tenant_id
  where i.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
    and i.public_token = nullif(current_setting('app.order_token', true), '')::uuid
    and i.source = 'storefront'
    and t.is_active
    and s.storefront_enabled;

create view storefront.order_lines as
  select
    li.position,
    li.description,
    li.qty,
    li.unit_price_cents,
    li.line_total_cents,
    li.personalization,
    case when p.made_to_order then p.lead_time_note end as lead_time_note
  from public.invoice_line_items li
  join public.invoices i on i.id = li.invoice_id and i.tenant_id = li.tenant_id
  join public.storefront_settings s on s.tenant_id = i.tenant_id
  join public.tenants t on t.id = i.tenant_id
  left join public.products p on p.id = li.product_id and p.tenant_id = li.tenant_id
  where i.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
    and i.public_token = nullif(current_setting('app.order_token', true), '')::uuid
    and i.source = 'storefront'
    and t.is_active
    and s.storefront_enabled;

revoke all on storefront.store, storefront.orders, storefront.order_lines
  from public, anon, authenticated;
grant select on storefront.store, storefront.orders, storefront.order_lines to storefront_reader;

-- ---------------------------------------------------------------------------
-- 3. Pricing
-- ---------------------------------------------------------------------------
-- p_cart: [{"product_id": uuid, "qty": 1..99, "personalization": {"<field id>": "answer"}}]
-- p_fulfillment: 'ship', 'pickup', or null (not chosen yet: no shipping in the total).
-- Returns {"ok": false, "error": "closed" | "invalid" | "fulfillment"} or
-- {"ok": true, "lines": [...], "subtotal_cents", "shipping_cents" (null when not chosen),
--  "tax_rate_bps", "tax_cents", "total_cents", "has_problems"}. A line's "problem" is null,
-- "unavailable" (not published, unknown, or not enough stock) or "options" (personalization).
create function public.storefront_price_cart(p_tenant_id uuid, p_cart jsonb, p_fulfillment text)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  shop record;
  item record;
  product record;
  field record;
  needed jsonb := '{}';
  answers jsonb;
  snapshot jsonb;
  answer text;
  problem text;
  unit bigint;
  line_total bigint;
  lines jsonb := '[]';
  subtotal bigint := 0;
  shipping bigint;
  tax bigint;
  problems boolean := false;
begin
  select s.ship_enabled, s.ship_flat_cents, s.free_ship_over_cents, s.tax_shipping,
         s.pickup_enabled, c.default_tax_rate_bps
    into shop
    from public.storefront_settings s
    join public.tenants t on t.id = s.tenant_id
    join public.company_settings c on c.tenant_id = s.tenant_id
   where s.tenant_id = p_tenant_id and t.is_active and s.storefront_enabled;
  if not found then
    return jsonb_build_object('ok', false, 'error', 'closed');
  end if;

  if jsonb_typeof(p_cart) is distinct from 'array' or jsonb_array_length(p_cart) not between 1 and 50
     or (p_fulfillment is not null and p_fulfillment not in ('ship', 'pickup')) then
    return jsonb_build_object('ok', false, 'error', 'invalid');
  end if;
  if (p_fulfillment = 'ship' and not shop.ship_enabled)
     or (p_fulfillment = 'pickup' and not shop.pickup_enabled) then
    return jsonb_build_object('ok', false, 'error', 'fulfillment');
  end if;

  -- Shape, and the quantity needed per product (stock is checked on the sum, as when paid).
  for item in select value as v from jsonb_array_elements(p_cart) loop
    if jsonb_typeof(item.v) is distinct from 'object'
       or (item.v ->> 'product_id') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       or jsonb_typeof(item.v -> 'qty') is distinct from 'number'
       or (item.v ->> 'qty') !~ '^[0-9]{1,2}$'
       or (item.v ->> 'qty')::integer not between 1 and 99
       or (item.v ? 'personalization' and jsonb_typeof(item.v -> 'personalization') <> 'object') then
      return jsonb_build_object('ok', false, 'error', 'invalid');
    end if;
    needed := jsonb_set(needed, array[item.v ->> 'product_id'],
      to_jsonb(coalesce((needed ->> (item.v ->> 'product_id'))::integer, 0) + (item.v ->> 'qty')::integer));
  end loop;

  for item in select value as v, ordinality as n from jsonb_array_elements(p_cart) with ordinality loop
    problem := null;
    snapshot := '[]';
    unit := null;
    line_total := null;

    select p.id, p.name, p.price_cents, p.sale_price_cents, p.sale_ends_at, p.stock_qty,
           p.made_to_order, p.lead_time_note
      into product
      from public.products p
     where p.tenant_id = p_tenant_id and p.id = (item.v ->> 'product_id')::uuid and p.is_published;

    if not found then
      problem := 'unavailable';
    else
      if not product.made_to_order
         and product.stock_qty < (needed ->> (item.v ->> 'product_id'))::integer then
        problem := 'unavailable';
      end if;

      -- Personalization: only this product's fields; required ones answered; choices from
      -- the list; text within its length and without control characters.
      answers := coalesce(item.v -> 'personalization', '{}');
      if exists (select 1 from jsonb_object_keys(answers) k
                  where not exists (select 1 from public.product_personalization_fields f
                                     where f.product_id = product.id and f.tenant_id = p_tenant_id
                                       and f.id::text = k))
         or exists (select 1 from jsonb_each(answers) a where jsonb_typeof(a.value) <> 'string') then
        problem := coalesce(problem, 'options');
      end if;
      for field in
        select f.id, f.label, f.kind, f.required, f.max_length, f.choices
          from public.product_personalization_fields f
         where f.product_id = product.id and f.tenant_id = p_tenant_id
         order by f.position
      loop
        answer := nullif(btrim(answers ->> field.id::text), '');
        if answer is null then
          if field.required then
            problem := coalesce(problem, 'options');
          end if;
        elsif (field.kind = 'choice' and not (answer = any (field.choices)))
           or (field.kind = 'text' and char_length(answer) > field.max_length)
           or answer ~ '[[:cntrl:]]' then
          problem := coalesce(problem, 'options');
        else
          snapshot := snapshot || jsonb_build_array(jsonb_build_object('label', field.label, 'value', answer));
        end if;
      end loop;

      unit := case when product.sale_price_cents is not null
                     and (product.sale_ends_at is null or product.sale_ends_at > now())
                   then product.sale_price_cents else product.price_cents end;
      line_total := unit * (item.v ->> 'qty')::integer;
      subtotal := subtotal + line_total;
    end if;

    problems := problems or problem is not null;
    lines := lines || jsonb_build_array(jsonb_build_object(
      'position', item.n,
      'product_id', item.v ->> 'product_id',
      'name', product.name,
      'qty', (item.v ->> 'qty')::integer,
      'unit_price_cents', unit,
      'line_total_cents', line_total,
      'personalization', case when jsonb_array_length(snapshot) > 0 then snapshot end,
      'made_to_order', coalesce(product.made_to_order, false),
      'lead_time_note', case when product.made_to_order then product.lead_time_note end,
      'problem', problem));
  end loop;

  shipping := case p_fulfillment
    when 'ship' then case when shop.free_ship_over_cents is not null
                               and subtotal >= shop.free_ship_over_cents
                          then 0 else shop.ship_flat_cents end
    when 'pickup' then 0
  end;
  -- Half-up rounding in integer arithmetic, as pricing.py: (a × bps + 5000) div 10000.
  tax := ((subtotal + case when shop.tax_shipping then coalesce(shipping, 0) else 0 end)
          * shop.default_tax_rate_bps + 5000) / 10000;

  return jsonb_build_object(
    'ok', true,
    'lines', lines,
    'subtotal_cents', subtotal,
    'shipping_cents', shipping,
    'tax_rate_bps', shop.default_tax_rate_bps,
    'tax_cents', tax,
    'total_cents', subtotal + coalesce(shipping, 0) + tax,
    'has_problems', problems);
end;
$$;

-- ---------------------------------------------------------------------------
-- 4. Idempotency lookup and the attempt limit
-- ---------------------------------------------------------------------------
-- Before anything else (Turnstile included): a repeated checkout key returns the order it
-- created ("replayed", same request) or "conflict" (a different request); else "none".
create function public.storefront_find_order(p_tenant_id uuid, p_checkout_key uuid, p_fingerprint text)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  found_order record;
begin
  select i.checkout_fingerprint, i.public_token, i.order_ref, i.total_cents
    into found_order
    from public.invoices i
    join public.tenants t on t.id = i.tenant_id
    join public.storefront_settings s on s.tenant_id = i.tenant_id
   where i.tenant_id = p_tenant_id and i.checkout_key = p_checkout_key
     and t.is_active and s.storefront_enabled;
  if not found then
    return jsonb_build_object('status', 'none');
  end if;
  if found_order.checkout_fingerprint is distinct from p_fingerprint then
    return jsonb_build_object('status', 'conflict');
  end if;
  return jsonb_build_object('status', 'replayed', 'public_token', found_order.public_token,
                            'order_ref', found_order.order_ref);
end;
$$;

-- At most 20 order attempts per client address per hour (every POST, whatever its outcome).
-- Returns false when over the limit; counts the attempt otherwise.
create function public.storefront_count_attempt(p_ip_hash text)
returns boolean
language plpgsql
volatile
security definer
set search_path = ''
as $$
begin
  if p_ip_hash !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid address hash' using errcode = 'invalid_parameter_value';
  end if;
  -- One check at a time per address, so parallel requests can't slip past the limit.
  perform pg_advisory_xact_lock(hashtextextended('order_attempt_ip:' || p_ip_hash, 0));
  if (select count(*) from public.public_rate_hits
       where scope = 'order_attempt_ip' and key_hash = p_ip_hash
         and created_at > now() - interval '1 hour') >= 20 then
    return false;
  end if;
  insert into public.public_rate_hits (scope, key_hash) values ('order_attempt_ip', p_ip_hash);
  delete from public.public_rate_hits where created_at < now() - interval '1 day';
  return true;
end;
$$;

-- ---------------------------------------------------------------------------
-- 5. Placing an order
-- ---------------------------------------------------------------------------
-- p_order: {"items": [...as storefront_price_cart...], "fulfillment": "ship" | "pickup",
--   "payment_choice": "bank_transfer" | "pay_on_pickup", "expected_total_cents": n,
--   "contact": {"first_name", "last_name", "email", "phone"?},
--   "ship_to": {"line1", "line2"?, "city", "state", "postal_code"} (ship only), "note"?}
-- The API validates the shape first; this function checks everything that depends on the
-- shop and its data, and is the only path that writes a storefront order.
-- Returns {"status": ...}: "created" | "replayed" (+ public_token, order_ref), "conflict",
-- "closed", "invalid" (+ reason), "rate_limited" (+ scope), "unavailable" | "price_changed"
-- (+ quote).
create function public.storefront_place_order(
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
          or (payment_choice = 'pay_on_pickup' and shop.pay_on_pickup_enabled and fulfillment = 'pickup')) then
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
-- 6. Who may execute them
-- ---------------------------------------------------------------------------
revoke all on function public.storefront_price_cart(uuid, jsonb, text) from public, anon, authenticated, service_role;
revoke all on function public.storefront_find_order(uuid, uuid, text) from public, anon, authenticated, service_role;
revoke all on function public.storefront_count_attempt(text) from public, anon, authenticated, service_role;
revoke all on function public.storefront_place_order(uuid, uuid, text, jsonb, text, text) from public, anon, authenticated, service_role;
revoke all on function public.personalization_choices_ok(text[]) from public, anon, authenticated, service_role;
revoke all on function public.personalization_snapshot_ok(jsonb) from public, anon, authenticated, service_role;

-- The two pure check functions run inside CHECK constraints, as the role doing the write
-- (app users, and the service role for webhooks and maintenance).
grant execute on function public.personalization_choices_ok(text[]) to authenticated, service_role;
grant execute on function public.personalization_snapshot_ok(jsonb) to authenticated, service_role;

grant execute on function public.storefront_price_cart(uuid, jsonb, text) to storefront_reader, storefront_writer;
grant execute on function public.storefront_find_order(uuid, uuid, text) to storefront_writer;
grant execute on function public.storefront_count_attempt(text) to storefront_writer;
grant execute on function public.storefront_place_order(uuid, uuid, text, jsonb, text, text) to storefront_writer;
