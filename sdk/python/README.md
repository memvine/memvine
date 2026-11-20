# memvine-sdk (Python)

```sh
pip install memvine-sdk
```

Memory for your agent in ten lines. With a project key and the sample data loaded
(`POST /v1/sample`, or **Load sample data** in the console):

```python
import os
from memvine_sdk import Memvine
mv = Memvine(os.environ["MEMVINE_KEY"], base_url=os.environ.get("MEMVINE_URL", "https://api.memvine.dev"))

s = mv.session("demo-1", agent="my-agent", identity="you@example.com")
s.send("user", "Retries cap at 3: the broker rate-limits us above that.")
s.send("assistant", "Set MAX_RETRIES = 3 in src/queue.py.")
s.close()                                    # tending turns the session into memory

r = mv.recall("When is the launch?")         # answered from the sample data
print(r.briefing)                            # hand this to your model as background
for i in r.items: print(i["memory"]["statement"], f"(was: {i['previous']})" if i["previous"] else "")
```

What a memory is, the edges between memories, and how memory is tended and forgotten:
[How Memvine memory works](../../docs/how-memory-works.md).

## Reference

```python
mv = Memvine("mv_sk_...", base_url="https://api.memvine.dev")

mv.ingest([{"id": "chat-42:1", "content": "## user\nWe moved the launch to 12 Nov.", "timestamp": "2026-10-03T14:02:00Z"}])
mv.remember("Always answer in British English.", kind="preference")   # a standing instruction

r = mv.recall("When is the launch?", budget=2000)
r.briefing     # the text to hand your model; "" when recall abstains
r.abstained    # True when nothing in memory is relevant enough to the question
r.standing     # standing rules: returned on every recall, abstained or not
r.items        # dicts with memory, score, previous (the value it superseded), conflicts

session = mv.session("run-17", agent="my-agent", identity="user-17")
session.send("user", "Let's use Postgres for the queue.")
session.send("assistant", "Postgres it is.")
print(session.brief())     # after compaction: this session's state, then relevant memory
session.close()            # remaining turns become long-term memory
```

Correct a fact: `remember("Launch is 19 Nov.", supersedes=id)` replaces that memory directly
(no judge; relation `"update"`), keeping its kind, subjects, category, thread and audience unless given.

Also: `memories(q=, status=, kind=)`, `memory(id)`, `retire(id)` (forget one memory),
`delete_source(episode_id)` (a source and the memories drawn only from it), and on a
session `state()`, `recall(query)`, `send_many(events)`.

Edges: `recall(query, include={"related": True})` gives each item `related: [{kind, direction, id,
statement}]` (`supersedes`, `contradicts`, `anchored`, `decided_in`, `extends`, `derived`; at most
6), `related(id)` returns one memory with every edge in both directions, and
`graph(since=, thread=, subject=, all=)` returns a neighborhood of facts, threads, files and people with
their edges (at most 300 nodes; what grew since `since` is flagged; `all=True` includes every thread,
not only the open ones).

Errors raise `MemvineError` with `.status` and `.code` (for example `project-cap-reached`).
Cost: 1 credit per 1,000 characters sent (identical re-sends are free), 0.1 credit per recall.

Tests: `pip install -e 'sdk/python[dev]'`, then `pytest sdk/python`. One test talks to a real server and is
skipped unless `MEMVINE_TEST_URL` and `MEMVINE_TEST_KEY` (a project key) are set.
