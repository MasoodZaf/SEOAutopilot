#!/usr/bin/env bash
# Point sign-in at a different Google OAuth client.
#
# Usage:
#   set-signin-client.sh --from-json /tmp/oauth-client.json   # downloaded from the console
#   set-signin-client.sh                                      # type the values instead
#
# Three values move together, and the third is the one that is easy to miss:
#
#   infra/local/web.env  OIDC_CLIENT_ID      the web tier starts the flow with it
#   infra/local/web.env  OIDC_CLIENT_SECRET  the web tier exchanges the code with it
#   .env.local           OIDC_AUDIENCE       the API checks the id_token's `aud`
#
# Change the first two alone and sign-in appears to work -- Google returns a
# token, the session cookie is set -- and then every API call 401s, because the
# audience the API insists on is still the old client. That reads as "the
# deployment is broken" rather than "one variable was missed".
#
# The secret is never echoed, never passed as an argument (where `ps` would
# show it), and never written to shell history.
set -euo pipefail
cd /opt/seo-autopilot

CALLBACK="https://seo.oryxenlabs.com/auth/callback"
JSON=""
if [ "${1:-}" = "--from-json" ]; then
  JSON="${2:?--from-json needs a path}"
  [ -r "$JSON" ] || { echo "Cannot read $JSON" >&2; exit 1; }
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
cp infra/local/web.env "infra/local/web.env.bak-signin-$STAMP"
cp .env.local ".env.local.bak-signin-$STAMP"
echo "Backed up both files with suffix .bak-signin-$STAMP"
echo

if [ -n "$JSON" ]; then
  echo "Reading the client from $JSON"
else
  read -rp  'New OAuth client ID: ' CLIENT_ID
  read -rsp 'New client secret (hidden): ' CLIENT_SECRET
  echo; echo
  export CLIENT_ID CLIENT_SECRET
fi

# Rewrite in place with python rather than sed: a secret can contain characters
# sed treats as delimiters or backreferences, and pushing it through a shell
# interpolation is how one ends up mangled, or logged.
JSON="$JSON" CALLBACK="$CALLBACK" python3 - <<'PY'
import os, io, re, json, sys

path = os.environ.get("JSON") or ""
if path:
    blob = json.load(io.open(path, encoding="utf-8"))
    node = blob.get("web") or blob.get("installed") or {}
    if not blob.get("web"):
        print("This is not a Web application client. Sign-in needs the Web type.", file=sys.stderr)
        sys.exit(1)
    cid = node.get("client_id", "")
    secret = node.get("client_secret", "")
    # The console will happily hand you a client whose redirect list does not
    # contain the one the app actually sends; that fails later, at the provider,
    # as redirect_uri_mismatch, naming nothing the reader can connect to this.
    uris = node.get("redirect_uris") or []
    if os.environ["CALLBACK"] not in uris:
        print(f"That client's redirect URIs do not include {os.environ['CALLBACK']}", file=sys.stderr)
        print(f"  it has: {uris}", file=sys.stderr)
        print("Fix it in the console first; nothing has been changed.", file=sys.stderr)
        sys.exit(1)
    print(f"  redirect URI present: {os.environ['CALLBACK']}")
else:
    cid = os.environ["CLIENT_ID"]
    secret = os.environ["CLIENT_SECRET"]

if not cid.endswith(".apps.googleusercontent.com"):
    print("That does not look like a Google client ID.", file=sys.stderr)
    sys.exit(1)
if len(secret) < 12:
    print("That secret is too short to be one -- nothing was changed.", file=sys.stderr)
    sys.exit(1)

def put(path, key, value):
    lines = io.open(path, encoding="utf-8").read().splitlines()
    out, seen = [], False
    for line in lines:
        if re.match(rf"\s*{re.escape(key)}\s*=", line):
            out.append(f"{key}={value}")
            seen = True
        else:
            out.append(line)
    if not seen:
        out.append(f"{key}={value}")
    io.open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(f"  {path}: {key} set")

put("infra/local/web.env", "OIDC_CLIENT_ID", cid)
put("infra/local/web.env", "OIDC_CLIENT_SECRET", secret)
put(".env.local", "OIDC_AUDIENCE", cid)
PY

chmod 600 infra/local/web.env

if [ -n "${JSON:-}" ]; then
  # The downloaded file is a live credential. Remove it here rather than
  # leaving it in /tmp for the next person with a shell on this box.
  shred -u "$JSON" 2>/dev/null || rm -f "$JSON"
  echo "  removed $JSON"
fi

echo
echo "Done. The client ID now set (public value, safe to share):"
grep '^OIDC_CLIENT_ID=' infra/local/web.env
echo
echo "Nothing has restarted yet. Tell Claude it is done and it will apply and verify."
