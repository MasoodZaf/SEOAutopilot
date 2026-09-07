# Deployment & Pilot Status

Operational record for the hosted pilot. Last updated **2026-09-07**.

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

**One-time, on the existing production database.** It has migrations applied and
no ledger, so replaying them would fail on the first `CREATE TABLE`. Adopt what
is already there, then run normally from that point on:

```bash
$DC exec -T api python -m app.cli.migrate --adopt-through 37 --database-url "..."
```

It refuses a migration whose file changed after it was applied, naming it: the
repository would otherwise be a wrong description of the database rather than a
stale one. If a migration ran but the process died before the ledger was
written, `--mark-applied <filename>` records that one file.

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

Register `https://seo.oryxenlabs.com/api/auth/callback` as an allowed redirect
URI with the provider.

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

**Still on `APP_ENV=development`.** The identity code is deployed but no OIDC
client is registered, so the pilot token remains the credential and the Caddy
gate — now actually applying — is still what stands in front of it.

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

- [ ] **codearc.net is being updated** — re-crawl when the owner says it's ready,
      and diff against crawl `957079c3`. Watch two numbers: how many pages still
      land on `/login` (was 496/500) and how many share a title (was 490/500).
- [ ] **Register the Google OAuth redirect URI** in Google Cloud Console:
      `https://seo.oryxenlabs.com/api/v1/connectors/oauth/callback`.
      Until then GSC cannot connect and there is no search-demand data, so
      opportunity ranking runs on crawl evidence alone.
- [ ] **Set Cloudflare SSL mode to Full (strict).**
- [ ] **Register the OIDC client** with the provider and set the four API and
      web variables in §5, then move the host to `APP_ENV=production`. Until
      that happens the host still runs in development mode.
- [ ] **Bootstrap the first owner** with `app.cli.bootstrap_owner`, sign in
      once, and confirm the pilot token no longer reaches the API.
- [ ] **Remove the Caddy basic-auth gate** once sign-in is exercised on the
      host. It is defence in depth now, not the authentication.
- [ ] **Adopt the migration ledger** on the production database
      (`--adopt-through 37`, §3) before the next deploy.
- [ ] **Install the backup timer** (§3) and confirm the first verified run.
- [ ] **Install the health timer** (§3) and clear whatever it reports on the
      first run — the pending emi-calculator rollback will be one of them.
- [ ] **Turn on rollback reconciliation** (§5a) and let it settle the
      emi-calculator rollback that has been pending since 2026-09-06.
- [ ] **Discard the 1,996 findings from crawl `42ff78c7`** — they measure the
      renderer bug, not the sites.
- [ ] Decide whether codearc.net's tutorials should be public. If they stay
      members-only, mark them `noindex` and drop them from the sitemap; leaving
      them crawlable *and* empty is the only bad option.

## 9. Gotchas worth remembering

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
