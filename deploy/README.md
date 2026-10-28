# Deploying Memvine for a private beta

One host, three containers: Postgres with pgvector, the API (with its synthesis worker), and the
console. Each beta customer is an **organization**: it gets one management key, signs in to the
console with it, and sees only its own projects.

## 1. Run the stack

```sh
cd deploy
cp .env.example .env        # set POSTGRES_PASSWORD, MEMVINE_OPERATOR_KEY, ANTHROPIC_API_KEY
docker compose up -d --build
curl -s localhost:8400/health     # {"ok":true,...}
```

| Service | Port | Notes |
| --- | --- | --- |
| API | 8400 (`MEMVINE_API_PORT`) | REST, MCP at `/mcp`, worker in-process |
| Console | 3000 (`MEMVINE_CONSOLE_PORT`) | Sign-in with an organization key |
| Postgres | internal | Volume `pg`; back it up |

Put both public ports behind TLS (Caddy, nginx, a cloud load balancer). Keys travel in headers, and
the console sets its session cookie `Secure` in production, so it only works over HTTPS (or on
`localhost`). `MEMVINE_INSECURE_COOKIES=1` on the console disables that for a trusted network.

The embedding model (BGE-small, ~130 MB) downloads on first use into the `fastembed` volume.

## 2. Onboard a beta customer

Create the organization with the operator key (keep that key to yourself):

```sh
curl -s -X POST https://api.example.com/v1/orgs \
  -H "Authorization: Bearer $MEMVINE_OPERATOR_KEY" -H 'content-type: application/json' \
  -d '{"name": "Acme Inc"}'
# → {"slug": "acme-inc", "key": "mv_mk_..."}   the key is shown once
```

or, on the host: `docker compose exec api memvine-server create-org "Acme Inc"`.

Send the customer the console address and their `mv_mk_` key. They:

1. Sign in to the console, open **Projects**, create a project per repository or app, and copy the
   project key and the ready-made connect command it shows.
2. In Claude Code, in that repository:
   ```
   /plugin marketplace add memvine/memvine
   /plugin install memvine@memvine
   /memvine:connect mv_sk_... their@email.com https://api.example.com --here
   ```
   (Until the plugin is on the default branch, use a local checkout path for the marketplace.)
3. Or any MCP client: `claude mcp add --transport http memvine https://api.example.com/mcp --header "Authorization: Bearer mv_sk_..."`.

They can add or revoke organization keys through the API (`/v1/org/keys`); the console's
Settings page will grow a screen for it.

## 3. Operate

- **Usage and caps.** Projects shows each project's credits this month; set a monthly cap there to
  stop ingestion at a limit (recall keeps working). Credits: 1 per 1,000 characters ingested, 0.1
  per recall.
- **Retention.** Set per project; raw transcripts older than the window are dropped, memories stay.
- **Model spend.** Extraction runs on `ANTHROPIC_API_KEY`. Roughly one extraction call per ~6,000
  characters of conversation, plus one reconciliation call per extracted fact and a page rebuild
  per changed subject. Sonnet 5.5 costs about $35 to ingest a 100K-token BEAM conversation set
  (20 conversations); `MEMVINE_LLM_MODEL=claude-haiku-5-5` is about a tenth of that.
- **Logs.** `docker compose logs -f api`. Each job's result and error are also visible per project
  in the console (Sources) and at `GET /v1/jobs`.
- **Backups.** `docker compose exec db pg_dump -U memvine memvine > backup.sql`.
- **Upgrades.** `git pull && docker compose up -d --build`. The schema applies itself at startup
  (statements are idempotent and skipped when unchanged).

## Not in the beta yet

Payment collection (credits are metered and capped, not charged), per-user accounts inside an
organization (one shared organization key), SSO and data residency. The audit log (Settings, or `GET /v1/audit`)
records every administrative change and memory edit per organization.
