# Roadmap

Memvine is an open-source project. The aim is memory that demonstrably works, for a long
conversation with an assistant (a small profile of the person beside the searchable conversation) and
for a team's coding agents (keep little),
with results anyone can reproduce. There are no paid plans and none are planned. Work items are
GitHub issues in this repo, labelled `now` / `next` / `later` / `parked`, with `P0`–`P2` and an
`area:` label.

## Now (this week)
- Personal projects and team spaces (a person's sessions stay theirs; the team gets what they
  share): deployed to the hosted service on 16 Nov (migration 0014), with the console's Share button and plugin 0.8.1 on main (an
  installed plugin gets it by updating). To learn from the first real teams: do people share what
  is suggested?
- Public release, the owner's to do by hand after their own check: make the repository public,
  switch on private vulnerability reporting, publish the client libraries on npm and PyPI (#11)
- Re-point both laptops' plugins to Railway (#2)
- Review real capture after a week of use (#6)
- A BEAM result scored the published way, beside plain search and full context, with cost and
  limits stated (#17; the local reading so far is in `benchmark/beam/README.md`)
- Self-hosting that works from a clean checkout (`deploy/README.md`)
- Operator steps left for production: a Slack address for alerts, an outside uptime check, a monthly
  limit on the Anthropic key, a lawyer's review of `/terms` and `/privacy`

## Next (this month)
- Cursor plugin (#8) · self-serve sign-up (#9)
- MCP with OAuth for Claude.ai and ChatGPT (#10) · publish the SDKs (#11)
- Files, PDFs and URLs as sources (#12) · webhooks on memory events (#13)
- Ask the team: judges for agent questions (#20, #21, #22; plan in `docs/plans/judges-plan.md`)

## Later
Principles per person for judges (#23), GitHub connector (#15), agent framework integrations (#16),
password reset by email, SSO/MFA.

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
and a runbook (`deploy/railway/README.md` section 9). Conversation mode (the conversation
stored whole and searched, a small profile of the person tidied in the background, and digests for
summary questions); the BEAM runner with plain-search and full-context baselines, scored the
published way on 15 conversations.
