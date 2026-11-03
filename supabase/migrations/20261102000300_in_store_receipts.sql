-- Slice C1: selling in the shop (TRD §6.16).
--   1. invoices.source takes 'in_store': sales and deposits taken on the New sale page.
--      Existing New-sale invoices stay 'admin' (nothing is backfilled).
--   2. company_settings.receipt_layout ('letter' default, '80mm', '58mm') and
--      receipt_footer: the shop's default receipt; the cashier can pick another per receipt.
--   3. receipt_settings(): what a receipt prints at the top and bottom (business name,
--      address, layout, footer) for owner and staff, without exposing company_settings (EIN)
--      to staff.
--   4. personalization_snapshot(): the website's personalization checks for one line, so
--      lines made in the admin store answers the same way storefront orders do.

-- ---------------------------------------------------------------------------
-- 1. Source
-- ---------------------------------------------------------------------------
alter table public.invoices drop constraint invoices_source_check;
alter table public.invoices
  add constraint invoices_source_check check (source in ('admin', 'storefront', 'in_store'));

-- ---------------------------------------------------------------------------
-- 2. Receipt settings
-- ---------------------------------------------------------------------------
-- The footer: 1..300 characters after trimming, line breaks allowed, no other control
-- characters (e.g. "Thank you! Exchanges within 30 days.").
alter table public.company_settings
  add column receipt_layout text not null default 'letter',
  add column receipt_footer text,
  add constraint company_settings_receipt_layout check (receipt_layout in ('letter', '80mm', '58mm')),
  add constraint company_settings_receipt_footer check (
    receipt_footer = btrim(receipt_footer)
    and char_length(receipt_footer) between 1 and 300
    and replace(receipt_footer, E'\n', '') !~ '[[:cntrl:]]');

-- ---------------------------------------------------------------------------
-- 3. receipt_settings(): owner and staff, same checks as current_tenant_currency()
-- ---------------------------------------------------------------------------
create function public.receipt_settings()
returns table (business_name text, address text, receipt_layout text, receipt_footer text)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_app_role text := (select auth.jwt() ->> 'app_role');
  v_tenant_text text := nullif((select auth.jwt() ->> 'tenant_id'), '');
  v_tenant_id uuid;
begin
  if v_app_role is null or v_app_role not in ('owner', 'staff') then
    raise exception 'only owner or staff can print receipts'
      using errcode = 'insufficient_privilege';
  end if;
  if v_tenant_text is null then
    raise exception 'no tenant in the token' using errcode = 'insufficient_privilege';
  end if;
  begin
    v_tenant_id := v_tenant_text::uuid;
  exception when invalid_text_representation then
    raise exception 'the token''s tenant is not valid' using errcode = 'insufficient_privilege';
  end;
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

  return query
    select b.business_name, c.address, c.receipt_layout, c.receipt_footer
      from public.company_settings c
      join public.brand_config b on b.tenant_id = c.tenant_id
     where c.tenant_id = v_tenant_id;
  if not found then
    raise exception 'company settings are missing for this tenant';  -- P0001
  end if;
end;
$$;
revoke all on function public.receipt_settings() from public, anon, authenticated, service_role;
grant execute on function public.receipt_settings() to authenticated;

-- ---------------------------------------------------------------------------
-- 4. personalization_snapshot(): the storefront's answer checks (storefront_price_cart)
-- ---------------------------------------------------------------------------
-- p_answers: {"<field id>": "answer"} for one line of p_product_id. Only that product's
-- fields; required ones answered; choices from the list; text within its length and without
-- control characters; answers trimmed, blank counts as not answered.
-- Returns {"ok": true, "snapshot": [{"label", "value"}...] or null (nothing answered)} or
-- {"ok": false, "label": <field label or null>, "reason": "invalid" | "unknown" |
-- "required" | "choice" | "length" | "characters"}.
-- Security invoker: the fields are read with the caller's rights (owner and staff of the
-- product's tenant), so another shop's product has no fields and any answer is "unknown".
create function public.personalization_snapshot(p_product_id uuid, p_answers jsonb)
returns jsonb
language plpgsql
stable
set search_path = ''
as $$
declare
  answers jsonb := coalesce(p_answers, '{}');
  field record;
  answer text;
  snapshot jsonb := '[]';
begin
  if jsonb_typeof(answers) <> 'object'
     or exists (select 1 from jsonb_each(answers) a where jsonb_typeof(a.value) <> 'string') then
    return jsonb_build_object('ok', false, 'label', null, 'reason', 'invalid');
  end if;
  if exists (select 1 from jsonb_object_keys(answers) k
              where not exists (select 1 from public.product_personalization_fields f
                                 where f.product_id = p_product_id and f.id::text = k)) then
    return jsonb_build_object('ok', false, 'label', null, 'reason', 'unknown');
  end if;
  for field in
    select f.id, f.label, f.kind, f.required, f.max_length, f.choices
      from public.product_personalization_fields f
     where f.product_id = p_product_id
     order by f.position
  loop
    answer := nullif(btrim(answers ->> field.id::text), '');
    if answer is null then
      if field.required then
        return jsonb_build_object('ok', false, 'label', field.label, 'reason', 'required');
      end if;
    elsif answer ~ '[[:cntrl:]]' then
      return jsonb_build_object('ok', false, 'label', field.label, 'reason', 'characters');
    elsif field.kind = 'choice' and not (answer = any (field.choices)) then
      return jsonb_build_object('ok', false, 'label', field.label, 'reason', 'choice');
    elsif field.kind = 'text' and char_length(answer) > field.max_length then
      return jsonb_build_object('ok', false, 'label', field.label, 'reason', 'length');
    else
      snapshot := snapshot || jsonb_build_array(jsonb_build_object('label', field.label, 'value', answer));
    end if;
  end loop;
  return jsonb_build_object('ok', true,
                            'snapshot', case when jsonb_array_length(snapshot) > 0 then snapshot end);
end;
$$;
revoke all on function public.personalization_snapshot(uuid, jsonb) from public, anon, authenticated, service_role;
grant execute on function public.personalization_snapshot(uuid, jsonb) to authenticated;
