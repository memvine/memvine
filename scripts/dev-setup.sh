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
# The server needs Python 3.12 or later, and the python3 a Mac ships with is older: with it the venv
# is made and the install then fails. Take the newest interpreter that qualifies, else uv's.
new_enough='import sys; sys.exit(sys.version_info < (3, 12))'
if [ ! -d .venv ]; then
  PY=""
  for candidate in python3.13 python3.12 python3; do
    # 2>/dev/null: a pyenv shim for a version that is not selected exists and fails loudly.
    if command -v "$candidate" >/dev/null && "$candidate" -c "$new_enough" 2>/dev/null; then PY="$candidate"; break; fi
  done
  if [ -n "$PY" ]; then
    "$PY" -m venv .venv
  elif command -v uv >/dev/null; then
    uv venv -q --python 3.12 .venv
  else
    echo "The server needs Python 3.12 or later. Install it (brew install python@3.12, or https://docs.astral.sh/uv/) and run this again." >&2
    exit 1
  fi
fi
if ! .venv/bin/python -c "$new_enough"; then
  echo "server/.venv was made with a Python older than 3.12. Remove server/.venv and run this again." >&2
  exit 1
fi
if .venv/bin/python -m pip --version >/dev/null 2>&1; then
  .venv/bin/python -m pip install -q -e '.[dev]'
else
  uv pip install -q --python .venv/bin/python -e '.[dev]'   # a venv made by uv has no pip of its own
fi

if command -v npm >/dev/null; then
  (cd "$ROOT/console" && npm ci --silent)
fi

echo "Ready. Server tests: cd server && .venv/bin/python -m pytest -q"
