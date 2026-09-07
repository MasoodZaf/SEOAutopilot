#!/usr/bin/env bash
# A backup that is verified to restore, kept for a bounded time.
#
# The deployment guide has always described taking a dump by hand before a
# risky change. That covers the risky changes somebody remembers to be worried
# about, which is not the same set as the ones that lose data. It also never
# established that any dump could be restored -- an untested backup is a belief,
# not a recovery plan, and the moment it is disproved is the worst one.
#
# So this runs on a timer, and every dump is restored into a scratch database
# and counted before it is kept. A dump that cannot be restored is deleted and
# reported as a failure, because a corrupt file in the backup directory is worse
# than none: it looks like a backup.
#
#   infra/scripts/backup.sh [--keep-days N] [--dir PATH]
set -euo pipefail

KEEP_DAYS=14
BACKUP_DIR="/opt/seo-autopilot/backups"
DB_USER="${POSTGRES_USER:-seo_autopilot}"
DB_NAME="${POSTGRES_DB:-seo_autopilot}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

while [ $# -gt 0 ]; do
  case "$1" in
    --keep-days) KEEP_DAYS="$2"; shift 2 ;;
    --dir) BACKUP_DIR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="$BACKUP_DIR/seo_autopilot_$STAMP.dump"
VERIFY_DB="restore_check_$STAMP"

fail() { echo "backup FAILED: $1" >&2; rm -f "$TARGET"; exit 1; }

$COMPOSE exec -T postgres pg_dump -U "$DB_USER" -d "$DB_NAME" --format=custom \
  > "$TARGET" || fail "pg_dump did not complete"

[ -s "$TARGET" ] || fail "the dump is empty"

# The half that makes this a recovery plan rather than a hope. Restoring into a
# scratch database proves the file is readable and complete, and counting a
# table nobody would expect to be empty proves it is not merely a valid archive
# of nothing.
$COMPOSE exec -T postgres createdb -U "$DB_USER" "$VERIFY_DB" || fail "could not create the verification database"
cleanup() { $COMPOSE exec -T postgres dropdb -U "$DB_USER" --if-exists "$VERIFY_DB" >/dev/null 2>&1 || true; }
trap cleanup EXIT

$COMPOSE exec -T postgres pg_restore -U "$DB_USER" -d "$VERIFY_DB" --no-owner --exit-on-error \
  < "$TARGET" || fail "the dump did not restore"

TENANTS="$($COMPOSE exec -T postgres psql -U "$DB_USER" -d "$VERIFY_DB" -tAc \
  'SELECT count(*) FROM tenant' | tr -d '[:space:]')"
[ "${TENANTS:-0}" -ge 1 ] || fail "the restored database has no tenants"

SITES="$($COMPOSE exec -T postgres psql -U "$DB_USER" -d "$VERIFY_DB" -tAc \
  'SELECT count(*) FROM site' | tr -d '[:space:]')"

# Only prune once this dump is known good, so a run that fails verification
# never removes the last backup that worked.
find "$BACKUP_DIR" -name 'seo_autopilot_*.dump' -type f -mtime "+$KEEP_DAYS" -delete

SIZE="$(du -h "$TARGET" | cut -f1)"
echo "backup ok: $TARGET ($SIZE), restored and verified: tenants=$TENANTS sites=$SITES"
