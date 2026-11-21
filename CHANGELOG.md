# Changelog

The memory service: server, console, plugins and client libraries. (The earlier git-native CLI has
its own changelog in `legacy/cli/`.)

## Unreleased

**Fixed**
- Anthropic backend: a request leaves the model room to think on top of the room its answer needs.
  Thinking counts toward `max_tokens`, so a short cap was spent before the answer began: on the
  hosted service recall lost about half of the fast model's picks that way and similarity decided
  instead. The reconcile judge could fail the same way without a sign; it now says so in the log.
- Recall without the model's pick (late, or no usable answer) serves no memory below the relevance
  threshold. A pending message or the branch's page that made the question a hit used to bring the
  closest memories with it, relevant or not.
- A pending message shares a word with the question only when one of its words starts with it:
  "port" is no longer found in "report".
- Claude Code and Codex plugins (0.8.2): a session with more than 500 unsent turns is sent in
  several requests. It was refused whole, and so was every later attempt, so the session stopped
  reaching memory.
- The server refuses a database that is not UTF8 and says why, instead of reporting changed
  migrations. `scripts/dev-setup.sh` creates its cluster as UTF-8 and works when run as root.

**Changed**
- The memory check waits for the model's pick on every recall (on the Claude CLI it never arrived
  within the server's three seconds, so similarity had been deciding) and checks two more things:
  a question about the project that memory cannot answer abstains, and the briefing was picked by
  the model.

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
- A forgotten password is reset with a one-time link: an owner or admin makes it in Settings, or
  the server's operator with `memvine-server reset-link` (also for a locked-out owner).

**Agents**
- Claude Code plugin (0.8.1) and Codex plugin, each installed from its marketplace.
- An MCP endpoint for any MCP client, a REST API, and TypeScript and Python libraries.

**Running it**
- Self-host with Docker: Postgres with pgvector, the API and the console (`deploy/README.md`).
- Anthropic models by default; any OpenAI-compatible server can be used, unmeasured.

**Measured**
- The memory check (coding mode, real model): 28 of 28. Its agent test: 9 of 9 questions right with
  memory, 0 of 9 without.
