<a id="readme-top"></a>

<p align="center"><img src="docs/images/memvine-run.png" alt="One lesson followed through a real run, in four steps. Conversation: you tell your agent the 90-second timeout in export.py is deliberate, and why. Memory saved: the Memvine console shows the one memory kept from that session, with your words as its source. Memory recalled: a teammate's agent, asked to lower the timeout, declines and gives the reason from memory. Memory rechecked: after export.py changes, the same memory is marked stale, May be outdated." width="960" /></p>

<h1 align="center">Memvine</h1>

<p align="center"><strong>Memory for your agents. Memory for your team.</strong><br />One agent learns it. Every agent knows it.</p>

<p align="center"><a href="#quick-start">Quick start</a> · <a href="#how-it-works">How it works</a> · <a href="#does-it-work">Does it work?</a> · <a href="docs/how-memory-works.md">Documentation</a> · <a href="ROADMAP.md">Roadmap</a> · <a href="https://memvine.dev">Website</a></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-3e8c5e?style=flat&amp;labelColor=0b1310" alt="License: MIT" /></a>
  <a href="#2-connect-an-agent"><img src="https://img.shields.io/badge/agents-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20MCP-3e8c5e?style=flat&amp;labelColor=0b1310" alt="Agents: Claude Code, Codex, MCP" /></a>
  <a href="deploy/README.md"><img src="https://img.shields.io/badge/self--host-Docker-3e8c5e?style=flat&amp;labelColor=0b1310" alt="Self-host with Docker" /></a>
  <a href="server"><img src="https://img.shields.io/badge/server-Python%203.12%20%C2%B7%20Postgres-3e8c5e?style=flat&amp;labelColor=0b1310" alt="Server: Python 3.12 and Postgres" /></a>
</p>

<details>
<summary>Table of contents</summary>

- [About](#about)
  - [Two modes](#two-modes)
- [Quick start](#quick-start)
  - [1. Run a server](#1-run-a-server)
  - [2. Connect an agent](#2-connect-an-agent)
  - [3. Or call it from your own code](#3-or-call-it-from-your-own-code)
- [How it works](#how-it-works)
- [Does it work?](#does-it-work)
- [Limits](#limits)
- [What is in this repository](#what-is-in-this-repository)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)

</details>

## About

[Memvine](https://memvine.dev) is shared memory for a team's coding agents. Teach one agent a rule
once, and every teammate's agent knows it the next day, whether they use Claude Code, Codex or
another MCP client.

- **Your sessions stay yours.** On a team, each person's sessions go to a project only they can
  open. The team's project refuses sessions and holds only the memories people chose to share.
- **Every memory can be checked.** Each one is a short, dated statement with the exact quote it
  came from and who said it. A web console lets you browse, correct and forget them.
- **It works where your agents are.** Plugins for Claude Code and Codex, an MCP endpoint for other
  agents, and TypeScript and Python libraries for your own code.
- **It notices when the code moves.** A memory about a file is tied to that file, and when the file
  changes it is marked as possibly outdated, so agents check the code instead of trusting old advice.
- **Yours to run.** The core is open source under MIT: run the server and console on your own
  machine with Docker, or use the hosted service at [app.memvine.dev](https://app.memvine.dev), open
  by invitation.

### Two modes

Each project uses one of two modes:

| Mode | Made for | What it keeps |
| --- | --- | --- |
| `coding` (the default) | A team's coding agents | Very little: the decisions, rules, gotchas, facts and preferences that the code itself cannot tell you |
| `conversation` | One person's long conversation with an assistant | The whole conversation, searchable, and a small profile of the person |

## Quick start

### 1. Run a server

You need Docker and a key for a model, because Memvine uses one to pick out the memories:
Anthropic's by default, or any server that speaks the OpenAI API.

```sh
git clone https://github.com/memvine/memvine && cd memvine/deploy
cp .env.example .env          # fill in the three values it asks for
docker compose up -d --build
```

The [self-hosting guide](deploy/README.md) covers the rest, including how to create a project and
its key. There is also a hosted server at [app.memvine.dev](https://app.memvine.dev), open by
invitation: write to hello@memvine.dev.

### 2. Connect an agent

Use your project key (`mv_sk_...`):

| Agent | How |
| --- | --- |
| Claude Code | `/plugin marketplace add memvine/memvine`, `/plugin install memvine@memvine`, then `/memvine:connect mv_sk_... you@company.com [api-url]`. On a team, add the team space with `/memvine:team mv_sk_...`. See [`plugins/claude-code`](plugins/claude-code). |
| OpenAI Codex CLI | `codex plugin marketplace add memvine/memvine`, `codex plugin add memvine@memvine`, then one connect command. See [`plugins/codex`](plugins/codex). |
| Any MCP client | Point it at `/mcp` on your server. |
| Your own code | The [TypeScript](sdk/typescript) or [Python](sdk/python) library, or the [HTTP API](server). |

### 3. Or call it from your own code

```ts
import { Memvine } from "@memvine/sdk";
const mv = new Memvine({ apiKey: process.env.MEMVINE_KEY!, baseUrl: process.env.MEMVINE_URL });

// During a session: send what is said.
const s = mv.session("demo-1", { agent: "my-agent", identity: "you@example.com" });
await s.send("user", "Retries cap at 3: the broker rate-limits us above that.");
await s.close();                              // Memvine turns the session into memory

// In a later session: ask before you start.
const r = await mv.recall("How many retries are allowed?");
console.log(r.briefing);                      // give this to your model as background
```

<details>
<summary>The same in Python</summary>

```python
import os
from memvine_sdk import Memvine
mv = Memvine(os.environ["MEMVINE_KEY"], base_url=os.environ["MEMVINE_URL"])

# During a session: send what is said.
s = mv.session("demo-1", agent="my-agent", identity="you@example.com")
s.send("user", "Retries cap at 3: the broker rate-limits us above that.")
s.close()                                     # Memvine turns the session into memory

# In a later session: ask before you start.
r = mv.recall("How many retries are allowed?")
print(r.briefing)                             # give this to your model as background
```

</details>

Install with `npm install @memvine/sdk` ([TypeScript](sdk/typescript/README.md)) or
`pip install memvine-sdk` ([Python](sdk/python/README.md)).

## How it works

1. **Your agent sends its conversation** to a Memvine server, through a plugin or a few lines of code.
2. **Memvine keeps what stays true.** A model reads the session and keeps only what a later
   session would act on. A newer fact replaces an older one, and the old one is kept as history.
3. **The next session starts briefed.** Memvine returns the memories a question needs, and adds
   nothing when the question is unrelated.

**On a team, your sessions stay yours.** Each person's sessions go to a project only they can open.
The team has a second project, a *team space*, that refuses sessions and holds only the memories
people chose to share. After a session Memvine says which of your new memories are worth sharing,
and you say which ones go. Every agent on the team is briefed from both.

More detail: [How Memvine memory works](docs/how-memory-works.md).

## Does it work?

Two tests, both in this repository and both runnable:

- **Coding mode.** The [memory check](benchmark/memory) uses a real model to test what must be
  kept, dropped and recalled: 28 of 28 pass. In its agent test, Claude Code answered 9 of 9
  questions correctly with Memvine's memory and 0 of 9 without it.
- **Conversation mode.** On the [BEAM](benchmark/beam/README.md) long-term memory benchmark
  (100K-token conversations, 300 questions, scored the published way):

  | | Score |
  | --- | ---: |
  | The whole conversation in the prompt | 0.903 |
  | Memvine | 0.892 |
  | Plain search over the conversation | 0.889 |

  Memvine is level with plain search here, not ahead of it. These are 15 of the benchmark's 20
  conversations, and Memvine's retrieval was adjusted on these same 15, so its score is not a
  held-out result: the five conversations not yet run are the fair test.

## Limits

- It does not beat plain search at recalling details from a long conversation. It is level with it.
- It learns from conversations and from what people add. It does not read your repository,
  documents or tickets by itself.
- It is built and measured on Anthropic models. Other models can be used but are not measured.
  Taking in a conversation costs model calls.
- No outside team has yet used it on real work for long enough to show that it helps. Whether
  people share what their memory suggests is not known either: that part is new and unmeasured.

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
| `docs/` | How it works, what it can and cannot do, security |
| `site/` | The memvine.dev homepage |

Each directory has its own README. [`legacy/cli/`](legacy/cli) holds an earlier, separate tool: a
command-line program that keeps memory as markdown files inside a repository, with no server. It is
not part of the memory service and is no longer developed.

## Documentation

| Read this | To learn |
| --- | --- |
| [How Memvine memory works](docs/how-memory-works.md) | What a memory is, and how it is made, replaced and forgotten |
| [What Memvine can and cannot do](docs/what-memvine-does.md) | Every claim, with what has and has not been measured |
| [Self-hosting guide](deploy/README.md) | Running, operating and upgrading your own server |
| [Security](docs/security.md) | What Memvine protects, what an operator has to set up, and the known limits |
| [Roadmap](ROADMAP.md) and [changelog](CHANGELOG.md) | What is planned and what has shipped |

## Contributing

Contributions are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) explains how to set up the project,
run the tests and open a pull request. To set up and test locally:

```sh
scripts/dev-setup.sh                          # Postgres, the server's environment, the console's packages
cd server && .venv/bin/python -m pytest -q    # the server's tests
```

## Security

Please report a security problem privately, as [SECURITY.md](SECURITY.md) describes, and not in a
public issue.

## Acknowledgements

The BEAM runner's dataset loading, model calls and scoring started from the
[harness Past Corp published](https://github.com/pastdotdev/benchmarks/tree/main/beam) for its own
BEAM run (MIT); the answer and judge prompts are ExaBase's, unchanged, and keep their own terms. Details are in
[benchmark/beam/NOTICE.md](benchmark/beam/NOTICE.md).

## License

[MIT](LICENSE)

<p align="right"><a href="#readme-top">Back to top</a></p>
