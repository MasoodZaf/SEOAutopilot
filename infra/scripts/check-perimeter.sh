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

# The sign-in routes must be served by the web tier. They used to sit under
# `/api/`, which the proxy sends to the API service, so the public origin
# answered a FastAPI 404 and the flow could never have completed.
reached="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$BASE/auth/login" || echo 000)"
if [ "$reached" = "404" ] || [ "$reached" = "000" ]; then
  echo "[FAIL] sign-in route reaches the web tier: /auth/login -> $reached"
  FAILED=1
else
  echo "[ok]   sign-in route reaches the web tier: /auth/login -> $reached"
fi

# Where sign-in sends people, and where it expects them back.
#
# A 307 to the provider is not enough. On 2026-09-07 the first real sign-in
# completed -- code exchanged, session minted -- and then redirected the browser
# to the container's own hostname and internal port, because the handler built
# the URL from the request it had received rather than from the public origin.
# It read as authentication being broken while it was in fact working.
#
# So this asserts the two origins that must be the public one. The redirect URI
# is also what the provider matches against its registered value, so a drift
# here is the difference between a working front door and `redirect_uri_mismatch`
# for everybody.
location="$(curl -sS -o /dev/null -D - --max-time 20 "$BASE/auth/login" 2>/dev/null \
  | tr -d '\r' | awk 'tolower($1) == "location:" {print $2}' | head -1 || true)"

if [ -z "$location" ]; then
  echo "[FAIL] sign-in starts an authorization: /auth/login sent no Location"
  FAILED=1
else
  case "$location" in
    https://accounts.google.com/*)
      echo "[ok]   sign-in starts an authorization at the provider" ;;
    *)
      echo "[FAIL] sign-in starts an authorization: went to $location"
      FAILED=1 ;;
  esac

  # The value the provider will compare against its registered redirect URI,
  # url-decoded far enough to read the origin.
  redirect="$(printf '%s' "$location" | tr '&' '\n' | sed -n 's/^redirect_uri=//p' \
    | sed 's/%3A/:/g; s/%2F/\//g')"
  case "$redirect" in
    "$BASE"/auth/callback)
      echo "[ok]   sign-in returns to the public origin: $redirect" ;;
    *)
      echo "[FAIL] sign-in returns to $redirect (expected $BASE/auth/callback)"
      FAILED=1 ;;
  esac
fi

# An unauthenticated visit to the control plane must be sent to a login page on
# this host. The redirect for this lives in proxy.ts, which never executed until
# an issuer was configured -- so it shipped already broken, pointing into the
# Docker network, and no test could see it.
gate="$(curl -sS -o /dev/null -D - --max-time 20 "$BASE/login" 2>/dev/null \
  | tr -d '\r' | awk 'tolower($1) == "location:" {print $2}' | head -1 || true)"
case "$gate" in
  ""|"$BASE"/*|/*)
    echo "[ok]   the login page stays on this origin" ;;
  *)
    echo "[FAIL] the login page redirects off-origin: $gate"
    FAILED=1 ;;
esac

if [ "$FAILED" -ne 0 ]; then
  echo
  echo "The perimeter is not refusing anonymous requests it should refuse."
  echo "Check OPERATOR_IPS in infra/local/caddy.env: it lists who may SKIP the"
  echo "prompt, so a broad range there disables the gate for everyone."
  exit 1
fi
echo
echo "perimeter ok"
