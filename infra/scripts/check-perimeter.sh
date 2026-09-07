#!/usr/bin/env bash
# Does the gate actually challenge an unauthenticated request?
#
# On 2026-09-07 it did not, and had not for some time. The Caddyfile was
# correct, the credentials were correct, and `OPERATOR_IPS` — which lists the
# addresses allowed to *skip* the prompt — had been set to `0.0.0.0/0,::/0`.
# `not client_ip 0.0.0.0/0` is never true, so `@protected` never matched and
# basic_auth never ran. /pilot and /settings were reachable by anyone, and the
# web tier injects an OWNER-role token, so that was full control of every site.
#
# Nothing detected it because every part looked right in isolation. Reading the
# config would not have caught it either; the value was in a gitignored env file
# and its meaning is inverted. So this asserts the property instead: an
# unauthenticated request to a protected path must be refused.
#
# Keep it running even after Cloudflare Access replaces basic auth. The question
# it asks does not change, only the thing that answers it.
set -euo pipefail

BASE="${1:-https://seo.oryxenlabs.com}"
FAILED=0

expect() {
  local path="$1" want="$2" label="$3"
  local got
  got="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$BASE$path" || echo 000)"
  if [ "$got" = "$want" ]; then
    echo "[ok]   $label: $path -> $got"
  else
    echo "[FAIL] $label: $path -> $got (expected $want)"
    FAILED=1
  fi
}

# The control plane must refuse an anonymous request.
expect /pilot 401 "control plane is gated"
expect /settings/connectors 401 "settings are gated"

# The API's own surface. None of these carry tenant data, but between them they
# hand a stranger a complete map of every endpoint and this deployment's
# operational posture, including whether the kill switch is on. They were all
# public until 2026-09-07.
expect /api/openapi.json 404 "API schema is not published"
expect /api/docs 404 "interactive docs are not published"
expect /api/metrics 401 "metrics need a scrape credential"

# ...and the marketing page must not be, or the check would pass on a site that
# is simply broken.
expect / 200 "public page still serves"
expect /api/health 200 "the API is actually up"

if [ "$FAILED" -ne 0 ]; then
  echo
  echo "The perimeter is not refusing anonymous requests it should refuse."
  echo "Check OPERATOR_IPS in infra/local/caddy.env: it lists who may SKIP the"
  echo "prompt, so a broad range there disables the gate for everyone."
  exit 1
fi
echo
echo "perimeter ok"
