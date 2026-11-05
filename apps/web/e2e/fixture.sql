-- Local precondition for review-flow.spec.ts: the pilot operator is the sole
-- owner of the codearc-pilot workspace, so the one-approver self-review path
-- is reachable. Local databases only; idempotent.
INSERT INTO app_user (id, subject, issuer, email, email_normalized)
VALUES ('019d0000-0000-7000-8000-00000000c002', 'local-pilot-operator', 'https://local.pilot', 'operator@local.pilot', 'operator@local.pilot')
ON CONFLICT (id) DO NOTHING;
INSERT INTO tenant_membership (tenant_id, user_id, role, status)
VALUES ('019d0000-0000-7000-8000-00000000c001', '019d0000-0000-7000-8000-00000000c002', 'owner', 'active')
ON CONFLICT DO NOTHING;
