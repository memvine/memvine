"""Are pull request reviews a better memory source than agent chat? A one-off test on a public repo.

  cd server && .venv/bin/python ../benchmark/sources/pr_replay.py OWNER/REPO --out DIR [--train 60] [--test 20] [--recall-only]

Takes the repo's latest merged PRs that reviewers really discussed, oldest first. The earlier ones
go into a throwaway local project (same extraction as production, on the local server, never the
hosted one). Each later PR then asks recall with only its title and description, what an agent knows
before the work. A judge model compares what recall served, and the repo's CONTRIBUTING guide, with
what reviewers actually asked for on that PR: their comments are the answer key.

Writes prs.json, memories.json, replay.json and report.txt to --out (keep that outside the repo). The
local database stays afterwards for inspection, and --recall-only replays on it again without
re-extracting (give a new --out holding the same prs.json). MEMVINE_RECALL_GATE=false replays with
similarity alone; a higher MEMVINE_RECALL_GATE_TIMEOUT suits the slower claude-cli backend.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "memory"))
os.environ.setdefault("MEMVINE_CHECK_DB", "memvine_pr_replay")
os.environ.setdefault("MEMVINE_CHECK_PORT", "8412")
import check as harness  # noqa: E402  (the memory check's server, database and HTTP helpers)

MIN_REVIEW_COMMENTS = 3
MAX_EPISODE_CHARS = 12_000
QUERY = """query($owner:String!,$name:String!,$cursor:String){repository(owner:$owner,name:$name){
pullRequests(states:MERGED,last:50,before:$cursor,orderBy:{field:UPDATED_AT,direction:ASC}){
pageInfo{hasPreviousPage startCursor}
nodes{number title body mergedAt url author{login}
comments(first:40){nodes{author{login} body createdAt}}
reviews(first:40){nodes{author{login} body submittedAt comments(first:40){nodes{author{login} body path createdAt}}}}}}}}"""


def human(author: dict | None, pr_author: str | None = None) -> bool:
    login = (author or {}).get("login") or ""
    return bool(login) and not login.endswith("[bot]") and "bot" not in login.lower() and login != pr_author


def gh_json(*args: str) -> dict:
    return json.loads(subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout)


def fetch(repo: str, want: int, max_pages: int = 30) -> list[dict]:
    """Merged PRs, oldest first, each with reviewers' comments and the whole discussion in order."""
    owner, name = repo.split("/")
    cursor, found = None, []
    for _ in range(max_pages):
        args = ["api", "graphql", "-f", f"query={QUERY}", "-F", f"owner={owner}", "-F", f"name={name}"]
        if cursor:
            args += ["-F", f"cursor={cursor}"]
        page = gh_json(*args)["data"]["repository"]["pullRequests"]
        for p in page["nodes"]:
            pr_author = (p["author"] or {}).get("login")
            review = [{"who": c["author"]["login"], "at": c["createdAt"], "path": c.get("path"), "body": c["body"]}
                      for r in p["reviews"]["nodes"] for c in r["comments"]["nodes"] if human(c["author"], pr_author)]
            if len(review) < MIN_REVIEW_COMMENTS:
                continue
            said = [{"who": c["author"]["login"], "at": c["createdAt"], "path": None, "body": c["body"]}
                     for c in p["comments"]["nodes"] if human(c["author"])]
            said += [{"who": r["author"]["login"], "at": r["submittedAt"], "path": None, "body": r["body"]}
                     for r in p["reviews"]["nodes"] if r["body"] and human(r["author"])]
            said += [{"who": c["author"]["login"], "at": c["createdAt"], "path": c.get("path"), "body": c["body"]}
                     for r in p["reviews"]["nodes"] for c in r["comments"]["nodes"] if human(c["author"])]
            said.sort(key=lambda c: c["at"] or "")
            found.append({"number": p["number"], "title": p["title"], "body": p["body"] or "", "merged_at": p["mergedAt"],
                          "url": p["url"], "author": pr_author, "review": review, "said": said})
        if len(found) >= want or not page["pageInfo"]["hasPreviousPage"]:
            break
        cursor = page["pageInfo"]["startCursor"]
    found.sort(key=lambda p: p["merged_at"])
    return found[-want:]


def episode(pr: dict, repo: str) -> dict:
    lines = [f"Pull request #{pr['number']} in {repo}: {pr['title']}", "", f"## {pr['author']} · {pr['merged_at']}", pr["body"].strip()]
    for c in pr["said"]:
        where = f" on {c['path']}" if c["path"] else ""
        lines += ["", f"## {c['who']} · {c['at']}{where}", c["body"].strip()]
    return {"id": f"pr-{pr['number']}", "timestamp": pr["merged_at"], "identity": pr["author"], "label": f"PR #{pr['number']}",
            "content": "\n".join(lines)[:MAX_EPISODE_CHARS]}


def guide(repo: str) -> str:
    """The repo's own written guidance: the static file an agent could read instead."""
    for path in ("CONTRIBUTING.md", ".github/CONTRIBUTING.md", "CONTRIBUTING.rst"):
        try:
            return base64.b64decode(gh_json("api", f"repos/{repo}/contents/{path}")["content"]).decode()[:30_000]
        except subprocess.CalledProcessError:
            continue
    return ""


JUDGE = """You score whether earlier project knowledge would have helped on one pull request.

PR: {title}

What reviewers said on this PR, numbered:
{comments}

A: knowledge served by a memory system for this PR, numbered:
{memories}

B: the project's written contributing guide:
<guide>
{guide}
</guide>

For each reviewer comment decide:
- reusable: true only if it applies a general project rule, convention or design preference that would also apply to a different PR. Comments about this PR's own logic, typos, questions, praise or thanks are false.
- by_memory: true only if a numbered item in A states that same guidance, clearly enough that an author who read it would not have needed the comment.
- by_guide: the same test against B.
For each item in A decide "useful": would an engineer starting this exact PR act differently because of it?

Be strict. A loosely related item is not a match.
Return ONLY JSON: {{"comments":[{{"n":1,"reusable":false,"by_memory":false,"by_guide":false,"why":"short"}}],"memories":[{{"n":1,"useful":false}}]}}"""


def judge(pr: dict, served: list[str], guide_text: str) -> dict:
    comments = "\n".join(f"{i}. [{c['who']}] {c['body'][:500]}" for i, c in enumerate(pr["review"][:25], 1))
    memories = "\n".join(f"{i}. {s}" for i, s in enumerate(served, 1)) or "(nothing served)"
    prompt = JUDGE.format(title=pr["title"], comments=comments, memories=memories, guide=guide_text or "(none)")
    # This machine may have the Memvine plugin installed: its hooks must not run on the judge's calls,
    # or every judged PR would be sent to the hosted project as a session.
    args = ["claude", "--print", "--no-session-persistence", "--model", "sonnet", "--tools", "",
            "--settings", '{"disableAllHooks": true}']
    for _ in range(3):
        out = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=300,
                             env={**os.environ, "MEMVINE_HOOKS_DISABLED": "1"}).stdout
        match = re.search(r"\{.*\}", out, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {"comments": [], "memories": [], "failed": True}


def report(repo: str, n_train: int, memories: list[dict], replay: list[dict]) -> str:
    comments = [c for r in replay for c in r["verdict"]["comments"]]
    reusable = [c for c in comments if c.get("reusable")]
    served = [m for r in replay for m in r["verdict"]["memories"]]
    both = sum(1 for c in reusable if c.get("by_memory") and c.get("by_guide"))
    only_memory = sum(1 for c in reusable if c.get("by_memory") and not c.get("by_guide"))
    only_guide = sum(1 for c in reusable if c.get("by_guide") and not c.get("by_memory"))
    answered = sum(1 for r in replay if r["served"])
    return "\n".join([
        f"{repo}: {n_train} PRs fed in, {len(replay)} replayed ({sum(1 for r in replay if r['verdict'].get('failed'))} unjudged)",
        f"memories kept: {len(memories)}",
        f"PRs where recall served something: {answered}/{len(replay)}",
        f"memories served: {len(served)}, judged useful for that PR: {sum(1 for m in served if m.get('useful'))}",
        f"reviewer comments: {len(comments)}, reusable guidance: {len(reusable)}",
        f"reusable comments anticipated: memory {only_memory + both}, guide {only_guide + both}, memory only {only_memory}, guide only {only_guide}",
    ])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--train", type=int, default=60)
    ap.add_argument("--test", type=int, default=20)
    ap.add_argument("--recall-only", action="store_true", help="replay on the existing database")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    prs_path = args.out / "prs.json"
    if prs_path.exists():
        prs = json.loads(prs_path.read_text())
    else:
        prs = fetch(args.repo, args.train + args.test)
        prs_path.write_text(json.dumps(prs, indent=1))
    train, test = prs[:-args.test], prs[-args.test:]
    print(f"{len(prs)} discussed PRs, {prs[0]['merged_at'][:10]} to {prs[-1]['merged_at'][:10]}; cutoff {test[0]['merged_at'][:10]}", flush=True)
    guide_text = guide(args.repo)
    print(f"guide: {len(guide_text)} characters", flush=True)

    mgmt = "mv_mk_check_" + secrets.token_hex(8)
    dsn = harness.ADMIN_DSN.rsplit("/", 1)[0] + "/" + harness.DB if args.recall_only else harness.reset_database()
    proc = harness.start_server(harness.ROOT / "server", dsn, mgmt)
    try:
        if not args.recall_only:
            harness.call("POST", "/v1/projects", mgmt, {"name": "pr-replay", "slug": "pr-replay",
                                                        "description": f"The open source project {args.repo}; memory comes from its pull request reviews."})
        key = harness.call("POST", "/v1/projects/pr-replay/keys", mgmt, {"name": "replay"})["key"]
        started = time.time()
        if not args.recall_only:
            items = [episode(p, args.repo) for p in train]
            for i in range(0, len(items), 50):
                harness.call("POST", "/v1/ingest", key, {"items": items[i:i + 50]})
            harness.wait_synthesized(key, timeout=5400)
        memories = [m for m in harness.call("GET", "/v1/memories", key) if m["status"] == "active"]
        (args.out / "memories.json").write_text(json.dumps(memories, indent=1, default=str))
        print(f"{len(memories)} memories from {len(train)} PRs in {int(time.time() - started)}s", flush=True)

        replay, model_chose = [], 0
        for pr in test:
            r = harness.call("POST", "/v1/recall", key, {"query": f"{pr['title']}\n\n{pr['body'][:800]}"})
            model_chose += r.get("picked_by_model", False)
            served = list(dict.fromkeys([*r["standing"], *(i["memory"]["statement"] for i in r["items"])]))
            verdict = judge(pr, served, guide_text)
            replay.append({"number": pr["number"], "title": pr["title"], "abstained": r["abstained"], "tokens": r["tokens"],
                           "top_relevance": r.get("top_relevance"), "served": served, "pages": [t["title"] for t in r["threads"]],
                           "verdict": verdict})
            print(f"#{pr['number']} served {len(served)}, pages {len(r['threads'])}, tokens {r['tokens']}", flush=True)
            (args.out / "replay.json").write_text(json.dumps(replay, indent=1, default=str))
        text = report(args.repo, len(train), memories, replay) + f"\nrecalls where the model chose: {model_chose}/{len(replay)}"
        (args.out / "report.txt").write_text(text + "\n")
        print(text, flush=True)
    finally:
        proc.terminate()
        proc.wait(timeout=20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
