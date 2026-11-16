# @memvine/sdk (TypeScript)

```sh
# from a clone of this repository; not on npm yet
cd sdk/typescript && npm ci        # installs and builds dist/
cd /path/to/your/project && npm install /path/to/memvine/sdk/typescript
```

Memory for your agent in ten lines. With a project key and the sample data loaded
(`POST /v1/sample`, or **Load sample data** in the console):

```ts
import { Memvine } from "@memvine/sdk";
const mv = new Memvine({ apiKey: process.env.MEMVINE_KEY!, baseUrl: process.env.MEMVINE_URL });

const s = mv.session("demo-1", { agent: "my-agent", identity: "you@example.com" });
await s.send("user", "Retries cap at 3: the broker rate-limits us above that.");
await s.send("assistant", "Set MAX_RETRIES = 3 in src/queue.py.");
await s.close();                                   // tending turns the session into memory

const r = await mv.recall("When is the launch?");  // answered from the sample data
console.log(r.briefing);                           // hand this to your model as background
for (const i of r.items) console.log(i.memory.statement, i.previous ? `(was: ${i.previous})` : "");
```

What a memory is, the edges between memories, and how memory is tended and forgotten:
[How Memvine memory works](../../docs/how-memory-works.md).

## Reference

```ts
const mv = new Memvine({ apiKey: "mv_sk_...", baseUrl: "https://api.memvine.dev" });

await mv.ingest([{ id: "chat-42:1", content: "## user\nWe moved the launch to 12 Nov.", timestamp: new Date() }]);
await mv.remember("Always answer in British English.", { kind: "preference" }); // a standing instruction

const r = await mv.recall("When is the launch?", { budget: 2000 });
r.briefing;   // the text to hand your model; "" when recall abstains
r.abstained;  // true when nothing in memory is relevant enough to the question
r.standing;   // standing rules: returned on every recall, abstained or not
r.items;      // { memory, score, previous, conflicts }: previous is the value it superseded

const session = mv.session("run-17", { agent: "my-agent", identity: "user-17" });
await session.send("user", "Let's use Postgres for the queue.");
await session.send("assistant", "Postgres it is.");
console.log((await session.brief()).text);   // after compaction: this session's state, then relevant memory
await session.close();                       // remaining turns become long-term memory
```

Correct a fact: `remember("Launch is 19 Nov.", { supersedes: id })` replaces that memory directly
(no judge; relation `"update"`), keeping its kind, subjects, category, thread and audience unless given.

Also: `memories({ q, status, kind })`, `memory(id)`, `retire(id)` (forget one memory),
`deleteSource(episodeId)` (a source and the memories drawn only from it), and on a session
`state()`, `recall(query)`, `sendMany(events)`.

Edges: `recall(query, { include: { related: true } })` gives each item `related: [{ kind, direction,
id, statement }]` (`supersedes`, `contradicts`, `anchored`, `decided_in`, `extends`, `derived`; at
most 6), `related(id)` returns one memory with every edge in both directions, and
`graph({ since, thread, subject, all })` returns a neighborhood of facts, threads, files and people with
their edges (at most 300 nodes; what grew since `since` is flagged; `all: true` includes every thread,
not only the open ones).

Errors throw `MemvineError` with `.status` and `.code` (for example `project-cap-reached`).
Node 18+ (uses `fetch`). Cost: 1 credit per 1,000 characters sent (identical re-sends are free),
0.1 credit per recall.

Tests: `MEMVINE_TEST_URL=... MEMVINE_TEST_KEY=mv_sk_... npm test`.
