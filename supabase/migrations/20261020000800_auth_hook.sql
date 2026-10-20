-- Custom access token hook (TRD §2.2). Adds tenant_id and app_role claims from tenant_members.
-- The claim is app_role, never "role": Supabase reserves the top-level role claim for the
-- Postgres role (authenticated / anon), and this function leaves it untouched.
-- Enabled in supabase/config.toml under [auth.hook.custom_access_token].

create function public.custom_access_token_hook(event jsonb)
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
    where tm.user_id = (event ->> 'user_id')::uuid
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

grant usage on schema public to supabase_auth_admin;
grant execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin;
revoke execute on function public.custom_access_token_hook(jsonb) from authenticated, anon, public;
