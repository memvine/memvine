# Running your own Memvine server

One host, three containers: Postgres with pgvector, the API (with its synthesis worker), and the
console. You need Docker and a key for a model, which turns conversation into memory: Anthropic's
by default, or any server that speaks the OpenAI API ("Other models" below). People are grouped into
**organizations**: each gets a management key, signs in to the console with it, and sees only its
own projects. For one person or one team, one organization is enough.

Other ways to run it: `deploy/railway/` (the hosted setup) and `deploy/laptop/` (a team server on
one Mac, using a Claude subscription instead of an API key).

## 1. Run the stack

```sh
cd deploy
cp .env.example .env        # set POSTGRES_PASSWORD, MEMVINE_OPERATOR_KEY and the model's key
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

Create the organization with the operator key (keep that key to yourself). On the host, in `deploy/`:

```sh
set -a; . ./.env; set +a          # reads MEMVINE_OPERATOR_KEY from .env into this shell
curl -s -X POST localhost:8400/v1/orgs \
  -H "Authorization: Bearer $MEMVINE_OPERATOR_KEY" -H 'content-type: application/json' \
  -d '{"name": "Northwind"}'
# → {"slug": "northwind", "key": "mv_mk_..."}   the key is shown once
```

or `docker compose exec api memvine-server create-org "Northwind"`. From another machine, use the
server's public address (written `https://api.example.com` below) in place of `localhost:8400`.

With the console address and that `mv_mk_` key:

1. Sign in to the console, open **Projects**, create a project per repository or app, and copy the
   project key and the ready-made connect command it shows. Or without the console:
   ```sh
   curl -s -X POST localhost:8400/v1/projects -H "Authorization: Bearer mv_mk_..." \
     -H 'content-type: application/json' -d '{"name": "Billing"}'             # → {"slug": "billing", ...}
   curl -s -X POST localhost:8400/v1/projects/billing/keys -H "Authorization: Bearer mv_mk_..." \
     -H 'content-type: application/json' -d '{"name": "laptop"}'              # → {"key": "mv_sk_..."}
   ```
   To try it before sending anything real: `POST /v1/sample` with the project key loads a small
   sample memory with no model call, and `POST /v1/recall` with `{"query": "When is the launch?"}`
   answers from it. (Recall asks the fast model which memories the question needs and falls back to
   similarity when the model cannot be reached, so it answers even with a placeholder API key.)
2. In Claude Code, in that repository:
   ```
   /plugin marketplace add memvine/memvine
   /plugin install memvine@memvine
   /memvine:connect mv_sk_... you@example.com https://api.example.com --here
   ```
3. Or any MCP client: `claude mcp add --transport http memvine https://api.example.com/mcp --header "Authorization: Bearer mv_sk_..."`.

Organization keys are added and revoked through the API (`/v1/org/keys`).

**Who can create an account.** With this compose file, only someone holding an invite link from an
organization admin (`MEMVINE_SIGNUP=invite`); the organization key above always signs in. Set
`MEMVINE_SIGNUP=open` in `.env` to let anyone who can reach the console create an account and an
organization of their own, or `closed` for neither. Any other server setting from
`server/README.md` goes in a file `deploy/.env.api`, one `NAME=value` per line, which the API
container reads when it exists.

**For a team whose members keep their sessions to themselves.** In the console's **New project**:
an owner or admin creates one **Team space**, and gives its key to the team; each person creates
their own project (**Only me**), connects their agent to it with the command shown, and then adds
the team space with `/memvine:team mv_sk_<team space key> https://api.example.com`. Sessions go to
each person's own project, which nobody else in the organization can open; the team space refuses
sessions and holds what people share (`docs/how-memory-works.md`, "Your own memory and the
team's"). Whoever holds the operator key and the database can still read everything on the server.

A project is in `coding` mode unless you choose otherwise: it keeps few, durable memories. Choose
`conversation` for one person's long conversation with an assistant
(`memory_mode` on the project; `docs/how-memory-works.md`).

## 3. Operate

- **Usage and caps.** Projects shows each project's credits this month; set a monthly cap there to
  stop ingestion at a limit (recall keeps working). Credits only count usage, nothing is charged:
  1 per 1,000 characters ingested, 0.1 per recall.
- **Retention.** Set per project; raw transcripts older than the window are dropped, memories stay.
- **Model spend.** Extraction runs on the model key in `.env`. Roughly one extraction call per ~6,000
  characters of conversation, plus one reconciliation call per extracted fact and a page rebuild
  per changed subject. Sonnet 5.5 costs about $35 to ingest a 100K-token BEAM conversation set
  (20 conversations); `MEMVINE_LLM_MODEL=claude-haiku-5-5` is about a tenth of that.
- **Logs.** `docker compose logs -f api`. Each job's result and error are also visible per project
  in the console (Sources) and at `GET /v1/jobs`.
- **Backups.** `docker compose exec db pg_dump -U memvine memvine > backup.sql`.
- **Upgrades.** Back up, then `git pull && docker compose up -d --build`. Pending schema migrations
  apply at startup, each in a transaction (`docker compose exec api memvine-server migrations`
  lists them; see "Schema and migrations" in `server/README.md`).

## Other models

Memvine is built and measured on Anthropic's models. It can use any server that speaks the OpenAI
chat completions API instead. In `.env`, in place of `ANTHROPIC_API_KEY`:

```sh
MEMVINE_LLM_BACKEND=openai
MEMVINE_LLM_API_KEY=sk-...
MEMVINE_LLM_MODEL=gpt-5.6
```

| Provider | `MEMVINE_LLM_BASE_URL` |
| --- | --- |
| OpenAI | leave it out |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Ollama on the same machine | `http://host.docker.internal:11434/v1`, and no key |
| Anything else | the address its documentation gives for the OpenAI-compatible API |

`MEMVINE_LLM_MODEL_FAST` names a cheaper model for the many small judgements (which memory a new
fact replaces, which memories a question needs); without it they run on `MEMVINE_LLM_MODEL`.

What to expect:

- **Quality is not measured on other models.** The prompts were written against Claude, and the
  memory check (`benchmark/memory`) has only been run on Claude. Run it on your model before you
  trust it: `MEMVINE_LLM_BACKEND=openai` with the settings above, then the command in that README.
- **Use a capable model.** It has to follow a long instruction and reply in exact JSON. In one
  trial an 8-billion-parameter local model chose the right two memories from a short session but
  wrote JSON that could not be read, so nothing was kept, and it took six minutes.
- **A local model needs a large context window.** The instruction is about 1,500 tokens, and the
  conversation, the model's thinking and its reply come on top. In the same trial Ollama's default
  window of 4,096 tokens overflowed and the reply was nonsense; `OLLAMA_CONTEXT_LENGTH=32768`
  fixed that.
- The server starts without checking the key. A wrong key or model name shows as failed jobs in
  the console (Sources) and in `docker compose logs api`.

## Not built

Password reset by email and SSO. The audit log (Settings, or `GET /v1/audit`)
records every administrative change and memory edit per organization.
