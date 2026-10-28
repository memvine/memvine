"""Answer coverage: a model-free proxy for how much of each reference answer reaches the answer model.

For every question, recall is run exactly as the harness runs it (same moment, same budget) and the
reference answer's key terms are looked up in the recall response: numbers and dates, capitalised
names, and content words of four letters or more. A question's coverage is the share of its key terms
found. Abstention questions are skipped (their reference answer says the information is absent).

This measures retrieval, not answering. A backend can cover every term and still be answered wrongly,
and a correct answer can be phrased in words the reference does not use. It costs nothing, needs no
model account, and moves when retrieval moves, which is what it is for between real runs.

    python coverage.py --split 100k --conversations 1 --backends rag fullcontext
    python coverage.py --split 100k --conversations 1 --backends memvine --memvine-tag "100k 1026-021200"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import defaultdict

from dataset import build_conversation, load_rows
from memory_api import BACKENDS, MemvineBackend, make_backend

STOP = set("""
about above after again against also among because been before being below between both could does doing during each
from further have having here into itself just more most much must never only other ought over same should since some
such than that their theirs them then there these they this those through under until upon very what when where which
while whom will with within without would your yours user assistant response provided chat information based mentioned
""".split())


def key_terms(answer: str) -> set[str]:
    terms: set[str] = set()
    terms.update(re.findall(r"\$?\d[\d,.:/-]*%?", answer))
    terms.update(w.lower() for w in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", answer))
    terms.update(w for w in re.findall(r"[a-z]{4,}", answer.lower()) if w not in STOP)
    return {t.strip(".,").lower() for t in terms if t.strip(".,")}


def coverage(answer: str, recalled_text: str) -> float | None:
    terms = key_terms(answer)
    if not terms:
        return None
    haystack = recalled_text.lower()
    return sum(1 for t in terms if t in haystack) / len(terms)


async def probe(backend_name: str, rows: list[dict], tags: list[str] | None) -> dict:
    backend = make_backend(backend_name, (tags or ["coverage"])[0])
    per_category: dict[str, list[float]] = defaultdict(list)
    tokens: list[int] = []
    try:
        for row in rows:
            conversation = build_conversation(row)
            if isinstance(backend, MemvineBackend):
                handle = None
                for tag in tags or []:  # a conversation's project carries the tag of the run that sent it
                    backend.tag = tag
                    try:
                        handle = await backend.reopen(conversation)
                        break
                    except SystemExit:
                        continue
                if handle is None:
                    raise SystemExit(f"no memvine project for conversation {conversation.id} under tags {tags}")
            else:
                handle = await backend.open(conversation)
                await backend.ingest(handle, conversation)
            for q in conversation.questions:
                if q.category == "abstention" or not q.gold_answers:
                    continue
                recalled = await backend.recall(handle, q.text, q.as_of, conversation.user_identity)
                tokens.append(int(recalled.get("usedEvidenceTokens") or 0))
                score = coverage(q.gold_answers[0], json.dumps(recalled, ensure_ascii=False))
                if score is not None:
                    per_category[q.category].append(score)
    finally:
        await backend.close()
    categories = {c: round(sum(v) / len(v), 3) for c, v in sorted(per_category.items())}
    scores = [s for v in per_category.values() for s in v]
    return {
        "backend": backend_name,
        "questions": len(scores),
        "coverage": round(sum(scores) / len(scores), 3) if scores else None,
        "meanEvidenceTokens": round(sum(tokens) / len(tokens)) if tokens else 0,
        "categories": categories,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--split", default="100k")
    parser.add_argument("--conversations", type=int, default=1)
    parser.add_argument("--offset", type=int, default=0, help="skip the first N conversations")
    parser.add_argument("--backends", nargs="+", choices=BACKENDS, default=["rag", "fullcontext"])
    parser.add_argument("--memvine-tag", nargs="+", help="run tag(s) of the memvine projects, e.g. '100k 1026-021200'; one per conversation run")
    args = parser.parse_args()
    rows, _ = load_rows(args.split)
    rows = rows[args.offset : args.offset + args.conversations]
    results = [await probe(b, rows, args.memvine_tag if b == "memvine" else None) for b in args.backends]

    categories = sorted({c for r in results for c in r["categories"]})
    print(f"\nanswer coverage (share of reference-answer key terms present in recall), BEAM {args.split}, {len(rows)} conversation(s)\n")
    print(f"  {'category':<26}" + "".join(f"{r['backend']:>13}" for r in results))
    for c in categories:
        print(f"  {c:<26}" + "".join(f"{r['categories'].get(c, float('nan')):>13.3f}" for r in results))
    print(f"  {'overall':<26}" + "".join(f"{(r['coverage'] or 0):>13.3f}" for r in results))
    print(f"  {'mean evidence tokens':<26}" + "".join(f"{r['meanEvidenceTokens']:>13}" for r in results))
    print("\n  Retrieval proxy only: not a BEAM score.\n")


if __name__ == "__main__":
    asyncio.run(main())
