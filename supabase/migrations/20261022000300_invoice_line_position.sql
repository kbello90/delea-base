-- Invoice lines keep their entry order: invoice_line_items.position, 1-based within an invoice.
-- Existing rows are numbered in id order (their original order can't be recovered). Writers
-- that set no position (seed, older callers) get the next one for that invoice.

alter table public.invoice_line_items add column position integer;

update public.invoice_line_items li
   set position = n.rn
  from (select id, row_number() over (partition by invoice_id order by id) as rn
          from public.invoice_line_items) n
 where n.id = li.id;

alter table public.invoice_line_items alter column position set not null;
alter table public.invoice_line_items add constraint invoice_line_items_position_positive
  check (position >= 1);
alter table public.invoice_line_items add constraint invoice_line_items_invoice_position_key
  unique (invoice_id, position);

create function public.invoice_line_items_assign_position()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.position is null then
    select coalesce(max(li.position), 0) + 1 into new.position
      from public.invoice_line_items li
     where li.invoice_id = new.invoice_id;
  end if;
  return new;
end;
$$;

-- Fires before the quote-only guard (BEFORE triggers run in name order).
create trigger invoice_line_items_10_position
  before insert on public.invoice_line_items
  for each row execute function public.invoice_line_items_assign_position();

revoke all on function public.invoice_line_items_assign_position() from public, anon, authenticated;
