"""The team example behind the screens on memvine.dev's homepage: your memory, and the team's.

Real Claude Code sessions (`claude -p`), the real plugin and a real model, on a local Memvine server
and console. Two people are scripted, each with a project only they can open, beside one team
space: you teach your agent a deploy rule and a preference of your own; a teammate teaches theirs
why a field that looks like a duplicate has to stay; each shares one memory with the team.

  python3 benchmark/how-it-works/team.py all       # setup, you, teammate, share, shots
  python3 benchmark/how-it-works/team.py <step>    # one step; `memories` lists what each project holds

Needs: a local server started with MEMVINE_SIGNUP=open and a real model, the console running
against it WITHOUT an operator key (people sign in, so a personal project can be opened), Claude
Code and Google Chrome. MEMVINE_DEMO_API (http://127.0.0.1:8421) and MEMVINE_DEMO_CONSOLE
(http://127.0.0.1:3121) when yours differ. Everything is written under
benchmark/how-it-works/out/team/ (ignored by git); `publish` copies the screenshots to site/.
The sign-in passwords are made up at setup and kept only in that folder's state file.
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("MEMVINE_DEMO_API", "http://127.0.0.1:8421")
os.environ.setdefault("MEMVINE_DEMO_CONSOLE", "http://127.0.0.1:3121")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as base  # noqa: E402  (the one-lesson example: its sample repository and helpers)

OUT = base.OUT / "team"
STATE = OUT / "state.json"
SHOTS = OUT / "shots"
call, sh, git, clean_env = base.call, base.sh, base.git, base.clean_env

# The sample repository, with a deploy target: what `make deploy` does is in the code; that it is
# the only way to deploy, and why, is not.
FILES = base.FILES | {
    "Makefile": """.PHONY: test deploy

test:
\t@python3 -m unittest -q

deploy: test
\t@./scripts/migrate.sh
\t@kubectl rollout restart deployment/billing-api
""",
    "scripts/migrate.sh": """#!/bin/sh
# Applies pending database migrations before the new version starts.
set -e
echo "applying migrations"
""",
}

YOU = [
    "I'm about to ship the invoice fix. Read the Makefile and tell me how this service gets deployed. Don't run anything.",
    ("From now on, always deploy with `make deploy`, never with kubectl directly. make deploy runs the database migrations "
     "first. Last month someone restarted the deployment with kubectl, the API went out without its migration, and invoicing "
     "was down for an hour."),
    "And one thing about how I work: keep pull requests small, one change each. I read every one before it merges.",
]
TEAMMATE = [base.YOU_1, base.YOU_2]


def load():
    if not STATE.exists():
        sys.exit("No run yet: start with the setup step (or all).")
    return json.loads(STATE.read_text())


def setup():
    """An organization of two, a project of their own for each, one team space, a sample repository."""
    if STATE.exists():
        sys.exit(f"A run exists in {OUT}. Start the server on an empty database and delete that folder to run again.")
    OUT.mkdir(parents=True, exist_ok=True)
    people = {}
    you = call("POST", "/v1/auth/signup", {"email": "you@example.com", "password": "home-" + secrets.token_urlsafe(12),
                                           "name": "You", "organization": "Northwind"}, token="")
    people["you"] = {"token": you["token"], "email": "you@example.com"}
    invite = call("POST", "/v1/org/invites", {"role": "member"}, token=you["token"])
    mate = call("POST", "/v1/auth/signup", {"email": "teammate@example.com", "password": "home-" + secrets.token_urlsafe(12),
                                            "name": "A teammate", "invite": invite["token"]}, token="")
    people["teammate"] = {"token": mate["token"], "email": "teammate@example.com"}
    team = call("POST", "/v1/projects", {"name": "Northwind team", "team_space": True,
                                         "description": "What the billing team's agents all know"}, token=you["token"])
    team_key = call("POST", f"/v1/projects/{team['slug']}/keys", {"name": "team"}, token=you["token"])["key"]

    bare = OUT / "billing-api.git"
    sh(["git", "init", "-q", "--bare", "-b", "main", str(bare)])
    seed = OUT / "seed"
    sh(["git", "clone", "-q", str(bare), str(seed)])
    for path, text in FILES.items():
        (seed / path).parent.mkdir(parents=True, exist_ok=True)
        (seed / path).write_text(text)
    (seed / "scripts" / "migrate.sh").chmod(0o755)
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "billing-api: the invoice response, and make deploy")
    git(seed, "push", "-q", "origin", "HEAD:main")

    for who, name in (("you", "billing-api"), ("teammate", "billing-api")):
        p = people[who]
        project = call("POST", "/v1/projects", {"name": name, "personal": True, "description": "Northwind's billing API"}, token=p["token"])
        key = call("POST", f"/v1/projects/{project['slug']}/keys", {"name": "laptop"}, token=p["token"])["key"]
        home = OUT / who / "home"
        home.mkdir(parents=True)
        plugin = OUT / who / "plugin"
        shutil.copytree(base.PLUGIN, plugin)
        hooks_file = plugin / "hooks" / "hooks.json"
        hooks = json.loads(hooks_file.read_text())
        for groups in hooks["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    hook["command"] = f'HOME="{home}" {hook["command"]}'
        hooks_file.write_text(json.dumps(hooks, indent=2))
        repo = OUT / who / "billing-api"
        sh(["git", "clone", "-q", str(bare), str(repo)])
        script = str(plugin / "bin" / "memvine-hook.mjs")
        print(who, "|", sh(["node", script, "connect", key, p["email"], base.API], cwd=repo, env=clean_env(str(home))).splitlines()[0].split(". The key")[0])
        print(who, "|", sh(["node", script, "team", team_key, base.API], cwd=repo, env=clean_env(str(home))).splitlines()[0][:160])
        p |= {"slug": project["slug"], "key": key, "home": str(home), "plugin": str(plugin), "repo": str(repo)}
    STATE.write_text(json.dumps({"team": {"slug": team["slug"], "key": team_key}, "people": people}, indent=1))
    STATE.chmod(0o600)
    print("team space:", team["slug"], "| projects:", people["you"]["slug"], people["teammate"]["slug"])


def agent(name, who, prompt, resume=None, max_turns=25):
    """One real Claude Code session in that person's copy of the repository, with their plugin."""
    d = load()["people"][who]
    cmd = ["claude", "-p", prompt, "--model", base.MODEL, "--max-turns", str(max_turns), "--setting-sources", "project",
           "--strict-mcp-config", "--output-format", "stream-json", "--verbose", "--allowedTools", *base.ALLOWED, "--plugin-dir", d["plugin"]]
    if resume:
        cmd += ["--resume", resume]
    started = time.time()
    out = subprocess.run(cmd, cwd=d["repo"], env=clean_env(), capture_output=True, text=True, timeout=900)
    (OUT / f"log-{name}.jsonl").write_text(out.stdout)
    session, answer = "", ""
    for line in out.stdout.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "system" and e.get("subtype") == "init":
            session = e.get("session_id", "")
        elif e.get("type") == "result":
            answer = e.get("result") or ""
    (OUT / f"run-{name}.json").write_text(json.dumps({"session_id": session, "answer": answer}, indent=1))
    print(f"[{name}] {time.time() - started:.0f}s, exit {out.returncode}")
    if out.returncode and not answer:
        print("   stderr:", out.stderr[:400])
    print("   " + answer.replace("\n", "\n   "))
    return session


def talk(who, prompts):
    session = None
    for i, prompt in enumerate(prompts, 1):
        session = agent(f"{who}-{i}", who, prompt, resume=session) or session
    key = load()["people"][who]["key"]
    started, kept = time.time(), {}
    while time.time() - started < 420:
        kept = call("GET", f"/v1/sessions/{session}/kept", token=key)
        if not kept.get("tending", True):
            break
        time.sleep(5)
    print(f"memory made in {time.time() - started:.0f}s: {kept.get('summary')}")
    memories(who)


def items(token, slug):
    got = call("GET", "/v1/memories", token=token, project=slug)
    return got.get("items", got) if isinstance(got, dict) else got


def memories(who=None):
    s = load()
    for name in ([who] if who else [*s["people"], "team"]):
        p = s["people"].get(name)
        rows = items(p["token"], p["slug"]) if p else items(s["people"]["you"]["token"], s["team"]["slug"])
        print(f"{name}:")
        for m in rows:
            print(f"   {m['id']} [{m['status']}] [{m.get('category')}] {m['statement']}")


def hook(who, *args):
    d = load()["people"][who]
    out = subprocess.run(["node", str(Path(d["plugin"]) / "bin" / "memvine-hook.mjs"), *args], cwd=d["repo"],
                         env=clean_env(d["home"]), capture_output=True, text=True, timeout=180)
    return (out.stdout + out.stderr).strip()


def share():
    """Each person shares one memory, the way /memvine:share does. Your preference stays yours."""
    s = load()
    for who in ("you", "teammate"):
        print(f"{who}: {hook(who, 'share')}")
        p = s["people"][who]
        mine = [m for m in items(p["token"], p["slug"]) if m["status"] == "active" and m.get("category") != "preference"]
        if not mine:
            sys.exit(f"{who} has nothing to share")
        print(f"{who}: {hook(who, 'share', mine[0]['id'])}")
    memories("team")


def shot(name, who, slug, path, *options):
    SHOTS.mkdir(exist_ok=True)
    token = load()["people"][who]["token"]
    print(sh(["node", str(base.HERE / "shot.mjs"), slug, str(SHOTS / f"{name}.png"), path, *options],
             env=os.environ | {"MEMVINE_DEMO_CONSOLE": base.CONSOLE, "MEMVINE_DEMO_TOKEN": token}))


def shots():
    """The four screens on the homepage, each at the console's narrow layout and at phone width."""
    s = load()
    mine, team = s["people"]["you"]["slug"], s["team"]["slug"]
    rule = next(m for m in items(s["people"]["you"]["token"], mine) if m.get("category") == "rule")
    for suffix, size in (("", ("--width", "760", "--height", "580")), ("-phone", ("--width", "430", "--height", "600"))):
        shot(f"home-made{suffix}", "you", mine, f"/memories/{rule['id']}", *size)
        shot(f"home-decide{suffix}", "you", mine, "/", *size, "--top", "New memories no one has looked at yet", "--tap", "reads every pull request")
        shot(f"home-yours{suffix}", "you", mine, "/projects", *size)
        shot(f"home-team{suffix}", "you", team, "/memories", *size)


def main(step):
    if step == "all":
        for s in ("setup", "you", "teammate", "share", "shots"):
            main(s)
    elif step == "setup":
        setup()
    elif step == "you":
        talk("you", YOU)
    elif step == "teammate":
        talk("teammate", TEAMMATE)
    elif step == "share":
        share()
    elif step == "shots":
        shots()
    elif step == "publish":
        for png in sorted(SHOTS.glob("home-*.png")):
            shutil.copyfile(png, base.ROOT / "site" / png.name)
            print("site/" + png.name)
    elif step == "memories":
        memories()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
