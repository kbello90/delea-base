-- Slice D1: email (TRD §6.16).
--   1. Events: production_started and deposit_received join the automation queue.
--   2. email_settings: per shop, the owner alerts switched on or off, the sender name, and the
--      reply-to address, used only once confirmed through a link.
--   3. email_messages: the outbox. One row per email to send, unique per shop and source key,
--      so a replayed event can't queue a second email. Rendered once, sent with the row id as
--      the provider's idempotency key, retried with backoff.
--   4. email_suppressions and email_provider_events: addresses that bounced or complained, and
--      the provider's webhook events (each recorded once).
--   5. email_worker: a role with no table grants that executes the outbox functions only (the
--      same pattern as payments_writer). Rules enforced here, whatever the caller:
--        - a demo shop's email goes to that shop's own owners only (owner alerts, tests),
--          never to anyone else;
--        - owner alerts go to the shop's owners only;
--        - suppressed addresses get nothing;
--        - per shop: 300 customer emails a day, 5 a day to one customer, 10 tests a day.

-- ---------------------------------------------------------------------------
-- 1. Events
-- ---------------------------------------------------------------------------
create function public.invoices_emit_production_started()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if old.production_started_at is null and new.production_started_at is not null then
    insert into public.automation_events (tenant_id, event_type, payload)
    values (new.tenant_id, 'production_started', jsonb_build_object('invoice_id', new.id));
  end if;
  return null;
end;
$$;

create trigger invoices_emit_production_started
  after update of production_started_at on public.invoices
  for each row execute function public.invoices_emit_production_started();

-- A payment that leaves part of the total due is a deposit. (Paid in full shows up as the
-- quote -> paid status change instead.)
create function public.payments_emit_deposit_received()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.kind = 'payment' and new.status = 'succeeded'
     and (tg_op = 'INSERT' or old.status is distinct from 'succeeded')
     and public.invoice_balance(new.invoice_id) > 0 then
    insert into public.automation_events (tenant_id, event_type, payload)
    values (new.tenant_id, 'deposit_received', jsonb_build_object(
      'invoice_id', new.invoice_id, 'payment_id', new.id, 'amount_cents', new.amount_cents));
  end if;
  return null;
end;
$$;

create trigger payments_emit_deposit_received
  after insert or update of status on public.payments
  for each row execute function public.payments_emit_deposit_received();

-- ---------------------------------------------------------------------------
-- 2. email_settings
-- ---------------------------------------------------------------------------
create table public.email_settings (
  tenant_id uuid primary key references public.tenants (id) on delete cascade,
  alert_new_order boolean not null default true,
  alert_low_stock boolean not null default true,
  alert_balance_due boolean not null default true,
  sender_name text check (sender_name is null or char_length(sender_name) between 1 and 60),
  reply_to_email text check (reply_to_email is null
    or (char_length(reply_to_email) <= 254 and reply_to_email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')),
  reply_to_confirmed_at timestamptz,
  reply_to_pending text check (reply_to_pending is null
    or (char_length(reply_to_pending) <= 254 and reply_to_pending ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')),
  reply_to_token_hash text check (reply_to_token_hash is null or reply_to_token_hash ~ '^[0-9a-f]{64}$'),
  reply_to_requested_at timestamptz,
  updated_at timestamptz not null default now(),
  constraint email_settings_confirmed_has_email check (reply_to_confirmed_at is null or reply_to_email is not null)
);

create trigger email_settings_90_updated_at
  before update on public.email_settings
  for each row execute function public.set_updated_at();

-- App users change the switches and the sender name only. The reply-to address moves through
-- email_request_reply_to (owner) and email_confirm_reply_to (the link), never directly.
create function public.email_settings_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  -- current_user, not the claims: inside email_request_reply_to / email_confirm_reply_to
  -- (SECURITY DEFINER) the statement runs as their owner, while a signed-in user's own write
  -- runs as `authenticated`.
  if current_user in ('authenticated', 'anon')
     and (new.reply_to_email is distinct from (case when tg_op = 'UPDATE' then old.reply_to_email end)
          or new.reply_to_confirmed_at is distinct from (case when tg_op = 'UPDATE' then old.reply_to_confirmed_at end)
          or new.reply_to_pending is distinct from (case when tg_op = 'UPDATE' then old.reply_to_pending end)
          or new.reply_to_token_hash is distinct from (case when tg_op = 'UPDATE' then old.reply_to_token_hash end)
          or new.reply_to_requested_at is distinct from (case when tg_op = 'UPDATE' then old.reply_to_requested_at end)) then
    raise exception 'the reply-to address is changed through its confirmation link'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

create trigger email_settings_10_guard
  before insert or update on public.email_settings
  for each row execute function public.email_settings_guard();

alter table public.email_settings enable row level security;
create policy email_settings_owner_select on public.email_settings for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner');
create policy email_settings_owner_insert on public.email_settings for insert to authenticated
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');
create policy email_settings_owner_update on public.email_settings for update to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner')
  with check (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
              and (select auth.jwt() ->> 'app_role') = 'owner');
revoke all on public.email_settings from anon, authenticated;
grant select, insert, update on public.email_settings to authenticated;

-- ---------------------------------------------------------------------------
-- 3. email_messages
-- ---------------------------------------------------------------------------
create table public.email_messages (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  kind text not null check (kind in ('owner_alert', 'customer', 'test', 'reply_to_confirmation')),
  template text not null check (template ~ '^[a-z_]{1,60}$'),
  source_key text not null check (char_length(source_key) between 1 and 300),
  recipient text not null check (char_length(recipient) <= 254
    and recipient ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
  from_name text not null check (char_length(from_name) between 1 and 60),
  reply_to text,
  subject text not null check (char_length(subject) between 1 and 200),
  html_body text not null check (char_length(html_body) <= 200000),
  text_body text not null check (char_length(text_body) <= 100000),
  status text not null default 'queued'
    check (status in ('queued', 'sending', 'sent', 'failed', 'suppressed')),
  attempts integer not null default 0 check (attempts between 0 and 20),
  next_attempt_at timestamptz not null default now(),
  last_error text check (last_error is null or char_length(last_error) <= 500),
  provider_message_id text,
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  constraint email_messages_one_per_source unique (tenant_id, source_key)
);
create index email_messages_due on public.email_messages (next_attempt_at) where status = 'queued';
create index email_messages_tenant_day on public.email_messages (tenant_id, created_at);

alter table public.email_messages enable row level security;
-- The owner reads the shop's log (subjects, recipients, status); nobody with an app role writes.
create policy email_messages_owner_select on public.email_messages for select to authenticated
  using (tenant_id = (select (auth.jwt() ->> 'tenant_id')::uuid)
         and (select auth.jwt() ->> 'app_role') = 'owner');
revoke all on public.email_messages from anon, authenticated;
grant select on public.email_messages to authenticated;

-- ---------------------------------------------------------------------------
-- 4. Suppressions and provider events (no app grants)
-- ---------------------------------------------------------------------------
create table public.email_suppressions (
  email text primary key check (email = lower(email) and char_length(email) <= 254),
  reason text not null check (reason in ('bounced', 'complained')),
  created_at timestamptz not null default now()
);
alter table public.email_suppressions enable row level security;
revoke all on public.email_suppressions from anon, authenticated;

create table public.email_provider_events (
  event_id text primary key check (char_length(event_id) between 1 and 200),
  event_type text not null,
  created_at timestamptz not null default now()
);
alter table public.email_provider_events enable row level security;
revoke all on public.email_provider_events from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 5. email_worker and its functions
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'email_worker') then
    create role email_worker nologin noinherit;
  end if;
end
$$;
grant email_worker to postgres;

-- The shop's owners' sign-in emails (lowercase).
create function public.email_owner_addresses(p_tenant_id uuid)
returns text[]
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(array_agg(lower(u.email::text) order by u.email), '{}')
    from public.tenant_members m
    join auth.users u on u.id = m.user_id
   where m.tenant_id = p_tenant_id and m.app_role = 'owner' and u.email is not null;
$$;

-- Queues one email. Returns its id, or null when this source already queued one. The rules
-- in the header apply here; a refused email is kept as 'suppressed' with the reason, so the
-- log shows it. The reply-to address is the shop's confirmed one, never the caller's.
create function public.email_enqueue(
  p_tenant_id uuid, p_kind text, p_template text, p_source_key text, p_recipient text,
  p_subject text, p_html text, p_text text)
returns uuid
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  shop record;
  v_recipient text := lower(trim(p_recipient));
  owners text[];
  v_status text := 'queued';
  v_reason text;
  v_id uuid;
begin
  select t.is_demo, coalesce(es.sender_name, b.business_name, t.name) as from_name,
         case when es.reply_to_confirmed_at is not null then es.reply_to_email end as reply_to
    into shop
    from public.tenants t
    left join public.brand_config b on b.tenant_id = t.id
    left join public.email_settings es on es.tenant_id = t.id
   where t.id = p_tenant_id and t.is_active;
  if not found then
    raise exception 'shop not found' using errcode = 'no_data_found';
  end if;
  owners := public.email_owner_addresses(p_tenant_id);

  if p_kind = 'owner_alert' and not (v_recipient = any (owners)) then
    raise exception 'owner alerts go to the shop''s owners only' using errcode = 'check_violation';
  end if;

  if shop.is_demo and not (v_recipient = any (owners)) then
    v_status := 'suppressed';
    v_reason := 'demo shop: only its own owners get email';
  elsif exists (select 1 from public.email_suppressions s where s.email = v_recipient) then
    v_status := 'suppressed';
    v_reason := 'address bounced or complained';
  elsif p_kind = 'customer' and (
          (select count(*) from public.email_messages m
            where m.tenant_id = p_tenant_id and m.kind = 'customer'
              and m.created_at > now() - interval '1 day') >= 300
          or (select count(*) from public.email_messages m
               where m.tenant_id = p_tenant_id and m.kind = 'customer' and m.recipient = v_recipient
                 and m.created_at > now() - interval '1 day') >= 5) then
    v_status := 'suppressed';
    v_reason := 'daily limit reached';
  elsif p_kind = 'test' and (select count(*) from public.email_messages m
                              where m.tenant_id = p_tenant_id and m.kind = 'test'
                                and m.created_at > now() - interval '1 day') >= 10 then
    v_status := 'suppressed';
    v_reason := 'daily limit reached';
  end if;

  insert into public.email_messages (tenant_id, kind, template, source_key, recipient, from_name,
                                     reply_to, subject, html_body, text_body, status, last_error)
  values (p_tenant_id, p_kind, p_template, p_source_key, v_recipient,
          left(regexp_replace(shop.from_name, '[<>"\r\n]', '', 'g'), 60),
          shop.reply_to, p_subject, p_html, p_text, v_status, v_reason)
  on conflict (tenant_id, source_key) do nothing
  returning id into v_id;
  return v_id;
end;
$$;

-- Claims up to p_limit unprocessed events (FOR UPDATE SKIP LOCKED: two workers never take the
-- same one), with what the worker needs to write the email. Events older than two days are
-- marked processed and never mailed (a worker starting late doesn't send old news). The locks
-- last until the caller's transaction ends, where it marks them processed.
create function public.email_claim_events(p_limit integer)
returns table (event_id uuid, tenant_id uuid, event_type text, payload jsonb, context jsonb)
language plpgsql
volatile
security definer
set search_path = ''
as $$
begin
  update public.automation_events e set processed_at = now()
   where e.processed_at is null and e.created_at < now() - interval '2 days';

  return query
  with claimed as (
    select e.id, e.tenant_id, e.event_type, e.payload
      from public.automation_events e
     where e.processed_at is null
     order by e.created_at
     limit greatest(1, least(p_limit, 500))
       for update skip locked
  )
  select c.id, c.tenant_id, c.event_type::text, c.payload,
         jsonb_build_object(
           'business_name', coalesce(b.business_name, t.name),
           'primary_color', b.primary_color,
           'is_demo', t.is_demo,
           'owners', to_jsonb(public.email_owner_addresses(c.tenant_id)),
           'alerts', jsonb_build_object(
             'new_order', coalesce(es.alert_new_order, true),
             'low_stock', coalesce(es.alert_low_stock, true),
             'balance_due', coalesce(es.alert_balance_due, true)),
           'invoice', (select jsonb_build_object(
                         'source', i.source, 'order_ref', i.order_ref,
                         'number', coalesce(i.invoice_number, i.quote_number),
                         'total_cents', i.total_cents, 'currency', cs.currency,
                         'customer_name', i.customer_name, 'fulfillment', i.fulfillment,
                         'payment_choice', i.payment_choice,
                         'items', (select count(*) from public.invoice_line_items li where li.invoice_id = i.id))
                         from public.invoices i
                         join public.company_settings cs on cs.tenant_id = i.tenant_id
                        where i.id = (c.payload ->> 'invoice_id')::uuid and i.tenant_id = c.tenant_id),
           'product', (select jsonb_build_object('name', p.name, 'sku', p.sku,
                                                 'stock_qty', p.stock_qty, 'threshold', p.low_stock_threshold)
                         from public.products p
                        where p.id = (c.payload ->> 'product_id')::uuid and p.tenant_id = c.tenant_id))
    from claimed c
    join public.tenants t on t.id = c.tenant_id
    left join public.brand_config b on b.tenant_id = c.tenant_id
    left join public.email_settings es on es.tenant_id = c.tenant_id;
end;
$$;

create function public.email_mark_events_processed(p_ids uuid[])
returns void
language sql
volatile
security definer
set search_path = ''
as $$
  update public.automation_events set processed_at = now()
   where id = any (p_ids) and processed_at is null;
$$;

-- Open quotes holding a deposit, past their due date, per shop that wants the alert.
create function public.email_balance_due()
returns table (tenant_id uuid, business_name text, owners text[], currency text, invoices jsonb)
language sql
stable
security definer
set search_path = ''
as $$
  select i.tenant_id, max(coalesce(b.business_name, t.name)), public.email_owner_addresses(i.tenant_id),
         max(cs.currency::text),
         jsonb_agg(jsonb_build_object(
           'number', coalesce(i.order_ref, i.quote_number), 'customer_name', i.customer_name,
           'due_date', i.due_date, 'balance_cents', public.invoice_balance(i.id))
           order by i.due_date, i.quote_number)
    from public.invoices i
    join public.tenants t on t.id = i.tenant_id and t.is_active
    join public.company_settings cs on cs.tenant_id = i.tenant_id
    left join public.brand_config b on b.tenant_id = i.tenant_id
    left join public.email_settings es on es.tenant_id = i.tenant_id
   where i.order_status = 'quote' and i.cancelled_at is null
     and i.due_date < (now() at time zone 'UTC')::date
     and public.invoice_amount_paid(i.id) > 0
     and public.invoice_balance(i.id) > 0
     and coalesce(es.alert_balance_due, true)
   group by i.tenant_id;
$$;

-- Sending: queued rows that are due, claimed and marked 'sending' (committed by the caller, so
-- another worker skips them). A row left 'sending' for 10 minutes (a worker died mid-send) is
-- queued again; the provider's idempotency key stops a second delivery.
create function public.email_claim_messages(p_limit integer)
returns table (id uuid, tenant_id uuid, recipient text, from_name text, reply_to text,
               subject text, html_body text, text_body text, attempts integer)
language plpgsql
volatile
security definer
set search_path = ''
as $$
begin
  update public.email_messages m set status = 'queued'
   where m.status = 'sending' and m.next_attempt_at < now() - interval '10 minutes';
  return query
  with due as (
    select m.id from public.email_messages m
     where m.status = 'queued' and m.next_attempt_at <= now()
     order by m.next_attempt_at
     limit greatest(1, least(p_limit, 200))
       for update skip locked
  )
  update public.email_messages m
     set status = 'sending', attempts = m.attempts + 1, next_attempt_at = now()
    from due
   where m.id = due.id
  returning m.id, m.tenant_id, m.recipient, m.from_name, m.reply_to, m.subject, m.html_body,
            m.text_body, m.attempts;
end;
$$;

create function public.email_mark_sent(p_id uuid, p_provider_id text)
returns void
language sql
volatile
security definer
set search_path = ''
as $$
  update public.email_messages
     set status = 'sent', sent_at = now(), provider_message_id = left(p_provider_id, 200), last_error = null
   where id = p_id and status = 'sending';
$$;

-- p_retry_at null: a permanent failure. Otherwise queued again for then.
create function public.email_mark_failed(p_id uuid, p_error text, p_retry_at timestamptz)
returns void
language sql
volatile
security definer
set search_path = ''
as $$
  update public.email_messages
     set status = case when p_retry_at is null then 'failed' else 'queued' end,
         next_attempt_at = coalesce(p_retry_at, next_attempt_at),
         last_error = left(p_error, 500)
   where id = p_id and status = 'sending';
$$;

-- The provider's webhook: each event once; a bounce or complaint suppresses the address.
create function public.email_record_provider_event(p_event_id text, p_event_type text, p_email text)
returns text
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  n integer;
begin
  insert into public.email_provider_events (event_id, event_type) values (p_event_id, p_event_type)
  on conflict (event_id) do nothing;
  get diagnostics n = row_count;
  if n = 0 then
    return 'duplicate';
  end if;
  if p_event_type in ('email.bounced', 'email.complained') and p_email is not null then
    insert into public.email_suppressions (email, reason)
    values (lower(trim(p_email)), case when p_event_type = 'email.bounced' then 'bounced' else 'complained' end)
    on conflict (email) do nothing;
    return 'suppressed';
  end if;
  return 'ignored';
end;
$$;

-- The owner asks to use a reply-to address: kept pending with the hash of a one-time token
-- that is emailed to that address (by the API, through email_enqueue).
create function public.email_request_reply_to(p_email text, p_token_hash text)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_tenant uuid := nullif((select auth.jwt() ->> 'tenant_id'), '')::uuid;
begin
  if (select auth.jwt() ->> 'app_role') is distinct from 'owner' or v_tenant is null then
    raise exception 'only the owner can change the reply-to address' using errcode = 'insufficient_privilege';
  end if;
  insert into public.email_settings (tenant_id, reply_to_pending, reply_to_token_hash, reply_to_requested_at)
  values (v_tenant, lower(trim(p_email)), p_token_hash, now())
  on conflict (tenant_id) do update
     set reply_to_pending = excluded.reply_to_pending,
         reply_to_token_hash = excluded.reply_to_token_hash,
         reply_to_requested_at = excluded.reply_to_requested_at;
end;
$$;

-- The link in the confirmation email. One use, valid for 24 hours. Returns the shop's name, or
-- null when the link is unknown, used or expired.
create function public.email_confirm_reply_to(p_token_hash text)
returns text
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_tenant uuid;
begin
  update public.email_settings
     set reply_to_email = reply_to_pending, reply_to_confirmed_at = now(),
         reply_to_pending = null, reply_to_token_hash = null
   where reply_to_token_hash = p_token_hash
     and reply_to_requested_at > now() - interval '1 day'
  returning tenant_id into v_tenant;
  if v_tenant is null then
    return null;
  end if;
  return (select coalesce(b.business_name, t.name) from public.tenants t
            left join public.brand_config b on b.tenant_id = t.id where t.id = v_tenant);
end;
$$;

revoke all on function public.invoices_emit_production_started() from public, anon, authenticated, service_role;
revoke all on function public.payments_emit_deposit_received() from public, anon, authenticated, service_role;
revoke all on function public.email_settings_guard() from public, anon, authenticated, service_role;
revoke all on function public.email_owner_addresses(uuid) from public, anon, authenticated, service_role;
revoke all on function public.email_enqueue(uuid, text, text, text, text, text, text, text) from public, anon, authenticated, service_role;
revoke all on function public.email_claim_events(integer) from public, anon, authenticated, service_role;
revoke all on function public.email_mark_events_processed(uuid[]) from public, anon, authenticated, service_role;
revoke all on function public.email_balance_due() from public, anon, authenticated, service_role;
revoke all on function public.email_claim_messages(integer) from public, anon, authenticated, service_role;
revoke all on function public.email_mark_sent(uuid, text) from public, anon, authenticated, service_role;
revoke all on function public.email_mark_failed(uuid, text, timestamptz) from public, anon, authenticated, service_role;
revoke all on function public.email_record_provider_event(text, text, text) from public, anon, authenticated, service_role;
revoke all on function public.email_request_reply_to(text, text) from public, anon, authenticated, service_role;
revoke all on function public.email_confirm_reply_to(text) from public, anon, authenticated, service_role;

grant execute on function public.email_enqueue(uuid, text, text, text, text, text, text, text) to email_worker;
grant execute on function public.email_claim_events(integer) to email_worker;
grant execute on function public.email_mark_events_processed(uuid[]) to email_worker;
grant execute on function public.email_balance_due() to email_worker;
grant execute on function public.email_claim_messages(integer) to email_worker;
grant execute on function public.email_mark_sent(uuid, text) to email_worker;
grant execute on function public.email_mark_failed(uuid, text, timestamptz) to email_worker;
grant execute on function public.email_record_provider_event(text, text, text) to email_worker;
grant execute on function public.email_confirm_reply_to(text) to email_worker;
grant execute on function public.email_request_reply_to(text, text) to authenticated;
