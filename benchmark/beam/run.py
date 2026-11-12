"""Run BEAM against a memory backend.

    python run.py --backend memvine --split 100k
    python run.py --backend fullcontext --split 100k
    python run.py --backend rag --split 100k

The evaluation procedure is the published one, unchanged (the dataset, ExaBase's prompts, the answer
and judge model, the scoring rules), so a score here compares with other systems' published BEAM
scores. The memory system is a pluggable backend (memory_api.py) with two reference arms beside
Memvine. The dataset, model and scoring code started from Past Corp's MIT-licensed harness: see
NOTICE.md and LICENSE-past-harness.

The run has three phases, each over every conversation:

    1. send: open a store for the conversation and ingest every turn
    2. wait until the backend has finished processing every store
    3. score: ask each question, answer it from what the backend recalls, and score the answer

Environment: OPENROUTER_API_KEY (or BEAM_FAKE_MODEL=1 for a plumbing run); for the memvine backend
also MEMVINE_API_URL and MEMVINE_MANAGEMENT_KEY.
"""

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import exabase_prompts
import httpx
import llm
import scoring
from dataset import (
    SPLITS,
    Conversation,
    Question,
    build_conversation,
    load_rows,
    utc_iso,
)
from log import duration, log
from memory_api import (
    BACKENDS,
    RECALL_LIMIT,
    RECALL_MAX_TOKENS,
    Handle,
    IngestionFailed,
    MemoryApiError,
    make_backend,
)

HARNESS_VERSION = "2-memvine"  # the published procedure (harness version 2), with pluggable backends
TOLERATE_MEMORY_ERRORS = os.environ.get("BEAM_TOLERATE_MEMORY_ERRORS", "") == "1"

CONVERSATIONS_SENT_AT_ONCE = 4
CONVERSATIONS_SCORED_AT_ONCE = 4
RECALLS_AT_ONCE = 2
PROGRESS_LOG_SECONDS = 30


# --- Phase 3, score: ask, answer, judge ------------------------------------------------------------------

async def answer_question(backend, handle: Handle, conversation: Conversation, question: Question,
                          recall_slots: asyncio.Semaphore) -> dict:
    try:
        async with recall_slots:
            recalled = await backend.recall(handle, question.text, question.as_of, conversation.user_identity)
        if str(recalled.get("asOf") or "") != question.as_of:
            raise RuntimeError(f"Recall answered as of {recalled.get('asOf')!r}, but the question was asked at {question.as_of!r}")

        # The answer model reads the backend's response exactly as returned.
        context = json.dumps(recalled, ensure_ascii=False, separators=(",", ":"))
        prompt = exabase_prompts.answer_prompt(question.text, context, question.category, question.annotations)
        answer = (await llm.complete_text(prompt)).strip()
    except (MemoryApiError, llm.ModelError, ValueError, RuntimeError, httpx.HTTPError) as error:
        if llm.stops_the_run(error):
            raise
        # A failing memory system is a broken run, not a wrong answer: stop rather than record zeros.
        # (Model-side failures keep the published rule and score 0.)
        if isinstance(error, MemoryApiError) and (error.status or 0) >= 500 and not TOLERATE_MEMORY_ERRORS:
            raise IngestionFailed(f"recall failed for {question.id}: {error}") from error
        # A recall or an answer that failed even after retries scores the question 0, and the
        # summary counts it: a failure is part of the result, never a reason to try again.
        log(f"{question.id}: failed, scored 0 ({str(error)[:160]})", conversation.id)
        return {
            "questionId": question.id, "conversationId": conversation.id, "category": question.category,
            "question": question.text, "asOf": question.as_of, "goldAnswers": question.gold_answers,
            "error": str(error)[:500], "judge": {"method": "failed"}, "score": 0.0,
        }

    score, verdicts = await scoring.score_question(question, answer)
    log(f"{question.id}: {len(recalled.get('results') or [])} results recalled "
        f"({recalled.get('usedEvidenceTokens', '?')} evidence tokens), score {score:.2f}", conversation.id)
    return {
        "questionId": question.id, "conversationId": conversation.id, "category": question.category,
        "question": question.text, "asOf": question.as_of, "goldAnswers": question.gold_answers,
        "recall": recalled, "answer": answer, "judge": verdicts, "score": score,
    }


# --- The run -----------------------------------------------------------------------------------

@dataclass
class Sent:
    conversation: Conversation
    handle: Handle


class Run:
    """What every conversation of one run shares."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.split = args.split
        self.backend_name = args.backend
        self.output = Path(args.output or f"results/{args.backend}-{args.split}")
        self.results = self.output / "results.jsonl"
        self.reusing = bool(getattr(args, "reuse", None))
        self.categories = set(args.categories or [])
        self.tag = args.reuse if self.reusing else datetime.now(UTC).strftime("%m%d-%H%M%S")
        self.backend = make_backend(args.backend, f"{args.split} {self.tag}")
        self.sending_slots = asyncio.Semaphore(CONVERSATIONS_SENT_AT_ONCE)
        self.scoring_slots = asyncio.Semaphore(CONVERSATIONS_SCORED_AT_ONCE)
        self.recall_slots = asyncio.Semaphore(RECALLS_AT_ONCE)
        self.write_lock = asyncio.Lock()
        self.processed = 0
        self.scored = 0

    async def send(self, row: dict) -> Sent:
        async with self.sending_slots:
            conversation = build_conversation(row)
            log(f"sending: {len(conversation.messages)} turns, {len(conversation.questions)} questions", conversation.id)
            handle = await self.backend.open(conversation)
            await self.backend.ingest(handle, conversation)
            return Sent(conversation, handle)

    async def reopen(self, row: dict) -> Sent:
        conversation = build_conversation(row)
        if not hasattr(self.backend, "reopen"):
            raise SystemExit(f"--reuse is not supported by the {self.backend_name} backend")
        return Sent(conversation, await self.backend.reopen(conversation))

    async def wait(self, sent: Sent, started: float) -> None:
        await self.backend.wait(sent.handle)
        self.processed += 1
        log(f"processed after {duration(time.monotonic() - started)}", sent.conversation.id)

    async def log_processing_progress(self, total: int, started: float) -> None:
        while True:
            await asyncio.sleep(PROGRESS_LOG_SECONDS)
            log(f"processing: {self.processed}/{total} conversations done after {duration(time.monotonic() - started)}")

    async def score(self, sent: Sent, total: int) -> None:
        async with self.scoring_slots:
            conversation = sent.conversation
            started = time.monotonic()
            rows = await asyncio.gather(*(answer_question(self.backend, sent.handle, conversation, question, self.recall_slots)
                                          for question in conversation.questions
                                          if not self.categories or question.category in self.categories))
            await self.save(rows)
            self.scored += 1
            mean = sum(row["score"] for row in rows) / len(rows) if rows else 0.0
            log(f"scored: mean {mean:.3f} over {len(rows)} questions in {duration(time.monotonic() - started)} "
                f"· {self.scored}/{total} conversations scored", conversation.id)

    async def save(self, rows: list[dict]) -> None:
        """A conversation's rows are written in one go, so a stopped run never leaves half of one."""
        lines = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        async with self.write_lock:
            with self.results.open("a", encoding="utf-8") as file:
                file.write(lines)

    def saved_rows(self) -> list[dict]:
        if not self.results.exists():
            return []
        return [json.loads(line) for line in self.results.read_text("utf-8").splitlines() if line.strip()]

    async def close(self) -> None:
        await self.backend.close()
        await llm.close()


async def run_phases(run: Run, to_run: list[dict]) -> None:
    if run.reusing:
        log(f"phase 1/3: reusing the stores of run {run.tag}; nothing is ingested")
        async with asyncio.TaskGroup() as group:
            reopening = [group.create_task(run.reopen(row)) for row in to_run]
        sent = [task.result() for task in reopening]
    else:
        log(f"phase 1/3: sending {len(to_run)} conversation(s) to {run.backend_name}")
        async with asyncio.TaskGroup() as group:
            sending = [group.create_task(run.send(row)) for row in to_run]
        sent = [task.result() for task in sending]

    log(f"phase 2/3: waiting until {run.backend_name} has processed {len(sent)} conversation(s)")
    started = time.monotonic()
    progress = asyncio.create_task(run.log_processing_progress(len(sent), started))
    try:
        async with asyncio.TaskGroup() as group:
            for conversation in sent:
                group.create_task(run.wait(conversation, started))
    finally:
        progress.cancel()

    log(f"phase 3/3: scoring {len(sent)} conversation(s)")
    async with asyncio.TaskGroup() as group:
        for conversation in sent:
            group.create_task(run.score(conversation, len(sent)))


def required_environment(backend: str) -> list[str]:
    names = [] if (llm.FAKE or llm.LOCAL_JUDGE) else ["OPENROUTER_API_KEY"]
    if backend == "memvine":
        names += ["MEMVINE_API_URL", "MEMVINE_MANAGEMENT_KEY"]
    return names


async def main_async(args: argparse.Namespace) -> None:
    missing = [name for name in required_environment(args.backend) if not os.environ.get(name, "").strip()]
    if missing:
        raise SystemExit(f"Set {', '.join(missing)} before running (see README.md).")

    run = Run(args)
    run.output.mkdir(parents=True, exist_ok=True)
    rows, revision = load_rows(args.split)
    rows = rows[args.offset:]
    if args.conversations is not None:
        rows = rows[:args.conversations]
    selected = [str(row.get("conversation_id", "")) for row in rows]
    already_scored = {row["conversationId"] for row in run.saved_rows()} & set(selected)
    to_run = [row for row in rows if str(row.get("conversation_id", "")) not in already_scored]
    log(f"BEAM {args.split} on {args.backend} (dataset revision {revision[:12]}), run {run.tag}: {len(selected)} conversation(s), "
        f"{len(already_scored)} already scored, {len(to_run)} to run · results in {run.results}")
    if llm.FAKE:
        log("BEAM_FAKE_MODEL=1: answers and verdicts are canned; this run checks plumbing, not quality")

    try:
        if to_run:
            await run_phases(run, to_run)
    except* (IngestionFailed, llm.ModelError) as failures:
        for failure in failures.exceptions:
            log(f"stopped: {failure}" + (" (scored conversations are kept; rerun with the same --output to finish)"
                                         if getattr(failure, "usage_limit", False) else ""))
        raise SystemExit(1) from None
    finally:
        await run.close()

    scored = [row for row in run.saved_rows() if row["conversationId"] in selected]
    summary = {
        "dataset": "BEAM",
        "split": args.split,
        "datasetRevision": revision,
        "backend": args.backend,
        "backendConfig": run.backend.config(),
        "conversations": len(selected),
        **scoring.summarize(scored),
        "answerModel": llm.MODEL,
        "judgeModel": llm.MODEL,
        "reasoningEffort": llm.REASONING_EFFORT,
        "recall": {"limit": RECALL_LIMIT, "maxTokens": RECALL_MAX_TOKENS},
        "prompts": {"source": exabase_prompts.SOURCE_URL, "sha256": exabase_prompts.SOURCE_SHA256},
        "runTag": run.tag,
        "reusedStores": run.reusing,
        "harnessVersion": HARNESS_VERSION,
        "fakeModel": llm.FAKE,
        "localJudge": bool(llm.LOCAL_JUDGE),
        "finishedAt": utc_iso(datetime.now(UTC)),
    }
    (run.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", "utf-8")
    print_summary(summary)


def print_summary(summary: dict) -> None:
    print()
    for category, scores in summary["categories"].items():
        print(f"  {category:<26} {scores['mean']:.3f}  ({scores['questions']} questions)")
    if summary["questions"]:
        print(f"\n  {'mean over questions':<26} {summary['microAverage']:.3f}  ({summary['questions']} questions)")
        print(f"  {'mean over categories':<26} {summary['macroAverage']:.3f}")
    if summary.get("fakeModel"):
        print("\n  (fake model: scores are meaningless)")
    if summary.get("localJudge"):
        print(f"\n  (local judge {summary['answerModel']}: real scores, not comparable with published BEAM numbers)")
    print()


def positive_number(text: str) -> int:
    number = int(text)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run BEAM against a memory backend.")
    parser.add_argument("--backend", choices=BACKENDS, default="memvine")
    parser.add_argument("--split", choices=SPLITS, default="100k")
    parser.add_argument("--conversations", type=positive_number, help="only the first N conversations (after --offset)")
    parser.add_argument("--offset", type=int, default=0, help="skip the first N conversations")
    parser.add_argument("--output", help="results directory (default: results/<backend>-<split>)")
    parser.add_argument("--categories", nargs="+", metavar="CATEGORY",
                        help="score only these question categories (for work on one category; not a benchmark result)")
    parser.add_argument("--reuse", metavar="TAG", help="score again against the stores of an earlier run (memvine only)")
    return parser.parse_args()


def main() -> None:
    asyncio.run(main_async(parse_arguments()))


if __name__ == "__main__":
    main()
