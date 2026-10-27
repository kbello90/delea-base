-- Slice D1: two more automation triggers (TRD §6.16). Their own migration: a new enum value
-- can't be used in the transaction that adds it.
--   production_started  a quote holding a deposit went into production
--   deposit_received    a payment left part of the total still due
-- ("New wholesale customer" was never a trigger; nothing to remove.)
alter type public.automation_trigger add value if not exists 'production_started';
alter type public.automation_trigger add value if not exists 'deposit_received';
