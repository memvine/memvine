"""Measure automatic capture on real Claude Code sessions, without a server or database.

Each transcript goes through the same steps as a live session: the plugin's parser (export.mjs,
with or without tool outcome lines), the server's checkpoint cuts (checkpoints.plan_cuts, as at
session close), and the v2 knowledge extraction with its gate (extraction.extract_knowledge).
Thread pages and reconciliation are left out: this measures which lessons extraction keeps.

Writes to --out:
  report.md    per session: turns, tool lines, failures and checks seen (the capture
               opportunities), episodes, lessons kept and refused, and the lessons themselves
  review.csv   one row per kept lesson, with a blank `label` column for a person to fill:
               ok | false_positive | trivia | duplicate | progress
  missed.csv   a blank sheet per session for lessons a person finds in the transcript that
               capture missed
  raw.json     everything above, for comparing runs

Run from server/ with its virtualenv (every LLM call is one extraction; mind the cost):

  # pipeline check, no model calls: a heuristic fake that turns each failure into a lesson
  .venv/bin/python ../benchmark/capture/capture.py --backend fake --pick 3 --max-turns 40

  # real extraction on two short sessions, at most 6 calls, via the local claude CLI
  .venv/bin/python ../benchmark/capture/capture.py --backend claude-cli --model haiku \\
      --pick 2 --max-turns 40 --max-episodes 6 --out /tmp/capture-run

  # the same sessions without tool lines (what plugin 0.5 sent), for an A/B
  ... --no-tools --out /tmp/capture-run-notools

Pass transcript paths instead of --pick to measure specific sessions.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("MEMVINE_EMBEDDINGS_BACKEND", "fake")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "server"))

from memvine_server.checkpoints import Event, plan_cuts, render
from memvine_server.extraction.extract import extract_knowledge
from memvine_server.extraction.llm import FakeLLM, make_llm
from memvine_server.extraction.prompts import KNOWLEDGE_SYSTEM

FAILED = re.compile(r"`([^`]*)` failed \(exit \d+\): ([^;]{10,200})")


def parse_at(value: str | None) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.now(UTC)


def pick_sessions(root: Path, count: int, min_turns: int, max_turns: int, with_failures: bool = False,
                  scan: int = 200) -> list[Path]:
    """The most recent transcripts whose prompts and replies number between min and max: short
    sessions, so a measurement run stays cheap. With `with_failures`, only sessions where some
    command failed (where fail-then-succeed lessons can be). Subagent transcripts are skipped."""
    paths = sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:scan]
    chosen = []
    for path in paths:
        # Too big to be short, or still being written (a session running now, this one included).
        if path.stat().st_size > 5_000_000 or datetime.now().timestamp() - path.stat().st_mtime < 600:
            continue
        sessions = export([path], tools=True)
        if not sessions:
            continue
        said = [t for t in sessions[0]["turns"] if t["role"] != "tool"]
        if not min_turns <= len(said) <= max_turns:
            continue
        if with_failures and not signals(sessions[0]["turns"])["failures"]:
            continue
        chosen.append(path)
        if len(chosen) >= count:
            break
    return chosen


def export(paths: list[Path], *, tools: bool) -> list[dict]:
    args = ["node", str(HERE / "export.mjs"), *([] if tools else ["--no-tools"]), *map(str, paths)]
    out = subprocess.run(args, check=True, capture_output=True, text=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def signals(turns: list[dict]) -> dict:
    """Capture opportunities visible in the tool lines, before any model looks."""
    tool = [t["content"] for t in turns if t["role"] == "tool"]
    return {
        "tool_lines": len(tool),
        "failures": sum(len(FAILED.findall(t)) for t in tool),
        "checks_exit0": sum(t.count("` exit 0") for t in tool),
        "reads": sum(1 for t in tool if re.search(r"\b(Read|Grep|Glob) ok", t)),
    }


class HeuristicFake(FakeLLM):
    """A stand-in model for pipeline checks: one gotcha per failed command in the excerpt,
    quoting the failure. Never a measure of quality."""

    async def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        self.calls.append((system, user))
        if system != KNOWLEDGE_SYSTEM:
            return "[]"
        excerpt = user.split("<excerpt>", 1)[-1]
        items = []
        for command, detail in FAILED.findall(excerpt)[:3]:
            items.append({"statement": f"`{command[:80]}` can fail with: {detail.strip()[:120]}", "category": "gotcha",
                          "why": "seen failing in a session", "scope": "project", "subjects": ["build"],
                          "assertion": "observed", "confidence": "low", "quote": detail.strip()[:120]})
        return json.dumps(items)


def trigger_of(quote: str, episode: str) -> str:
    """Which capture trigger a kept lesson most likely came from, by where its quote sits."""
    for block in episode.split("\n\n## "):
        if quote and quote[:40] in block:
            if block.startswith("tool") and " failed (" in block:
                return "fail-then-succeed?"
            if block.startswith("tool") and "exit 0" in block:
                return "verified-by-success?"
            if block.startswith("tool"):
                return "tool-line"
            return "conversation"
    return "?"


async def run(args) -> int:
    if args.transcripts:
        paths = [Path(p).expanduser() for p in args.transcripts]
    else:
        paths = pick_sessions(Path(args.root).expanduser(), args.pick, args.min_turns, args.max_turns, args.with_failures)
    if not paths:
        print("no transcripts matched", file=sys.stderr)
        return 1
    sessions = export(paths, tools=not args.no_tools)

    if args.backend == "fake":
        llm = HeuristicFake()
    else:
        if args.model:
            from memvine_server import config
            config.settings.llm_model = args.model
            config.settings.claude_cli_model = args.model
        llm = make_llm(args.backend)

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    budget = args.max_episodes
    results = []
    for s in sessions:
        events = [Event(i + 1, t["role"], t["content"], parse_at(t.get("at"))) for i, t in enumerate(s["turns"])]
        cuts = plan_cuts(events, now=datetime.now(UTC), final=True) if events else []
        project = Path(s["cwd"] or "unknown").name
        label = f"Claude Code · {project}" + (f" · {s['branch']}" if s.get("branch") else "")
        row = {"session": s["session"], "path": s["path"], "project": project, "said": sum(1 for e in events if e.role != "tool"),
               **signals(s["turns"]), "episodes": len(cuts), "extracted": 0, "kept": 0, "refused_gate": 0,
               "dropped_no_quote": 0, "lessons": [], "skipped_episodes": 0}
        for n, (start, end) in enumerate(cuts):
            if budget is not None and budget <= 0:
                row["skipped_episodes"] += 1
                continue
            part = events[start : end + 1]
            content = render(part)
            result = await extract_knowledge(llm, label=label, occurred_at=part[0].at, content=content, project=project, threads=[])
            budget = None if budget is None else budget - 1
            row["extracted"] += 1
            row["kept"] += len(result.items)
            row["refused_gate"] += result.dropped_gate
            row["dropped_no_quote"] += result.dropped_no_quote
            for c in result.items:
                row["lessons"].append({"episode": f"{part[0].seq}-{part[-1].seq}", "category": c.category, "scope": c.scope,
                                       "assertion": c.assertion, "statement": c.statement, "why": c.why, "quote": c.quote,
                                       "trigger": trigger_of(c.quote, content)})
            print(f"{s['session'][:8]} episode {n + 1}/{len(cuts)}: {len(result.items)} kept, {result.dropped_gate} refused",
                  file=sys.stderr)
        results.append(row)

    write_outputs(out, results, args)
    print(f"wrote {out / 'report.md'}, {out / 'review.csv'}, {out / 'missed.csv'}", file=sys.stderr)
    return 0


def write_outputs(out: Path, results: list[dict], args) -> None:
    (out / "raw.json").write_text(json.dumps({"args": vars(args), "sessions": results}, indent=1, default=str))
    with (out / "review.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["session", "episode", "category", "scope", "assertion", "trigger", "statement", "why", "quote",
                    "label", "note"])
        for r in results:
            for lesson in r["lessons"]:
                w.writerow([r["session"], lesson["episode"], lesson["category"], lesson["scope"], lesson["assertion"],
                            lesson["trigger"], lesson["statement"], lesson["why"], lesson["quote"], "", ""])
    with (out / "missed.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["session", "transcript", "missed lesson (statement)", ("trigger (fail-then-succeed | read-then-applied | "
                    "verified-by-success | stated)"), "where in the transcript", "note"])
        for r in results:
            w.writerow([r["session"], r["path"], "", "", "", ""])

    lines = ["# Capture measurement", "",
             (f"Backend `{args.backend}`{f' ({args.model})' if args.model else ''}, tool lines "
             f"{'off (plugin 0.5)' if args.no_tools else 'on (plugin 0.6)'}, {len(results)} session(s)."), "",
             "| session | project | prompts+replies | tool lines | failures | checks exit 0 | episodes (run) | kept | refused by gate | no quote |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['session'][:8]} | {r['project']} | {r['said']} | {r['tool_lines']} | {r['failures']} | "
                     f"{r['checks_exit0']} | {r['episodes']} ({r['extracted']}) | {r['kept']} | {r['refused_gate']} | "
                     f"{r['dropped_no_quote']} |")
    total = sum(r["kept"] for r in results)
    lines += ["", (f"Lessons kept: {total} over {len(results)} session(s) "
              f"({total / max(1, len(results)):.1f} per session)."), ""]
    for r in results:
        lines += [f"## {r['session']}", "", f"`{r['path']}`", ""]
        if r["skipped_episodes"]:
            lines += [f"{r['skipped_episodes']} episode(s) not extracted (--max-episodes).", ""]
        for lesson in r["lessons"] or []:
            lines.append(f"- **{lesson['category']}** ({lesson['assertion']}, {lesson['trigger']}): {lesson['statement']}"
                         + (f" — _{lesson['why']}_" if lesson["why"] else ""))
        if not r["lessons"]:
            lines.append("- (no lessons kept)")
        lines.append("")
    lines += ["## Reviewing", "",
              ("Label each row of review.csv: ok, false_positive (not true or not a lesson), trivia (run-specific), "
              "duplicate, progress (a status or step of the work). Then read the transcripts and add what capture "
              "missed to missed.csv. Precision = ok / labelled; recall needs missed.csv: ok / (ok + missed).")]
    (out / "report.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("transcripts", nargs="*", help="Claude Code transcript .jsonl files (default: --pick recent ones)")
    p.add_argument("--root", default="~/.claude/projects", help="where --pick looks")
    p.add_argument("--pick", type=int, default=2, help="how many recent short sessions to measure")
    p.add_argument("--min-turns", type=int, default=6, help="--pick: fewest prompts and replies")
    p.add_argument("--max-turns", type=int, default=40, help="--pick: most prompts and replies")
    p.add_argument("--with-failures", action="store_true", help="--pick: only sessions where a command failed")
    p.add_argument("--backend", choices=["fake", "claude-cli", "anthropic"], default="fake")
    p.add_argument("--model", help="model override (claude-cli: haiku, sonnet; anthropic: a model id)")
    p.add_argument("--max-episodes", type=int, default=6, help="cap on extraction calls for the whole run (0: none)")
    p.add_argument("--no-tools", action="store_true", help="send no tool lines, as plugin 0.5 did")
    p.add_argument("--out", default="capture-out", help="output folder")
    args = p.parse_args()
    if args.max_episodes == 0:
        args.max_episodes = None
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
