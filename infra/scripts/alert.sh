#!/bin/sh
# Tell a person a unit failed. Sends the unit name and its FAIL lines only:
# both health checks print counts and URLs of our own origin, never content,
# so nothing tenant-specific leaves the box.
set -u
unit="${1:?unit name}"
host="$(hostname)"
fails="$(journalctl -u "$unit" -n 80 --no-pager -o cat 2>/dev/null | grep -E '^\[FAIL\]|FAIL' | tail -8)"
message="SEO Autopilot: $unit failed on $host
${fails:-see journalctl -u $unit}"

if [ -z "${ALERT_WEBHOOK_URL:-}" ]; then
  echo "no ALERT_WEBHOOK_URL in /etc/seo-autopilot/alert.env; alert only in the journal:"
  echo "$message"
  exit 0
fi

case "$ALERT_WEBHOOK_URL" in
  https://hooks.slack.com/*)
    body="$(printf '%s' "$message" | python3 -c 'import json,sys; print(json.dumps({"text": sys.stdin.read()}))')"
    curl -fsS --max-time 15 -H 'Content-Type: application/json' -d "$body" "$ALERT_WEBHOOK_URL" >/dev/null ;;
  https://signal.callmebot.com/*)
    # Set ALERT_WEBHOOK_URL to .../signal/send.php?phone=+NUMBER&apikey=KEY
    # The URL holds the key, so curl reads it from stdin to keep it out of `ps`.
    printf 'url = "%s"\n' "$ALERT_WEBHOOK_URL" | curl -fsS --max-time 15 --get --config - \
      --data-urlencode "text=$message" >/dev/null ;;
  https://*)
    curl -fsS --max-time 15 -H 'Title: SEO Autopilot alert' -H 'Priority: high' \
      --data-binary "$message" "$ALERT_WEBHOOK_URL" >/dev/null ;;
  *)
    echo "refused: ALERT_WEBHOOK_URL must be https"; exit 1 ;;
esac && echo "alert sent for $unit"
