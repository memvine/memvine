# Roadmap

The product hub (vision, decisions, competitor comparison, open questions) lives in Notion:
**Memvine — Product Hub** (private to the founders). Work items are GitHub issues in this repo,
labelled `now` / `next` / `later` / `parked`, with `P0`–`P2` and an `area:` label.

## Now (this week)
- Re-point both laptops' plugins to Railway (#2)
- Review real capture after a week of use (#6)
- Prove recall helps a coding agent: the four-way test on trap tasks, then memories shown to five teams
- Operator steps left for production: a Slack address for alerts, an outside uptime check, a monthly
  limit on the Anthropic key, a lawyer's review of `/terms` and `/privacy`

## Next (this month)
- Cursor plugin (#8) · self-serve sign-up with free credits and a cost preview (#9)
- MCP with OAuth for Claude.ai and ChatGPT (#10) · publish the SDKs (#11)
- Files, PDFs and URLs as sources (#12) · webhooks on memory events (#13)
- Ask the team: judges for agent questions (#20, #21, #22; plan in `docs/plans/judges-plan.md`)

## Later
Principles per person for judges (#23), GitHub connector (#15), agent framework integrations (#16), published benchmarks (#17),
billing (#18), SSO/MFA, SOC 2 and VPC self-hosting.

## Parked
Context graph (#19; plans in `docs/plans/`) — until plain-text memory is proven with real teams.

## Shipped (October 2026)
Hosting on Railway; memory admission rules (three tests); a tight recall briefing; one memory model;
review loop; memory that ages; history and activity feed; recency weighting; per-project memory
rules and categories; profiles; background consolidation; security pass; Connect page; Claude Code
plugin 0.7.2 and Codex plugin 0.6.1; the local memory check (agent test: 9/9 right with memory,
0/9 without) and the production smoke test.

## Shipped (November 2026)
Addresses `api.`, `app.` and `www.memvine.dev`; nightly backups, each one restore-tested; recall
that lets the fast model pick what the task needs; production readiness for invited teams: health
checks on `api` and `console`, pinned server dependencies, HSTS on the API, terms and privacy drafts,
and a runbook (`deploy/railway/README.md` section 9).
