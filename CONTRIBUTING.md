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
