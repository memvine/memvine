"""The working example behind memvine.dev/how-it-works: one lesson, followed through a real run.

Real Claude Code sessions (`claude -p`), the real plugin and a real model, on a local Memvine server
and console. The two people are scripted: you tell your agent why a timeout is 90 seconds; a
teammate's fresh agent is asked to lower it; a commit then changes the file the memory is about.

  python3 benchmark/how-it-works/run.py all        # setup, you, shot-kept, teammate, change, recheck, shot-rechecked
  python3 benchmark/how-it-works/run.py <step>     # one step; `memories` lists what the project holds

Needs: a local server started with MEMVINE_SIGNUP=open and a real model (MEMVINE_LLM_BACKEND), the
console running against it with an operator key, Claude Code and Google Chrome. Set
MEMVINE_DEMO_KEY to that operator key (mv_mk_...); MEMVINE_DEMO_API (http://127.0.0.1:8419) and
MEMVINE_DEMO_CONSOLE (http://127.0.0.1:3119) when yours differ. Everything is written under
benchmark/how-it-works/out/ (ignored by git); the screenshots go to out/shots/, and `publish`
copies them over site/how-kept.png and site/how-rechecked.png.

Isolation from your own setup: each person gets their own HOME and a copy of the plugin whose hooks
run under it, sessions start with `--setting-sources project --strict-mcp-config`, and Claude Code's
own memory is switched off, so only Memvine carries the lesson. The model's words differ from run
to run; the outcome (one memory kept, the teammate's agent declines, the memory turns stale) held
on each of three runs on 17 Nov 2026.
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PLUGIN = ROOT / "plugins" / "claude-code"
OUT = HERE / "out"
STATE = OUT / "state.json"
API = os.environ.get("MEMVINE_DEMO_API", "http://127.0.0.1:8419").rstrip("/")
CONSOLE = os.environ.get("MEMVINE_DEMO_CONSOLE", "http://127.0.0.1:3119").rstrip("/")
MODEL = os.environ.get("MEMVINE_DEMO_MODEL", "sonnet")
ALLOWED = ["Read", "Write", "Edit", "Glob", "Grep", "Bash(make:*)", "Bash(python3:*)", "Bash(ls:*)", "Bash(cat:*)",
           "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)"]

# The sample repository: Northwind's billing API, one nightly export to the bank.
FILES = {
    "README.md": """# billing-api

Northwind's billing API. `export.py` sends each day's settled invoices to the bank.

    make test
""",
    "Makefile": """.PHONY: test

test:
\t@python3 -m unittest -q
""",
    "export.py": '''"""Nightly export: sends the day's settled invoices to the bank over SFTP."""
import csv
import io
from datetime import date

BANK_HOST = "sftp.bank.northwind.example"
TIMEOUT_SECONDS = 90
BATCH_SIZE = 500


def rows(invoices):
    return [[i["id"], i["customer"], i["total_cents"]] for i in invoices if i["status"] == "settled"]


def render(invoices):
    out = io.StringIO()
    csv.writer(out).writerows([["invoice", "customer", "total_cents"], *rows(invoices)])
    return out.getvalue()


def push(connect, invoices, day=None):
    """Upload the day's file. `connect` opens the SFTP session; tests pass a fake one."""
    name = f"northwind-{(day or date.today()).isoformat()}.csv"
    with connect(BANK_HOST, timeout=TIMEOUT_SECONDS) as session:
        session.put(name, render(invoices))
    return name
''',
    "test_export.py": """import unittest
from contextlib import contextmanager
from datetime import date

import export


class Fake:
    def __init__(self):
        self.sent, self.timeout = {}, None

    @contextmanager
    def connect(self, host, timeout):
        self.timeout = timeout
        yield self

    def put(self, name, body):
        self.sent[name] = body


class ExportTest(unittest.TestCase):
    def test_only_settled_invoices_are_sent(self):
        fake = Fake()
        invoices = [{"id": 1, "customer": "Northwind Traders", "total_cents": 129900, "status": "settled"},
                    {"id": 2, "customer": "Fabrikam", "total_cents": 4500, "status": "open"}]
        name = export.push(fake.connect, invoices, day=date(2026, 11, 16))
        self.assertEqual(name, "northwind-2026-11-16.csv")
        self.assertIn("1,Northwind Traders,129900", fake.sent[name])
        self.assertNotIn("Fabrikam", fake.sent[name])

    def test_the_configured_timeout_is_used(self):
        fake = Fake()
        export.push(fake.connect, [], day=date(2026, 11, 16))
        self.assertEqual(fake.timeout, export.TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
""",
}

YOU_1 = "I'm tidying export.py before the release. Read it and tell me what you would change. Don't edit anything yet."
YOU_2 = ("Leave the timeout in export.py alone. 90 seconds is deliberate: the bank's SFTP server takes over a minute to answer "
         "at month end. It was lowered to 15 once and the nightly export failed for three days.")
TEAMMATE = "The export job takes too long to give up when the bank is down. Lower the timeout in export.py to 10 seconds."


def operator_key():
    key = os.environ.get("MEMVINE_DEMO_KEY", "")
    if not key.startswith("mv_mk_"):
        sys.exit("Set MEMVINE_DEMO_KEY to the local server's operator key (mv_mk_...).")
    return key


def call(method, path, body=None, token=None, project=None):
    headers = {"Content-Type": "application/json"}
    if token is None:
        token = operator_key()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if project:
        headers["X-Memvine-Project"] = project
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: {e.read().decode()[:300]}")


def sh(args, cwd=None, env=None, check=True):
    out = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    if check and out.returncode:
        sys.exit(f"{' '.join(map(str, args))} failed: {out.stderr[:400]}")
    return out.stdout.strip()


def load():
    if not STATE.exists():
        sys.exit("No run yet: start with the setup step (or all).")
    return json.loads(STATE.read_text())


def git(repo, *args):
    return sh(["git", "-c", "user.name=demo", "-c", "user.email=demo@example.com", *args], cwd=repo)


def clean_env(home=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "MEMVINE"))}
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"      # only Memvine carries the lesson
    return env | ({"HOME": home} if home else {})


def setup():
    """A project, a sample repository, and two people each with their own HOME and plugin copy."""
    OUT.mkdir(exist_ok=True)
    if STATE.exists():
        old = load()
        owner = {"token": old["token"]}
        req = urllib.request.Request(f"{API}/v1/projects/{old['slug']}?confirm={old['slug']}", method="DELETE",
                                     headers={"Authorization": f"Bearer {old['token']}"})
        try:
            urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            print("could not delete the previous project:", e.code)
        for name in ("billing-api.git", "seed", "you", "teammate", "shots"):
            shutil.rmtree(OUT / name, ignore_errors=True)
    else:
        password = "demo-" + secrets.token_urlsafe(12)
        owner = call("POST", "/v1/auth/signup", {"email": "you@example.com", "password": password, "name": "You",
                                                 "organization": "Northwind Billing"}, token="")
    project = call("POST", "/v1/projects", {"name": "billing-api", "description": "Northwind's billing API"}, token=owner["token"])
    key = call("POST", f"/v1/projects/{project['slug']}/keys", {"name": "laptops"}, token=owner["token"])["key"]
    state = {"slug": project["slug"], "key": key, "token": owner["token"], "devs": {}}
    bare = OUT / "billing-api.git"
    sh(["git", "init", "-q", "--bare", "-b", "main", str(bare)])
    seed = OUT / "seed"
    sh(["git", "clone", "-q", str(bare), str(seed)])
    for path, text in FILES.items():
        (seed / path).write_text(text)
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "billing-api: the nightly export")
    git(seed, "push", "-q", "origin", "HEAD:main")
    for who in ("you", "teammate"):
        home = OUT / who / "home"
        home.mkdir(parents=True)
        plugin = OUT / who / "plugin"
        shutil.copytree(PLUGIN, plugin)
        hooks_file = plugin / "hooks" / "hooks.json"
        hooks = json.loads(hooks_file.read_text())
        for groups in hooks["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    hook["command"] = f'HOME="{home}" {hook["command"]}'
        hooks_file.write_text(json.dumps(hooks, indent=2))
        repo = OUT / who / "billing-api"
        sh(["git", "clone", "-q", str(bare), str(repo)])
        connected = sh(["node", str(plugin / "bin" / "memvine-hook.mjs"), "connect", key, f"{who}@example.com", API], env=clean_env(str(home)))
        print(who, "|", connected.splitlines()[0].split(". The key")[0])
        state["devs"][who] = {"home": str(home), "plugin": str(plugin), "repo": str(repo)}
    STATE.write_text(json.dumps(state, indent=1))
    print("project:", project["slug"])


def agent(name, who, prompt, resume=None, max_turns=25):
    """One real Claude Code session in that person's copy of the repository, with their plugin."""
    d = load()["devs"][who]
    cmd = ["claude", "-p", prompt, "--model", MODEL, "--max-turns", str(max_turns), "--setting-sources", "project",
           "--strict-mcp-config", "--output-format", "stream-json", "--verbose", "--allowedTools", *ALLOWED, "--plugin-dir", d["plugin"]]
    if resume:
        cmd += ["--resume", resume]
    started = time.time()
    out = subprocess.run(cmd, cwd=d["repo"], env=clean_env(), capture_output=True, text=True, timeout=900)
    (OUT / f"log-{name}.jsonl").write_text(out.stdout)
    run = {"session_id": "", "answer": "", "tools": []}
    for line in out.stdout.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "system" and e.get("subtype") == "init":
            run["session_id"] = e.get("session_id", "")
        elif e.get("type") == "assistant":
            for block in e.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    i = block.get("input", {})
                    run["tools"].append(f"{block.get('name')}: {i.get('command') or Path(i.get('file_path') or '').name or i.get('pattern') or ''}")
        elif e.get("type") == "result":
            run["answer"] = e.get("result") or ""
    (OUT / f"run-{name}.json").write_text(json.dumps(run, indent=1))
    print(f"[{name}] {time.time() - started:.0f}s, exit {out.returncode}, tools {run['tools']}")
    if out.returncode and not run["answer"]:
        print("   stderr:", out.stderr[:400])
    print("   " + run["answer"].replace("\n", "\n   "))
    return run


def wait_kept(session_id, limit=420):
    key = load()["key"]
    started = time.time()
    kept = {}
    while time.time() - started < limit:
        kept = call("GET", f"/v1/sessions/{session_id}/kept", token=key)
        if not kept.get("tending", True):
            break
        time.sleep(5)
    print(f"memory made in {time.time() - started:.0f}s: {kept.get('summary')}")
    for i, m in enumerate(kept.get("kept", []), 1):
        print(f"   {i}. [{m['category']}] {m['statement']}")


def hook(who, *args, stdin=None):
    d = load()["devs"][who]
    out = subprocess.run(["node", str(Path(d["plugin"]) / "bin" / "memvine-hook.mjs"), *args], cwd=d["repo"], env=clean_env(d["home"]),
                         input=json.dumps(stdin) if stdin is not None else None, capture_output=True, text=True, timeout=180)
    return out.stdout + out.stderr


def project_memories():
    items = call("GET", "/v1/memories", project=load()["slug"])
    return items.get("items", items) if isinstance(items, dict) else items


def memories():
    for m in project_memories():
        print(f"   {m['id']} [{m['status']}] [{m.get('category')}] {m['statement']}" + (f"   ({m['stale_reason']})" if m.get("stale_reason") else ""))


def shot(name):
    """The memory's page in the console at phone width, from the top to the end of its evidence."""
    (OUT / "shots").mkdir(exist_ok=True)
    memory = project_memories()[0]["id"]
    print(sh(["node", str(HERE / "shot.mjs"), load()["slug"], str(OUT / "shots" / f"{name}.png"), f"/memories/{memory}",
              "--width", "430", "--height", "1400", "--until", "Evidence"], env=os.environ | {"MEMVINE_DEMO_CONSOLE": CONSOLE}))


def main(step):
    if step == "all":
        for s in ("setup", "you", "shot-kept", "teammate", "change", "recheck", "shot-rechecked"):
            main(s)
    elif step.startswith("shot-"):
        shot(step[5:])
    elif step == "setup":
        setup()
    elif step == "you":
        first = agent("you-1", "you", YOU_1)
        agent("you-2", "you", YOU_2, resume=first["session_id"])
        wait_kept(first["session_id"])
        memories()
    elif step == "teammate":
        agent("teammate", "teammate", TEAMMATE)
    elif step == "change":
        # A real edit to the file the memory is about: retry when the bank drops the connection.
        repo = Path(load()["devs"]["you"]["repo"])
        f = repo / "export.py"
        text = f.read_text().replace("TIMEOUT_SECONDS = 90\n", "TIMEOUT_SECONDS = 90\nRETRIES = 2\n")
        text = text.replace("""    with connect(BANK_HOST, timeout=TIMEOUT_SECONDS) as session:
        session.put(name, render(invoices))
    return name""", """    for attempt in range(RETRIES + 1):
        try:
            with connect(BANK_HOST, timeout=TIMEOUT_SECONDS) as session:
                session.put(name, render(invoices))
            return name
        except ConnectionResetError:
            if attempt == RETRIES:
                raise""")
        f.write_text(text)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "export: retry when the bank drops the connection")
        git(repo, "push", "-q", "origin", "HEAD:main")
        print(git(repo, "log", "--oneline", "-2"))
    elif step == "recheck":
        # The teammate pulls the change; their next session start checks the code memory is anchored to.
        d = load()["devs"]["teammate"]
        git(d["repo"], "pull", "-q", "--ff-only")
        print(hook("teammate", "brief", stdin={"hook_event_name": "SessionStart", "source": "startup",
                                               "session_id": f"teammate-recheck-{int(time.time())}", "cwd": d["repo"]})[:3000])
        memories()
    elif step == "publish":
        for name, target in (("kept", "how-kept.png"), ("rechecked", "how-rechecked.png")):
            shutil.copyfile(OUT / "shots" / f"{name}.png", ROOT / "site" / target)
            print("site/" + target, "<-", f"out/shots/{name}.png")
    elif step == "memories":
        memories()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
