-- Send Email Hook (hosted-demo plan step 4, TRD §6.6): with the hook on, Supabase Auth sends no
-- email itself; it calls POST /api/v1/auth/email-hook, which asks this function whether the
-- email may go out and then sends it through the app's email provider.
--
-- Only the two Auth emails the app uses are ever sent:
--   invite    the shop named by the invite (tenant_id in the invite's data, set by the API:
--             the new user has no membership yet) exists, is active and is not a demo shop;
--   recovery  the account belongs to an active shop and to no demo shop.
-- Every other type (magic link, email change, sign-up, reauthentication, notifications) is
-- refused: the app uses none of them, and a browser holding only the publishable key could ask
-- Auth for some of them directly. A refusal returns its reason; the hook answers 200 and sends
-- nothing.
--
-- Returns one row: decision ('send' or the reason) and, for an invite that may go out, the
-- shop's business name for the subject (read here, not from the user's editable metadata).

create function public.api_auth_email_decision(p_user uuid, p_action text, p_tenant uuid)
returns table (decision text, business_name text)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if p_action is null or p_action not in ('invite', 'recovery') then
    return query select 'type not used'::text, null::text;
    return;
  end if;

  if p_user is null or not exists (select 1 from auth.users u where u.id = p_user) then
    return query select 'no account'::text, null::text;
    return;
  end if;

  if p_action = 'invite' then
    return query
      select case
               when t.id is null or not t.is_active then 'unknown shop'
               when t.is_demo then 'demo'
               else 'send'
             end::text,
             case when t.is_active and not t.is_demo then coalesce(b.business_name, t.name) end::text
        from (select 1) one
        left join public.tenants t on t.id = p_tenant
        left join public.brand_config b on b.tenant_id = t.id;
    return;
  end if;

  -- recovery
  if exists (select 1
               from public.tenant_members m
               join public.tenants t on t.id = m.tenant_id
              where m.user_id = p_user and t.is_demo) then
    return query select 'demo'::text, null::text;
    return;
  end if;
  if not exists (select 1
                   from public.tenant_members m
                   join public.tenants t on t.id = m.tenant_id
                  where m.user_id = p_user and t.is_active) then
    return query select 'no membership'::text, null::text;
    return;
  end if;
  return query select 'send'::text, null::text;
end;
$$;

revoke all on function public.api_auth_email_decision(uuid, text, uuid)
  from public, anon, authenticated, service_role;
grant execute on function public.api_auth_email_decision(uuid, text, uuid) to delea_api;
