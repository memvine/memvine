# Memvine on your laptop, for a small team

Your machine runs the database, the API (with the worker that turns conversations into memory)
and the console. Tailscale Funnel gives it a free public HTTPS address, so teammates anywhere can
sign up from an invite link and connect their agents. Nothing is paid for. It works only while
the laptop is awake and online.

## You (once)

1. Install Docker and Tailscale and sign in to Tailscale. The server's Python environment and the
   console's packages are already installed (`server/.venv`, `console/node_modules`).
2. Start everything:
   ```sh
   deploy/laptop/memvine-laptop.sh start
   ```
3. Open http://127.0.0.1:3400 and create your account. The first account on a server may sign up
   without an invite; after that, sign-up needs an invite link (`MEMVINE_SIGNUP=invite`).
4. Projects you created before accounts existed live in the `default` organization. To own them:
   ```sh
   cd server && .venv/bin/memvine-server add-member you@company.com --org default --rename "Your team"
   ```
   then pick it under Settings → Switch organization.
5. Publish it:
   ```sh
   deploy/laptop/memvine-laptop.sh share
   ```
   The first time, Tailscale may print a link to allow Funnel on your tailnet: open it and approve.
   The script prints the two public addresses:

   | | Address |
   | --- | --- |
   | Console | `https://<your machine>.<tailnet>.ts.net:8443` |
   | API (plugins, MCP, SDKs) | `https://<your machine>.<tailnet>.ts.net:10000` |

   Right after `share`, the addresses can fail for a minute while Tailscale's relay picks the
   change up ("cannot open the page", or TLS errors); try again shortly.

6. Invite people: Settings → Members → Create invite link, and send each person their link. While
   the server is shared, invite links carry the public address, even when you browse the console at
   localhost.

`memvine-laptop.sh status` shows what runs where, `restart` picks up new code and stays shared, `unshare` takes it off the internet (local use
keeps working), `stop` stops it all. Logs are in `~/.memvine/server/`.

## Your teammates

1. Open the invite link, create an account. They land in your organization as a member.
2. In the console, pick the project, open **API keys**, create a key. The confirmation shows the exact
   command to run.
3. In Claude Code, in their checkout of that repository:
   ```
   /plugin marketplace add memvine/memvine
   /plugin install memvine@memvine
   /memvine:connect mv_sk_... their@email.com https://<your machine>.<tailnet>.ts.net:10000 --here
   ```
   Every session in that folder then starts briefed from the team's memory, and what is said in it
   becomes memory. `/memvine:backfill` imports their earlier Claude Code sessions of this repository.

Codex, Cursor and other MCP clients: see `plugins/claude-code/README.md`, with the API address above.

## What it costs and what to watch

- **Model spend.** Without `ANTHROPIC_API_KEY`, the worker uses your Claude subscription through
  `claude -p`. A few people's daily sessions fit; heavy use hits the subscription's limits, and the
  worker then pauses for five minutes at a time (memory arrives later, nothing is lost). With an API
  key in `~/.memvine/server/env` (`ANTHROPIC_API_KEY=...`), restart and it uses the API instead.
- **Caps.** Projects → ··· → Edit settings sets a monthly credit cap per project.
- **Pausing.** `PATCH /v1/projects/<slug>` with `{"processing_paused": true}` keeps what is sent
  but runs no synthesis for that project until resumed.
- **Who can get in.** Only people with an invite link (or anyone, with `MEMVINE_SIGNUP=open` in
  `~/.memvine/server/env`). Failed sign-ins are throttled. The operator key in
  `~/.memvine/server/operator-key` administers every organization: keep it on this machine.
- **Backups.** `deploy/laptop/memvine-laptop.sh backup` dumps the database to `~/.memvine/backups`
  (the newest 14 nightly dumps are kept); `schedule-backups` runs it every night at 03:17 through
  launchd (a run missed while the laptop sleeps happens when it wakes), `unschedule-backups` stops
  it, and `restore-test` restores the newest dump into a scratch database and compares its counts
  with the live one. A real restore: `docker exec -i $(docker compose -f server/docker-compose.yml ps -q db)
  pg_restore -U memvine -d memvine --clean --no-owner < <dump>` with the server stopped.
