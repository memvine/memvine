import assert from "node:assert/strict";
import { test } from "node:test";
import { MARKER, parseTranscript, redact } from "./memvine-hook.mjs";

const line = (obj) => JSON.stringify(obj);

test("parseTranscript keeps prompts and replies, drops machine turns", () => {
  const raw = [
    line({ type: "user", timestamp: "2026-10-25T10:00:00Z", cwd: "/w/app", gitBranch: "main", message: { content: "Move launch to 12 Nov." } }),
    line({ type: "assistant", timestamp: "2026-10-25T10:00:05Z", message: { content: [{ type: "text", text: "Done: checklist updated." }, { type: "tool_use", name: "Edit" }] } }),
    line({ type: "user", timestamp: "2026-10-25T10:00:06Z", toolUseResult: { ok: true }, message: { content: [{ type: "tool_result", content: "ok" }] } }),
    line({ type: "user", isSidechain: true, message: { content: "subagent chatter" } }),
    line({ type: "user", message: { content: "<system-reminder>ignore me</system-reminder>" } }),
    line({ type: "user", message: { content: `${MARKER} · recalled memory ===\nold recall` } }),
    line({ type: "progress", data: 1 }),
    "not json",
    line({ type: "user", message: { content: "Also the token is ghp_abcdefghijklmnopqrstuvwxyz123456 ok?" } }),
  ].join("\n");
  const { turns, branch, cwd } = parseTranscript(raw);
  assert.equal(branch, "main");
  assert.equal(cwd, "/w/app");
  assert.deepEqual(turns.map((t) => [t.role, t.content]), [
    ["user", "Move launch to 12 Nov."],
    ["assistant", "Done: checklist updated."],
    ["user", "Also the token is [redacted] ok?"],
  ]);
  assert.equal(turns[0].at, "2026-10-25T10:00:00Z");
});

test("redact strips the usual key shapes and keeps the rest", () => {
  assert.equal(redact("key mv_sk_AbCdEfGhIjKlMnOpQrStUv here"), "key [redacted] here");
  assert.equal(redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123"), "Authorization: [redacted]");
  assert.equal(redact("DATABASE_PASSWORD=hunter2 and more"), "DATABASE_PASSWORD=[redacted] and more");
  assert.equal(redact("plain sentence with sk-less words"), "plain sentence with sk-less words");
  assert.equal(redact("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"), "[redacted]");
});

import { bindingFor } from "./memvine-hook.mjs";

test("bindingFor picks the deepest project folder containing the cwd", () => {
  const file = { apiKey: "global", projects: { "/w/app": { apiKey: "app" }, "/w/app/services/api": { apiKey: "api" } } };
  assert.equal(bindingFor(file, "/w/app/src").apiKey, "app");
  assert.equal(bindingFor(file, "/w/app/services/api/x").apiKey, "api");
  assert.equal(bindingFor(file, "/w/application").apiKey, "global", "a prefix of the name is not a parent folder");
  assert.equal(bindingFor(file, "/elsewhere").apiKey, "global");
});

test("findTranscripts finds this folder's sessions and its subfolders', not look-alikes", async () => {
  const { mkdtempSync, mkdirSync, writeFileSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const { findTranscripts, transcriptDirName } = await import("./memvine-hook.mjs");
  const root = mkdtempSync(join(tmpdir(), "mv-transcripts-"));
  const session = (dir, id, cwd, at) => {
    mkdirSync(join(root, transcriptDirName(dir)), { recursive: true });
    writeFileSync(join(root, transcriptDirName(dir), `${id}.jsonl`), [
      line({ type: "user", timestamp: at, cwd, message: { content: "Use Postgres for the queue." } }),
      line({ type: "assistant", timestamp: at, cwd, message: { content: [{ type: "text", text: "Agreed." }] } }),
    ].join("\n"));
  };
  session("/w/app", "s2", "/w/app", "2026-10-02T10:00:00Z");
  session("/w/app", "s1", "/w/app", "2026-10-01T10:00:00Z");
  session("/w/app/web", "s3", "/w/app/web", "2026-10-03T10:00:00Z");
  session("/w/app-x", "s4", "/w/app-x", "2026-10-04T10:00:00Z");   // encodes like /w/app/x
  assert.equal(transcriptDirName("/w/app.v2"), "-w-app-v2");
  const found = findTranscripts("/w/app", root);
  assert.deepEqual(found.map((t) => t.sessionId), ["s1", "s2", "s3"]);
  assert.equal(found[0].turns.length, 2);
});

test("repoId gives every clone of a repository the same name", async () => {
  const { repoId } = await import("./memvine-hook.mjs");
  assert.equal(repoId("git@github.com:Acme/App.git", "/w/app"), "github.com/acme/app");
  assert.equal(repoId("https://github.com/acme/app.git", "/w/app"), "github.com/acme/app");
  assert.equal(repoId("https://user:tok@gitlab.example.com/team/app", "/x"), "gitlab.example.com/team/app");
  assert.equal(repoId("", "/w/app"), "app");
});

test("parseTranscript lists the files the session's tools touched, not their content", () => {
  const raw = [
    line({ type: "user", cwd: "/w/app", message: { content: "Fix the retry." } }),
    line({ type: "assistant", message: { content: [{ type: "tool_use", name: "Read", input: { file_path: "/w/app/src/worker.py" } }] } }),
    line({ type: "assistant", message: { content: [{ type: "text", text: "Done." }, { type: "tool_use", name: "Edit", input: { file_path: "/w/app/src/worker.py", old_string: "secret" } }] } }),
    line({ type: "assistant", isSidechain: true, message: { content: [{ type: "tool_use", name: "Read", input: { file_path: "/w/app/other.py" } }] } }),
  ].join("\n");
  const { files, turns } = parseTranscript(raw);
  assert.deepEqual(files, ["/w/app/src/worker.py"]);
  assert.equal(turns.length, 2);
});

test("codeContext hashes touched files like git does, inside the repository only", async () => {
  const { mkdtempSync, writeFileSync, mkdirSync, realpathSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const { execFileSync } = await import("node:child_process");
  const { codeContext, hashFiles } = await import("./memvine-hook.mjs");
  const top = realpathSync(mkdtempSync(join(tmpdir(), "mv-repo-")));
  execFileSync("git", ["init", "-q"], { cwd: top });
  execFileSync("git", ["remote", "add", "origin", "git@github.com:acme/app.git"], { cwd: top });
  mkdirSync(join(top, "src"));
  writeFileSync(join(top, "src", "worker.py"), "RETRIES = 3\n");
  const expected = execFileSync("git", ["hash-object", "src/worker.py"], { cwd: top, encoding: "utf8" }).trim();
  const ctx = codeContext(join(top, "src"), [join(top, "src", "worker.py"), "/etc/hosts", join(top, "gone.py")]);
  assert.equal(ctx.repo, "github.com/acme/app");
  assert.deepEqual(ctx.files, { "src/worker.py": expected });
  assert.deepEqual(hashFiles(top, ["src/worker.py", "gone.py"]), { "src/worker.py": expected, "gone.py": null });
});

test("currentBranch names the checked-out branch, nothing outside git or on a detached HEAD", async () => {
  const { mkdtempSync, writeFileSync, realpathSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const { execFileSync } = await import("node:child_process");
  const { currentBranch } = await import("./memvine-hook.mjs");
  const top = realpathSync(mkdtempSync(join(tmpdir(), "mv-branch-")));
  const git = (...args) => execFileSync("git", args, { cwd: top, encoding: "utf8" }).trim();
  git("init", "-q", "-b", "release-billing-v4.2.0");
  writeFileSync(join(top, "a.txt"), "a\n");
  git("add", "a.txt");
  git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a");
  assert.equal(currentBranch(top), "release-billing-v4.2.0");
  git("checkout", "-q", "--detach");
  assert.equal(currentBranch(top), undefined, "detached HEAD");
  assert.equal(currentBranch(realpathSync(mkdtempSync(join(tmpdir(), "mv-nogit-")))), undefined, "not a repository");
  assert.equal(currentBranch(""), undefined);
});

test("a disconnected folder sends nothing, its siblings keep the parent's binding", async () => {
  const { bindingFor } = await import("./memvine-hook.mjs");
  const file = { projects: { "/w": { apiKey: "mv_sk_parent", identity: "a@b.c" }, "/w/lead": { disconnected: true } } };
  assert.equal(bindingFor(file, "/w/billing-v2").apiKey, "mv_sk_parent");
  assert.equal(bindingFor(file, "/w/lead").disconnected, true);
  assert.equal(bindingFor(file, "/w/lead/sub").disconnected, true);
  assert.equal(bindingFor(file, "/w/leader").apiKey, "mv_sk_parent", "a name that only starts the same is not inside it");
});

// ------------------------------------------------------------------ tool outcomes

const use = (id, name, input) => line({ type: "assistant", cwd: "/w/app", message: { content: [{ type: "tool_use", id, name, input }] } });
const result = (id, content, isError = false, at = "2026-10-25T10:01:00Z") => line({
  type: "user", cwd: "/w/app", timestamp: at, toolUseResult: isError ? `Error: ${content}` : { stdout: content },
  message: { content: [{ type: "tool_result", tool_use_id: id, content, is_error: isError }] },
});
const say = (role, text) => line({ type: role, cwd: "/w/app", timestamp: "2026-10-25T10:00:00Z",
  message: { content: role === "user" ? text : [{ type: "text", text }] } });

test("a stretch of tool work becomes one tool turn: failures and checks with their outcome, the rest named", () => {
  const raw = [
    say("user", "Why does the export test fail?"),
    use("t1", "Read", { file_path: "/w/app/src/export.py" }),
    result("t1", "def export(): ...SECRET FILE CONTENT..."),
    use("t2", "Grep", { pattern: "RETRIES" }),
    result("t2", "src/export.py:3"),
    use("t3", "Bash", { command: "cd /w/app && uv run pytest tests/test_export.py -q" }),
    result("t3", "Exit code 1\nE   psycopg.OperationalError: connection refused on port 5432\n1 failed in 0.4s", true),
    use("t4", "Bash", { command: "git status --short" }),
    result("t4", " M src/export.py"),
    say("assistant", "The test needs Postgres on 5433, not 5432; MEMVINE_TEST_DATABASE_URL points there."),
    use("t5", "Bash", { command: "PGPORT=5433 uv run pytest tests/test_export.py -q" }),
    result("t5", "....\n4 passed in 0.9s"),
    use("t6", "ToolSearch", { query: "select:Monitor" }),
    result("t6", "ok"),
    use("t7", "Bash", { command: "rm -rf build" }),
    result("t7", "Permission for this action was denied by the Claude Code auto mode classifier.", true),
    say("assistant", "Fixed: the suite passes against 5433."),
  ].join("\n");
  const { turns } = parseTranscript(raw);
  assert.deepEqual(turns.map((t) => t.role), ["user", "tool", "assistant", "tool", "assistant"]);
  const [first, second] = turns.filter((t) => t.role === "tool").map((t) => t.content);
  assert.equal(first,
    "[tools] Read ok: src/export.py; Grep ok: RETRIES; "
    + "Bash `uv run pytest tests/test_export.py -q` failed (exit 1): E psycopg.OperationalError: connection refused on port 5432 1 failed in 0.4s; "
    + "Bash ok: `git status --short`");
  assert.ok(!first.includes("SECRET FILE CONTENT"), "file contents never leave");
  assert.equal(second, "[tools] Bash `PGPORT=5433 uv run pytest tests/test_export.py -q` exit 0: .... 4 passed in 0.9s",
    "a check with an env prefix is still a check; denied calls and bookkeeping tools left out");
  assert.equal(turns[1].at, "2026-10-25T10:01:00Z");

  const without = parseTranscript(raw, { outcomes: false }).turns;
  assert.deepEqual(without.map((t) => t.role), ["user", "assistant", "assistant"], "toolOutcomes off: prompts and replies only");
});

test("outcome lines shorten paths, keep scratch paths out, redact, and stay bounded", async () => {
  const { commandGist, outcomeLine, outcomeOf, shortPaths } = await import("./memvine-hook.mjs");
  const { homedir } = await import("node:os");
  assert.equal(shortPaths("/w/app/src/a.py and /w/app", "/w/app"), "src/a.py and .");
  assert.equal(shortPaths("/w/application/x", "/w/app"), "/w/application/x", "a look-alike folder is not the working folder");
  assert.equal(shortPaths("cat /private/tmp/claude-501/x/scratchpad/a.mjs /var/folders/ab/T/y", ""), "cat <tmp> <tmp>");
  assert.equal(shortPaths(`${homedir()}/.cache/x`, ""), "~/.cache/x");
  assert.equal(commandGist('cd "/w/app" && W=1; FOO="a b" && npm test', "/w/app"), "npm test");
  assert.equal(commandGist("python3 - <<'EOF'\nprint(1)\nEOF"), "python3 - <<'EOF'", "first line only");

  const check = outcomeOf({ name: "Bash", input: { command: "npm run build 2>&1 | tail -3" } }, { content: "built in 2s" });
  assert.equal(check.status, "exit 0");
  assert.equal(check.detail, "built in 2s");
  const plain = outcomeOf({ name: "Bash", input: { command: "cat src/test/core.test.ts" } }, { content: "lots of code" });
  assert.equal(plain.status, "ok", "a path with 'test' in it is not a check");
  assert.equal(plain.detail, "");
  const leaked = outcomeOf({ name: "Bash", input: { command: "deploy" } }, { content: "Exit code 1\nGITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123 rejected", is_error: true });
  assert.ok(!outcomeLine([leaked]).includes("ghp_"), "secrets are redacted");
  const many = Array.from({ length: 40 }, (_, i) => outcomeOf({ name: "Bash", input: { command: `make target${i}` } }, { content: "x".repeat(500), is_error: true }));
  assert.ok(outcomeLine(many).length <= 1200);
  const reads = Array.from({ length: 9 }, (_, i) => ({ tool: "Read", target: `f${i}.py`, status: "ok", detail: "" }));
  assert.equal(outcomeLine(reads), "[tools] Read ok: f0.py, f1.py, f2.py, f3.py, f4.py, f5.py (9 in all)");
});

test("resumeIndex carries a session sent by the older plugin over to tool turns without resending", async () => {
  const { resumeIndex, sentMark } = await import("./memvine-hook.mjs");
  const turns = [{ role: "user" }, { role: "tool" }, { role: "assistant" }, { role: "user" }, { role: "tool" }, { role: "assistant" }];
  assert.equal(resumeIndex(turns, undefined), 0);
  assert.equal(resumeIndex(turns, { sentThrough: 2 }), 3, "old count of 2 prompts/replies: resume after the first reply");
  assert.equal(resumeIndex(turns, { sentThrough: 4 }), 6);
  assert.equal(resumeIndex(turns, { sentThrough: 2, fmt: 2, said: 1 }), 2, "current format: the index itself");
  const mark = sentMark(turns.slice(0, 3));
  assert.deepEqual(mark, { sentThrough: 3, said: 2, fmt: 2 });
  const plain = turns.filter((t) => t.role !== "tool");
  assert.equal(resumeIndex(plain, mark, false), 2, "outcomes switched off: resume after the same prompts and replies");
  assert.equal(resumeIndex(turns, sentMark(plain.slice(0, 2), false), true), 3);
});

// ------------------------------------------------------------------ what memory kept, and forgetting

test("unreportedSessions: this folder's earlier sessions not yet reported, newest first", async () => {
  const { unreportedSessions } = await import("./memvine-hook.mjs");
  const now = Date.parse("2026-10-26T12:00:00Z");
  const state = { sessions: {
    a: { cwd: "/w/app", closedAt: "2026-10-26T09:00:00Z" },
    b: { cwd: "/w/app", lastSentAt: "2026-10-26T11:00:00Z" },
    c: { cwd: "/w/other", closedAt: "2026-10-26T11:30:00Z" },
    d: { cwd: "/w/app", closedAt: "2026-10-26T11:40:00Z", keptShown: true },
    e: { cwd: "/w/app", closedAt: "2026-10-01T11:40:00Z" },
    f: { cwd: "/w/app", closedAt: "2026-10-26T11:50:00Z", backfilled: true },
    now: { cwd: "/w/app", lastSentAt: "2026-10-26T11:59:00Z" },
  } };
  assert.deepEqual(unreportedSessions(state, "/w/app", "now", now).map(([id]) => id), ["b", "a"]);
});

test("session start shows what the previous session kept once tended; forget retires one", async () => {
  const { execFile } = await import("node:child_process");
  const { mkdtempSync, mkdirSync, readFileSync, writeFileSync } = await import("node:fs");
  const { createServer } = await import("node:http");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const script = fileURLToPath(new URL("./memvine-hook.mjs", import.meta.url));

  let tending = true;
  const seen = [];
  const kept = [
    { id: "11111111-1111-1111-1111-111111111111", category: "decision", statement: "Launch is 12 Nov.", previous: "Launch is 29 Oct." },
    { id: "22222222-2222-2222-2222-222222222222", category: "gotcha", statement: "Retries cap at 3 in src/queue.py." },
  ];
  const server = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url, body: body ? JSON.parse(body) : null });
      res.writeHead(200, { "Content-Type": "application/json" });
      if (req.url === "/v1/sessions/prev/kept") {
        res.end(JSON.stringify({ session_id: "prev", tending, kept, superseded: 1, contradictions: 0,
          summary: "Memory kept 1 decision and 1 gotcha; 1 fact superseded." }));
      } else if (req.url.startsWith("/v1/recall")) {
        const asked = body ? JSON.parse(body) : {};
        res.end(JSON.stringify({ briefing: "=== memvine · recalled ===\n[decision 15 Oct, supersedes 6 Oct] Launch is 12 Nov", abstained: false,
          profiles: asked.profiles ? "=== memvine · about you ===\n- Prefers async review." : "" }));
      } else if (req.method === "PATCH") {
        res.end(JSON.stringify({ id: req.url.split("/").pop(), statement: "Retries cap at 3 in src/queue.py.", status: "retired" }));
      } else res.end("{}");
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const home = mkdtempSync(join(tmpdir(), "mv-kept-"));
    const work = join(home, "app");
    mkdirSync(work);
    mkdirSync(join(home, ".memvine"));
    writeFileSync(join(home, ".memvine", "state.json"), JSON.stringify({ sessions: {
      prev: { cwd: work, closedAt: new Date().toISOString(), sentThrough: 4 } } }));
    const env = { ...process.env, HOME: home, MEMVINE_API_URL: `http://127.0.0.1:${server.address().port}`, MEMVINE_API_KEY: "mv_sk_test123", MEMVINE_IDENTITY: "" };
    const run = (args, stdin, cwd = work) => new Promise((resolve) => {
      const child = execFile(process.execPath, [script, ...args], { env, cwd }, (e, stdout) => resolve(stdout));
      child.stdin.end(stdin === undefined ? undefined : JSON.stringify(stdin));
    });
    const start = { hook_event_name: "SessionStart", source: "startup", session_id: "next", cwd: work };

    // Still tending: the brief alone, and the next start asks again.
    let out = JSON.parse(await run(["brief"], start));
    assert.equal(out.systemMessage, undefined);
    assert.match(out.hookSpecificOutput.additionalContext, /Launch is 12 Nov/);

    tending = false;
    out = JSON.parse(await run(["brief"], start));
    assert.equal(out.systemMessage, 'memvine · last session here: Memory kept 1 decision and 1 gotcha; 1 fact superseded. To undo one, say "forget the last one" (or name it).');
    const context = out.hookSpecificOutput.additionalContext;
    // The session-start brief opens with the profiles, then what recall found.
    assert.ok(context.startsWith("=== memvine · about you ===\n- Prefers async review.\n\n=== memvine · recalled ===\n[decision 15 Oct, supersedes 6 Oct] Launch is 12 Nov\nBackground from memvine"), context);
    assert.equal(seen.find((s) => s.url === "/v1/recall").body.profiles, true);
    assert.match(context, /\n1\. \[decision\] Launch is 12 Nov \(superseded: Launch is 29 Oct\)\n2\. \[gotcha\] Retries cap at 3 in src\/queue\.py\n/);
    assert.match(context, /"forget the last one" is number 2\), run `node ".*memvine-hook\.mjs" forget <number>`/);

    // Shown once.
    out = JSON.parse(await run(["brief"], start));
    assert.equal(out.systemMessage, undefined);
    assert.equal(seen.filter((s) => s.url === "/v1/sessions/prev/kept").length, 2);

    // "Forget the last one", from a subfolder too.
    assert.match(await run(["forget"]), /1\. \[decision\] Launch is 12 Nov\.\n {2}2\. \[gotcha\] Retries cap/);
    const sub = join(work, "src");
    mkdirSync(sub);
    assert.equal(await run(["forget", "the", "last", "one"], undefined, sub), "Forgotten: Retries cap at 3 in src/queue.py.\n");
    const patch = seen.find((s) => s.method === "PATCH");
    assert.deepEqual(patch, { method: "PATCH", url: `/v1/memories/${kept[1].id}`, body: { status: "retired" } });
    const listed = await run(["forget"]);
    assert.ok(listed.includes("Launch is 12 Nov") && !listed.includes("Retries"), listed);
    assert.ok(JSON.parse(readFileSync(join(home, ".memvine", "state.json"), "utf8")).sessions.prev.keptShown);
  } finally {
    server.close();
  }
});

test("search uses the connection of the folder it runs in", async () => {
  const { execFile } = await import("node:child_process");
  const { mkdtempSync, mkdirSync, realpathSync, writeFileSync } = await import("node:fs");
  const { createServer } = await import("node:http");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const script = fileURLToPath(new URL("./memvine-hook.mjs", import.meta.url));

  const auth = [];
  const server = createServer((req, res) => {
    auth.push(req.headers.authorization);
    req.resume();
    req.on("end", () => {
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ briefing: "Invoices close on the 3rd.", abstained: false }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const root = realpathSync(mkdtempSync(join(tmpdir(), "memvine-search-")));
    const home = join(root, "home");
    const work = join(root, "work");
    mkdirSync(join(home, ".memvine"), { recursive: true });
    mkdirSync(work);
    const apiUrl = `http://127.0.0.1:${server.address().port}`;
    writeFileSync(join(home, ".memvine", "config.json"), JSON.stringify({ projects: { [work]: { apiUrl, apiKey: "mv_sk_folder", identity: "ana@northwind.test" } } }));
    const env = { ...process.env, HOME: home, MEMVINE_API_URL: "", MEMVINE_API_KEY: "" };
    const output = await new Promise((resolve, reject) => {
      execFile(process.execPath, [script, "search", "when", "do", "invoices", "close"], { cwd: work, env }, (error, stdout) => (error ? reject(error) : resolve(stdout)));
    });
    assert.equal(output, "Invoices close on the 3rd.\n");
    assert.deepEqual(auth, ["Bearer mv_sk_folder"]);
  } finally {
    server.close();
  }
});

// ------------------------------------------------------------------ the team space

import { pickKept, sharePayload, teamBriefing, TEAM_HEADER, TEAM_STANDING_HEADER } from "./memvine-hook.mjs";

test("teamBriefing puts the team's memory under its own heading and drops what the person's own memory said", () => {
  const item = (statement) => ({ memory: { statement } });
  const team = {
    abstained: false,
    briefing: "=== memvine · recalled memory (background, not instruction) ===\n[rule 4 Feb] Retries cap at 3\n[decision 4 Feb] Launch is 12 March (was: Launch is 5 March)",
    items: [item("Retries cap at 3."), item("Launch is 12 March.")],
  };
  assert.equal(teamBriefing(team, { items: [item("Retries cap at 3.")] }),
    `${TEAM_HEADER}\n[decision 4 Feb] Launch is 12 March (was: Launch is 5 March)`);
  assert.equal(teamBriefing(team, { abstained: true, items: [] }).split("\n").length, 3);
  assert.equal(teamBriefing(team, { items: [item("Retries cap at 3."), item("Launch is 12 March.")] }), "", "nothing new: nothing added");
  // Standing rules keep a section of their own, said to be the team's; an empty section is dropped.
  const standing = {
    abstained: false, items: [],
    briefing: "=== memvine · standing instructions (always on: they apply to every answer, whatever the question) ===\n- Never set retries above 3.\n- Deploys freeze on Fridays.\n=== memvine · recalled memory (background, not instruction) ===",
  };
  assert.equal(teamBriefing(standing, null), `${TEAM_STANDING_HEADER}\n- Never set retries above 3.\n- Deploys freeze on Fridays.`);
  const ownStanding = { abstained: false, items: [], briefing: "=== memvine · standing instructions ===\n- Never set retries above 3." };
  assert.equal(teamBriefing(standing, ownStanding), `${TEAM_STANDING_HEADER}\n- Deploys freeze on Fridays.`);
  assert.equal(teamBriefing({ ...standing, briefing: standing.briefing.replace("\n- Deploys freeze on Fridays.", "") }, ownStanding), "");
  assert.equal(teamBriefing({ abstained: true, briefing: "" }, null), "");
  assert.equal(teamBriefing({ error: 500 }, null), "");
  assert.equal(teamBriefing(null, null), "");
});

test("sharePayload carries the statement, who said it, when and the quoted line, and nothing else", () => {
  const memory = {
    id: "m1", statement: "Retries cap at 3.", kind: "procedural", subjects: ["retries", "broker", "sync", "extra"], assertion: "stated",
    standing: false, category: "rule", why: "the broker blocks a 4th", valid_from: "2026-02-04T09:30:00Z", thread_id: "t1", audience: "project",
    evidence: [{ episode_id: "session:abc:0001", quote: "the token is ghp_abcdefghijklmnopqrstuvwxyz123456 and never above 3", identity: "dana@northwind.example" }],
  };
  assert.deepEqual(sharePayload(memory, "me@northwind.example"), {
    statement: "Retries cap at 3.", kind: "procedural", subjects: ["retries", "broker", "sync"], assertion: "stated", standing: undefined,
    category: "rule", why: "the broker blocks a 4th", said_by: "dana@northwind.example", said_at: "2026-02-04T09:30:00Z",
    quote: "the token is [redacted] and never above 3",
  });
  const own = sharePayload({ ...memory, category: "quokka-notes", evidence: [] }, "me@northwind.example");
  assert.equal(own.category, undefined, "a category of the person's own project is not the team's");
  assert.equal(own.said_by, "me@northwind.example");
  assert.equal(own.quote, undefined);
});

test("pickKept reads numbers in any phrasing, all, last and an id", () => {
  const items = [
    { id: "a", statement: "one", suggest: true },
    { id: "b", statement: "two" },
    { id: "c", statement: "three", suggest: true, shared: true },
    { id: "d", statement: "four", suggest: true, forgotten: true },
    { id: "e", statement: "five", suggest: true },
  ];
  const ids = (arg) => pickKept(items, arg).map((k) => k.id);
  assert.deepEqual(ids("all"), ["a", "e"], "suggested, not shared, not forgotten");
  assert.deepEqual(ids("1 and 2"), ["a", "b"]);
  assert.deepEqual(ids("2,5"), ["b", "e"]);
  assert.deepEqual(ids("#3"), ["c"]);
  assert.deepEqual(ids("4"), [], "forgotten");
  assert.deepEqual(ids("the last one"), ["e"]);
  assert.deepEqual(ids("the launch one"), []);
  assert.deepEqual(ids("11111111-1111-1111-1111-111111111111"), ["11111111-1111-1111-1111-111111111111"]);
});

test("with a team space: sessions go to the person's own project only, recall reads both, share copies one memory with its author", async () => {
  const { execFile } = await import("node:child_process");
  const { mkdtempSync, mkdirSync, readFileSync, writeFileSync } = await import("node:fs");
  const { createServer } = await import("node:http");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const script = fileURLToPath(new URL("./memvine-hook.mjs", import.meta.url));

  const kept = [
    { id: "11111111-1111-1111-1111-111111111111", category: "decision", statement: "Launch is 12 March.", quote: "Launch moves to 12 March.", suggest_share: true, shared: false },
    { id: "22222222-2222-2222-2222-222222222222", category: "preference", statement: "Sam likes short answers.", quote: "keep it short", suggest_share: false, shared: false },
    { id: "33333333-3333-3333-3333-333333333333", category: "gotcha", statement: "Retries cap at 3.", quote: "the broker blocks a 4th retry", suggest_share: true, shared: false },
  ];
  const listen = (handler) => new Promise((resolve) => {
    const seen = [];
    const server = createServer((req, res) => {
      let body = "";
      req.on("data", (c) => { body += c; });
      req.on("end", () => {
        const call = { method: req.method, url: req.url, key: req.headers.authorization, body: body ? JSON.parse(body) : null };
        seen.push(call);
        const [status, payload] = handler(call);
        res.writeHead(status, { "Content-Type": "application/json" });
        res.end(JSON.stringify(payload));
      });
    });
    server.listen(0, "127.0.0.1", () => resolve({ server, seen, url: `http://127.0.0.1:${server.address().port}` }));
  });
  const team = { space: true, policy: "ask" };
  const own = await listen((call) => {
    if (call.url === "/v1/sessions/prev/kept") return [200, { session_id: "prev", tending: false, kept, superseded: 0, contradictions: 0, summary: "Memory kept 1 decision, 1 gotcha and 1 preference." }];
    if (call.url === "/v1/recall") return [200, { abstained: false, items: [{ memory: { statement: "Retries cap at 3." } }], briefing: "=== memvine · recalled memory (background, not instruction) ===\n[gotcha 4 Feb] Retries cap at 3" }];
    if (call.method === "GET" && call.url.startsWith("/v1/memories/")) {
      const k = kept.find((x) => call.url.endsWith(x.id));
      const extra = k.id.startsWith("4444") ? { supersedes: kept[0].id } : k.id.startsWith("1111") ? { shared_ref: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" } : {};
      return [200, { ...extra, id: k.id, statement: k.statement, kind: "semantic", subjects: ["launch"], assertion: "stated", category: k.category, status: "active",
        valid_from: "2026-02-04T09:30:00Z", evidence: [{ episode_id: "session:prev:0001", quote: k.quote, identity: "priya@northwind.example" }] }];
    }
    if (call.url === "/v1/project") return [200, { slug: "mine", name: "Mine", team_space: false, share_policy: "ask" }];
    return [200, {}];
  });
  const shared = await listen((call) => {
    if (call.url === "/v1/project") return [200, { slug: "team", name: "Northwind team", team_space: team.space, share_policy: team.policy }];
    if (call.url === "/v1/recall") return [200, { abstained: false, items: [{ memory: { statement: "Retries cap at 3." } }, { memory: { statement: "Deploys freeze on Fridays." } }],
      briefing: "=== memvine · recalled memory (background, not instruction) ===\n[gotcha 4 Feb] Retries cap at 3\n[rule 2 Feb] Deploys freeze on Fridays" }];
    if (call.method === "POST" && call.url === "/v1/memories") return [201, { memory: { id: `team-${call.body.statement.length}`, statement: call.body.statement }, relation: call.body.supersedes ? "update" : "new" }];
    if (call.method === "PATCH") return [200, { id: call.url.split("/").pop(), status: "retired" }];
    return [403, { error: { code: "team-space" } }];
  });
  try {
    const home = mkdtempSync(join(tmpdir(), "mv-team-"));
    const work = join(home, "app");
    mkdirSync(work);
    mkdirSync(join(home, ".memvine"));
    const statePath = join(home, ".memvine", "state.json");
    const configPath = join(home, ".memvine", "config.json");
    writeFileSync(statePath, JSON.stringify({ sessions: { prev: { cwd: work, closedAt: new Date().toISOString(), sentThrough: 4 } } }));
    writeFileSync(configPath, JSON.stringify({ apiUrl: own.url, apiKey: "mv_sk_own_key_0000", identity: "sam@northwind.example" }));
    const env = { ...process.env, HOME: home, MEMVINE_API_URL: "", MEMVINE_API_KEY: "", MEMVINE_IDENTITY: "", MEMVINE_TEAM_API_KEY: "", MEMVINE_TEAM_API_URL: "" };
    const run = (args, stdin, cwd = work) => new Promise((resolve) => {
      const child = execFile(process.execPath, [script, ...args], { env, cwd }, (e, stdout) => resolve(stdout));
      child.stdin.end(stdin === undefined ? undefined : JSON.stringify(stdin));
    });
    const teamCalls = (pattern) => shared.seen.filter((c) => pattern.test(`${c.method} ${c.url}`));

    // A key that takes sessions is not a team space: refused, nothing saved.
    team.space = false;
    assert.match(await run(["team", "mv_sk_team_key_0000", shared.url]), /takes sessions, so it is not a team space[\s\S]*Nothing saved/);
    assert.equal(JSON.parse(readFileSync(configPath, "utf8")).team, undefined);
    assert.match(await run(["team", "mv_sk_own_key_0000", own.url]), /key of your own project/);
    team.space = true;
    assert.match(await run(["team", "mv_sk_team_key_0000", shared.url]), /Connected the team space "Northwind team"[\s\S]*Nothing is shared until you say so/);
    assert.deepEqual(JSON.parse(readFileSync(configPath, "utf8")).team, { apiKey: "mv_sk_team_key_0000", apiUrl: shared.url, policy: "ask" });
    assert.match(await run(["status"]), /Team space: http:\/\/127\.0\.0\.1:\d+\. Sessions stay in your own project/);

    // Session start: the person's memory, then what the team adds; the kept line offers sharing.
    const out = JSON.parse(await run(["brief"], { hook_event_name: "SessionStart", source: "startup", session_id: "next", cwd: work }));
    assert.equal(out.systemMessage, 'memvine · last session here: Memory kept 1 decision, 1 gotcha and 1 preference. It is private to you; 2 of them could be shared with the team: ask "what can I share?" or say "share all". To undo one, say "forget the last one" (or name it).');
    const context = out.hookSpecificOutput.additionalContext;
    assert.ok(context.includes(`[gotcha 4 Feb] Retries cap at 3\n\n${TEAM_HEADER}\n[rule 2 Feb] Deploys freeze on Fridays\nBackground from memvine`), context);
    assert.equal(context.match(/Retries cap at 3\n/g).length, 1, "what both memories hold is said once");
    assert.match(context, /1\. \[decision\] Launch is 12 March \(could be shared with the team\)\n2\. \[preference\] Sam likes short answers\n3\. \[gotcha\] Retries cap at 3 \(could be shared with the team\)/);
    assert.match(context, /private to the user[\s\S]*only when the user names what to share[\s\S]*Never share on your own/);
    assert.equal(teamCalls(/POST \/v1\/memories/).length, 0, "asking is not sharing");

    // A prompt is answered from both.
    const prompted = JSON.parse(await run(["prompt"], { hook_event_name: "UserPromptSubmit", session_id: "next", cwd: work, prompt: "When do deploys freeze?" }));
    assert.ok(prompted.hookSpecificOutput.additionalContext.includes(`${TEAM_HEADER}\n[rule 2 Feb] Deploys freeze on Fridays`));
    const asked = teamCalls(/POST \/v1\/recall/).pop();
    assert.equal(asked.key, "Bearer mv_sk_team_key_0000");
    assert.equal(asked.body.session_id, undefined, "the team space is never told about a session");

    // The list, then sharing what was named.
    const listed = await run(["share"]);
    assert.match(listed, /1\. \[decision\] Launch is 12 March\. {2}\(suggested\)\n {5}goes with it, in the words said: "Launch moves to 12 March\."\n {2}2\. \[preference\] Sam likes short answers\.\n/);
    assert.equal(await run(["share", "1"]), "Shared with the team: Launch is 12 March.\n");
    const [posted] = teamCalls(/POST \/v1\/memories/);
    assert.deepEqual(posted.body, { statement: "Launch is 12 March.", kind: "semantic", subjects: ["launch"], assertion: "stated", category: "decision",
      said_by: "priya@northwind.example", said_at: "2026-02-04T09:30:00Z", quote: "Launch moves to 12 March." });
    const mark = own.seen.find((c) => c.method === "PATCH");
    assert.deepEqual([mark.url, mark.body], [`/v1/memories/${kept[0].id}`, { shared_ref: "team-19" }]);
    assert.match(await run(["share"]), /1\. \[decision\] Launch is 12 March\. {2}\(shared\)\n {2}2\./);
    assert.match(await run(["share", "1"]), /Nothing to share for "1"/, "not twice");

    // "all" shares what is suggested and still private: the gotcha, never the preference.
    assert.equal(await run(["share", "all"]), "Shared with the team: Retries cap at 3.\n");
    assert.deepEqual(teamCalls(/POST \/v1\/memories/).map((c) => c.body.statement), ["Launch is 12 March.", "Retries cap at 3."]);

    // Taking one back retires the team's copy and clears the mark on the person's own.
    assert.equal(await run(["unshare", "last"]), "Taken back from the team: Retries cap at 3.\n");
    const retired = teamCalls(/PATCH/).pop();
    assert.deepEqual([retired.url, retired.body], ["/v1/memories/team-17", { status: "retired" }]);
    assert.deepEqual(own.seen.filter((c) => c.method === "PATCH").pop().body, { shared_ref: null });

    // A memory that replaced one already shared replaces the team's copy of it, with no judge asked.
    const state = JSON.parse(readFileSync(statePath, "utf8"));
    kept.push({ id: "44444444-4444-4444-4444-444444444444", category: "decision", statement: "Launch is 19 March.", quote: "Launch slips to 19 March.", suggest_share: true, shared: false, updates_shared: true });
    state.kept[work].items.push({ id: kept[3].id, category: "decision", statement: "Launch is 19 March.", quote: "Launch slips to 19 March.", suggest: true, updates: true });
    writeFileSync(statePath, JSON.stringify(state));
    assert.match(await run(["share"]), /4\. \[decision\] Launch is 19 March\. {2}\(suggested: replaces what you shared\)/);
    assert.equal(await run(["share", "4"]), "Shared with the team: Launch is 19 March. (it replaced an older value in the team's memory)\n");
    const replacing = teamCalls(/POST \/v1\/memories/).pop();
    assert.equal(replacing.body.supersedes, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "the team's copy of the memory it replaced");
    assert.equal(replacing.body.statement, "Launch is 19 March.");
    kept.pop();  // the rest of the test is about the first three

    // A session's turns go to the person's own project and never to the team space.
    const transcript = join(home, "t.jsonl");
    writeFileSync(transcript, [
      JSON.stringify({ type: "user", timestamp: "2026-02-05T10:00:00Z", cwd: work, message: { content: "Cap the retries at 3 please." } }),
      JSON.stringify({ type: "assistant", timestamp: "2026-02-05T10:00:05Z", message: { content: [{ type: "text", text: "Done, capped at 3." }] } }),
    ].join("\n"));
    await run(["flush", "next", transcript, work, "--close"]);
    assert.equal(own.seen.filter((c) => c.url === "/v1/sessions/next/events").length, 1);
    assert.equal(own.seen.filter((c) => c.url === "/v1/sessions/next/close").length, 1);
    assert.deepEqual(teamCalls(/sessions|ingest/), [], "no session ever reaches the team space");

    // A team that shares automatically: the suggested ones go without being asked, the preference never.
    team.policy = "auto";
    writeFileSync(statePath, JSON.stringify({ sessions: { prev: { cwd: work, closedAt: new Date().toISOString(), sentThrough: 4 } } }));
    const before = teamCalls(/POST \/v1\/memories/).length;
    const auto = JSON.parse(await run(["brief"], { hook_event_name: "SessionStart", source: "startup", session_id: "third", cwd: work }));
    assert.match(auto.systemMessage, /2 of them are being shared with the team \(say "unshare" to take one back\)/);
    for (let i = 0; i < 100 && teamCalls(/POST \/v1\/memories/).length < before + 2; i += 1) await new Promise((r) => setTimeout(r, 50));
    assert.deepEqual(teamCalls(/POST \/v1\/memories/).slice(before).map((c) => c.body.statement), ["Launch is 12 March.", "Retries cap at 3."]);

    // Disconnecting the team space: recall is the person's own again.
    assert.match(await run(["team", "--off"]), /No team space on this machine any more/);
    const alone = JSON.parse(await run(["prompt"], { hook_event_name: "UserPromptSubmit", session_id: "next", cwd: work, prompt: "When do deploys freeze?" }));
    assert.ok(!alone.hookSpecificOutput.additionalContext.includes("team memory"));
  } finally {
    own.server.close();
    shared.server.close();
  }
});

// ------------------------------------------------------------------ sending a long backlog

test("a backlog longer than one request is sent in several, and a failed one does not resend what arrived", async () => {
  const { execFile } = await import("node:child_process");
  const { mkdtempSync, mkdirSync, readFileSync, realpathSync, writeFileSync } = await import("node:fs");
  const { createServer } = await import("node:http");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const script = fileURLToPath(new URL("./memvine-hook.mjs", import.meta.url));

  // The server's own limits: at most 500 turns a request, 100,000 characters a turn.
  const requests = [];
  let failNext = 0;
  const server = createServer((req, res) => {
    let raw = "";
    req.on("data", (chunk) => { raw += chunk; });
    req.on("end", () => {
      const events = JSON.parse(raw).events;
      const refused = events.length > 500 || events.some((e) => e.content.length > 100_000);
      const down = !refused && failNext > 0 && (failNext -= 1) === 0;
      if (!refused && !down) requests.push(events);
      res.statusCode = refused ? 422 : down ? 503 : 202;
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify(refused ? { detail: "too long" } : {}));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const root = realpathSync(mkdtempSync(join(tmpdir(), "memvine-backlog-")));
    const home = join(root, "home");
    const work = join(root, "work");
    mkdirSync(join(home, ".memvine"), { recursive: true });
    mkdirSync(work);
    const apiUrl = `http://127.0.0.1:${server.address().port}`;
    writeFileSync(join(home, ".memvine", "config.json"), JSON.stringify({ projects: { [work]: { apiUrl, apiKey: "mv_sk_folder", identity: "ana@northwind.test" } } }));
    const env = { ...process.env, HOME: home, MEMVINE_API_URL: "", MEMVINE_API_KEY: "" };
    const flush = (session, transcript) => new Promise((resolve, reject) => {
      execFile(process.execPath, [script, "flush", session, transcript, work], { cwd: work, env }, (error) => (error ? reject(error) : resolve()));
    });
    const sent = (session) => JSON.parse(readFileSync(join(home, ".memvine", "state.json"), "utf8")).sessions[session].sentThrough;
    const said = (n) => `Invoice ${n} was checked against the Northwind ledger.`;
    const transcript = (name, pairs, first = (n) => said(n)) => {
      const path = join(root, name);
      const rows = [];
      for (let n = 1; n <= pairs; n += 1) {
        rows.push(line({ type: "user", timestamp: "2026-02-05T10:00:00Z", cwd: work, message: { content: n === 1 ? first(n) : said(n) } }));
        rows.push(line({ type: "assistant", timestamp: "2026-02-05T10:00:05Z", message: { content: [{ type: "text", text: `Checked ${n}.` }] } }));
      }
      writeFileSync(path, rows.join("\n"));
      return path;
    };

    // 900 turns, the first of them longer than the server takes: three requests, every turn once.
    await flush("long", transcript("long.jsonl", 450, () => "x".repeat(150_000)));
    assert.deepEqual(requests.map((r) => r.length), [400, 400, 100]);
    assert.equal(requests[0][0].content.length, 100_000, "the long turn is cut to what the server takes");
    assert.equal(requests[2][99].content, "Checked 450.");
    assert.equal(sent("long"), 900);

    // The second request fails: the first one's turns are recorded as sent, and the next send starts after them.
    requests.length = 0;
    failNext = 2;
    const path = transcript("flaky.jsonl", 300);
    await flush("flaky", path);
    assert.deepEqual([requests.map((r) => r.length), sent("flaky")], [[400], 400]);
    await flush("flaky", path);
    assert.deepEqual([requests.map((r) => r.length), sent("flaky")], [[400, 200], 600]);
    assert.equal(requests[1][0].content, said(201), "nothing is sent twice");
  } finally {
    server.close();
  }
});
