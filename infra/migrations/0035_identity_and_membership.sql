BEGIN;

-- Real identity, so the app can run with APP_ENV=production at all.
--
-- Until now there were no users. `core/auth.py` compared one bearer token
-- against an environment variable and returned a hardcoded OWNER, and both it
-- and `config.py` required `app_env == "development"` for that to work. Setting
-- APP_ENV=production therefore did not harden the deployment, it broke it:
-- every authenticated route returned 401. So the public pilot ran in
-- development mode with the perimeter as its only real authentication, which is
-- exactly the outcome the environment guard was written to prevent.
--
-- Three tables replace that. A verified OIDC token proves who someone is; it
-- says nothing about whose data they may see, which is what `tenant_membership`
-- records and `tenant_invitation` is how it comes to exist. Without an
-- invitation the alternatives are first-login-wins or trusting an email domain,
-- and both hand a tenant's data to whoever gets there first.

-- Identity is not tenant-scoped: one person can be invited into several
-- tenants, and the row that proves who they are has to be readable before any
-- tenant is known.
CREATE TABLE app_user (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  issuer text NOT NULL CHECK(char_length(issuer) BETWEEN 8 AND 512),
  subject text NOT NULL CHECK(char_length(subject) BETWEEN 1 AND 255),
  email text NOT NULL CHECK(char_length(email) BETWEEN 3 AND 320),
  -- Lowercased at the application boundary. Matching an invitation is the only
  -- thing email is trusted for, and "Ada@example.com" must not miss an
  -- invitation addressed to "ada@example.com".
  email_normalized text NOT NULL CHECK(email_normalized = lower(email_normalized)),
  display_name text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'active' CHECK(status IN('active','suspended')),
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz,
  -- The provider's subject is the identity. Email is a label a provider may
  -- change, so it cannot be what a returning user is recognised by.
  UNIQUE(issuer,subject),
  UNIQUE(email_normalized)
);

CREATE TABLE tenant_membership (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  user_id uuid NOT NULL REFERENCES app_user(id),
  role text NOT NULL
    CHECK(role IN('owner','admin','seo_manager','editor','developer','viewer')),
  status text NOT NULL DEFAULT 'active' CHECK(status IN('active','suspended')),
  invited_by uuid REFERENCES app_user(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,user_id),
  UNIQUE(id,tenant_id)
);
CREATE INDEX tenant_membership_user_idx ON tenant_membership(user_id,status);
CREATE INDEX tenant_membership_tenant_idx ON tenant_membership(tenant_id,status);

CREATE TABLE tenant_invitation (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  email_normalized text NOT NULL
    CHECK(char_length(email_normalized) BETWEEN 3 AND 320
          AND email_normalized = lower(email_normalized)),
  role text NOT NULL
    CHECK(role IN('owner','admin','seo_manager','editor','developer','viewer')),
  -- Null for the first owner of a tenant, who by definition has nobody to be
  -- invited by. That invitation is made by an operator on the host, not through
  -- the API, and it is the only one without a human behind it.
  invited_by uuid REFERENCES app_user(id),
  expires_at timestamptz NOT NULL,
  accepted_at timestamptz,
  accepted_user_id uuid REFERENCES app_user(id),
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  -- An acceptance names a user or it did not happen.
  CHECK((accepted_at IS NULL) = (accepted_user_id IS NULL)),
  UNIQUE(id,tenant_id)
);
-- One live invitation per address per tenant. A second one would let two roles
-- race to be the one that is accepted.
CREATE UNIQUE INDEX tenant_invitation_open_idx
  ON tenant_invitation(tenant_id,email_normalized)
  WHERE accepted_at IS NULL AND revoked_at IS NULL;
CREATE INDEX tenant_invitation_pending_idx
  ON tenant_invitation(email_normalized,expires_at)
  WHERE accepted_at IS NULL AND revoked_at IS NULL;

-- ENABLE *and* FORCE. Migration 0027 forced every table that existed then, and
-- these are the first tables created since. Enabling alone would leave them
-- open: the services connect as a role that owns these tables, and an owner
-- bypasses its own policies unless they are forced. A new table that looks
-- protected and is not is the precise defect 0027 exists to have fixed.
ALTER TABLE app_user ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_user FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_membership ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_membership FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_invitation ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_invitation FORCE ROW LEVEL SECURITY;

-- Authentication has the same shape of problem the OAuth callback has: the
-- tenant is what the lookup establishes, so the lookup cannot be tenant scoped.
-- It gets the same kind of narrow exception -- a GUC set on a session used by
-- nothing but the auth dependency, on these three tables only. Everything else
-- in the schema stays closed to that session, and the request adopts
-- `app.tenant_id` from the membership it resolves before reading anything.
CREATE POLICY app_user_authentication ON app_user
  USING(current_setting('app.authenticating',true)='on')
  WITH CHECK(current_setting('app.authenticating',true)='on');
CREATE POLICY tenant_membership_authentication ON tenant_membership
  USING(current_setting('app.authenticating',true)='on')
  WITH CHECK(current_setting('app.authenticating',true)='on');
CREATE POLICY tenant_invitation_authentication ON tenant_invitation
  USING(current_setting('app.authenticating',true)='on')
  WITH CHECK(current_setting('app.authenticating',true)='on');

-- Under a tenant scope, a member is visible to their own tenant and no other.
-- The subquery reads `tenant_membership`, which is itself scoped by the policy
-- below, so this cannot be widened by asking it about somebody else's tenant.
CREATE POLICY app_user_tenant_visibility ON app_user
  FOR SELECT
  USING(EXISTS(
    SELECT 1 FROM tenant_membership m
    WHERE m.user_id = app_user.id
      AND m.tenant_id = nullif(current_setting('app.tenant_id',true),'')::uuid
  ));

CREATE POLICY tenant_membership_tenant_isolation ON tenant_membership
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
CREATE POLICY tenant_invitation_tenant_isolation ON tenant_invitation
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

COMMIT;
