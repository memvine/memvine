# Contributing to memvine

Thanks for your interest! memvine is young and contributions genuinely shape it.

This repository holds two things: the Memvine memory service (`server/`, `console/`, `plugins/`,
`sdk/`, `deploy/`, `benchmark/`), and the earlier git-native CLI (`src/`, published on npm as
`memvine`). They are set up and tested separately.

## The memory service

You need Docker (or Debian/Ubuntu, where the script installs Postgres itself), Python 3.12 or later,
and Node 22 or later.

```bash
git clone https://github.com/memvine/memvine
cd memvine
scripts/dev-setup.sh                         # Postgres + pgvector on :5433, server/.venv, console packages
cd server && .venv/bin/python -m pytest -q   # a fake model and a fake embedder: no key needed
.venv/bin/ruff check memvine_server tests ../benchmark
cd ../console && npx tsc --noEmit && npm run build
cd .. && node --test plugins/codex/bin/ plugins/claude-code/bin/
```

- `server/README.md` has the routes, the settings and how migrations work. A schema change is a new
  numbered file in `server/memvine_server/migrations/`; an applied one is never edited.
- A change to extraction, reconciliation or recall is checked with a real model before it ships:
  `cd server && .venv/bin/python ../benchmark/memory/check.py` (about five minutes; needs
  `ANTHROPIC_API_KEY` with `MEMVINE_LLM_BACKEND=anthropic`, or a local Claude subscription). Its
  results are quoted for Claude; a run on another model (`MEMVINE_LLM_BACKEND=openai`) says so.
- Examples in code, tests and docs use the invented "Northwind" billing company. Never put a real
  company's, customer's or employer's details in the repository.
- CI does not run on pushes: run the checks above before opening a pull request.

## The git-native CLI

```bash
npm install
npm run build
npm test
```

Its principles (please keep these, for the CLI in `src/`):

1. **No LLM calls inside the CLI.** Judgment belongs to the calling agent via
   MCP tool descriptions. It stays pure git + files.
2. **No servers, no databases, no accounts.** That is what the memory service is for.
3. **The store format is the API.** `.memvine/` files must stay human-readable,
   diffable, and mergeable. Schema changes need strong justification before 1.0.

## Good first issues

Check the issue tracker for `good-first-issue` labels. Bug reports with a
reproducing repo are gold.

## Secrets

Never commit API keys, Memvine keys (`mv_sk_`, `mv_mk_`, `mv_us_`, `mv_in_`), `.env` files,
benchmark outputs or session transcripts. [gitleaks](https://github.com/gitleaks/gitleaks) scans the
full history in `.github/workflows/secret-scan.yml`, which runs only when started by hand (Actions →
Secret scan → Run workflow); pushes do not trigger CI. The pre-commit hook below is the everyday guard.

To catch a secret before it is committed, turn on the pre-commit hook once per clone:

```bash
brew install gitleaks            # or a release binary from github.com/gitleaks/gitleaks
git config core.hooksPath scripts/git-hooks
```

Scan by hand with `gitleaks detect --config .gitleaks.toml --log-opts=--all`. Tests that need a
fake credential should reuse one of the fixtures allowlisted in `.gitleaks.toml`. If a real secret
is ever committed, rotate it first; deleting it in a later commit does not remove it from history.
