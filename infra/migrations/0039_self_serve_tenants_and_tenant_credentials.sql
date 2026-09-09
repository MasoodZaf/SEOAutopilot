BEGIN;

-- Two changes, one purpose: let somebody who is not us run this on their own
-- domain, with their own provider accounts, without seeing anything of ours.
--
-- Until now a tenant could only come into being by an operator running SQL by
-- hand -- `bootstrap_owner` invites the first owner of a tenant that must
-- already exist, and the only code that ever constructed one was the
-- development-only local pilot route, hardcoded to slug 'codearc-pilot'. So
-- the only way to give a second person access was to invite them into ours,
-- which is the opposite of isolating them.
--
-- And every tenant borrowed *our* Google OAuth client and *our* GitHub App
-- from the process environment. That works for an estate we own. It is wrong
-- for anybody else: their consent screen names our application, their API
-- calls spend our project's quota, and revoking one tenant's access means
-- revoking everyone's. A credential that belongs to a tenant belongs in a
-- row, scoped like every other thing a tenant owns.

-- Provenance, and the thing the per-user cap is counted against. Nullable
-- because every tenant that existed before this migration was made by an
-- operator, and inventing a creator for them would be a lie in a column
-- people will read as fact.
ALTER TABLE tenant ADD COLUMN created_by uuid REFERENCES app_user(id);

-- A tenant's own credential for a third-party provider.
--
-- Deliberately not `connector_secret`: that table is keyed on a connector,
-- which is per *site*, and these are per tenant and exist before any site
-- does. You cannot authorize Search Console for your first site until the
-- OAuth client that authorization runs through already exists.
CREATE TABLE tenant_credential (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  provider text NOT NULL CHECK(provider IN('google_oauth_client','github_app')),
  -- The non-secret half: a Google client id, a GitHub app id and slug. Kept
  -- in the clear on purpose, so listing what a tenant has configured -- and
  -- showing them which client id is in use -- never decrypts anything.
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- The secret half, sealed the same way connector secrets are: AES-GCM under
  -- the deployment's key, with the tenant, provider and key version bound in
  -- as additional authenticated data so a row cannot be replayed into another
  -- tenant by moving it.
  ciphertext bytea NOT NULL,
  nonce bytea NOT NULL,
  aad_hash text NOT NULL,
  key_version text NOT NULL,
  created_by uuid REFERENCES app_user(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  -- Rotation supersedes rather than overwrites, so an authorization that is
  -- mid-flight against the old client can still be reasoned about afterwards.
  revoked_at timestamptz
);

-- One live credential per provider per tenant. Two would mean an
-- authorization started under one client and finished under another, which
-- fails at the token exchange with an error nobody can act on.
CREATE UNIQUE INDEX tenant_credential_live_idx
  ON tenant_credential(tenant_id, provider)
  WHERE revoked_at IS NULL;
CREATE INDEX tenant_credential_tenant_idx ON tenant_credential(tenant_id, provider);

-- ENABLE *and* FORCE, for the reason 0027 and 0035 both record: the services
-- own this table, and an owner bypasses its own policies unless forced. A new
-- table holding other people's OAuth client secrets is the last place to get
-- that wrong.
ALTER TABLE tenant_credential ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_credential FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_credential_tenant_isolation ON tenant_credential
  USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

-- The OAuth callback needs the tenant's client secret to exchange the code,
-- and arrives from Google with no session. It gets no new exception here: the
-- callback resolves the tenant from the state row first -- which is what
-- `connector_oauth_state_callback_lookup` in 0027 is for -- and sets
-- app.tenant_id from that row before reading anything else. By the time this
-- table is touched there is a tenant scope, so the policy above applies
-- normally.

-- A verification challenge that expires in thirty minutes assumes the person
-- can publish a TXT record within thirty minutes, which is true through a
-- Cloudflare API token and false almost everywhere else. Registrars that make
-- you edit a zone by hand routinely take longer than that to serve the new
-- record, so the challenge expired before the DNS it was waiting for existed,
-- and the only recovery was to start again and lose the race again.
--
-- The token is 32 random bytes, single-use, stored as a SHA-256 hash, and
-- checked against a record only the domain's controller can publish. A longer
-- window costs nothing it was protecting.
--
-- This default is only a backstop: `SiteService.create_verification_challenge`
-- supplies `expires_at` on every insert, so the column default is never what
-- decides. `VERIFICATION_CHALLENGE_LIFETIME` in `services/sites.py` is, and the
-- two are deliberately the same number. Changing this line alone changes
-- nothing anybody would observe -- which is exactly what happened when it was
-- tried, and is why the constant now has a test.
ALTER TABLE site_verification_challenge
  ALTER COLUMN expires_at SET DEFAULT now() + interval '24 hours';

COMMIT;
