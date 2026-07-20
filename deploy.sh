#!/usr/bin/env bash
# SMR — Docker Swarm deploy script (PRD Sprint 10 §Deploy + Observability).
#
# Bootstraps a single-node swarm, builds the web image, deploys the stack,
# runs database migrations against the live `web` service, and idempotently
# creates the admin superuser.
#
# Usage:
#   ./deploy.sh                 # full deploy
#   ./deploy.sh --no-build       # skip image rebuild
#   ./deploy.sh --no-superuser  # skip superuser creation
#
# Requires: docker, .env.production present in repo root.

set -euo pipefail

STACK_NAME="${STACK_NAME:-smr}"
ENV_FILE="${ENV_FILE:-.env.production}"
WEB_IMAGE="${WEB_IMAGE:-smr/web:latest}"

# --- helpers -----------------------------------------------------------------

log() {
  printf '[deploy] %s\n' "$*"
}

require_env() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    log "ERROR: ${ENV_FILE} not found. Copy .env.production.example -> ${ENV_FILE} and fill in secrets."
    exit 1
  fi
}

# --- swarm -------------------------------------------------------------------

ensure_swarm() {
  if ! docker info --format '{{.Swarm.LocalNodeState}}' | grep -q active; then
    log "Swarm inactive. Initializing single-node swarm..."
    docker swarm init --advertise-addr "$(hostname -I | awk '{print $1}')"
  else
    log "Swarm already active."
  fi
}

# --- image -------------------------------------------------------------------

build_image() {
  if [[ "${BUILD_IMAGE:-yes}" == "no" ]]; then
    log "Skipping image build (--no-build)."
    return
  fi
  log "Building ${WEB_IMAGE}..."
  docker build -t "${WEB_IMAGE}" .
}

# --- stack -------------------------------------------------------------------

deploy_stack() {
  log "Deploying stack ${STACK_NAME}..."
  # shellcheck disable=SC1090
  set -a; source "${ENV_FILE}"; set +a
  docker stack deploy -c docker-stack.yml "${STACK_NAME}"
}

wait_for_web() {
  log "Waiting for web service to be ready..."
  local tries=0
  until docker service ps "${STACK_NAME}_web" --filter 'desired-state=running' --format '{{.CurrentState}}' | grep -q Running; do
    tries=$((tries + 1))
    if [[ ${tries} -gt 60 ]]; then
      log "Timed out waiting for ${STACK_NAME}_web to be running."
      return 1
    fi
    sleep 5
  done
  log "Web service is up."
}

# --- migrations + superuser --------------------------------------------------

exec_web() {
  # Run a one-shot command inside a fresh ephemeral container on the swarm network.
  docker run --rm \
    --env-file "${ENV_FILE}" \
    --env DJANGO_SETTINGS_MODULE=smr.settings \
    --network "${STACK_NAME}_smr_internal" \
    "${WEB_IMAGE}" "$@"
}

run_migrations() {
  log "Running migrations..."
  exec_web python3 manage.py migrate --noinput
}

collectstatic() {
  log "Collecting static files..."
  exec_web python3 manage.py collectstatic --noinput || true
}

create_superuser() {
  if [[ "${CREATE_SUPERUSER:-yes}" == "no" ]]; then
    log "Skipping superuser (--no-superuser)."
    return
  fi
  log "Ensuring admin superuser exists..."
  local superuser_script
  superuser_script=$(cat <<'PY'
import os, sys
import django
django.setup()
from accounts.models import User
username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@smr.trade")
password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
if User.objects.filter(username=username).exists():
    print(f"[superuser] {username} already exists.")
    sys.exit(0)
if not password:
    print("[superuser] DJANGO_SUPERUSER_PASSWORD not set; skipping creation.")
    sys.exit(0)
User.objects.create_superuser(username=username, email=email, password=password)
print(f"[superuser] created {username}.")
PY
)
  exec_web python3 -c "${superuser_script}"
}

# --- main --------------------------------------------------------------------

main() {
  local args=("$@")
  for arg in "${args[@]}"; do
    case "${arg}" in
      --no-build) BUILD_IMAGE=no ;;
      --no-superuser) CREATE_SUPERUSER=no ;;
      --help|-h)
        cat <<EOF
Usage: ./deploy.sh [--no-build] [--no-superuser]
EOF
        exit 0 ;;
    esac
  done

  require_env
  ensure_swarm
  build_image
  deploy_stack
  wait_for_web
  run_migrations
  collectstatic
  create_superuser

  log "Deploy of stack ${STACK_NAME} complete."
  log "Services:"
  docker service ls --filter "label=com.docker.stack.namespace=${STACK_NAME}"
}

main "$@"
