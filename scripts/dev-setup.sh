#!/bin/bash
# Set up a fresh machine or cloud session to develop and test Memvine:
# Postgres 16 + pgvector on localhost:5433 (user/password memvine), the server's virtualenv with
# dev tools, and the console's packages. Safe to run again.
#
#   scripts/dev-setup.sh
#
# With Docker available it runs server/docker-compose.yml; otherwise (Debian/Ubuntu, e.g. a cloud
# session) it installs Postgres 16 and pgvector with apt and runs a cluster on port 5433.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  docker compose -f "$ROOT/server/docker-compose.yml" up -d --wait db
else
  SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  if ! command -v pg_ctlcluster >/dev/null || ! ls /usr/share/postgresql/*/extension/vector.control >/dev/null 2>&1; then
    $SUDO apt-get update -qq
    $SUDO apt-get install -y -qq postgresql-common >/dev/null
    yes | $SUDO /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh >/dev/null 2>&1 || true
    $SUDO apt-get install -y -qq postgresql-16 postgresql-16-pgvector >/dev/null
  fi
  if ! pg_lsclusters 2>/dev/null | grep -q "^16 *memvine "; then
    $SUDO pg_createcluster 16 memvine --port 5433 >/dev/null
  fi
  $SUDO pg_ctlcluster 16 memvine start 2>/dev/null || true
  $SUDO -u postgres psql -p 5433 -qc "DO \$\$BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memvine') THEN CREATE ROLE memvine LOGIN SUPERUSER PASSWORD 'memvine'; END IF; END\$\$;"
  $SUDO -u postgres psql -p 5433 -tAc "SELECT 1 FROM pg_database WHERE datname='memvine'" | grep -q 1 \
    || $SUDO -u postgres createdb -p 5433 -O memvine memvine
fi

cd "$ROOT/server"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -e '.[dev]'

if command -v npm >/dev/null; then
  (cd "$ROOT/console" && npm ci --silent)
fi

echo "Ready. Server tests: cd server && .venv/bin/python -m pytest -q"
