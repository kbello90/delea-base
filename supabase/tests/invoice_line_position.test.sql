begin;
create extension if not exists pgtap with schema extensions;
select * from no_plan();

-- Migration 20261022000300: invoice_line_items.position (1-based entry order per invoice).
-- Everything is rolled back.

select has_column('public', 'invoice_line_items', 'position', 'invoice_line_items.position exists');
select col_not_null('public', 'invoice_line_items', 'position', 'position is not null');
select col_type_is('public', 'invoice_line_items', 'position', 'integer', 'position is an integer');
select has_trigger('public', 'invoice_line_items', 'invoice_line_items_10_position',
                   'a missing position is assigned on insert');

-- Existing (seed) lines were numbered 1..n within each invoice.
select is((select count(*) from (
             select invoice_id from public.invoice_line_items
              where tenant_id in ('11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222')
              group by invoice_id
             having min(position) <> 1 or max(position) <> count(*)) bad),
          0::bigint, 'every seed invoice''s lines are numbered 1..n');

-- A quote with lines inserted without a position: 1, 2, 3 in insert order.
insert into public.invoices (id, tenant_id, customer_name)
values ('f2000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Position test');
insert into public.invoice_line_items (id, invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents)
values ('f2100000-0000-0000-0000-00000000000c', 'f2000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'C', 1, 100, 100);
insert into public.invoice_line_items (id, invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents)
values ('f2100000-0000-0000-0000-00000000000a', 'f2000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'A', 1, 100, 100);
insert into public.invoice_line_items (id, invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents)
values ('f2100000-0000-0000-0000-00000000000b', 'f2000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'B', 1, 100, 100);
select results_eq(
  $q$select description from public.invoice_line_items
      where invoice_id = 'f2000000-0000-0000-0000-000000000001' order by position$q$,
  $q$values ('C'), ('A'), ('B')$q$,
  'lines without a position keep their insert order, not id order');

-- An explicit position is kept; a duplicate or non-positive one is refused.
insert into public.invoices (id, tenant_id, customer_name)
values ('f2000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'Position test 2');
insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents, position)
values ('f2000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'Second', 1, 100, 100, 2),
       ('f2000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'First', 1, 100, 100, 1);
select results_eq(
  $q$select description, position from public.invoice_line_items
      where invoice_id = 'f2000000-0000-0000-0000-000000000002' order by position$q$,
  $q$values ('First', 1), ('Second', 2)$q$,
  'explicit positions are kept');
select throws_ok(
  $q$insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents, position)
     values ('f2000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'Dup', 1, 100, 100, 2)$q$,
  '23505', null, 'two lines of one invoice cannot share a position');
select throws_ok(
  $q$insert into public.invoice_line_items (invoice_id, tenant_id, description, qty, unit_price_cents, line_total_cents, position)
     values ('f2000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'Zero', 1, 100, 100, 0)$q$,
  '23514', null, 'a position starts at 1');

select * from finish();
rollback;
