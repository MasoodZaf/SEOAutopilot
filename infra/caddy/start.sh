#!/bin/sh
# Start or reload Caddy, admitting other projects' site files one at a time.
#
#   start.sh run      the container's command
#   start.sh reload   docker exec seo-autopilot-caddy-1 sh /etc/caddy/start.sh reload
#
# Other projects (VOCA, NexHR, ...) own one file each in /opt/edge/sites on the
# host, mounted read-only at /etc/caddy-sites. Caddy refuses to start at all if
# any imported file is broken, or defines a hostname that is already defined,
# so a plain glob import would let one project's typo take every site on this
# proxy down at the next restart or reboot. Instead, each file is copied into
# the directory the Caddyfile actually imports only if the whole config still
# validates with it. A refused file costs only its own sites; if an earlier
# version of it was admitted, that version stays.
#
# The base Caddyfile must validate with no imported files, or nothing starts:
# our own config being wrong should stay loud.
set -eu

MODE="${1:-run}"
CONFIG=/etc/caddy/Caddyfile
SOURCE=/etc/caddy-sites
LIVE=/tmp/caddy-sites
NEXT=/tmp/caddy-sites.next

check() {
  CADDY_SITES_DIR="$1" caddy validate --config "$CONFIG" --adapter caddyfile >/tmp/caddy-sites.log 2>&1
}

rm -rf "$NEXT"
mkdir -p "$NEXT" "$LIVE"

if ! check "$NEXT"; then
  echo "caddy-sites: the base Caddyfile does not validate on its own" >&2
  cat /tmp/caddy-sites.log >&2
  exit 1
fi

refused=0
for file in "$SOURCE"/*.caddy; do
  [ -e "$file" ] || continue
  name=$(basename "$file")
  cp "$file" "$NEXT/$name"
  if check "$NEXT"; then
    echo "caddy-sites: admitted $name"
    continue
  fi
  refused=1
  echo "caddy-sites: REFUSED $name" >&2
  grep -i "error" /tmp/caddy-sites.log | tail -n 3 >&2 || true
  rm -f "$NEXT/$name"
  if [ -f "$LIVE/$name" ]; then
    cp "$LIVE/$name" "$NEXT/$name"
    if check "$NEXT"; then
      echo "caddy-sites: kept the previously admitted $name" >&2
      continue
    fi
    rm -f "$NEXT/$name"
  fi
  echo "caddy-sites: $name is not served" >&2
done

rm -rf "$LIVE"
mv "$NEXT" "$LIVE"

case "$MODE" in
  run)
    exec caddy run --config "$CONFIG" --adapter caddyfile
    ;;
  reload)
    caddy reload --config "$CONFIG" --adapter caddyfile
    # Non-zero tells the project that ran it that its file was refused.
    exit "$refused"
    ;;
  *)
    echo "usage: start.sh run|reload" >&2
    exit 2
    ;;
esac
