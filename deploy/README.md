# Running your own Memvine server

One host, three containers: Postgres with pgvector, the API (with its synthesis worker), and the
console. You need Docker and an Anthropic API key (extraction runs on it). People are grouped into
**organizations**: each gets a management key, signs in to the console with it, and sees only its
own projects. For one person or one team, one organization is enough.

Other ways to run it: `deploy/railway/` (the hosted setup) and `deploy/laptop/` (a team server on
one Mac, using a Claude subscription instead of an API key).

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

## 2. Create an organization and connect an agent

Create the organization with the operator key (keep that key to yourself):

```sh
curl -s -X POST https://api.example.com/v1/orgs \
  -H "Authorization: Bearer $MEMVINE_OPERATOR_KEY" -H 'content-type: application/json' \
  -d '{"name": "Northwind"}'
# → {"slug": "northwind", "key": "mv_mk_..."}   the key is shown once
```

or, on the host: `docker compose exec api memvine-server create-org "Northwind"`.

With the console address and that `mv_mk_` key:

1. Sign in to the console, open **Projects**, create a project per repository or app, and copy the
   project key and the ready-made connect command it shows.
2. In Claude Code, in that repository:
   ```
   /plugin marketplace add memvine/memvine
   /plugin install memvine@memvine
   /memvine:connect mv_sk_... you@example.com https://api.example.com --here
   ```
   (Until the plugin is on the default branch, use a local checkout path for the marketplace.)
3. Or any MCP client: `claude mcp add --transport http memvine https://api.example.com/mcp --header "Authorization: Bearer mv_sk_..."`.

Organization keys are added and revoked through the API (`/v1/org/keys`).

A project is in `coding` mode unless you choose otherwise: it keeps few, durable memories. Choose
`conversation` for a long conversation with an assistant, where every concrete detail is kept
(`memory_mode` on the project; `docs/how-memory-works.md`).

## 3. Operate

- **Usage and caps.** Projects shows each project's credits this month; set a monthly cap there to
  stop ingestion at a limit (recall keeps working). Credits only count usage, nothing is charged:
  1 per 1,000 characters ingested, 0.1 per recall.
- **Retention.** Set per project; raw transcripts older than the window are dropped, memories stay.
- **Model spend.** Extraction runs on `ANTHROPIC_API_KEY`. Roughly one extraction call per ~6,000
  characters of conversation, plus one reconciliation call per extracted fact and a page rebuild
  per changed subject. Sonnet 5.5 costs about $35 to ingest a 100K-token BEAM conversation set
  (20 conversations); `MEMVINE_LLM_MODEL=claude-haiku-5-5` is about a tenth of that.
- **Logs.** `docker compose logs -f api`. Each job's result and error are also visible per project
  in the console (Sources) and at `GET /v1/jobs`.
- **Backups.** `docker compose exec db pg_dump -U memvine memvine > backup.sql`.
- **Upgrades.** Back up, then `git pull && docker compose up -d --build`. Pending schema migrations
  apply at startup, each in a transaction (`docker compose exec api memvine-server migrations`
  lists them; see "Schema and migrations" in `server/README.md`).

## Not built

Password reset by email and SSO. The audit log (Settings, or `GET /v1/audit`)
records every administrative change and memory edit per organization.
