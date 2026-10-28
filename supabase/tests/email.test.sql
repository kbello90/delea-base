begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migrations 20261029000100-0200: the new events, email_settings, the outbox and email_worker.
-- Everything is rolled back.

create function pg_temp.as_role(p_role text, p_sql text)
returns text
language plpgsql
as $$
declare
  v text;
begin
  perform set_config('role', p_role, true);
  begin
    execute p_sql into v;
  exception when others then
    perform set_config('role', 'none', true);
    raise;
  end;
  perform set_config('role', 'none', true);
  return v;
end;
$$;

create function pg_temp.worker(p_sql text)
returns text
language sql
as $$ select pg_temp.as_role('email_worker', p_sql); $$;

create function pg_temp.set_claims(p_tenant uuid, p_role text)
returns void
language plpgsql
as $$
begin
  perform set_config('request.jwt.claims', json_build_object(
    'sub', gen_random_uuid(), 'role', 'authenticated', 'tenant_id', p_tenant, 'app_role', p_role)::text, true);
end;
$$;

-- Queue one email of tenant A; returns the status ('none' when the source already queued one).
create function pg_temp.enqueue(p_kind text, p_to text, p_key text, p_tenant uuid default '11111111-1111-1111-1111-111111111111')
returns text
language plpgsql
as $$
declare
  v_id text;
begin
  v_id := pg_temp.worker(format(
    'select public.email_enqueue(%L, %L, %L, %L, %L, %L, %L, %L)::text',
    p_tenant, p_kind, 'it_template', p_key, p_to, 'Subject', '<p>Hi</p>', 'Hi'));
  if v_id is null then
    return 'none';
  end if;
  return (select status from public.email_messages where id = v_id::uuid);
end;
$$;

grant email_worker to postgres;

insert into auth.users (id, email) values
  ('ef000000-0000-0000-0000-00000000000a', 'Owner-A@Example.test'),
  ('ef000000-0000-0000-0000-00000000000b', 'owner-b@example.test');
insert into public.tenant_members (user_id, tenant_id, app_role) values
  ('ef000000-0000-0000-0000-00000000000a', '11111111-1111-1111-1111-111111111111', 'owner'),
  ('ef000000-0000-0000-0000-00000000000b', '22222222-2222-2222-2222-222222222222', 'owner');

-- ---------------------------------------------------------------------------
-- 1. Who may run what
-- ---------------------------------------------------------------------------
select ok(not has_function_privilege(r, 'public.email_enqueue(uuid, text, text, text, text, text, text, text)', 'execute'),
          r || ' cannot queue email')
  from unnest(array['anon', 'authenticated', 'service_role', 'storefront_writer', 'payments_writer']) r;
select ok(not has_function_privilege(r, 'public.email_claim_messages(integer)', 'execute'), r || ' cannot claim email')
  from unnest(array['anon', 'authenticated', 'service_role']) r;
select ok(has_function_privilege('email_worker', 'public.email_claim_messages(integer)', 'execute'),
          'email_worker claims email');
select ok(not has_table_privilege('email_worker', 'public.' || t, 'select'), 'email_worker has no grant on ' || t)
  from unnest(array['email_messages', 'email_settings', 'automation_events', 'email_suppressions']) t;
select ok(not has_table_privilege('authenticated', 'public.' || t, 'select'), 'authenticated has no grant on ' || t)
  from unnest(array['email_suppressions', 'email_provider_events']) t;

-- ---------------------------------------------------------------------------
-- 2. The outbox rules
-- ---------------------------------------------------------------------------
select is(pg_temp.enqueue('owner_alert', 'owner-a@example.test', 'k-alert'), 'queued', 'an owner alert to the owner is queued');
select is((select recipient from public.email_messages where source_key = 'k-alert'), 'owner-a@example.test',
          'the address is stored in lowercase');
select is(pg_temp.enqueue('owner_alert', 'owner-a@example.test', 'k-alert'), 'none',
          'the same source never queues a second email');
select throws_ok($q$select pg_temp.enqueue('owner_alert', 'someone@example.test', 'k-alert-2')$q$,
                 '23514', null, 'owner alerts go to the shop''s owners only');
select throws_ok($q$select pg_temp.enqueue('owner_alert', 'owner-b@example.test', 'k-alert-3')$q$,
                 '23514', null, 'not to another shop''s owner either');
select is(pg_temp.enqueue('customer', 'shopper@example.test', 'k-cust-1'), 'queued', 'a customer email is queued');

-- A demo shop: its own owner only.
update public.tenants set is_demo = true where id = '22222222-2222-2222-2222-222222222222';
select is(pg_temp.enqueue('test', 'owner-b@example.test', 'k-demo-test', '22222222-2222-2222-2222-222222222222'), 'queued',
          'a demo shop may email its own owner');
select is(pg_temp.enqueue('customer', 'shopper@example.test', 'k-demo-cust', '22222222-2222-2222-2222-222222222222'), 'suppressed',
          'a demo shop never emails a customer');
select is((select last_error from public.email_messages where source_key = 'k-demo-cust'),
          'demo shop: only its own owners get email', 'and says why');
select is(pg_temp.enqueue('reply_to_confirmation', 'someone@example.test', 'k-demo-rt', '22222222-2222-2222-2222-222222222222'), 'suppressed',
          'nor anyone else');

-- Suppressed addresses and limits.
select is(pg_temp.worker($q$select public.email_record_provider_event('evt_b1', 'email.bounced', 'Gone@Example.test')$q$),
          'suppressed', 'a bounce suppresses the address');
select is(pg_temp.worker($q$select public.email_record_provider_event('evt_b1', 'email.bounced', 'gone@example.test')$q$),
          'duplicate', 'each provider event once');
select is(pg_temp.enqueue('customer', 'gone@example.test', 'k-gone'), 'suppressed', 'a suppressed address gets nothing');
select is(pg_temp.enqueue('customer', 'many@example.test', 'k-many-' || n), 'queued', 'customer email ' || n)
  from generate_series(1, 5) n;
select is(pg_temp.enqueue('customer', 'many@example.test', 'k-many-6'), 'suppressed', 'at most 5 a day to one customer');
select is(pg_temp.enqueue('test', 'owner-a@example.test', 'k-test-' || n), 'queued', 'test ' || n) from generate_series(1, 10) n;
select is(pg_temp.enqueue('test', 'owner-a@example.test', 'k-test-11'), 'suppressed', 'at most 10 tests a day');
-- 300 customer emails a day per shop. Every attempt counts, whatever its status.
select count(*) from (
  select pg_temp.enqueue('customer', 'bulk-' || n || '@example.test', 'k-bulk-' || n)
    from generate_series(1, 300 - (select count(*)::int from public.email_messages
                                    where tenant_id = '11111111-1111-1111-1111-111111111111' and kind = 'customer')) n
) filled;
select is((select count(*) from public.email_messages
            where tenant_id = '11111111-1111-1111-1111-111111111111' and kind = 'customer'), 300::bigint,
          'the shop is at 300 customer emails today');
select is((select count(*) from public.email_messages where source_key like 'k-bulk-%' and status <> 'queued'), 0::bigint,
          'and every one of them was queued');
select is(pg_temp.enqueue('customer', 'one-more@example.test', 'k-bulk-301'), 'suppressed', 'at most 300 customer emails a day per shop');
select is((select last_error from public.email_messages where source_key = 'k-bulk-301'), 'daily limit reached', 'and says why');
update public.tenants set is_demo = false where id = '22222222-2222-2222-2222-222222222222';
select is(pg_temp.enqueue('customer', 'one-more@example.test', 'k-bulk-b', '22222222-2222-2222-2222-222222222222'), 'queued',
          'another shop has its own 300');
update public.tenants set is_demo = true where id = '22222222-2222-2222-2222-222222222222';
-- The rest of this file sends as if the day were quiet.
delete from public.email_messages where source_key like 'k-bulk-%';

-- ---------------------------------------------------------------------------
-- 3. Reply-to: confirmed through the link before use
-- ---------------------------------------------------------------------------
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select throws_ok($q$select pg_temp.as_role('authenticated', $$insert into public.email_settings (tenant_id, reply_to_email, reply_to_confirmed_at) values ('11111111-1111-1111-1111-111111111111', 'x@example.test', now()) returning 'x'$$)$q$,
                 '42501', null, 'the owner cannot set a confirmed reply-to directly');
select is(pg_temp.as_role('authenticated', $$select public.email_request_reply_to('Shop@Example.test', repeat('a', 64))::text$$),
          '', 'the owner asks for a reply-to address');
select is(pg_temp.as_role('authenticated', $$update public.email_settings set alert_low_stock = false returning alert_low_stock::text$$),
          'false', 'and switches an alert off');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select throws_ok($q$select pg_temp.as_role('authenticated', $$select public.email_request_reply_to('x@example.test', repeat('b', 64))::text$$)$q$,
                 '42501', null, 'staff cannot');
select set_config('request.jwt.claims', '', true);
select is(pg_temp.enqueue('customer', 'before@example.test', 'k-before'), 'queued', 'queued before the confirmation');
select is((select reply_to from public.email_messages where source_key = 'k-before'), null, 'has no reply-to yet');
select is(pg_temp.worker($q$select public.email_confirm_reply_to(repeat('f', 64))$q$), null, 'an unknown link does nothing');
select isnt(pg_temp.worker($q$select public.email_confirm_reply_to(repeat('a', 64))$q$), null, 'the link confirms it');
select is(pg_temp.worker($q$select public.email_confirm_reply_to(repeat('a', 64))$q$), null, 'once');
select is(pg_temp.enqueue('customer', 'after@example.test', 'k-after'), 'queued', 'queued after the confirmation');
select is((select reply_to from public.email_messages where source_key = 'k-after'), 'shop@example.test',
          'carries the confirmed reply-to');
update public.email_settings set reply_to_pending = 'late@example.test', reply_to_token_hash = repeat('c', 64),
       reply_to_requested_at = now() - interval '2 days'
 where tenant_id = '11111111-1111-1111-1111-111111111111';
select is(pg_temp.worker($q$select public.email_confirm_reply_to(repeat('c', 64))$q$), null, 'a link older than a day is refused');

-- ---------------------------------------------------------------------------
-- 4. Events: deposit received and production started
-- ---------------------------------------------------------------------------
insert into public.invoices (id, tenant_id, customer_name, subtotal_cents, tax_cents, total_cents)
values ('e6000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Email test', 1000, 0, 1000);
insert into public.payments (tenant_id, invoice_id, kind, method, amount_cents, currency)
values ('11111111-1111-1111-1111-111111111111', 'e6000000-0000-0000-0000-000000000001', 'payment', 'cash', 300, 'USD');
select is((select count(*)::int from public.automation_events where event_type = 'deposit_received'
             and payload ->> 'invoice_id' = 'e6000000-0000-0000-0000-000000000001'), 1, 'a part payment is a deposit');
update public.invoices set production_started_at = now() where id = 'e6000000-0000-0000-0000-000000000001';
select is((select count(*)::int from public.automation_events where event_type = 'production_started'
             and payload ->> 'invoice_id' = 'e6000000-0000-0000-0000-000000000001'), 1, 'production started');
insert into public.payments (tenant_id, invoice_id, kind, method, amount_cents, currency)
values ('11111111-1111-1111-1111-111111111111', 'e6000000-0000-0000-0000-000000000001', 'payment', 'cash', 700, 'USD');
select is((select count(*)::int from public.automation_events where event_type = 'deposit_received'
             and payload ->> 'invoice_id' = 'e6000000-0000-0000-0000-000000000001'), 1, 'the payment that completes it is not a deposit');

-- ---------------------------------------------------------------------------
-- 5. Claiming events and sending
-- ---------------------------------------------------------------------------
insert into public.automation_events (tenant_id, event_type, payload, created_at)
values ('11111111-1111-1111-1111-111111111111', 'stock_low', '{}', now() - interval '3 days');
select is((select count(*)::int from pg_temp.as_role('email_worker', 'select count(*)::text from public.email_claim_events(500)') x),
          1, 'claiming works');
select is((select count(*)::int from public.automation_events where created_at < now() - interval '2 days' and processed_at is null),
          0, 'events older than two days are closed without email');
select is((select (context -> 'invoice' ->> 'total_cents') from public.email_claim_events(500)
            where payload ->> 'invoice_id' = 'e6000000-0000-0000-0000-000000000001' and event_type = 'production_started'),
          '1000', 'a claimed event carries what the email needs');

update public.email_messages set status = 'suppressed' where source_key <> 'k-alert';
select is(pg_temp.worker($q$select count(*)::text from public.email_claim_messages(10)$q$), '1', 'one due email is claimed');
select is((select status || '/' || attempts from public.email_messages where source_key = 'k-alert'), 'sending/1',
          'marked sending, first attempt');
select is(pg_temp.worker($q$select count(*)::text from public.email_claim_messages(10)$q$), '0', 'and not claimed twice');
select pg_temp.worker(format($q$select public.email_mark_failed(%L, 'timeout', now() - interval '1 second')::text$q$,
                             (select id from public.email_messages where source_key = 'k-alert')));
select is((select status from public.email_messages where source_key = 'k-alert'), 'queued', 'a temporary failure is queued again');
select pg_temp.worker($q$select count(*)::text from public.email_claim_messages(10)$q$);
select pg_temp.worker(format($q$select public.email_mark_sent(%L, 're_msg_1')::text$q$,
                             (select id from public.email_messages where source_key = 'k-alert')));
select is((select status || '/' || attempts || '/' || provider_message_id from public.email_messages where source_key = 'k-alert'),
          'sent/2/re_msg_1', 'sent on the second attempt');
-- A worker died mid-send: after 10 minutes the email is queued again.
update public.email_messages set status = 'sending', next_attempt_at = now() - interval '11 minutes' where source_key = 'k-after';
select is(pg_temp.worker($q$select count(*)::text from public.email_claim_messages(10)$q$), '1', 'a stuck email is retried');

-- ---------------------------------------------------------------------------
-- 6. The owner reads the log; nobody with an app role writes it
-- ---------------------------------------------------------------------------
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'owner');
select isnt(pg_temp.as_role('authenticated', 'select count(*)::text from public.email_messages'), '0', 'the owner reads the log');
select throws_ok($q$select pg_temp.as_role('authenticated', $$insert into public.email_messages (tenant_id, kind, template, source_key, recipient, from_name, subject, html_body, text_body) values ('11111111-1111-1111-1111-111111111111', 'test', 'x', 'x', 'a@b.co', 'x', 'x', 'x', 'x') returning 'x'$$)$q$,
                 '42501', null, 'nor writes it');
select pg_temp.set_claims('11111111-1111-1111-1111-111111111111', 'staff');
select is(pg_temp.as_role('authenticated', 'select count(*)::text from public.email_messages'), '0', 'staff see no email');
select pg_temp.set_claims('22222222-2222-2222-2222-222222222222', 'owner');
select is(pg_temp.as_role('authenticated', $$select count(*)::text from public.email_messages where tenant_id = '11111111-1111-1111-1111-111111111111'$$),
          '0', 'another shop''s owner sees none');

select * from finish();
rollback;
