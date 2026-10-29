# Roadmap

The product hub (vision, decisions, competitor comparison, open questions) lives in Notion:
**Memvine — Product Hub** (private to the founders). Work items are GitHub issues in this repo,
labelled `now` / `next` / `later` / `parked`, with `P0`–`P2` and an `area:` label.

## Now (this week)
- Anthropic API key on Railway (#1), then the real-synthesis production smoke test (#3)
- Re-point both laptops' plugins to Railway (#2); custom domains app/api.memvine.dev (#4)
- Nightly backups of the Railway database (#5)
- Review real capture after a week of use (#6)

## Next (this month)
- Cursor plugin (#8) · self-serve sign-up with free credits and a cost preview (#9)
- MCP with OAuth for Claude.ai and ChatGPT (#10) · publish the SDKs (#11)
- Files, PDFs and URLs as sources (#12) · webhooks on memory events (#13)

## Later
GitHub connector (#15), agent framework integrations (#16), published benchmarks (#17),
billing (#18), SSO/MFA, SOC 2 and VPC self-hosting.

## Parked
Context graph (#19; plans in `docs/plans/`) — until plain-text memory is proven with real teams.

## Shipped (October 2026)
Hosting on Railway; memory admission rules (three tests); a tight recall briefing; one memory model;
review loop; memory that ages; history and activity feed; recency weighting; per-project memory
rules and categories; profiles; background consolidation; security pass; Connect page; Claude Code
plugin 0.7.2 and Codex plugin 0.6.1; the local memory check (agent test: 9/9 right with memory,
0/9 without) and the production smoke test.
