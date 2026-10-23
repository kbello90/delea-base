-- Expenses slice (TRD §6.8):
--   1. category becomes a fixed list (enum), required. Legacy text maps through
--      expense_category_from_legacy(): 'services' -> professional_services; null, empty or
--      unknown -> other.
--   2. receipt_url (free text from the client, null everywhere) is replaced by a private
--      receipt: an object path in the expense-receipts bucket plus its type, size and time.
--   3. The expense-receipts bucket: private, 10 MB, images and PDF, no client access.

-- ---------------------------------------------------------------------------
-- 1. Categories
-- ---------------------------------------------------------------------------
create type public.expense_category as enum (
  'materials', 'packaging', 'shipping', 'marketing', 'platform_fees', 'software', 'equipment',
  'rent', 'utilities', 'phone', 'insurance', 'professional_services', 'taxes_licenses',
  'travel', 'meals', 'other'
);

-- The legacy mapping, kept as a function so it can be tested (pgTAP) on data the seed no
-- longer has.
create function public.expense_category_from_legacy(p_category text)
returns public.expense_category
language sql
immutable
set search_path = ''
as $$
  select case
    when lower(btrim(p_category)) = 'services' then 'professional_services'::public.expense_category
    when lower(btrim(p_category)) in (
      select unnest(enum_range(null::public.expense_category))::text)
      then lower(btrim(p_category))::public.expense_category
    else 'other'::public.expense_category
  end;
$$;
revoke all on function public.expense_category_from_legacy(text) from public, anon, authenticated;

-- ai.expense_summary reads the column, so it is dropped and recreated around the type change
-- (same definition; category now reads as the enum's text).
drop view ai.expense_summary;

alter table public.expenses
  alter column category type public.expense_category
  using public.expense_category_from_legacy(category);
alter table public.expenses alter column category set default 'other';
alter table public.expenses alter column category set not null;

create view ai.expense_summary as
  select
    date_trunc('month', e.expense_date)::date as month,
    e.category,
    count(*)::integer as expense_count,
    sum(e.amount_cents)::bigint as total_cents,
    coalesce(sum(e.amount_cents) filter (where e.is_tax_deductible), 0)::bigint as deductible_cents
  from public.expenses e
  where e.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
  group by 1, 2;

-- The same grants as migration 10 gave it: select for ai_readonly only.
revoke all on ai.expense_summary from public, anon, authenticated;
grant select on ai.expense_summary to ai_readonly;

-- ---------------------------------------------------------------------------
-- 2. Receipts
-- ---------------------------------------------------------------------------
alter table public.expenses drop column receipt_url;
alter table public.expenses
  add column receipt_path text,
  add column receipt_content_type text,
  add column receipt_size_bytes integer,
  add column receipt_uploaded_at timestamptz;

alter table public.expenses
  -- Only under the row's own tenant and expense: {tenant_id}/{expense_id}/{uuid}.{ext}.
  add constraint expenses_receipt_path_own check (
    receipt_path is null
    or receipt_path ~ ('^' || tenant_id::text || '/' || id::text
                       || '/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(jpg|png|webp|pdf)$')),
  add constraint expenses_receipt_all_or_none check (
    (receipt_path is null) = (receipt_content_type is null)
    and (receipt_path is null) = (receipt_size_bytes is null)
    and (receipt_path is null) = (receipt_uploaded_at is null)),
  add constraint expenses_receipt_type check (
    receipt_content_type is null
    or receipt_content_type in ('image/jpeg', 'image/png', 'image/webp', 'application/pdf')),
  add constraint expenses_receipt_size check (
    receipt_size_bytes is null or receipt_size_bytes between 1 and 10485760);

-- ---------------------------------------------------------------------------
-- 3. The private bucket, with no client access
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types) values
  ('expense-receipts', 'expense-receipts', false, 10485760,
   '{image/jpeg,image/png,image/webp,application/pdf}');

-- RESTRICTIVE, so no permissive policy added later can open the bucket to clients. Uploads
-- go through signed upload URLs the backend issues; reads through short-lived signed URLs.
create policy expense_receipts_no_client_insert on storage.objects
  as restrictive for insert to anon, authenticated
  with check (bucket_id <> 'expense-receipts');
create policy expense_receipts_no_client_update on storage.objects
  as restrictive for update to anon, authenticated
  using (bucket_id <> 'expense-receipts')
  with check (bucket_id <> 'expense-receipts');
create policy expense_receipts_no_client_delete on storage.objects
  as restrictive for delete to anon, authenticated
  using (bucket_id <> 'expense-receipts');
create policy expense_receipts_no_client_select on storage.objects
  as restrictive for select to anon, authenticated
  using (bucket_id <> 'expense-receipts');
