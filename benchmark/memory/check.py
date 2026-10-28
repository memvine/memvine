"""The memory check: does plain-text memory work for an agent? Run it before shipping any change
to extraction, reconciliation or recall. Uses the real model; never runs in CI.

  cd server && .venv/bin/python ../benchmark/memory/check.py [--server-dir DIR] [--skip-cases] [--skip-agent] [--agent-e2e]

It starts a throwaway API server (from --server-dir, default this checkout's server/) on its own
database, then:
  1. keep/drop cases: what extraction admits (benchmark/knowledge/cases.py)
  2. the October test: three facts become the right memories, each is recalled first, an
     unrelated question abstains, a re-send changes nothing, a contradiction supersedes
  3. the briefing: what the agent receives for one question carries the answer once, nothing
     unrelated, under a token ceiling
  4. a fresh agent given only the briefing answers correctly
  5. (--agent-e2e) a real Claude Code agent, run headless with the plugin connected and again
     without memory, deploys, answers and tests the way memory says (benchmark/memory/agent_e2e.py:
     six short `claude -p` runs in a scratch repo, isolated from the person's own setup)
and prints PASS/FAIL per check (exit 1 on any FAIL). Synthesis runs on MEMVINE_LLM_BACKEND
(claude-cli by default here: a few minutes on a Claude subscription; anthropic with an API key).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# Before any server module reads its settings: synthesis on the Claude subscription unless told otherwise.
os.environ.setdefault("MEMVINE_LLM_BACKEND", "claude-cli")
os.environ.setdefault("MEMVINE_CLAUDE_CLI_CONCURRENCY", "4")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ADMIN_DSN = os.environ.get("MEMVINE_CHECK_ADMIN_URL", "postgresql://memvine:memvine@localhost:5433/memvine")
DB = "memvine_check"
PORT = int(os.environ.get("MEMVINE_CHECK_PORT", "8411"))
BRIEFING_CEILING = int(os.environ.get("MEMVINE_CHECK_TOKENS", "300"))  # tokens for a one-fact question

EPISODES = [
    {"id": "ep1", "timestamp": "2026-10-20T10:00:00Z", "content": "User: For the muse project, the production database is Postgres 16 running on port 5544. Assistant: Got it, Postgres 16 on port 5544 for muse production."},
    {"id": "ep2", "timestamp": "2026-10-21T10:00:00Z", "content": "User: Always deploy muse with `make deploy ENV=prod` from the repo root, never with kubectl apply directly. Assistant: Understood, I'll use make deploy ENV=prod."},
    {"id": "ep3", "timestamp": "2026-10-22T10:00:00Z", "content": "User: The muse frontend uses SvelteKit, and we decided against Next.js because the team wants smaller bundles. Assistant: Noted: SvelteKit chosen over Next.js for bundle size."},
]
UPDATE = {"id": "ep4", "timestamp": "2026-10-25T10:00:00Z", "content": "User: Update for muse: we migrated the production database, it now runs on port 6432 behind PgBouncer instead of 5544. Assistant: Noted, muse prod DB is now on port 6432 via PgBouncer."}
DB_QUESTION = "Which port does the muse production database use?"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""), flush=True)
    return ok


def call(method: str, path: str, key: str, body=None, project: str | None = None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    if project:
        req.add_header("X-Memvine-Project", project)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def reset_database() -> str:
    import psycopg

    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{DB}"')
    return ADMIN_DSN.rsplit("/", 1)[0] + "/" + DB


def start_server(server_dir: Path, dsn: str, mgmt: str) -> subprocess.Popen:
    env = {**os.environ, "MEMVINE_DATABASE_URL": dsn, "MEMVINE_MANAGEMENT_KEY": mgmt, "MEMVINE_ROW_SECURITY": "false",
           "MEMVINE_WORKER_ENABLED": "true"}
    # -c puts the working directory first on sys.path, so --server-dir's code is the code tested.
    proc = subprocess.Popen([sys.executable, "-c", f"import uvicorn; uvicorn.run('memvine_server.main:app', host='127.0.0.1', port={PORT}, log_level='warning')"],
                            cwd=server_dir, env=env, stdout=subprocess.DEVNULL, stderr=open(HERE / "check-server.log", "w"))
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2)
            return proc
        except Exception:  # noqa: BLE001
            time.sleep(1)
    proc.kill()
    raise SystemExit(f"server did not start; see {HERE / 'check-server.log'}")


def wait_synthesized(key: str, timeout: int = 900) -> list:
    deadline = time.time() + timeout
    while time.time() < deadline:
        eps = call("GET", "/v1/episodes", key)
        if eps and all(e.get("job_status") not in ("queued", "running") for e in eps):
            return eps
        time.sleep(5)
    raise SystemExit("synthesis did not finish in time")


def recall(key: str, query: str) -> dict:
    return call("POST", "/v1/recall", key, {"query": query})


def briefing_checks(r: dict, label: str) -> None:
    text = r["briefing"]
    check(f"{label}: the current port is the answer", "6432" in text and r["items"] and "6432" in r["items"][0]["memory"]["statement"],
          r["items"][0]["memory"]["statement"][:80] if r["items"] else "no items")
    check(f"{label}: the old port is shown only as history", "5544" not in text or "was:" in text)
    check(f"{label}: nothing unrelated (no SvelteKit)", "SvelteKit" not in text and "Next.js" not in text)
    deploy_mentions = text.count("make deploy ENV=prod")
    check(f"{label}: the standing deploy rule appears once", deploy_mentions == 1, f"{deploy_mentions} times")
    check(f"{label}: within {BRIEFING_CEILING} tokens", r["tokens"] <= BRIEFING_CEILING, f"{r['tokens']} tokens")


def fresh_agent(briefing: str) -> str:
    prompt = (f"{briefing}\n\n---\nYou are a new engineer's coding agent on the muse project. Using only the memory above, "
              "answer in two lines: (1) which port to connect to for the muse production database, (2) the exact deploy command.")
    out = subprocess.run(["claude", "-p", "--model", "sonnet"], input=prompt, capture_output=True, text=True, timeout=300)
    return out.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server-dir", type=Path, default=ROOT / "server")
    ap.add_argument("--skip-cases", action="store_true")
    ap.add_argument("--skip-agent", action="store_true")
    ap.add_argument("--agent-e2e", action="store_true", help="section 5: six headless Claude Code runs with and without the plugin")
    args = ap.parse_args()

    if not args.skip_cases:
        sys.path.insert(0, str(args.server_dir))
        sys.path.insert(0, str(ROOT / "benchmark" / "knowledge"))
        from cases import run_cases

        print("== 1. keep/drop cases", flush=True)
        for name, ok, items in asyncio.run(run_cases()):
            check(f"case {name}", ok, "; ".join(items)[:120] if items else "kept nothing")

    print(f"== 2. the October test (server: {args.server_dir})", flush=True)
    mgmt = "mv_mk_check_" + secrets.token_hex(8)
    proc = start_server(args.server_dir, reset_database(), mgmt)
    try:
        call("POST", "/v1/projects", mgmt, {"name": "muse", "slug": "muse",
                                             "description": "muse: a web app (SvelteKit frontend, Postgres) and how it is deployed"})
        key = call("POST", "/v1/projects/muse/keys", mgmt, {"name": "check"})["key"]
        call("POST", "/v1/ingest", key, {"items": EPISODES})
        wait_synthesized(key)
        mems = [m for m in call("GET", "/v1/memories", key) if m["status"] == "active"]
        cats = sorted(m.get("category") or "" for m in mems)
        check("three facts become three memories", len(mems) == 3, f"{len(mems)}: {cats}")
        check("the deploy rule is standing", any(m.get("standing") and "make deploy" in m["statement"] for m in mems))
        for q, want in [(DB_QUESTION, "5544"), ("Why does muse use SvelteKit?", "SvelteKit")]:
            r = recall(key, q)
            top = r["items"][0]["memory"]["statement"] if r["items"] else ""
            check(f"recalled first: {q}", want in top, top[:80])
        r = recall(key, "How do I deploy muse?")
        check("recalled: How do I deploy muse?", "make deploy ENV=prod" in r["briefing"])
        r = recall(key, "What is the weather in Paris?")
        check("an unrelated question abstains", r["abstained"] and r["tokens"] == 0, f"{r.get('abstain_reason')} at {r.get('top_relevance')}")
        again = call("POST", "/v1/ingest", key, {"items": EPISODES})
        check("a re-send changes nothing", again["queued"] == 0 and all(i["outcome"] == "unchanged" for i in again["items"]))
        call("POST", "/v1/ingest", key, {"items": [UPDATE]})
        wait_synthesized(key)
        mems = call("GET", "/v1/memories", key) or []
        old = [m for m in mems if "5544" in m["statement"] and "6432" not in m["statement"]]
        check("the contradiction supersedes the old value", bool(old) and all(m["status"] == "superseded" for m in old),
              ", ".join(m["status"] for m in old) or "old value missing")

        print("== 3. the briefing", flush=True)
        r = recall(key, DB_QUESTION)
        briefing_checks(r, "port question")
        (HERE / "check-briefing.txt").write_text(r["briefing"])

        if not args.skip_agent:
            print("== 4. a fresh agent, briefing only", flush=True)
            r = recall(key, "I need to connect to the muse production database and then ship a deploy. What port, and what is the deploy command?")
            answer = fresh_agent(r["briefing"])
            check("the fresh agent uses the current port", "6432" in answer, answer.replace("\n", " ")[:160])
            check("the fresh agent uses the deploy command", "make deploy ENV=prod" in answer)

        if args.agent_e2e:
            from agent_e2e import run_section

            run_section(call, check, key, PORT, wait_synthesized, HERE)
    finally:
        proc.terminate()
        proc.wait(timeout=20)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
