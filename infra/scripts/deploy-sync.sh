#!/usr/bin/env bash
# Sync the working tree to the production host.
#
# This exists because the exclude list is not a convenience -- three of its
# entries are the only thing standing between a deploy and a production
# outage, and they were being retyped by hand each time.
#
# On 2026-09-20 a resync kept `--exclude='.env.local'` and dropped
# `--exclude='infra/local/'`. The laptop's `infra/local/web.env` is 88 bytes
# holding only a pilot token; it overwrote the host's 418-byte copy and took
# `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `APP_BASE_URL` and
# `WEB_SESSION_SECRET` with it. Sign-in returned 503 for a day, and because
# compose declares that env_file `required: false`, nothing was logged. The
# client secret was not recoverable from the host -- only a stale pre-split
# backup survived -- so it had to be fetched again from the Cloud Console.
#
# The tell that a file came from a laptop: on the host it is owned by uid 501
# `staff` rather than root.
#
# Everything under `infra/local/` and the two dotenv files is host state that
# exists ONLY on the host. Nothing in this repo can regenerate it. Never add a
# way to override the excludes from the command line.
set -euo pipefail

HOST="${1:-oryxen}"
DEST="${2:-/opt/seo-autopilot/}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# A guard against running this from somewhere that merely looks like the repo.
[ -f "$HERE/docker-compose.prod.yml" ] || {
  echo "Not the repository root: $HERE" >&2
  exit 1
}

echo "Syncing $HERE -> $HOST:$DEST"
echo "Host-only state (infra/local/, .env.local, .env) is never sent."
echo

# --delete is deliberately absent. It is destructive on the remote and has no
# way to distinguish a file this repo no longer has from a file the host is
# supposed to own. Prune by hand when something genuinely needs removing.
# `--stats`, not `--info=stats1`: macOS 15 replaced rsync with openrsync,
# which reports itself as "rsync version 2.6.9 compatible" and predates the
# `--info=` family. `--stats` is understood by both.
rsync -az --stats \
  --exclude='.git/' --exclude='node_modules/' --exclude='.next/' \
  --exclude='.turbo/' --exclude='.venv-*/' --exclude='.venv/' --exclude='__pycache__/' \
  --exclude='*.pyc' --exclude='*.tsbuildinfo' \
  --exclude='.ruff_cache/' --exclude='.pytest_cache/' --exclude='._*' \
  --exclude='.env.local' --exclude='.env' --exclude='infra/local/' \
  "$HERE/" "$HOST:$DEST"

echo
echo "Synced. Nothing has been rebuilt or restarted."
echo "On the host:"
echo "  cd ${DEST%/}"
echo "  DC=\"docker compose -f docker-compose.yml -f docker-compose.prod.yml\""
echo "  \$DC build <service> && \$DC up -d <service>"
echo
echo "Then assert the perimeter still holds, from your machine:"
echo "  bash infra/scripts/check-perimeter.sh"
