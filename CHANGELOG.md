# Changelog

The memory service: server, console, plugins and client libraries. (The earlier git-native CLI has
its own changelog in `legacy/cli/`.)

## 0.1.0 (first public release)

**Memory**
- Two modes, chosen per project. `coding` keeps only what passes three tests (an agent on another
  task would act differently, the code cannot tell it, it stays true). `conversation` stores the
  conversation whole, searches it word for word, and keeps a small profile of the person.
- Each memory keeps its source: the quoted words, who said them and when.
- Memories about code are tied to the files they name and are marked as possibly outdated when
  those files change.
- A newer value replaces an older one and keeps the old one as history; two that disagree are both
  shown until someone settles it.
- Recall gives an agent a short briefing for its task, with standing rules always on, and says
  nothing when the question is unrelated.

**Teams**
- Each person's sessions go to a project only they can open. A team space holds only the memories
  people chose to share, and every agent on the team is briefed from both.
- Review in the console (keep, wrong, forget); forgotten memories are never learned again.

**Agents**
- Claude Code plugin (0.8.1) and Codex plugin, each installed from its marketplace.
- An MCP endpoint for any MCP client, a REST API, and TypeScript and Python libraries.

**Running it**
- Self-host with Docker: Postgres with pgvector, the API and the console (`deploy/README.md`).
- Anthropic models by default; any OpenAI-compatible server can be used, unmeasured.

**Measured**
- The memory check (coding mode, real model): 28 of 28. Its agent test: 9 of 9 questions right with
  memory, 0 of 9 without.
- BEAM, 100K conversations, 15 of 20, scored the published way: Memvine 0.892, plain search 0.889,
  the whole conversation 0.903. Not held out: retrieval was adjusted on these conversations.
