# Memvine

**Memory for AI agents.** Open source, MIT licensed.

An AI agent forgets everything when its session ends. Memvine remembers for it. It reads what is
said in a session, keeps the few things that stay true (a decision, a rule, a gotcha), and gives
them to the next session before it starts.

![A sample project's memory: threads of work in the middle, the facts learned in them around each one, and the files those facts depend on at the edge.](docs/memory-graph.svg)

## How it works

1. **Your agent sends its conversation** to a Memvine server, through a plugin or a few lines of code.
2. **Memvine keeps what stays true.** Each memory is a short, dated statement with the exact quote
   it came from and who said it. A newer fact replaces an older one, and the old one is kept as history.
3. **The next session starts briefed.** Memvine returns the memories a question needs, and adds
   nothing when the question is unrelated.

Each project uses one of two modes:

| Mode | Made for | What it keeps |
| --- | --- | --- |
| `coding` (the default) | A team's coding agents | Very little: the decisions, rules, gotchas, facts and preferences that the code itself cannot tell you |
| `conversation` | One person's long conversation with an assistant | The whole conversation, searchable, and a small profile of the person |

More detail: [How Memvine memory works](docs/how-memory-works.md).

## Get started

**1. Get a server.** Run your own with Docker. You need an Anthropic API key, because Memvine uses
a model to pick out the memories.

```sh
git clone https://github.com/memvine/memvine && cd memvine/deploy
cp .env.example .env          # fill in the three values it asks for
docker compose up -d --build
```

The [self-hosting guide](deploy/README.md) covers the rest, including how to create a project and
its key. There is also a hosted server at [app.memvine.dev](https://app.memvine.dev), open by
invitation: write to hello@memvine.dev.

**2. Connect an agent** with your project key (`mv_sk_...`):

| Agent | How |
| --- | --- |
| Claude Code | `/plugin marketplace add memvine/memvine`, `/plugin install memvine@memvine`, then `/memvine:connect mv_sk_... you@company.com [api-url]`. See [`plugins/claude-code`](plugins/claude-code). |
| OpenAI Codex CLI | One `install` command. See [`plugins/codex`](plugins/codex). |
| Any MCP client | Point it at `/mcp` on your server. |
| Your own code | The [TypeScript](sdk/typescript) or [Python](sdk/python) library, or the [HTTP API](server). |

**3. Or use it from your own code:**

```ts
import { Memvine } from "@memvine/sdk";
const mv = new Memvine({ apiKey: process.env.MEMVINE_KEY!, baseUrl: process.env.MEMVINE_URL });

// During a session: send what is said.
const s = mv.session("demo-1", { agent: "my-agent", identity: "priya@example.com" });
await s.send("user", "Retries cap at 3: the broker rate-limits us above that.");
await s.close();                              // Memvine turns the session into memory

// In a later session: ask before you start.
const r = await mv.recall("How many retries are allowed?");
console.log(r.briefing);                      // give this to your model as background
```

For now the libraries are installed from a clone of this repository
([TypeScript](sdk/typescript/README.md), [Python](sdk/python/README.md)).

## Does it work

Two tests, both in this repository and both runnable:

- **Coding mode.** The [memory check](benchmark/memory) uses a real model to test what must be
  kept, dropped and recalled: 25 of 25 pass. In its agent test, Claude Code answered 9 of 9
  questions correctly with Memvine's memory and 0 of 9 without it.
- **Conversation mode.** On the [BEAM](benchmark/beam/README.md) long-term memory benchmark
  (100K-token conversations, 300 questions, scored the published way):

  | | Score |
  | --- | ---: |
  | The whole conversation in the prompt | 0.903 |
  | Memvine | 0.892 |
  | Plain search over the conversation | 0.889 |

  Memvine is level with plain search here, not ahead of it.

## Limits

- It does not beat plain search at recalling details from a long conversation. It is level with it.
- It learns from conversations and from what people add. It does not read your repository,
  documents or tickets by itself.
- It needs Anthropic models, and taking in a conversation costs model calls.
- No outside team has yet used it on real work for long enough to show that it helps.

The full list, with what has and has not been measured:
[What Memvine can and cannot do](docs/what-memvine-does.md).

## What is in this repository

| Directory | What it is |
| --- | --- |
| `server/` | The memory server (Python, FastAPI, Postgres with pgvector) |
| `console/` | The web console for browsing and reviewing memories (Next.js) |
| `plugins/` | The Claude Code and Codex plugins |
| `sdk/` | The TypeScript and Python libraries |
| `deploy/` | Running your own server with Docker |
| `benchmark/` | The memory check and the BEAM benchmark |
| `docs/` | How it works, what it can and cannot do, [security](docs/security.md) |
| `site/` | The memvine.dev homepage |

Each directory has its own README. To work on Memvine, see [CONTRIBUTING.md](CONTRIBUTING.md). To
report a security problem, see [SECURITY.md](SECURITY.md).

There is also an earlier, separate tool in [`src/`](src): a command-line program that keeps memory
as markdown files inside a repository, with no server. See [docs/git-native-cli.md](docs/git-native-cli.md).

## License

[MIT](LICENSE)
