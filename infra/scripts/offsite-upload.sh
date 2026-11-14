#!/usr/bin/env bash
# Encrypt one verified dump and copy it to S3-compatible storage (R2, B2, S3).
# Does nothing until /etc/seo-autopilot/backup.env provides the settings below.
#
#   OFFSITE_S3_URL=https://<account>.r2.cloudflarestorage.com/<bucket>/seo-autopilot
#   OFFSITE_S3_REGION=auto
#   OFFSITE_S3_KEY_ID=...
#   OFFSITE_S3_SECRET=...
#   OFFSITE_PASSPHRASE_FILE=/etc/seo-autopilot/backup.pass
#
# Keep a copy of the passphrase away from this server: without it the copies
# cannot be restored. Expire old objects with a lifecycle rule on the bucket.
#
# Restore: gpg --batch --pinentry-mode loopback --passphrase-file PASS -d FILE.gpg > FILE.dump
set -euo pipefail

DUMP="${1:?dump file}"
if [ -z "${OFFSITE_S3_URL:-}" ]; then
  echo "off-site copy skipped: OFFSITE_S3_URL is not set"
  exit 0
fi
: "${OFFSITE_S3_KEY_ID:?OFFSITE_S3_KEY_ID is required}"
: "${OFFSITE_S3_SECRET:?OFFSITE_S3_SECRET is required}"
: "${OFFSITE_PASSPHRASE_FILE:?OFFSITE_PASSPHRASE_FILE is required}"
case "$OFFSITE_S3_URL" in https://*) ;; *) echo "refused: OFFSITE_S3_URL must be https" >&2; exit 1 ;; esac

ENCRYPTED="$(mktemp "${TMPDIR:-/tmp}/offsite.XXXXXX")"
trap 'rm -f "$ENCRYPTED"' EXIT
gpg --batch --yes --pinentry-mode loopback --passphrase-file "$OFFSITE_PASSPHRASE_FILE" \
  --symmetric --cipher-algo AES256 --output "$ENCRYPTED" "$DUMP"

TARGET="${OFFSITE_S3_URL%/}/$(basename "$DUMP").gpg"
# The credentials go through a config on stdin so they never appear in the process list.
printf 'user = "%s:%s"\n' "$OFFSITE_S3_KEY_ID" "$OFFSITE_S3_SECRET" | curl --config - \
  --fail --silent --show-error --max-time 1800 \
  --aws-sigv4 "aws:amz:${OFFSITE_S3_REGION:-auto}:s3" \
  --upload-file "$ENCRYPTED" "$TARGET" >/dev/null
echo "off-site copy ok: $(basename "$TARGET")"
