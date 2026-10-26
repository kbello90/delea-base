-- Team slice (TRD §6.6, §6.10):
--   1. tenant_members.invited_by no longer blocks deleting the inviter's Auth user.
--   2. is_current_member(): the membership re-check. The API runs it once per request
--      (user_session, ai_session); current_tax_rate_bps() and current_tenant_currency() run it
--      too. A token whose (user, tenant, role) no longer matches a membership of an active
--      tenant is refused, although it is still valid.
--   3. The access token hook skips inactive tenants.
--   4. tenant_members guard for app roles: no members changes in demo tenants, no new owners
--      (invite or promotion), only the role can change, and a tenant keeps at least one owner.
--   5. tenant_member_list() and account_memberships(): what the owner's Team page needs from
--      auth.users (email, invite status), without exposing auth.users.
--   6. password_reset_attempts: abuse limits for the public password-reset endpoint.

-- ---------------------------------------------------------------------------
-- 1. invited_by
-- ---------------------------------------------------------------------------
alter table public.tenant_members
  drop constraint tenant_members_invited_by_fkey,
  add constraint tenant_members_invited_by_fkey
    foreign key (invited_by) references auth.users (id) on delete set null;

-- ---------------------------------------------------------------------------
-- 2. Membership re-check
-- ---------------------------------------------------------------------------
-- Two primary-key lookups: tenant_members (user_id, tenant_id) and tenants (id).
-- Not granted to any app role: the API calls it on its own connection role before switching
-- to `authenticated`, and the SECURITY DEFINER functions below call it as their owner.
create function public.is_current_member(p_user uuid, p_tenant uuid, p_role text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
      from public.tenant_members m
      join public.tenants t on t.id = m.tenant_id
     where m.user_id = p_user
       and m.tenant_id = p_tenant
       and m.app_role::text = p_role
       and t.is_active);
$$;
revoke all on function public.is_current_member(uuid, uuid, text)
  from public, anon, authenticated, service_role;

create or replace function public.current_tax_rate_bps()
returns integer
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  -- Claims come from the caller's verified JWT (request.jwt.claims), read with auth.jwt(),
  -- the same way the RLS policies read them. SECURITY DEFINER doesn't change these.
  v_app_role text := (select auth.jwt() ->> 'app_role');
  v_tenant_id uuid := nullif((select auth.jwt() ->> 'tenant_id'), '')::uuid;
  v_rate integer;
begin
  if v_app_role is null or v_app_role not in ('owner', 'staff') then
    raise exception 'only owner or staff can read the tax rate'
      using errcode = 'insufficient_privilege';
  end if;
  if v_tenant_id is null then
    raise exception 'no tenant in the token' using errcode = 'insufficient_privilege';
  end if;
  -- A token issued before the membership was removed, the role changed or the tenant was
  -- deactivated is refused here, although still valid.
  if not public.is_current_member((select auth.uid()), v_tenant_id, v_app_role) then
    raise exception 'not a member of this tenant with this role'
      using errcode = 'insufficient_privilege';
  end if;

  select cs.default_tax_rate_bps into v_rate
    from public.company_settings cs
   where cs.tenant_id = v_tenant_id;
  if not found then
    raise exception 'company settings are missing for this tenant';  -- P0001
  end if;
  return v_rate;
end;
$$;

create or replace function public.current_tenant_currency()
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
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

  -- The same re-check as current_tax_rate_bps(), now including tenants.is_active.
  if not public.is_current_member((select auth.uid()), v_tenant_id, v_app_role) then
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

-- ---------------------------------------------------------------------------
-- 3. Access token hook: memberships of inactive tenants give no claims
-- ---------------------------------------------------------------------------
create or replace function public.custom_access_token_hook(event jsonb)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  claims jsonb := coalesce(event -> 'claims', '{}'::jsonb);
  member record;
begin
  -- TODO: users who belong to several tenants get their oldest membership. Add tenant
  -- switching (e.g. a selected tenant in app_metadata) when multi-tenant users are supported.
  select tm.tenant_id, tm.app_role
    into member
    from public.tenant_members tm
    join public.tenants t on t.id = tm.tenant_id
    where tm.user_id = (event ->> 'user_id')::uuid
      and t.is_active
    order by tm.created_at, tm.tenant_id
    limit 1;

  if found then
    claims := claims
      || jsonb_build_object('tenant_id', member.tenant_id, 'app_role', member.app_role);
  else
    claims := claims - 'tenant_id' - 'app_role';
  end if;

  return jsonb_set(event, '{claims}', claims);
end;
$$;

-- ---------------------------------------------------------------------------
-- 4. tenant_members guard
-- ---------------------------------------------------------------------------
-- App users reach tenant_members only as owners (RLS: tenant_members_owner_all), so the
-- tenant row and the tenant's members read below are visible to the caller. If they weren't,
-- the reads come back empty and the guard refuses (is_demo unknown, owner count 0).
-- Administrative connections are trusted: provisioning (postgres), tenant delete cascades and
-- Auth user delete cascades (supabase_auth_admin).
create function public.tenant_members_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_tenant uuid := coalesce(new.tenant_id, old.tenant_id);
  v_is_demo boolean;
  v_owners integer;
begin
  if current_user in ('postgres', 'supabase_admin', 'supabase_auth_admin') then
    return coalesce(new, old);
  end if;

  -- One change at a time per tenant, so two owners can't remove each other at once.
  perform pg_advisory_xact_lock(hashtext('tenant_members:' || v_tenant::text));

  select t.is_demo into v_is_demo from public.tenants t where t.id = v_tenant;
  if not found then
    raise exception 'Not allowed.' using errcode = 'insufficient_privilege';
  end if;
  if v_is_demo then
    raise exception 'Team changes are turned off in the demo.'
      using errcode = 'insufficient_privilege';
  end if;

  if tg_op = 'INSERT' and new.app_role = 'owner' then
    raise exception 'Owners can''t be invited. Invite staff or an accountant.'
      using errcode = 'insufficient_privilege';
  end if;

  if tg_op = 'UPDATE' then
    if new.user_id is distinct from old.user_id
       or new.tenant_id is distinct from old.tenant_id
       or new.invited_by is distinct from old.invited_by
       or new.created_at is distinct from old.created_at then
      raise exception 'Only a member''s role can change.'
        using errcode = 'insufficient_privilege';
    end if;
    if new.app_role = 'owner' and old.app_role <> 'owner' then
      raise exception 'Members can''t be made owners.'
        using errcode = 'insufficient_privilege';
    end if;
  end if;

  if tg_op in ('UPDATE', 'DELETE') and old.app_role = 'owner'
     and (tg_op = 'DELETE' or new.app_role <> 'owner') then
    select count(*) into v_owners
      from public.tenant_members m
     where m.tenant_id = v_tenant and m.app_role = 'owner';
    if v_owners <= 1 then
      raise exception 'A business must keep at least one owner.';  -- P0001: 409
    end if;
  end if;

  return coalesce(new, old);
end;
$$;
revoke all on function public.tenant_members_guard() from public, anon, authenticated;

create trigger tenant_members_10_guard
  before insert or update or delete on public.tenant_members
  for each row execute function public.tenant_members_guard();

-- ---------------------------------------------------------------------------
-- 5. Team page reads (owner only)
-- ---------------------------------------------------------------------------
-- Raises unless the caller's token is a current owner membership; returns the tenant.
create function public.require_current_owner()
returns uuid
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_tenant_text text := nullif((select auth.jwt() ->> 'tenant_id'), '');
  v_tenant_id uuid;
begin
  if (select auth.jwt() ->> 'app_role') is distinct from 'owner' or v_tenant_text is null then
    raise exception 'only the owner can manage the team' using errcode = 'insufficient_privilege';
  end if;
  begin
    v_tenant_id := v_tenant_text::uuid;
  exception when invalid_text_representation then
    raise exception 'the token''s tenant is not valid' using errcode = 'insufficient_privilege';
  end;
  if not public.is_current_member((select auth.uid()), v_tenant_id, 'owner') then
    raise exception 'not a member of this tenant with this role'
      using errcode = 'insufficient_privilege';
  end if;
  return v_tenant_id;
end;
$$;
revoke all on function public.require_current_owner() from public, anon, authenticated, service_role;

-- The caller's team. status: 'invited' until the invite is accepted (email confirmed).
-- invite_expired: the invite link (24 h, auth otp_expiry) has lapsed; resend to get a new one.
create function public.tenant_member_list()
returns table (
  user_id uuid,
  email text,
  app_role public.app_role,
  status text,
  invite_expired boolean,
  invited_at timestamptz,
  last_sign_in_at timestamptz,
  created_at timestamptz
)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_tenant_id uuid := public.require_current_owner();
begin
  return query
    select m.user_id,
           u.email::text,
           m.app_role,
           case when u.email_confirmed_at is null then 'invited' else 'active' end,
           u.email_confirmed_at is null
             and coalesce(u.invited_at, m.created_at) < now() - interval '24 hours',
           u.invited_at,
           u.last_sign_in_at,
           m.created_at
      from public.tenant_members m
      join auth.users u on u.id = m.user_id
     where m.tenant_id = v_tenant_id
     order by m.created_at, u.email;
end;
$$;
revoke all on function public.tenant_member_list() from public, anon, authenticated, service_role;
grant execute on function public.tenant_member_list() to authenticated;

-- Whether an email already has an account, and where it belongs, as far as the owner needs
-- to know: in this tenant, or anywhere else (never which tenant). No row: no account.
create function public.account_memberships(p_email text)
returns table (user_id uuid, confirmed boolean, member_here boolean, member_elsewhere boolean)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_tenant_id uuid := public.require_current_owner();
begin
  return query
    select u.id,
           u.email_confirmed_at is not null,
           exists (select 1 from public.tenant_members m
                    where m.user_id = u.id and m.tenant_id = v_tenant_id),
           exists (select 1 from public.tenant_members m
                    where m.user_id = u.id and m.tenant_id <> v_tenant_id)
      from auth.users u
     where lower(u.email) = lower(trim(p_email));
end;
$$;
revoke all on function public.account_memberships(text) from public, anon, authenticated, service_role;
grant execute on function public.account_memberships(text) to authenticated;

-- ---------------------------------------------------------------------------
-- 6. Password-reset abuse limits
-- ---------------------------------------------------------------------------
-- One row per request to POST /api/v1/auth/password-reset. Emails and IP addresses are stored
-- as SHA-256 hashes only. Written and read by the backend's own connection (public endpoint);
-- no app role has grants. Rows older than a day are deleted as new ones arrive.
create table public.password_reset_attempts (
  id bigint generated by default as identity primary key,
  email_hash text not null,
  ip_hash text not null,
  created_at timestamptz not null default now()
);
create index password_reset_attempts_email on public.password_reset_attempts (email_hash, created_at);
create index password_reset_attempts_ip on public.password_reset_attempts (ip_hash, created_at);
alter table public.password_reset_attempts enable row level security;
revoke all on public.password_reset_attempts from anon, authenticated;
