-- current_tenant_currency(): the caller's tenant currency for owner, staff and cpa, without
-- exposing company_settings (EIN, Stripe account) to staff or cpa. Same style as
-- current_tax_rate_bps() (migration 20261020001200), plus a membership check.
--
-- Membership is "a tenant_members row exists" (the table has no status or revoked column);
-- the access token hook copies tenant_id and app_role from such a row. Like the hook, this
-- does not look at tenants.is_active.

create function public.current_tenant_currency()
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  -- Claims come from the caller's verified JWT (request.jwt.claims), read with auth.jwt(),
  -- the same way the RLS policies read them. SECURITY DEFINER doesn't change these.
  v_app_role text := (select auth.jwt() ->> 'app_role');
  v_tenant_text text := nullif((select auth.jwt() ->> 'tenant_id'), '');
  v_tenant_id uuid;
  v_currency text;
begin
  if v_app_role is null or v_app_role not in ('owner', 'staff', 'cpa') then
    raise exception 'only owner, staff or cpa can read the currency'
      using errcode = 'insufficient_privilege';
  end if;
  if v_tenant_text is null then
    raise exception 'no tenant in the token' using errcode = 'insufficient_privilege';
  end if;
  begin
    v_tenant_id := v_tenant_text::uuid;
  exception when invalid_text_representation then
    -- A malformed claim is an authorization failure (42501), not a data error (22P02).
    raise exception 'the token''s tenant is not valid' using errcode = 'insufficient_privilege';
  end;

  -- The token's user must still hold that membership with that role: a token issued before
  -- the membership was removed or the role changed is refused here, although still valid.
  if not exists (
    select 1
      from public.tenant_members m
     where m.user_id = (select auth.uid())
       and m.tenant_id = v_tenant_id
       and m.app_role::text = v_app_role
  ) then
    raise exception 'not a member of this tenant with this role'
      using errcode = 'insufficient_privilege';
  end if;

  select cs.currency::text into v_currency
    from public.company_settings cs
   where cs.tenant_id = v_tenant_id;
  if not found then
    raise exception 'company settings are missing for this tenant';  -- P0001
  end if;
  return v_currency;
end;
$$;
revoke all on function public.current_tenant_currency() from public, anon, authenticated, service_role;
grant execute on function public.current_tenant_currency() to authenticated;
