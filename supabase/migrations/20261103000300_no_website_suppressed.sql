-- A customer email for a shop with no website address is recorded, not dropped
-- (hosted-demo plan step 2, decision 2026-11-03).
--
-- A customer email links to the order page on the shop's primary storefront domain
-- (migration 20261103000200). A shop without one has no order page, so its customer emails
-- are not sent. Until now the worker skipped them silently; now email_enqueue keeps each one
-- as 'suppressed' with the reason 'no website address', so the owner's email log shows
-- "Not sent: no website address". Decided here, for every caller, like the other reasons.
-- Unchanged otherwise; create or replace keeps the grant to email_worker only.

create or replace function public.email_enqueue(
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

  if p_kind = 'customer' and not exists (
       select 1 from public.tenant_domains d where d.tenant_id = p_tenant_id and d.is_primary) then
    v_status := 'suppressed';
    v_reason := 'no website address';
  elsif shop.is_demo and not (v_recipient = any (owners)) then
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
