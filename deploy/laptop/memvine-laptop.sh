#!/bin/bash
# Run Memvine on this machine for a small team: Postgres (Docker), the API with its worker, and a
# production build of the console. `share` publishes both over Tailscale Funnel (free HTTPS on
# your tailnet name), so teammates anywhere can sign up from an invite link.
#
#   deploy/laptop/memvine-laptop.sh start     start the database, API and console
#   deploy/laptop/memvine-laptop.sh share     publish them on https://<this machine>.ts.net
#   deploy/laptop/memvine-laptop.sh restart   pick up new code (API and console), staying shared
#   deploy/laptop/memvine-laptop.sh status    what is running and where
#   deploy/laptop/memvine-laptop.sh unshare   stop publishing (local use keeps working)
#   deploy/laptop/memvine-laptop.sh stop      unshare and stop everything
#   deploy/laptop/memvine-laptop.sh backup    dump the database now (keeps the newest 14)
#   deploy/laptop/memvine-laptop.sh restore-test   restore the newest dump into a scratch database
#                                             and check it holds the projects and memories
#   deploy/laptop/memvine-laptop.sh schedule-backups    back up every night at 03:17 (launchd)
#   deploy/laptop/memvine-laptop.sh unschedule-backups  stop the nightly backup
#
# Settings (environment, or ~/.memvine/server/env):
#   MEMVINE_SIGNUP       invite (default: only invite links; the first account may sign up freely),
#                        open (anyone who has the address), closed
#   ANTHROPIC_API_KEY    synthesis on the API; without it, `claude -p` on this machine's
#                        Claude subscription (fine for a few people, rate-limited)
#   API_PORT / CONSOLE_PORT   local ports (8400 / 3400)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATE="$HOME/.memvine/server"
mkdir -p "$STATE"
chmod 700 "$STATE"
[ -f "$STATE/env" ] && set -a && . "$STATE/env" && set +a

API_PORT="${API_PORT:-8400}"
CONSOLE_PORT="${CONSOLE_PORT:-3400}"
# Funnel serves on 443, 8443 and 10000 only. 443 is often taken by `tailscale serve`; these are not.
PUBLIC_CONSOLE_PORT=8443
PUBLIC_API_PORT=10000

log() { printf '%s\n' "$*"; }

alive() { [ -f "$STATE/$1.pid" ] && kill -0 "$(cat "$STATE/$1.pid")" 2>/dev/null; }

ts_host() {
  command -v tailscale >/dev/null || return 1
  tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'
}

# Public addresses while shared (Funnel on), local ones otherwise.
is_shared() {
  local funnel; funnel="$(tailscale funnel status 2>/dev/null || true)"
  grep -q ":$PUBLIC_CONSOLE_PORT" <<< "$funnel"
}

public_api_url() {
  local host
  if is_shared && host="$(ts_host)" && [ -n "$host" ]; then echo "https://$host:$PUBLIC_API_PORT"; else echo "http://127.0.0.1:$API_PORT"; fi
}

public_console_url() {
  local host
  if is_shared && host="$(ts_host)" && [ -n "$host" ]; then echo "https://$host:$PUBLIC_CONSOLE_PORT"; else echo "http://127.0.0.1:$CONSOLE_PORT"; fi
}

operator_key() {
  # The operator key administers every organization on this server. It stays on this machine.
  if [ ! -f "$STATE/operator-key" ]; then
    (umask 077; printf 'mv_mk_%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" > "$STATE/operator-key")
  fi
  cat "$STATE/operator-key"
}

wait_for() {
  for _ in $(seq 1 90); do curl -sf -o /dev/null "$1" && return 0; sleep 1; done
  log "Timed out waiting for $1"; return 1
}

start() {
  log "Database"
  docker compose -f "$ROOT/server/docker-compose.yml" up -d --wait db >/dev/null

  if alive api; then log "API already running (pid $(cat "$STATE/api.pid"))"; else
    local backend=claude-cli
    [ -n "${ANTHROPIC_API_KEY:-}" ] && backend=anthropic
    log "API on 127.0.0.1:$API_PORT (synthesis: $backend, sign-up: ${MEMVINE_SIGNUP:-invite})"
    [ "$backend" = claude-cli ] && log "  No ANTHROPIC_API_KEY: synthesis uses this machine's Claude subscription."
    (cd "$ROOT/server" || exit 1
      MEMVINE_MANAGEMENT_KEY="$(operator_key)" MEMVINE_SIGNUP="${MEMVINE_SIGNUP:-invite}" \
      MEMVINE_LLM_BACKEND="${MEMVINE_LLM_BACKEND:-$backend}" MEMVINE_EMBEDDINGS_BACKEND=fastembed \
      nohup .venv/bin/memvine-server serve --host 127.0.0.1 --port "$API_PORT" >> "$STATE/api.log" 2>&1 < /dev/null &
      echo $! > "$STATE/api.pid")
    wait_for "http://127.0.0.1:$API_PORT/health"
  fi

  if alive console; then log "Console already running (pid $(cat "$STATE/console.pid"))"; else
    log "Console on 127.0.0.1:$CONSOLE_PORT (building; a minute the first time)"
    (cd "$ROOT/console" && NEXT_DIST_DIR=.next-laptop ./node_modules/.bin/next build >> "$STATE/console.log" 2>&1 < /dev/null) || { log "Console build failed; see $STATE/console.log"; exit 1; }
    public_api_url > "$STATE/console.public"
    public_console_url > "$STATE/console.origin"
    (cd "$ROOT/console" || exit 1
      NEXT_DIST_DIR=.next-laptop MEMVINE_API_URL="http://127.0.0.1:$API_PORT" \
      MEMVINE_PUBLIC_API_URL="$(cat "$STATE/console.public")" \
      MEMVINE_PUBLIC_CONSOLE_URL="$(cat "$STATE/console.origin")" \
      nohup ./node_modules/.bin/next start -H 127.0.0.1 -p "$CONSOLE_PORT" >> "$STATE/console.log" 2>&1 < /dev/null &
      echo $! > "$STATE/console.pid")
    wait_for "http://127.0.0.1:$CONSOLE_PORT/login"
  fi
  status
}

share() {
  local host
  host="$(ts_host)" || { log "Tailscale is not running. Install it and sign in: https://tailscale.com/download"; exit 1; }
  # The first time, Tailscale may print a link to enable Funnel for your tailnet and wait until
  # you approve it in the browser.
  log "Publishing with Tailscale Funnel (if a link appears, open it to allow Funnel, once)."
  tailscale funnel --bg --https="$PUBLIC_CONSOLE_PORT" "http://127.0.0.1:$CONSOLE_PORT"
  tailscale funnel --bg --https="$PUBLIC_API_PORT" "http://127.0.0.1:$API_PORT"
  # The console shows the public API address in connect commands: restart it if it was started
  # before sharing.
  if alive console && [ "$(cat "$STATE/console.origin" 2>/dev/null)" != "$(public_console_url)" ]; then
    stop_one console; start >/dev/null
  fi
  log "Shared. Anyone with these addresses can reach the sign-in page and the API:"
  log "  Console  https://$host:$PUBLIC_CONSOLE_PORT"
  log "  API      https://$host:$PUBLIC_API_PORT"
}

unshare() {
  command -v tailscale >/dev/null || return 0
  # `funnel ... off` on a port that is not shared waits forever: only turn off what is on.
  local shared; shared="$(tailscale funnel status 2>/dev/null || true)"
  for port in "$PUBLIC_CONSOLE_PORT" "$PUBLIC_API_PORT"; do
    if grep -q ":$port" <<< "$shared"; then tailscale funnel --https="$port" off < /dev/null >/dev/null 2>&1 || true; fi
  done
  log "Not shared."
}

stop_one() {
  if alive "$1"; then
    local pid; pid="$(cat "$STATE/$1.pid")"
    # npx and the server start child processes: stop the whole group.
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
    # The API finishes or requeues a running job before it exits (up to about a minute).
    for _ in $(seq 1 120); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  fi
  # Whatever still listens on the port (a grandchild of npx) goes too.
  local port; [ "$1" = api ] && port="$API_PORT" || port="$CONSOLE_PORT"
  lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
  rm -f "$STATE/$1.pid"
}

stop() {
  unshare
  stop_one console
  stop_one api
  log "Stopped the API and console. The database keeps running (docker compose -f server/docker-compose.yml stop)."
}

status() {
  local host=""
  host="$(ts_host 2>/dev/null)" || true
  alive api && log "API      http://127.0.0.1:$API_PORT  (log: $STATE/api.log)" || log "API      stopped"
  alive console && log "Console  http://127.0.0.1:$CONSOLE_PORT  (log: $STATE/console.log)" || log "Console  stopped"
  # Captured first: with pipefail, grep -q closing the pipe early would fail the whole test.
  local funnel; funnel="$(tailscale funnel status 2>/dev/null || true)"
  if [ -n "$host" ] && grep -q ":$PUBLIC_CONSOLE_PORT" <<< "$funnel"; then
    log "Shared   https://$host:$PUBLIC_CONSOLE_PORT  (API https://$host:$PUBLIC_API_PORT)"
  else
    log "Shared   no (run: $0 share)"
  fi
  log "Operator key (administers every organization; keep it to yourself): $STATE/operator-key"
}

# Backups: custom-format pg_dump files in ~/.memvine/backups, the newest BACKUP_KEEP kept.
BACKUPS="$HOME/.memvine/backups"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
PLIST="$HOME/Library/LaunchAgents/dev.memvine.backup.plist"

db_container() { docker compose -f "$ROOT/server/docker-compose.yml" ps -q db; }

backup() {
  mkdir -p "$BACKUPS"; chmod 700 "$BACKUPS"
  local c; c="$(db_container)"; [ -n "$c" ] || { log "The database is not running; start it first."; exit 1; }
  local file="$BACKUPS/memvine-nightly-$(date +%Y%m%d-%H%M%S).dump"
  docker exec "$c" pg_dump -U memvine -Fc memvine > "$file.part" && mv "$file.part" "$file"
  chmod 600 "$file"
  # Only nightly dumps rotate; dumps taken by hand under other names are left alone.
  ls -1t "$BACKUPS"/memvine-nightly-*.dump 2>/dev/null | tail -n +"$((BACKUP_KEEP + 1))" | while read -r old; do rm -f "$old"; done
  log "Backed up to $file ($(du -h "$file" | cut -f1)); $(ls -1 "$BACKUPS"/memvine-nightly-*.dump | wc -l | tr -d ' ') nightly dumps kept"
}

restore_test() {
  local file; file="$(ls -1t "$BACKUPS"/memvine-nightly-*.dump 2>/dev/null | head -1)"
  [ -n "$file" ] || { log "No nightly dump yet; run: $0 backup"; exit 1; }
  local c; c="$(db_container)"
  docker exec "$c" psql -U memvine -qc "DROP DATABASE IF EXISTS memvine_restore_check" -c "CREATE DATABASE memvine_restore_check"
  docker exec -i "$c" pg_restore -U memvine -d memvine_restore_check --no-owner < "$file"
  local live restored
  live="$(docker exec "$c" psql -U memvine -d memvine -Atc "SELECT count(*) FROM projects")|$(docker exec "$c" psql -U memvine -d memvine -Atc "SELECT count(*) FROM memory_items")"
  restored="$(docker exec "$c" psql -U memvine -d memvine_restore_check -Atc "SELECT count(*) FROM projects")|$(docker exec "$c" psql -U memvine -d memvine_restore_check -Atc "SELECT count(*) FROM memory_items")"
  docker exec "$c" psql -U memvine -qc "DROP DATABASE memvine_restore_check"
  log "Restored $(basename "$file"): projects|memories = $restored (live now: $live)"
}

schedule_backups() {
  mkdir -p "$(dirname "$PLIST")" "$BACKUPS"
  cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.memvine.backup</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$ROOT/deploy/laptop/memvine-laptop.sh</string><string>backup</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>17</integer></dict>
  <key>StandardOutPath</key><string>$BACKUPS/backup.log</string>
  <key>StandardErrorPath</key><string>$BACKUPS/backup.log</string>
</dict></plist>
PLIST_EOF
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  log "Nightly backup at 03:17 (a missed run happens when the laptop wakes); log: $BACKUPS/backup.log"
}

unschedule_backups() {
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  log "Nightly backup stopped"
}

case "${1:-}" in
  start) start ;;
  restart) stop_one console; stop_one api; start ;;
  share) share ;;
  unshare) unshare ;;
  stop) stop ;;
  status) status ;;
  backup) backup ;;
  restore-test) restore_test ;;
  schedule-backups) schedule_backups ;;
  unschedule-backups) unschedule_backups ;;
  *) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
