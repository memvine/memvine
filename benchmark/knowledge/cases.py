"""Keep/drop cases for what memory admits, run against the real extraction model.

  cd server && .venv/bin/python ../benchmark/knowledge/cases.py [prompt_file]

Uses the configured LLM backend (MEMVINE_LLM_BACKEND; claude-cli or anthropic). Every case must
print OK: "keep" cases must yield an item, "drop" cases none. prompt_file tries a draft of
KNOWLEDGE_SYSTEM without changing the server. benchmark/memory/check.py runs these too.
Measured 2026-10-27: 8/8 with the current rules; 2026-10-28: 10/10 with the two instructed cases (claude-cli, sonnet).

A case is (name, excerpt) or (name, excerpt, project instructions): the third element is a
project's memory instructions (projects.memory_instructions, prompts.instructions_block), so a case
can check that "never keep X" drops what would otherwise be kept and "also keep Y" keeps what would
otherwise be dropped.

Private drop cases. Memories people forgot or marked wrong in their own projects are exported with
`memvine-server feedback-export <slug> >> ~/.memvine/eval/drop-cases.jsonl` (one JSON object per
line: statement, excerpt, profile, ...). When that file exists (or MEMVINE_DROP_CASES names
another), each case replays its source excerpt through extraction and passes when no extracted item
matches the forgotten statement. An item matches when its embedding (the configured embedder,
MEMVINE_EMBEDDINGS_BACKEND) is at least DROP_MATCH_SIMILARITY alike, or when it shares at least
DROP_MATCH_OVERLAP of the forgotten statement's keywords (words of 4+ letters, and numbers). These cases quote
real source text: they stay in that local file and are never written into this repository.
"""
import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

CASES = [
    ("keep: db port", "User: For the muse project, the production database is Postgres 16 running on port 5544. Assistant: Got it, Postgres 16 on port 5544 for muse production."),
    ("keep: deploy rule", "User: Always deploy muse with `make deploy ENV=prod` from the repo root, never with kubectl apply directly. Assistant: Understood, I'll use make deploy ENV=prod."),
    ("keep: decision", "User: The muse frontend uses SvelteKit, and we decided against Next.js because the team wants smaller bundles. Assistant: Noted: SvelteKit chosen over Next.js for bundle size."),
    ("keep: preference", "User: Please keep PR descriptions short, three bullets max, I won't read more. Assistant: Will do."),
    ("drop: progress", "User: where are we? Assistant: I pushed branch fix/retry, opened PR #88 and the CI run is green: 142 passed. Waiting on Dana's review."),
    ("drop: code description", "Assistant: I read src/export/worker.py. The export worker retries 3 times with exponential backoff starting at 2s, then writes the failure to the dead_letter table. [tools] Read ok: src/export/worker.py"),
    ("drop: bug trace", "Assistant: Root cause found: OrderService.total() rounds before applying the discount (order_service.py:212), so totals are off by a cent on discounted orders. I'll fix it by rounding after. [tools] Bash `pytest tests/test_orders.py` failed (exit 1) ... then passed (exit 0): 12 passed"),
    ("keep: env gotcha", "[tools] Bash `make test` failed (exit 1): E could not connect to server: port 5432 ... Assistant: Port 5432 on the dev box is the legacy billing DB, not ours; our test DB is on 5433. Setting TEST_DB_PORT=5433 fixed it. [tools] Bash `TEST_DB_PORT=5433 make test` ok (exit 0): 230 passed"),
    # Project instructions: a durable rule that would be kept, but it is about a named customer.
    ("drop: instructed no customer names",
     "User: For muse billing: Brightwater Dental's contract gives them 60-day payment terms instead of our usual 30, so never send them a dunning email before day 60. Assistant: Got it, no dunning emails to Brightwater Dental before day 60.",
     "Never keep anything about customer names."),
    # Project instructions: a step of incident work, which the default rules drop as the work itself.
    ("keep: instructed runbook step",
     "User: checkout is erroring again. Assistant: The 5xx rate on muse checkout recovered. What fixed it: I turned on the `payments_fallback` flag in the LaunchDarkly console, waited until the checkout 5xx rate was under 1%, and turned the flag off again once the payment provider's status page was green.",
     "Also keep on-call runbook steps."),
]
PROFILE = "muse — a web app (SvelteKit frontend, Postgres) and how it is deployed"
DROP_CASES = Path(os.environ.get("MEMVINE_DROP_CASES", "~/.memvine/eval/drop-cases.jsonl")).expanduser()
DROP_MATCH_SIMILARITY = 0.85
DROP_MATCH_OVERLAP = 0.7


def load_private_cases(path: Path = DROP_CASES) -> list[dict]:
    """The private drop cases, or none when the file is absent. Cases without an excerpt (dropped
    by retention) cannot be replayed and are skipped."""
    if not path.exists():
        return []
    cases = []
    for line in path.read_text().splitlines():
        if line.strip():
            case = json.loads(line)
            if case.get("excerpt") and case.get("statement"):
                cases.append(case)
    return cases


def keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9_]+(?:[.-][a-z0-9_]+)*", text.lower())
    return {w for w in words if len(w) >= 4 or any(ch.isdigit() for ch in w)}


def keyword_overlap(forgotten: str, statement: str) -> float:
    """The share of the forgotten statement's keywords the extracted statement repeats."""
    want = keywords(forgotten)
    return len(want & keywords(statement)) / len(want) if want else 0.0


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = sum(x * x for x in a) ** 0.5, sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


async def run_private_cases(prompt_file: str | None = None, path: Path = DROP_CASES) -> list[tuple[str, bool, list[str]]]:
    """(case name, passed, matching items) for every private drop case: passed when extraction over
    the excerpt yields nothing that matches the forgotten statement."""
    cases = load_private_cases(path)
    if not cases:
        return []
    from memvine_server.embeddings import make_embedder
    from memvine_server.extraction import extract as ex
    from memvine_server.extraction.llm import make_llm

    if prompt_file:
        ex.KNOWLEDGE_SYSTEM = open(prompt_file).read()
    llm, embedder = make_llm(), make_embedder()

    async def one(case):
        r = await ex.extract_knowledge(llm, label="Claude Code", occurred_at=datetime.fromisoformat(case.get("created_at") or "2026-10-20T00:00:00+00:00"),
                                       content=case["excerpt"], project=case.get("profile") or PROFILE, threads=[])
        statements = [c.statement for c in r.items]
        vectors = await embedder.embed([case["statement"], *statements]) if statements else [[]]
        hits = [f"[{c.category}] {c.statement}" for c, v in zip(r.items, vectors[1:], strict=True)
                if cosine(vectors[0], v) >= DROP_MATCH_SIMILARITY or keyword_overlap(case["statement"], c.statement) >= DROP_MATCH_OVERLAP]
        return f"drop ({case.get('kind', 'forgotten')}): {case['statement'][:70]}", not hits, hits

    return list(await asyncio.gather(*(one(c) for c in cases)))


async def run_cases(prompt_file: str | None = None) -> list[tuple[str, bool, list[str]]]:
    """(case name, passed, items kept) for every case."""
    from memvine_server.extraction import extract as ex
    from memvine_server.extraction.llm import make_llm

    if prompt_file:
        ex.KNOWLEDGE_SYSTEM = open(prompt_file).read()
    llm = make_llm()

    async def one(name, text, instructions=None):
        r = await ex.extract_knowledge(llm, label="Claude Code · muse · main", occurred_at=datetime(2026, 10, 20, tzinfo=UTC),
                                       content=text, project=PROFILE, threads=[], instructions=instructions)
        items = [f"[{c.category}] {c.statement}" for c in r.items]
        return name, bool(items) == name.startswith("keep"), items

    return list(await asyncio.gather(*(one(*case) for case in CASES)))


if __name__ == "__main__":
    prompt = sys.argv[1] if len(sys.argv) > 1 else None
    results = asyncio.run(run_cases(prompt))
    private = asyncio.run(run_private_cases(prompt))
    if private:
        results += private
        print(f"({len(private)} private drop cases from {DROP_CASES})")
    for name, ok, items in results:
        print(("OK  " if ok else "FAIL"), name, "->", items or "[]")
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)
