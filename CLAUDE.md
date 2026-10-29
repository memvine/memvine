# Memvine: notes for agents working on this repo

Memvine is a hosted team memory service for coding agents. Client plugins (Claude Code, Codex) send
session conversation to a server, which turns it into a small set of durable memories plus pages
for work in progress, and serves recall back into each session.

## Layout

- `server/`: Python 3.12, FastAPI, Postgres + pgvector. Ingest → synthesis (`synthesis.py`:
  thread pages via `threads.py`, knowledge via `extraction/`) → reconcile (`reconcile.py`) →
  recall (`recall.py`). Migrations in `server/memvine_server/migrations/` (numbered, applied on
  start; never edit an applied one, add a new file).
- `console/`: Next.js 15 customer console (`lib/api-live.ts` talks to the server; `lib/api-mock.ts`
  when `MEMVINE_API_URL` is unset).
- `plugins/claude-code`, `plugins/codex`: the agent plugins (hooks send turns, recall each prompt).
- `sdk/typescript`, `sdk/python`; `benchmark/`: the memory check and evaluation harnesses.
- `deploy/railway/` (hosting, in progress), `deploy/laptop/` (a team server on one Mac).
- `docs/how-memory-works.md`; `docs/plans/` holds parked plans (graph work: on hold, below).

## Setup and tests

```bash
scripts/dev-setup.sh                       # Postgres+pgvector on :5433, server venv, console packages
cd server && .venv/bin/python -m pytest -q  # tests use a fake model and fake embedder
.venv/bin/ruff check memvine_server tests ../benchmark
cd ../console && npx tsc --noEmit && npm run build
node --test plugins/codex/bin/ plugins/claude-code/bin/
```
Give parallel test runs their own database: `MEMVINE_TEST_DATABASE_URL=postgresql://memvine:memvine@localhost:5433/memvine_test_<name>`.

GitHub Actions are manual-only (the account's quota ran out): never rely on CI; run checks locally.

## The memory check: the gate for extraction and recall changes

```bash
cd server && .venv/bin/python ../benchmark/memory/check.py            # ~5 min, real model
cd server && .venv/bin/python ../benchmark/memory/check.py --agent-e2e  # + real Claude Code with/without memory (~30 min)
```
Real model calls: `MEMVINE_LLM_BACKEND=claude-cli` (a Claude subscription, local machine) or
`anthropic` with `ANTHROPIC_API_KEY`. Last results: 23/23; agent test 9/9 right with memory,
0/9 without. Don't ship a change to extraction, reconciliation or recall without running it.

## Decisions to keep

- **Too much memory is worse than too little.** A memory is kept only if it passes three tests:
  an agent on a *different* task would act differently because of it; the code, config, README,
  ticket or git history can't tell them (except traps where the code misleads); it is still true
  after the task. Work in flight, bug traces, test results, PR contents, release scope, "as of"
  states and descriptions of code are never memory. Prefer missing a marginal item over storing
  noise. (`KNOWLEDGE_SYSTEM` in `extraction/prompts.py`; cases in `benchmark/knowledge/cases.py`.)
- **One memory model.** Thread pages are for *work* (release, task, PR, ticket, multi-step effort),
  never for a single fact; durable knowledge (decision, rule, gotcha, fact, preference) is memory.
  There is no "v1/v2" anywhere.
- **Recall carries what the question needs and nothing twice**, abstains on unrelated questions,
  and keeps standing rules in their own always-on section.
- **Unreviewed memories are served.** Review (Keep / Wrong / Forget) is feedback, not a gate;
  forgotten memories become private drop cases and are never re-learned.
- **Graph work is on hold** (`docs/plans/`): plain-text memory must work for agents first.
- Examples in code, tests and docs use the invented "Northwind" billing world. Never put real
  company, customer or employer details in the repo.

## Status (28 Oct 2026)

Priorities and what's next: `ROADMAP.md` and the GitHub issues labelled now/next/later.


- Hosted on Railway (project `memvine`: `postgres`, `api`, `console`; see `deploy/railway/README.md`).
  Deploy with `railway up server --path-as-root --service api` (and `console`) or GitHub auto-deploy.
  The laptop server is retired.
  After each deploy: `MEMVINE_SMOKE_KEY=mv_mk_... python3 benchmark/smoke/hosted.py --api <url> --console <url>`
  (throwaway project, deleted at the end; `--no-synthesis` until the model key is set).
- Pending: custom domains `app.memvine.dev` / `api.memvine.dev` (the DNS provider DNS CNAMEs), the
  Anthropic API key on `api`, re-pointing agent plugins to the new API address.
- Later: security pass, terms and privacy pages, billing, SSO; the open-core split is undecided.
