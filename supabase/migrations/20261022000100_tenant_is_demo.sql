-- tenants.is_demo: a demo tenant (the DELEA Showcase). Anything that would reach the outside
-- world (the future automation worker sending email, Stripe) must skip demo tenants.
--
-- Only an administrative connection (the showcase loader, as postgres) sets it. No app role
-- may write it, owner included. Today authenticated can't update or insert tenants at all
-- (tenants has a select policy only), anon and ai_readonly have no grants, and service_role
-- bypasses RLS. The trigger below refuses setting or changing is_demo for every role except
-- postgres and supabase_admin, so it stays unwritable for service_role now and for app roles
-- even if a tenants update policy (say, for the name) is added later.

alter table public.tenants add column is_demo boolean not null default false;

create function public.tenants_is_demo_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if current_user in ('postgres', 'supabase_admin') then
    return new;
  end if;
  if (tg_op = 'INSERT' and new.is_demo)
     or (tg_op = 'UPDATE' and new.is_demo is distinct from old.is_demo) then
    raise exception 'is_demo can only be set by an administrative connection'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;
revoke all on function public.tenants_is_demo_guard() from public, anon, authenticated;

create trigger tenants_10_is_demo_guard
  before insert or update on public.tenants
  for each row execute function public.tenants_is_demo_guard();
