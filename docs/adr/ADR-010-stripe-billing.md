# ADR-010: One Stripe plan, mirrored locally and off by default

## Status

Accepted 2026-11-07.

## Decision

Each workspace has one subscription to one Stripe price, with a 14-day trial that needs no card.
The price lives in Stripe, so it can change without a release. Workspaces that existed before
billing are comped.

- Stripe Checkout and the customer portal handle cards. We store ids, status and dates only.
- Access is decided from the local `tenant_billing` row, so a Stripe outage locks nobody out.
- A signed webhook is the only thing that changes subscription status. The signature is checked,
  each event id applies once, an older event cannot undo a newer one, and an event naming another
  customer is ignored.
- `BILLING_ENFORCED` is separate from configuration, so a checkout can be tested first. The API will
  not start with enforcement on and no way to pay.
- Lapsed workspaces keep read access. Crawls, deploys, publishing, the assistant, drafting and
  scheduled routines stop with `subscription_required`.

## Consequences

Deleting a workspace does not cancel its Stripe subscription; an operator cancels it first. The
refund position is not in the terms and needs a decision with legal review.

## Rollback

Set `BILLING_ENFORCED=false`. Tables can then be dropped per migration 0059.
