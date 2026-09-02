# Deployment & Pilot Status

Operational record for the hosted pilot. Last updated **2026-09-02**.

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
rsync -az --delete \
  --exclude='.git/' --exclude='node_modules/' --exclude='.next/' \
  --exclude='.turbo/' --exclude='.venv-*/' --exclude='__pycache__/' \
  --exclude='*.pyc' --exclude='*.tsbuildinfo' \
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

Migrations are plain SQL, applied in order. **The loop below is for a fresh
database only** — the files are not idempotent, so re-running an applied one
fails on the first `CREATE TABLE`:

```bash
for f in infra/migrations/*.sql; do
  $DC exec -T postgres psql -q -U seo_autopilot -d seo_autopilot \
    -v ON_ERROR_STOP=1 -f - < "$f" || break
done
```

For an incremental deploy, name only the new range:

```bash
for f in infra/migrations/00{20,21,22,23,24,25,26}_*.sql; do
  $DC exec -T postgres psql -q -U seo_autopilot -d seo_autopilot \
    -v ON_ERROR_STOP=1 -f - < "$f" || break
done
```

Back up first; the dump is small enough to be routine:

```bash
mkdir -p backups
$DC exec -T postgres pg_dump -U seo_autopilot -d seo_autopilot --format=custom \
  > "backups/seo_autopilot_$(date -u +%Y%m%dT%H%M%SZ).dump"
```

26 migrations are applied; the database holds 52 tables.

## 4. Secrets

Server-only, never in git, all `chmod 600`:

| File | Holds |
|---|---|
| `.env` | `POSTGRES_PASSWORD`, `DOMAIN` |
| `.env.local` | API/worker runtime config, pilot token, connector keys |
| `infra/local/web.env` | `API_BASE_URL`, pilot token for the web tier |
| `infra/local/caddy.env` | `BASIC_AUTH_USER`, `BASIC_AUTH_HASH`, `OPERATOR_IPS` |

`infra/local/*.env` and `.env*` are gitignored. Operator IP addresses live in
`OPERATOR_IPS` specifically to keep them out of version control.

## 5. Access control — read this before changing auth

The app **has no user accounts**. There is no `users` table, no password column,
no per-user roles; `core/auth.py` verifies a single bearer token and returns a
hardcoded `Role.OWNER`. `oidc_issuer_url` only selects a 401 error string — OIDC
is not implemented.

Both `config.py` and `auth.py` require `app_env == "development"` for that token
to work, so the app **cannot run with `APP_ENV=production`** — every authenticated
route would return 401. This is accepted for a pilot.

Consequence: the web tier injects an OWNER-role token server-side, so anyone who
reaches `/pilot` has full control. **The perimeter is the only real auth.**

Current gate (interim, in `infra/caddy/Caddyfile`):

- HTTP basic auth on `/pilot*` and `/settings*`
- Addresses in `OPERATOR_IPS` skip the prompt
- Cloudflare's published ranges are trusted proxies, so `client_ip` resolves to
  the real visitor rather than an edge IP

Planned replacement: **Cloudflare Access**, which gives named identities at the
door without touching the codebase. Remove the `basic_auth` block once it fronts
the app.

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
- [ ] **Replace basic auth with Cloudflare Access**, then remove the interim gate.
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
- **The migration loop in §3 only works on a fresh database.** The files use bare
  `CREATE TABLE`, so re-running an applied migration aborts the loop. Name the new
  range explicitly on an incremental deploy.
- **`rsync --delete` is the documented sync, but it is destructive on the remote.**
  When a deploy only adds files, dropping `--delete` is equivalent and safer.
  Keep it for a deploy that removes or renames files, and check what it would
  remove first with `--dry-run`.
