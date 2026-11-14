-- Which Stripe mode (sandbox or live) the stored customer and subscription belong to.
--
-- Stripe ids do not say which mode they come from, and a sandbox customer id is
-- refused by the live API. Without this, a workspace that ran a test checkout
-- would be stuck after the switch to live keys: Subscribe and the portal would
-- fail on the old customer, and a test subscription's 'active' status would keep
-- granting access that nobody pays for. See ADR-010.
--
-- NULL means no Stripe object yet, or one stored before this column; it is
-- trusted as the current mode. Production had no Stripe keys before this, so
-- no row there holds a Stripe id.
--
-- Additive. Rollback: ALTER TABLE tenant_billing DROP COLUMN stripe_livemode;
BEGIN;

ALTER TABLE tenant_billing ADD COLUMN stripe_livemode boolean;

COMMIT;
