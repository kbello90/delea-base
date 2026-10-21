-- App users (any app_role) may delete only quotes. A paid invoice is a financial record:
-- correct it with cancel/refund plus a new invoice. Service role exempt.
create function public.invoices_delete_quote_only_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (select auth.jwt() ->> 'app_role') is not null and old.order_status <> 'quote' then
    raise exception 'only quotes can be deleted; cancel or refund this invoice instead'
      using errcode = 'insufficient_privilege';
  end if;
  return old;
end;
$$;

create trigger invoices_05_delete_quote_only
  before delete on public.invoices
  for each row execute function public.invoices_delete_quote_only_guard();

revoke all on function public.invoices_delete_quote_only_guard() from public, anon, authenticated;
