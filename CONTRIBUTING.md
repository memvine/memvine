# Contributing to memvine

Thanks for your interest! memvine is young and contributions genuinely shape it.

## Setup

```bash
git clone https://github.com/memvine/memvine
cd memvine
npm install
npm run build
npm test
```

## Principles (please keep these)

1. **No LLM calls inside memvine.** Judgment belongs to the calling agent via
   MCP tool descriptions. memvine stays pure git + files.
2. **No servers, no databases, no accounts.** If a feature needs
   infrastructure, it probably belongs in a separate project.
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
