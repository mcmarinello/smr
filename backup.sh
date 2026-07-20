#!/usr/bin/env bash
# SMR — database backup script (PRD Sprint 10 §Deploy e Observabilidade).
#
# Runs pg_dump against the live `db` service (or a local Postgres if invoked
# outside the swarm), writes a timestamped gzip dump, and optionally uploads
# it to S3 when AWS_S3_BUCKET is set.
#
# Usage:
#   ./backup.sh                       # local dump
#   ./backup.sh --remote               # exec pg_dump inside the swarm db service
#   ./backup.sh --out /backups         # custom output dir
#
# Restore test:
#   gunzip -c smr_YYYYmmdd_HHMMSS.sql.gz | psql postgres://smr:...@db:5432/smr_restore

set -euo pipefail

STACK_NAME="${STACK_NAME:-smr}"
OUT_DIR="${OUT_DIR:-./backups}"
TS="$(date -u +%Y%m%d_%H%M%S)"
FILE_NAME="smr_${TS}.sql.gz"

log() { printf '[backup] %s\n' "$*"; }

mkdir -p "${OUT_DIR}"
OUT_PATH="${OUT_DIR%/}/${FILE_NAME}"

run_remote=0
for arg in "$@"; do
  case "${arg}" in
    --remote) run_remote=1 ;;
    --out) shift_next=1 ;;
    *)
      if [[ "${shift_next:-0}" == "1" ]]; then
        OUT_DIR="${arg}"
        mkdir -p "${OUT_DIR}"
        OUT_PATH="${OUT_DIR%/}/${FILE_NAME}"
        shift_next=0
      fi
      ;;
  esac
done

# Pull DB coordinates from .env.production if available, else environment.
if [[ -f .env.production ]]; then
  # shellcheck disable=SC1091
  set -a; source .env.production; set +a
fi

DB_URL="${DATABASE_URL:-postgres://smr:smrpass@localhost:5432/smr}"

if [[ ${run_remote} -eq 1 ]]; then
  log "Running pg_dump inside swarm service ${STACK_NAME}_db..."
  docker service ps --filter "name=${STACK_NAME}_db" --format "{{.Name}}" | head -1 | while read -r task; do
    :
  done
  # Stream the dump out of the running db container.
  docker exec "$(
    docker service ps --filter "desired-state=running" --format "{{.Name}}.{{.ID}}" "${STACK_NAME}_db" | head -1
  )" pg_dump --clean --if-exists --quote-all-identifiers "${DB_URL}" | gzip > "${OUT_PATH}"
else
  log "Running local pg_dump..."
  if ! command -v pg_dump >/dev/null 2>&1; then
    log "pg_dump not installed locally. Use './backup.sh --remote'."
    exit 1
  fi
  pg_dump --clean --if-exists --quote-all-identifiers "${DB_URL}" | gzip > "${OUT_PATH}"
fi

log "Wrote ${OUT_PATH} ($(du -h "${OUT_PATH}" | cut -f1))."

# --- S3 upload (optional) ----------------------------------------------------

if [[ -n "${AWS_S3_BUCKET:-}" ]]; then
  if ! command -v aws >/dev/null 2>&1; then
    log "AWS_S3_BUCKET set but 'aws' CLI not installed. Skipping upload."
    exit 0
  fi
  s3_path="s3://${AWS_S3_BUCKET%/}/backups/${FILE_NAME}"
  log "Uploading to ${s3_path}..."
  aws s3 cp "${OUT_PATH}" "${s3_path}" \
    --region "${AWS_S3_REGION:-us-east-1}" \
    ${AWS_S3_KMS_KEY_ID:+--sse aws:kms}
  log "Upload complete."
else
  log "AWS_S3_BUCKET not set; S3 upload skipped (placeholder)."
fi
