"""Section 5 of the memory check: does memory change what a real agent DOES?

Three tasks run with `claude -p` in a throwaway "muse" repo, each twice: with the Claude Code
plugin (plugins/claude-code) connected to the check server's project, and without memory. The
verdict comes from facts, not prose: which make targets and kubectl calls ran (both are fakes that
write to a log outside the repo), the TEST_DB_PORT they ran with, and the number in the answer.

Isolation from the person's own setup (their Claude Code login is still used):
  - `--setting-sources project` and `--strict-mcp-config`: their user settings, installed plugins
    (including their own memvine plugin), user hooks and MCP servers are not loaded;
  - the plugin is loaded for the run with `--plugin-dir`, from a copy whose hooks run with HOME set
    to a temporary folder, so its config (written by its own `connect --here`) and state never touch
    ~/.memvine; MEMVINE_* variables are not passed to the agent;
  - the transcripts Claude Code writes under ~/.claude/projects for the scratch folder are deleted
    afterwards, so no later backfill can pick them up;
  - ~/.memvine/config.json and ~/.claude/settings.json are hashed before and after, and
    ~/.memvine/state.json (which the person's own sessions keep writing) must not name a check
    session or the scratch folder; either is a FAIL.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "claude-code"
MODEL = os.environ.get("MEMVINE_CHECK_AGENT_MODEL", "sonnet")
MAX_TURNS = os.environ.get("MEMVINE_CHECK_AGENT_TURNS", "8")

# The gotcha the October episodes do not carry; ingested here like any other conversation.
TEST_PORT_EPISODE = {"id": "ep5", "timestamp": "2026-10-26T10:00:00Z", "content": (
    "User: Heads up for muse: the tests need TEST_DB_PORT=5433. Port 5432 on our machines is another app's "
    "database (billing), so `make test` without it connects to the wrong DB and fails. "
    "Assistant: Got it, I'll always run the muse tests with TEST_DB_PORT=5433.")}

TASKS = [
    ("deploy", "Deploy muse to production."),
    ("port", "What port should I use to connect to the muse production database? Answer with the number."),
    ("tests", "Run the muse tests."),
]

ALLOWED = ["Read", "Glob", "Grep", "Bash(make:*)", "Bash(make *)", "Bash(kubectl:*)", "Bash(kubectl *)",
           "Bash(TEST_DB_PORT=* make *)", "Bash(ENV=* make *)"]

# The test target passes only on the right port, compared by checksum so reading the Makefile does
# not give the port away (`printf %s 5433 | cksum`).
TEST_PORT_SUM = "38935078 4"

MAKEFILE = """\
# muse build and release targets
ENV ?= staging

deploy:
\t@echo "make deploy ENV=$(ENV)" >> {log}
\t@echo "Deploying muse to $(ENV)... done"

test:
\t@echo "make test TEST_DB_PORT=$(TEST_DB_PORT)" >> {log}
\t@if [ "$$(printf %s '$(TEST_DB_PORT)' | cksum)" = "{ok}" ]; then echo "42 passed in 1.8s"; \\
\telse echo 'FAILED (42 errors): psycopg.errors.UndefinedTable: relation "muse_users" does not exist'; exit 1; fi

.PHONY: deploy test
"""

README = """\
# muse

A small web app: SvelteKit frontend, Postgres behind it, running on Kubernetes.

- `src/` the app
- `k8s/` Kubernetes manifests (`k8s/prod`, `k8s/staging`)
- `Makefile` build and release targets
"""

KUBECTL = """\
#!/bin/sh
echo "kubectl $*" >> {log}
case "$1" in
  apply) echo "deployment.apps/muse configured";;
  *) echo "No resources found";;
esac
"""

USER_FILES = [Path.home() / ".memvine" / "config.json", Path.home() / ".claude" / "settings.json"]
USER_STATE = Path.home() / ".memvine" / "state.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"


def _scratch(base: Path) -> tuple[Path, Path, Path]:
    """The muse repo, the folder holding the fake kubectl, and the log both fakes write to."""
    repo, bin_dir, log = base / "muse", base / "bin", base / "calls.log"
    (repo / "k8s" / "prod").mkdir(parents=True)
    (repo / "k8s" / "staging").mkdir(parents=True)
    (repo / "src").mkdir()
    bin_dir.mkdir()
    (repo / "Makefile").write_text(MAKEFILE.format(log=log, ok=TEST_PORT_SUM))
    (repo / "README.md").write_text(README)
    (repo / "src" / "app.js").write_text("export const name = 'muse';\n")
    for env in ("prod", "staging"):
        (repo / "k8s" / env / "deployment.yaml").write_text(
            f"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: muse\n  namespace: muse-{env}\n")
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(KUBECTL.format(log=log))
    kubectl.chmod(0o755)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=check@memvine.dev", "-c", "user.name=check", "commit", "-qm", "muse"], cwd=repo, check=True)
    return repo, bin_dir, log


def _plugin_copy(base: Path, hook_home: Path) -> Path:
    """The plugin as shipped, with each hook command run under the temporary HOME."""
    copy = base / "plugin"
    shutil.copytree(PLUGIN, copy)
    hooks_file = copy / "hooks" / "hooks.json"
    hooks = json.loads(hooks_file.read_text())
    for groups in hooks["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                assert hook["command"].startswith("node "), hook["command"]
                hook["command"] = f'HOME="{hook_home}" {hook["command"]}'
    hooks_file.write_text(json.dumps(hooks, indent=2))
    return copy


def _agent_env(bin_dir: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "MEMVINE"))}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return env


def _run(prompt: str, repo: Path, bin_dir: Path, log: Path, plugin: Path | None) -> dict:
    log.write_text("")
    cmd = ["claude", "-p", prompt, "--model", MODEL, "--max-turns", MAX_TURNS, "--setting-sources", "project",
           "--strict-mcp-config", "--output-format", "stream-json", "--verbose", "--allowedTools", *ALLOWED]
    if plugin:
        # The plugin's skill searches memory with its own script; a person would allow that, so the run does.
        cmd += ["--plugin-dir", str(plugin), "--allowedTools", f'Bash(node "{plugin}/bin/memvine-hook.mjs" search:*)']
    out = subprocess.run(cmd, cwd=repo, env=_agent_env(bin_dir), capture_output=True, text=True, timeout=600)
    run = {"session_id": "", "plugins": [], "commands": [], "denied": [], "answer": "", "hook_context": [], "error": ""}
    for line in out.stdout.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "system" and d.get("subtype") == "init":
            run["session_id"] = d.get("session_id", "")
            run["plugins"] = [p.get("name") for p in d.get("plugins", [])]
        elif d.get("type") == "system" and "hook" in str(d.get("subtype", "")):
            text = json.dumps(d)
            if "memvine" in text:
                run["hook_context"].append(text[:4000])
        elif d.get("type") == "assistant":
            for block in d.get("message", {}).get("content", []):
                if block.get("type") == "tool_use" and block.get("name") == "Bash":
                    run["commands"].append(block.get("input", {}).get("command", ""))
        elif d.get("type") == "result":
            run["answer"] = d.get("result") or ""
            run["denied"] = [json.dumps(x.get("tool_input", x))[:200] for x in d.get("permission_denials", [])]
            if d.get("is_error"):
                run["error"] = d.get("subtype", "error")
    if out.returncode and not run["error"]:
        run["error"] = (out.stderr or "exit %d" % out.returncode)[:300]
    run["calls"] = [c for c in log.read_text().splitlines() if c.strip()]
    return run


def _outcome(task: str, run: dict) -> str:
    """The fact the task is judged on, as one short string."""
    calls = run["calls"]
    if task == "deploy":
        made = [c for c in calls if c.startswith("make deploy")]
        kube = [c for c in calls if c.startswith("kubectl apply")]
        return "; ".join(made + kube) or "nothing deployed"
    if task == "tests":
        return "; ".join(c for c in calls if c.startswith("make test")) or "no tests run"
    ports = re.findall(r"\b\d{4,5}\b", run["answer"])
    return ",".join(dict.fromkeys(ports)) or "no port"


def _describe(run: dict) -> str:
    cmds = " | ".join(run["commands"]) or "no commands"
    extra = f"; denied: {', '.join(run['denied'])}" if run["denied"] else ""
    err = f"; error: {run['error']}" if run["error"] else ""
    return f"ran [{cmds}]{extra}{err}; answer: {run['answer'].strip()[:160]!r}".replace("\n", " ")


def run_section(call, check, key: str, port: int, wait_synthesized, report_dir: Path) -> None:
    print("== 5. a real agent with the plugin", flush=True)
    before = {p: _digest(p) for p in USER_FILES}
    call("POST", "/v1/ingest", key, {"items": [TEST_PORT_EPISODE]})
    wait_synthesized(key)
    mems = [m for m in call("GET", "/v1/memories", key) if m["status"] == "active"]
    check("seeded: the test-port gotcha is a memory", any("5433" in m["statement"] for m in mems),
          next((m["statement"][:80] for m in mems if "5433" in m["statement"]), "missing"))

    base = Path(tempfile.mkdtemp(prefix="memvine-agent-e2e-"))
    projects_dir = Path.home() / ".claude" / "projects"
    before_projects = set(os.listdir(projects_dir)) if projects_dir.exists() else set()
    report: dict = {"base": str(base), "runs": {}}
    try:
        repo, bin_dir, log = _scratch(base)
        hook_home = base / "home"
        hook_home.mkdir()
        plugin = _plugin_copy(base, hook_home)
        connect = subprocess.run(["node", str(plugin / "bin" / "memvine-hook.mjs"), "connect", key, "check-agent",
                                  f"http://127.0.0.1:{port}", "--here"], cwd=repo, capture_output=True, text=True,
                                 env={**_agent_env(bin_dir), "HOME": str(hook_home)}, timeout=30)
        bound = json.loads((hook_home / ".memvine" / "config.json").read_text()).get("projects", {}) \
            if (hook_home / ".memvine" / "config.json").exists() else {}
        check("the plugin connects the scratch folder (connect --here, temporary HOME)",
              any(Path(p).resolve() == repo.resolve() for p in bound), connect.stdout.strip()[:120])

        outcomes: dict[str, dict[str, str]] = {}
        for task, prompt in TASKS:
            for mode in ("without", "with"):
                run = _run(prompt, repo, bin_dir, log, plugin if mode == "with" else None)
                report["runs"][f"{task}/{mode}"] = run
                outcomes.setdefault(task, {})[mode] = _outcome(task, run)
                print(f"      {task} {mode.upper()} memory: {outcomes[task][mode]} — {_describe(run)}", flush=True)
                if mode == "without":
                    check(f"{task} WITHOUT: no memvine plugin loaded", "memvine" not in run["plugins"], ",".join(run["plugins"]))
                else:
                    check(f"{task} WITH: the plugin is loaded", "memvine" in run["plugins"], ",".join(run["plugins"]))

        w = {t: report["runs"][f"{t}/with"] for t, _ in TASKS}
        deploy_calls = w["deploy"]["calls"]
        check("WITH memory the agent deploys with make deploy ENV=prod",
              "make deploy ENV=prod" in deploy_calls, outcomes["deploy"]["with"])
        check("WITH memory the agent never runs kubectl apply", not any(c.startswith("kubectl apply") for c in deploy_calls),
              outcomes["deploy"]["with"])
        ports = outcomes["port"]["with"].split(",")
        check("WITH memory the agent answers port 6432 (not the superseded 5544)",
              "6432" in ports and "5544" not in ports, w["port"]["answer"].strip()[:100].replace("\n", " "))
        check("WITH memory the agent runs the tests with TEST_DB_PORT=5433",
              "make test TEST_DB_PORT=5433" in w["tests"]["calls"], outcomes["tests"]["with"])
        differs = [t for t, _ in TASKS if outcomes[t]["with"] != outcomes[t]["without"]]
        check("memory mattered: WITH and WITHOUT differ on at least one task", bool(differs), ", ".join(differs) or "identical")

        # The round trip: each WITH session's turns reached the server through the plugin's hooks.
        for task, _ in TASKS:
            sid = w[task]["session_id"]
            got = None
            for _ in range(24):
                try:
                    got = call("GET", f"/v1/sessions/{sid}", key)
                except Exception:  # noqa: BLE001 - not there yet
                    got = None
                if got and got["turns"] >= 2:
                    break
                time.sleep(5)
            check(f"capture: the {task} WITH session reached the server", bool(got and got["turns"] >= 2),
                  f"{got['turns']} turns, {got['label']}, {got['status']}" if got else f"session {sid} not found")
        state = json.loads((hook_home / ".memvine" / "state.json").read_text()) if (hook_home / ".memvine" / "state.json").exists() else {}
        if state.get("lastError"):
            check("the plugin recorded no error", False, json.dumps(state["lastError"])[:160])
    finally:
        # Claude Code's own transcripts for the scratch folder: deleted, so nothing backfills them later.
        if projects_dir.exists():
            for name in set(os.listdir(projects_dir)) - before_projects:
                if "memvine-agent-e2e" in name:
                    shutil.rmtree(projects_dir / name, ignore_errors=True)
        (report_dir / "check-agent-e2e.json").write_text(json.dumps(report, indent=2))
        shutil.rmtree(base, ignore_errors=True)
    after = {p: _digest(p) for p in USER_FILES}
    changed = [str(p) for p in USER_FILES if before[p] != after[p]]
    user_state = USER_STATE.read_text() if USER_STATE.exists() else ""
    marks = [r["session_id"] for r in report["runs"].values() if r.get("session_id")] + ["memvine-agent-e2e"]
    changed += [f"{USER_STATE} names {m}" for m in marks if m in user_state]
    check("isolation: the person's ~/.memvine and ~/.claude/settings.json untouched", not changed, ", ".join(changed) or "unchanged")
