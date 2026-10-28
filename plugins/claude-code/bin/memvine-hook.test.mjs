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
        res.end(JSON.stringify({ briefing: "=== memvine · recalled ===\n[decision 15 Oct, supersedes 6 Oct] Launch is 12 Nov", abstained: false }));
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
    assert.ok(context.startsWith("=== memvine · recalled ===\n[decision 15 Oct, supersedes 6 Oct] Launch is 12 Nov\nBackground from memvine"), context);
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
