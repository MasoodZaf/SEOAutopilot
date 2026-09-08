# Deployment & Pilot Status

Operational record for the hosted pilot. Last updated **2026-09-08**.

This file is the follow-up point: where the system runs, how to operate it, what
the first crawl found, and what is still open. No secrets are recorded here —
only where they live.

---

## 1. Host

| | |
|---|---|
| Provider | Hetzner Cloud, Helsinki (`hel1`) |
| IP | `2.29.6.61` (IPv6 `2a01:4f9:c012:7adb::1`) |
| OS | Ubuntu 26.04 LTS |
| Size | 4 vCPU AMD EPYC · 7.6 GB RAM · 75 GB disk |
| Access | `ssh oryxen` (alias in `~/.ssh/config`, key `~/.ssh/oryxenlabs`, user `root`) |
| Deploy root | `/opt/seo-autopilot` |

Hardening applied: 4 GB swap (`vm.swappiness=10`), `ufw` active (22/80/443 only,
default deny inbound), `fail2ban` on the sshd jail, unattended-upgrades enabled,
SSH key-only (`PermitRootLogin prohibit-password`, `PasswordAuthentication no`).

Verified to survive a reboot: swap, firewall, fail2ban and all seven containers
come back unattended.

## 2. Public endpoint

**https://seo.oryxenlabs.com** — Cloudflare-proxied (orange cloud), Let's Encrypt
certificate obtained by Caddy over HTTP-01 through the proxy, auto-renewing.

Set Cloudflare SSL mode to **Full (strict)**. There is a real certificate on the
origin, so anything less is a downgrade.

| Path | Serves |
|---|---|
| `/` | Public marketing page |
| `/pilot`, `/settings` | Control plane — **gated** (see §5) |
| `/api/*` | API, public only for the Google OAuth callback |

## 3. Running it

The repo has no git remote on the server; code is synced by rsync from the
development machine:

```bash
rsync -az \
  --exclude='.git/' --exclude='node_modules/' --exclude='.next/' \
  --exclude='.turbo/' --exclude='.venv-*/' --exclude='.venv/' --exclude='__pycache__/' \
  --exclude='*.pyc' --exclude='*.tsbuildinfo' \
  --exclude='.ruff_cache/' --exclude='.pytest_cache/' --exclude='._*' \
  --exclude='.env.local' --exclude='.env' --exclude='infra/local/' \
  ./ oryxen:/opt/seo-autopilot/
```

Everything on the host runs through the production overlay:

```bash
cd /opt/seo-autopilot
DC="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

$DC ps                    # status
$DC logs -f <service>     # follow logs
$DC build <service>       # rebuild after an rsync
$DC up -d <service>       # apply a new image
```

Services: `postgres`, `redis`, `api`, `worker`, `crawler`, `web`, `caddy`.
All carry `restart: unless-stopped`. Only `caddy` listens publicly; `web` and
`api` are bound to loopback (`127.0.0.1:3001` and `127.0.0.1:8001`).

### Migrations

The database records what it has applied, and the runner applies what is
missing. Naming the range by hand is no longer necessary and no longer
supported — it was correct exactly as often as somebody counted correctly under
deploy pressure, and both failure modes were quiet.

```bash
$DC exec -T api python -m app.cli.migrate --dry-run \
  --database-url "postgresql://seo_autopilot:$POSTGRES_PASSWORD@postgres:5432/seo_autopilot"
$DC exec -T api python -m app.cli.migrate \
  --database-url "postgresql://seo_autopilot:$POSTGRES_PASSWORD@postgres:5432/seo_autopilot"
```

The URL is the **owning** role, not `seo_autopilot_app` — migrations alter
schema and the application role holds DML only.

The production overlay mounts `infra/migrations` into the API container
read-only, so the runner reads what was rsynced rather than what the image was
built with. Without that, a migration-only deploy runs against the previous
image's copy and reports `would apply 0 migration(s)` — which reads exactly like
"already up to date".

**One-time, on the existing production database.** It has migrations applied and
no ledger, so replaying them would fail on the first `CREATE TABLE`. Adopt what
is already there, then run normally from that point on:

```bash
$DC exec -T api python -m app.cli.migrate --adopt-through 37 --database-url "..."
```

**Adoption is a claim, so check it.** The ledger records which files a database
has *run*; it cannot record whether they had their effect, and `--adopt-through`
records them as applied on your word. That word was wrong by one file on
2026-09-07 — migration `0034` widens two CHECK constraints, had never run on
production, and was adopted as done. Nothing noticed until the GA4 connector
answered 500 on its first real request. After adopting, and as a periodic check:

```bash
$DC exec -T api python -m app.cli.schema_diff --database-url "..."
```

It builds a reference by running every migration into a scratch database and
compares every column and CHECK constraint against the live one, so a constraint
that silently never widened is visible before something trips over it.

It refuses a migration whose file changed after it was applied, naming it: the
repository would otherwise be a wrong description of the database rather than a
stale one. If a migration ran but the process died before the ledger was
written, `--mark-applied <filename>` records that one file.

### What the API publishes to the internet

`handle_path /api/*` proxies the whole API, so anything the application serves
without authentication is public. Until 2026-09-07 that included the OpenAPI
schema (146 KB), the interactive docs, and the Prometheus metrics — no tenant
data, but a complete map of every endpoint plus this deployment's operational
posture, including whether the kill switch is on.

| Path | Now |
|---|---|
| `/api/health` | public, deliberately — liveness |
| `/api/v1/system/readiness` | public; discloses the deployment/autopilot flags. Accepted. |
| `/api/v1/connectors/oauth/callback` | public, necessarily — Google redirects here |
| `/api/metrics` | needs `Authorization: Bearer $METRICS_SCRAPE_TOKEN` |
| `/api/docs`, `/api/openapi.json` | not served unless `API_DOCS_ENABLED=true` |
| everything else under `/api/v1` | authenticated |

Both new settings are **off by default and keyed on their own flag, not on
`APP_ENV`**. The live host runs as `development` until the identity cutover, so
an environment check would have exempted the one deployment that matters — the
same shape of mistake as `OPERATOR_IPS`. `check-perimeter.sh` asserts all of it
hourly.

### Noticing when it is quietly broken

`python -m app.cli.health` counts the things that look like normal operation
from outside and are not: runs leased to a worker that died, an outbox nobody is
draining, connectors that have been asking for re-consent for days, rollbacks
nothing has ever checked, and a backup directory that stopped being written to.

```bash
cp infra/scripts/seo-autopilot-health.* /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now seo-autopilot-health.timer
systemctl --failed          # a failing invariant shows up here
journalctl -u seo-autopilot-health -n 50
```

Hourly. A failing check exits non-zero, so it becomes a failed unit and a
journal entry rather than a number on a dashboard nobody opens. It reads counts
and ages across tenants on the relay role, never content.

### Timeouts

Migration `0038` bounds how long a statement, a lock wait, or an abandoned
transaction can last. Every one of these was `0` before it.

| | `seo_autopilot_app` | `seo_autopilot_relay` |
|---|---|---|
| `statement_timeout` | 60s | 120s |
| `lock_timeout` | 10s | 15s |
| `idle_in_transaction_session_timeout` | 120s | 900s |

Unbounded waits were survivable while nothing took a row lock. Membership
changes now read the row they modify `FOR UPDATE`, so a request that dies
holding its transaction open would otherwise block every later membership change
until somebody noticed and killed the backend.

The relay role's idle limit is deliberately long: the rollback reconciler holds
that connection while it asks GitHub about each pending revert, and a short
limit would kill the sweep mid-run and drop its advisory lock. The application
role's `statement_timeout` is per statement, and both the API and the crawler's
end-of-crawl batch issue many small ones — a 500-page crawl is 500 upserts, not
one long insert.

### Backups

`infra/scripts/backup.sh` dumps, **restores the dump into a scratch database**,
counts what came back, and only then keeps the file and prunes old ones. An
untested backup is a belief rather than a recovery plan, and a corrupt file in
the backup directory is worse than none because it looks like a backup.

```bash
cp infra/scripts/seo-autopilot-backup.* /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now seo-autopilot-backup.timer
systemctl list-timers seo-autopilot-backup
```

Nightly at 02:30 UTC, 14 days retained, `Persistent=true` so a host that was off
takes the backup on the way back rather than skipping a day. Verification
failure fails the unit, so a broken backup shows up in `systemctl` and the
journal instead of passing quietly.

## 4. Secrets

Server-only, never in git, all `chmod 600`:

| File | Holds |
|---|---|
| `.env` | `POSTGRES_PASSWORD`, `DOMAIN` |
| `.env.local` | API/worker runtime config, OIDC issuer/audience, connector keys |
| `infra/local/web.env` | `API_BASE_URL`, OIDC client id/secret, `WEB_SESSION_SECRET` |
| `infra/local/caddy.env` | `BASIC_AUTH_USER`, `BASIC_AUTH_HASH`, `OPERATOR_IPS` |

`infra/local/*.env` and `.env*` are gitignored. Operator IP addresses live in
`OPERATOR_IPS` specifically to keep them out of version control.

## 5. Access control

The app has real identity. `core/oidc.py` verifies an OIDC ID token against the
configured provider's key set, and `tenant_membership` decides whose data the
person behind it may act on. A verified token proves who somebody is and grants
nothing on its own.

**This replaced the pilot arrangement**, which was: one bearer token compared
against an environment variable, a hardcoded `Role.OWNER`, and a validator that
required `app_env == "development"` for any of it to work. `APP_ENV=production`
therefore did not harden the deployment — it returned 401 on every authenticated
route, so the public host ran in development mode with Caddy basic auth as its
only real authentication. That is gone.

### What an operator has to set

API (`.env.local`):

| Variable | Meaning |
|---|---|
| `APP_ENV` | `production`. Refused unless `OIDC_ISSUER_URL` is set. |
| `OIDC_ISSUER_URL` | The provider, https only. Its discovery document supplies the key set. |
| `OIDC_AUDIENCE` | This application's client id, checked against the token's `aud`. |
| `OIDC_JWKS_URI` | Optional. Pins the key set instead of discovering it. |

Web (`infra/local/web.env`):

| Variable | Meaning |
|---|---|
| `OIDC_ISSUER_URL` | Same issuer as the API. |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | The confidential client for the code exchange. |
| `WEB_SESSION_SECRET` | ≥32 chars. Encrypts the session cookie. Not a credential the API trusts. |
| `APP_BASE_URL` | Public origin, so the redirect URI is right. |

Register `https://seo.oryxenlabs.com/auth/callback` as an allowed redirect URI
with the provider — **not** `/api/auth/callback`. Everything under `/api/` is
proxied to the API service, so a callback there reaches FastAPI and 404s; the
sign-in routes are served by the web tier from `/auth/*`.

### The first owner

Every membership comes from an invitation and every invitation comes from a
member, which leaves the first one with nowhere to come from. That case is a
command on the host, not a route — an endpoint that mints owners would be a way
into any tenant:

```bash
$DC exec -T api python -m app.cli.bootstrap_owner \
  --tenant-slug codearc-pilot --email you@example.com
```

It refuses a tenant that already has an active owner and writes an audit event
with `actor_type='operator'`. Everyone after that is invited from the app, by an
owner or an admin. The invitation is claimed on first sign-in, and only against
an address the provider marked verified.

### Rules the app enforces

- An admin cannot create an owner; only an owner can.
- A tenant cannot lose its last owner, by demotion or removal.
- Nobody edits their own membership.
- Removal suspends the membership rather than deleting it, so audit rows and
  proposals that name the user id stay resolvable.

### The perimeter

The Caddy basic-auth gate on `/pilot*` and `/settings*` is now defence in depth
rather than the authentication itself, and can be removed once sign-in is
exercised on the host. Cloudflare's published ranges stay trusted proxies so
`client_ip` resolves to the real visitor.

Migration `0035` adds `app_user`, `tenant_membership` and `tenant_invitation`.
It ENABLEs *and* FORCEs row-level security: these are the first tables created
since `0027`, and enabling alone would leave them open, because the services own
them and an owner bypasses its own policies unless they are forced.

## 5b. The cutover to production mode

Retiring the pilot token is a two-variable change, and getting that wrong takes
the API down rather than leaving it insecure.

`APP_ENV=production` alone **does not start**. `LOCAL_PILOT_AUTH_ENABLED` is
`true` on the host, and the settings validator refuses that combination:

```
Value error, Local pilot authentication is development-only
```

Verified on the running host, both directions, without changing anything:

```bash
# refused
$DC exec -T -e APP_ENV=production api python -c 'from app.core.config import Settings; Settings()'

# validates
$DC exec -T -e APP_ENV=production -e LOCAL_PILOT_AUTH_ENABLED=false api \
  python -c 'from app.core.config import Settings; Settings()'
```

### Before

The invitation must be claimed. A row here is the difference between switching
credentials and locking everybody out:

```bash
$DC exec -T postgres psql -qAt -U seo_autopilot -d seo_autopilot \
  -c "SELECT email_normalized FROM app_user" \
  -c "SELECT role, status FROM tenant_membership" \
  -c "SELECT accepted_at FROM tenant_invitation"
```

An `app_user`, an active `owner` membership, and a non-null `accepted_at`. If
any is missing, sign-in has not completed and the cutover must wait — the token
being replaced is the only other way in.

### The change

Both variables, in one edit, then recreate:

```bash
sed -i 's/^APP_ENV=.*/APP_ENV=production/' .env.local
sed -i 's/^LOCAL_PILOT_AUTH_ENABLED=.*/LOCAL_PILOT_AUTH_ENABLED=false/' .env.local
$DC up -d --force-recreate api
```

### After

The pilot token must stop working, and the service must still be up. Both
halves matter: a dead API also returns nothing to a pilot token.

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://seo.oryxenlabs.com/api/health   # 200
TOKEN=$(grep '^LOCAL_PILOT_AUTH_TOKEN=' .env.local | cut -d= -f2-)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8001/v1/sites                                                 # 401
infra/scripts/check-perimeter.sh
```

Then load `/pilot` in the browser you signed in with. A signed-in session must
still work when the token no longer does; if it does not, back out.

### Backing out

Reverse both variables and recreate. The pilot token is valid again the moment
the API restarts — which is exactly why `.env.local` keeps it rather than
deleting it at cutover.

```bash
sed -i 's/^APP_ENV=.*/APP_ENV=development/' .env.local
sed -i 's/^LOCAL_PILOT_AUTH_ENABLED=.*/LOCAL_PILOT_AUTH_ENABLED=true/' .env.local
$DC up -d --force-recreate api
```

Only after a signed-in session is confirmed against the running host should the
Caddy basic-auth gate come off.

## 5a. Rollback reconciliation

Rolling back a **merged** deployment opens a revert pull request and stops. The
adapter has never had merge authority, so the deployed content stays live until
a person merges — which is why that state is `rollback_pending` and not
`rolled_back`.

Nothing used to find out what happened next, so a rollback stayed pending for
ever. TheCalcHive's emi-calculator has been in exactly that state since
2026-09-06: revert closed unmerged, change still live, receipt still waiting.

The API now polls for it. Set in `.env.local`:

| Variable | Meaning |
|---|---|
| `ROLLBACK_RECONCILE_ENABLED` | `true` to run the sweep. |
| `ROLLBACK_RECONCILE_INTERVAL_SECONDS` | Default 300, minimum 60. |
| `RELAY_DATABASE_URL` | The `seo_autopilot_relay` role. Without it the sweep finds nothing. |

Three outcomes, and each means something different about the live site:

| Revert pull request | Rollback | Deployment | Proposal |
|---|---|---|---|
| merged | `applied` | `rolled_back` | `failed` |
| closed without merging | `failed` | back to `applied` | stays `deployed` |
| still open | `pending`, `reconciled_at` set | unchanged | unchanged |

Anything else — a repository that cannot be read, an expired token — leaves the
receipt pending and writes the reason to `reconcile_error`. A rate limit must
not be able to decide a governance outcome.

The sweep holds a Postgres advisory lock, so more than one API process sweeps
once rather than N times. Transitions are audited with `actor_type='system'`,
`actor_id='reconciler'`: no person did this, a poll observed that somebody had.

## 6. Sites and crawls

All three verified by DNS TXT (`_seo-autopilot.<host>`), all Cloudflare-hosted.

| Site | Pages | State |
|---|---|---|
| codearc.net | 500 | `partial` — hit the 500-page cap |
| thecalchive.com | 32 | `completed` |
| wordkitapp.com | 13 | `completed` |

| Crawl | Policy | Status |
|---|---|---|
| `42ff78c7` | `auto` | **Superseded — findings invalid** |
| `957079c3` | `always` | Current baseline for codearc.net |

Tenant `codearc-pilot`, mode **observe**. `DEPLOYMENTS_ENABLED` and
`AUTOPILOT_ENABLED` are both `false`.

## 6a. Always-on workflows (deployed 2026-09-02)

Migrations `0020`–`0026` added scheduled routines, the keyword workspace,
sitemap coverage, content briefs, competitor tracking, answer-engine readiness,
and the agent workspace at `/pilot/workspace`.

| Flag | Value | Effect |
|---|---|---|
| `ROUTINES_ENABLED` | `true` | Starts the scheduler and routine consumer loops in the worker |
| `NOTIFICATIONS_ENABLED` | `false` | No outbound webhook configured yet |

**No individual routine is enabled.** The flag only starts the loops; work
happens when a routine is enabled per site, from the workspace Skills tab or by
asking the agent ("run a crawl and audit every day"). Routines gather evidence
and produce reports; they hold no deployment authority and are skipped while a
site is unverified or frozen.

Backup taken immediately before this deploy:
`backups/seo_autopilot_20260902T144526Z.dump` (1.1 MB, pre-0020 schema).

Verified after deploy: 52 tables with row-level security on all twenty new ones,
`sites=3 pages=545 opportunities=2541` unchanged, API healthy, 15 agent skills
listed, the agent answering from real production evidence with citations, and
`/pilot/workspace` gated by the same basic-auth rule as `/pilot`.

## 6b. Fix deploy (2026-09-02, later the same day)

Three defects in the always-on workflow code, all found by exercising the live
paths rather than re-reading the diff. No migrations; schema unchanged at 52
tables.

| Defect | Symptom in production |
|---|---|
| `session.rollback()` in three `IntegrityError` handlers | Asking the agent for the same thing twice inside a minute returned 500 and lost the turn |
| Router scored `run`/`scan` as imperatives anywhere | "When did the last crawl run?" **started a crawl**; "Show me the daily report" scheduled it daily |
| A refused claim was acked with no record | A run queued before a freeze sat in `queued` for ever, after the agent said it was queued |

The first is the same class as the `commit()` defect fixed earlier: both end the
request transaction, and `app.tenant_id` is transaction-local, so row-level
security rejects every later write. `services/api/tests/test_unit_of_work.py`
now fails if any service calls `commit()` or `rollback()`. That invariant has
caused two defects and is invisible to the service tests, because they all run
against a mocked session — worth remembering before adding another.

Backup taken first: `backups/seo_autopilot_20260902T155421Z.dump` (1.2 MB).
Rebuilt and restarted `api`, `worker` and `crawler`. Verified after deploy:
questions no longer start work, a repeated request answers `blocked` instead of
500, a crawl driven through the agent completed end to end in 8s on the rebuilt
crawler, `sites=3 pages=545 opportunities=2541` unchanged, no errors in any
service log, and `/pilot` and `/pilot/workspace` still 401 behind the gate.

The frozen-site path was then exercised on the live system, against WordKit,
and the freeze lifted immediately afterwards. Both halves hold: while frozen
the agent answered `blocked / site_frozen` and `POST /v1/routines/{id}/runs`
returned 409, and a run queued *before* the freeze — the race the worker fix
exists for — was recorded `skipped / site_frozen` instead of being stranded.
Afterwards: no site frozen, no run left in `queued`, counts unchanged.

## 6c. Identity, reconciliation and GA4 deploy (2026-09-07)

Migrations `0035`–`0037` applied through the new runner: the ledger was adopted
at 34, then three ran. 52 tables → 57. `sites=3 pages=964 proposals=51`
unchanged. Backup taken and **restored into a scratch database** before starting
(`backups/pre_identity_20260907T061913Z.dump`, counts matched).

Rebuilt and restarted `api`, `worker`, `web`. Caddy reloaded for the fix below.

**Two live defects were found by exercising the deployed system, not by reading
the diff. Neither was caused by this deploy.**

**1 — the perimeter gate had never been applying.** `/pilot` and `/settings`
returned 200 to an anonymous request, with no `WWW-Authenticate`. Confirmed from
a direct origin connection so no allowlist could explain it.

`OPERATOR_IPS` was `0.0.0.0/0,::/0`. That variable lists who may *skip* the
prompt, so a catch-all does not widen operator access — it removes the gate for
everyone: `not client_ip 0.0.0.0/0` is never true, so `@protected` never matched
and `basic_auth` never ran. Since the web tier injects an OWNER-role token,
anyone who found the URL had full control of all three sites.

Every piece was individually correct — Caddyfile, credentials, trusted proxies —
which is why nothing caught it. Set to `192.0.2.1/32` (TEST-NET-1, never
routable), so the exempt list is effectively empty and the directive stays
valid; the operator now sees the prompt and has the credentials.
`infra/scripts/check-perimeter.sh` asserts the *behaviour* hourly, because
reading the config would not have caught this either.

**2 — the web tier could not authenticate to the API.** `web.env` held a
`LOCAL_PILOT_AUTH_TOKEN` that did not match the API's, so every server-side call
returned 401. `/settings/connectors` surfaced it as a 500; `/pilot` swallowed
it and rendered a page with no live data. Aligned with the API's token; both
pages now render real data for all three sites. This class of mismatch
disappears once OIDC is on, because the web tier will forward the user's own
token instead of holding one.

**Rollback reconciliation turned on** (`ROLLBACK_RECONCILE_ENABLED=true`). The
emi-calculator rollback that had been `rollback_pending` since 2026-09-06
settled on the first sweep: rollback `failed`, deployment back to `applied`,
proposal still `deployed` — the revert (PR #3) was closed unmerged, so nothing
was undone and the records now say so. Audited as `actor_type='system'`,
`actor_id='reconciler'`, `change_reversed=false`.

**Timers installed.** `seo-autopilot-backup` nightly at 02:30 UTC (first run
verified: 2.0 MB, restored, tenants=1 sites=3) and `seo-autopilot-health`
hourly, which runs the perimeter check and the six invariants. Both green.

**Follow-on the same day.** `/settings/members` added — list who is in the
tenant, change a role, remove behind a confirmation step, invite an address,
revoke an unclaimed invitation — reachable from a new Settings nav. The weekly
report gained an engagement section from GA4 (`REPORT_SCHEMA_VERSION` 2), kept
strictly separate from the Search Console section. Deployed and verified: page
renders, 401 through the gate, no errors, all six invariants pass.

Inviting from the app while the deployment still uses the operator token
returns `inviter_is_not_a_signed_in_user` — the pilot token names an actor id
that was never a person, so there is nobody to record as the inviter. That is
the honest answer until the OIDC cutover; the first owner comes from
`app.cli.bootstrap_owner` either way.

**A credential that was not secret.** `LOCAL_PILOT_REVIEWER_TOKEN` — the second
operator's bearer token, which exists so the author of a proposal cannot approve
it — was a 36-character UUID that is also a live `proposal.id`. The config check
requires 32 characters and a UUID satisfies it, so validation passed while the
value failed to be a secret at all.

That id appears in the pilot UI, in `audit_event` and `outbox_event` rows, and
in every database backup, so anyone with read access to a single proposal held
the reviewer's credential and could approve work they had authored — with
`DEPLOYMENTS_ENABLED=true`, that reaches customer repositories. Not reachable
from the internet: listing proposals needs authentication.

Rotated 2026-09-07 to 64 random hex characters; the old value now returns 401
and the operator token is unaffected. The previous `.env.local` is kept as
`.env.local.bak-reviewer-*` on the host. Read the new value with:

```bash
ssh oryxen "grep '^LOCAL_PILOT_REVIEWER_TOKEN=' /opt/seo-autopilot/.env.local"
```

Both pilot tokens must be generated (`openssl rand -hex 32`), never copied from
anything the system publishes.

**Still on `APP_ENV=development`.** The identity code is deployed but no OIDC
client is registered, so the pilot token remains the credential and the Caddy
gate — now actually applying — is still what stands in front of it.

## 6d. GA4 connected (2026-09-07)

The first engagement data the system has ever held. The GA4 property belongs to
**CodeArc**, not TheCalcHive — so the connector is bound to `codearc.net`
(connector `1a6bce67`, `properties/546281601`).

A 30-day backfill returned 49 rows across 10 landing pages, 61 sessions and 267
views for 2026-08-08 to 2026-09-06; 37 of the 49 resolved to a known `page_id`.
Seven sessions land on `(not set)`, which is GA4's own unattributed bucket, not
a defect on this side. A daily `analytics_sync` routine runs at 05:20 UTC.

What this proves and what it does not. It proves the whole path — authorize,
consent, stream-host binding, token exchange, `runReport` paging, upsert, page
resolution. It does not give the system anything to *measure*: CodeArc's crawl
findings were quarantined as invalid by `0028`, so there are no proposals on
that site whose effect this data could show. Measurement still waits on
TheCalcHive, which has no GA4 property at all.

Three things had to be fixed before any of this was possible, none of them
visible from reading a single file:

- The authorize route validated its body with `ConnectorAuthorizationCreate`,
  the Search Console schema, so every valid GA4 property was refused as an
  "invalid Search Console URL-prefix property".
- No page called the route, so connecting meant hand-writing an API call.
- `access_type=offline` was missing, so Google would have issued no refresh
  token and the renewal path in `server-api.ts` was unreachable code.

**The OAuth callback redirects to `/settings`, which the Caddy gate protects.**
A successful consent therefore ends on a basic-auth prompt, which reads exactly
like a failure. It is not: the connector is already `active` by then. Worth
knowing before the next person cancels out of a flow that had already worked.

## 7. Audit findings (2026-08-29)

Full report: https://claude.ai/code/artifact/2e4f154d-700c-4754-a997-aea9ebb83b17

The first crawl reported 2,042 findings. **1,996 were an artifact of a renderer
bug** — `adaptive-fetcher.ts` snapshotted pages at `domcontentloaded`, before the
JS bundle executed, so a client-rendered app returned an empty shell. Fixed; the
renderer now waits for `load` and for the body to contain real text. H1 detection
across codearc.net went from 0/500 to 500/500.

Genuine findings, in priority order:

1. **Critical — codearc.net, 496 pages.** Nearly every URL client-side redirects
   anonymous visitors to `/login`, rendering as a 58-word sign-in page. Only `/`,
   `/pricing` and `/prompt-lab` serve public content. Google renders JavaScript,
   so this is what Googlebot sees. Verified with request-blocking disabled, with
   a real desktop Chrome user-agent, across three URLs — not a crawler artifact.
2. **High — codearc.net, 490 pages.** All share one identical `<title>`. Verified
   from raw HTML, independent of the crawl.
3. **Medium — thecalchive.com, 32 pages.** Every page carries the homepage H1
   ("Every calculator you'll ever need") while titles are correctly unique.
4. **Clean — wordkitapp.com.** 1,610 words per page average, 30 internal links,
   an H1 everywhere. Twelve low-severity title/H1 mismatches.

## 8. Open items

Verified against the live host on **2026-09-08**. An open-items list that has
drifted is worse than none, because people act on it.

**Done since this list was last checked:**

- [x] **The Google OAuth redirect URI is registered.** Search Console and GA4
      are both `active`; the connector callback is exercised.
- [x] **The OIDC client is registered** and the API and web variables are set.
      `/auth/login` redirects to Google with PKCE, state and nonce, and the
      hourly perimeter check asserts the redirect URI is the public origin.
- [x] **The first owner is bootstrapped** — an owner invitation for
      `mas.zaf@gmail.com` on `codearc-pilot`, claimed on first sign-in.
- [x] **The migration ledger is adopted** — 38 applied rows, and
      `app.cli.schema_diff` reports the database matches the migrations.
- [x] **The backup timer is installed and verified.** Nightly at 02:30 UTC;
      every dump is restored into a scratch database and counted before it is
      kept. Newest dump is hours old, not weeks.
- [x] **The health timer is installed.** Hourly, and it runs the perimeter
      check first, so both questions are answered by one unit. Eight invariants,
      all clear.
- [x] **Rollback reconciliation is on, and it settled.** The emi-calculator
      revert (`mindTools#3`) was **closed without merging** on 2026-09-06, so
      the receipt is `failed`, the deployment receipt is back to `applied`, and
      the proposal is `deployed`. That is the honest end state, not a fault:
      somebody looked at the revert and decided against it. The change is live.

**Still open, and each one is blocked on a person rather than on code:**

- [ ] **Sign in once**, then move the host to `APP_ENV=production`. The
      invitation is open and unclaimed — no `app_user`, no membership. Until a
      real session exists the pilot token stays valid, deliberately: nothing
      retires the old credential before the new one is proven.
- [ ] **The Caddy basic-auth gate is removed in the repo and still running on
      the host.** `5d84a95` takes it out; the container was never recreated, and
      a single-file bind mount does not follow an rsync (§9), so the gate is
      still up. Applying it is one `up -d --force-recreate caddy`. Deliberately
      left for a moment when it is the thing being done, rather than arriving as
      a side effect of some unrelated deploy.
- [ ] **Set Cloudflare SSL mode to Full (strict).**
- [x] **codearc.net's Search Console is connected** (2026-09-08). The property
      is `https://codearc.net/`, a **URL-prefix** property — `sc-domain:` does
      not exist for that site, and the first attempt was correctly refused. The
      daily routine ran immediately and wrote 17 rows for 2026-08-30..09-05:
      18 impressions, 0 clicks, average position 71.2. That site now has both
      connectors, the only one in the estate that does.
- [ ] **TheCalcHive has no GA4 property at all.** Measurement there needs a
      property created and the tag installed, and GA4 collects nothing
      retroactively, so the clock starts the day it goes live.
- [ ] **codearc.net is being updated** — re-crawl when the owner says it is
      ready and diff against crawl `957079c3`. Watch two numbers: how many pages
      still land on `/login` (was 496/500) and how many share a title (was
      490/500).
- [ ] Decide whether codearc.net's tutorials should be public. If they stay
      members-only, mark them `noindex` and drop them from the sitemap; leaving
      them crawlable *and* empty is the only bad option.
- [ ] **The branch is not pushed.** Everything here is committed locally and
      deployed, and there is no remote copy of any of it.

## 8a. What the search data actually says (2026-09-08)

Worth stating plainly, because it changes what "measurement" means here.

`search_metric` holds **75 rows over sixteen months** for TheCalcHive: 83
impressions, 2 clicks, and nothing at all after **2026-07-18**. That is not a
broken sync. The connector is `sc-domain:thecalchive.com`, a domain property
that covers everything, the backfill walked 488 days, and the daily run
completes cleanly. Later days are absent because Google suppresses dimensional
rows below its anonymisation threshold, and this site is below it almost every
day.

So the H1 and title work cannot be evaluated on this site, and waiting longer
will not change that. One impression a week is not a signal any test separates
from noise. The pipeline is proven; the traffic is not there.

GA4 is on **codearc.net**, a different site, whose findings were quarantined as
invalid. So the estate currently has engagement data for a site with no
proposals and proposals for a site with no measurable traffic.

## 9. Gotchas worth remembering

- **A single-file bind mount does not follow an rsync.** The Caddyfile is
  mounted file-to-file (`./infra/caddy/Caddyfile:/etc/caddy/Caddyfile:ro`).
  rsync writes a temporary file and renames it over the target, which replaces
  the inode, and the container goes on serving the file it was started with.
  Found on 2026-09-08: the host file no longer contained `basic_auth` and the
  running container still did.

  A `restart` does not fix it either — the mount is resolved when the container
  is *created*. Any Caddyfile change therefore needs
  `$DC up -d --force-recreate caddy`, and the way to confirm it is to read the
  file from inside:

  ```bash
  $DC exec -T caddy grep -n basic_auth /etc/caddy/Caddyfile
  ```

  The failure is quiet in the dangerous direction: a security change looks
  deployed, is not, and then applies by surprise at whatever unrelated moment
  something next recreates the container.
- **Compose interpolates `$` in `env_file` values.** A bcrypt hash passed through
  `environment:` gets silently truncated. Caddy credentials therefore come from
  `env_file`, with `$` doubled in the file.
- **The crawler writes observations in one batch at the end of a crawl.** Page
  counts sit at 0 while it is clearly working — this is not a hang.
- **macOS ships bash 3.2**, which has no associative arrays. Helper scripts on the
  dev machine must be 3.2-compatible or use a newer bash explicitly.
- **Verification challenges expire after 30 minutes.** Reissue rather than reusing
  stale TXT values; leftovers from earlier local runs will not match.
- **Migrations are tracked in `schema_migration` now.** The old advice — name the
  new range by hand, because re-running an applied file aborts the loop — no
  longer applies. The runner also refuses a file whose contents changed after it
  was applied, so edit a migration that has shipped and the next deploy stops.
- **The rsync excludes miss local tool caches.** `.ruff_cache/` and
  `.pytest_cache/` are shipped to the server on every deploy. Harmless, but add
  them to the exclude list when next editing the command.
- **`rsync --delete` is the documented sync, but it is destructive on the remote.**
  When a deploy only adds files, dropping `--delete` is equivalent and safer.
  Keep it for a deploy that removes or renames files, and check what it would
  remove first with `--dry-run`.
