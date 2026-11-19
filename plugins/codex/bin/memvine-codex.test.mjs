import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtempSync, readFileSync, statSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  BLOCK_START, conflictingMemvine, mergeHooks, parseRollout, patchedFiles, removeHooks, removeMcpBlock, upsertMcpBlock,
} from "./memvine-codex.mjs";

const SCRIPT = fileURLToPath(new URL("./memvine-codex.mjs", import.meta.url));
const line = (obj) => JSON.stringify(obj);
const conn = { apiUrl: "https://api.example.dev/", apiKey: "mv_sk_test123" };

const USER_TOML = `model = "gpt-6"
approval_policy = "on-request"

[mcp_servers.mermaid]
command = "npx"
args = ["-y", "mermaid-mcp"]

[mcp_servers.mermaid.env_http_headers]
X = "Y"

[projects."/w/app"]
trust_level = "trusted"
`;

// ------------------------------------------------------------------ config.toml

test("upsertMcpBlock appends one managed table and leaves the rest byte for byte", () => {
  const { text } = upsertMcpBlock(USER_TOML, conn);
  assert.ok(text.startsWith(USER_TOML));
  assert.match(text, /\[mcp_servers\.memvine\]\nurl = "https:\/\/api\.example\.dev\/mcp"\nhttp_headers = \{ Authorization = "Bearer mv_sk_test123" \}/);
  assert.equal(text.split("[mcp_servers.memvine]").length, 2);
});

test("upsertMcpBlock is idempotent and updates the key in place", () => {
  const once = upsertMcpBlock(USER_TOML, conn).text;
  assert.equal(upsertMcpBlock(once, conn).text, once);
  const rotated = upsertMcpBlock(once, { ...conn, apiKey: "mv_sk_new456" }).text;
  assert.equal(rotated.split(BLOCK_START).length, 2);
  assert.ok(rotated.includes("mv_sk_new456") && !rotated.includes("mv_sk_test123"));
});

test("removeMcpBlock restores the original file", () => {
  const { text, removed } = removeMcpBlock(upsertMcpBlock(USER_TOML, conn).text);
  assert.equal(removed, true);
  assert.equal(text, USER_TOML);
  assert.deepEqual(removeMcpBlock(USER_TOML), { text: USER_TOML, removed: false, unmanaged: false });
});

test("a hand-written [mcp_servers.memvine] table is replaced, not duplicated", () => {
  const hand = `${USER_TOML}\n[mcp_servers.memvine]\nurl = "https://old/mcp"\n\n[mcp_servers.memvine.http_headers]\nAuthorization = "Bearer mv_sk_old"\n\n[tui]\ntheme = "dark"\n`;
  const { text, replacedUnmanaged } = upsertMcpBlock(hand, conn);
  assert.equal(replacedUnmanaged, true);
  assert.ok(!text.includes("mv_sk_old") && !text.includes("https://old/mcp"));
  assert.ok(text.includes('[tui]\ntheme = "dark"'));
  assert.equal((text.match(/^\[mcp_servers\.memvine/gm) || []).length, 1);
});

test("forms the editor cannot place safely are refused", () => {
  assert.equal(conflictingMemvine('[mcp_servers]\nmemvine = { url = "x" }\n'), true);
  assert.equal(conflictingMemvine('mcp_servers.memvine.url = "x"\n'), true);
  assert.equal(conflictingMemvine(USER_TOML), false);
  assert.throws(() => upsertMcpBlock('mcp_servers.memvine.url = "x"\n', conn), /inline table or a dotted key/);
});

test("an empty or missing config.toml gets just the block", () => {
  const { text } = upsertMcpBlock("", conn);
  assert.ok(text.startsWith(BLOCK_START));
  assert.equal(removeMcpBlock(text).text, "");
});

// ------------------------------------------------------------------ hooks.json

const OTHER = { hooks: { Stop: [{ hooks: [{ type: "command", command: "say done" }] }], PreToolUse: [{ matcher: "Bash", hooks: [{ type: "command", command: "audit.sh" }] }] }, note: "kept" };

test("mergeHooks adds the five hooks once, after the person's own", () => {
  const once = mergeHooks(OTHER, "/h/.memvine/codex/bin/memvine-codex.mjs");
  assert.deepEqual(Object.keys(once.hooks).sort(), ["PreCompact", "PreToolUse", "SessionEnd", "SessionStart", "Stop", "UserPromptSubmit"]);
  assert.equal(once.hooks.Stop[0].hooks[0].command, "say done");
  assert.equal(once.hooks.Stop[1].hooks[0].command, 'node "/h/.memvine/codex/bin/memvine-codex.mjs" stop');
  assert.equal(once.note, "kept");
  assert.deepEqual(mergeHooks(once, "/h/.memvine/codex/bin/memvine-codex.mjs"), once);
});

test("removeHooks takes out only Memvine's hooks", () => {
  const { doc, removed } = removeHooks(mergeHooks(OTHER, "/x/memvine-codex.mjs"));
  assert.equal(removed, 5);
  assert.deepEqual(doc, OTHER);
});

// ------------------------------------------------------------------ the rollout

const ROLLOUT = [
  line({ type: "session_meta", payload: { id: "s1", cwd: "/w/app", source: "cli", thread_source: "user", git: { branch: "main" } } }),
  line({ type: "response_item", timestamp: "2026-10-26T10:00:00Z", payload: { type: "message", role: "developer", content: [{ type: "input_text", text: "<permissions instructions>..." }] } }),
  line({ type: "response_item", timestamp: "2026-10-26T10:00:01Z", payload: { type: "message", role: "user", content: [{ type: "input_text", text: "<environment_context><cwd>/w/app</cwd></environment_context>" }] } }),
  line({ type: "response_item", timestamp: "2026-10-26T10:00:02Z", payload: { type: "message", role: "user", content: [{ type: "input_text", text: "# AGENTS.md instructions for /w/app\n..." }, { type: "input_text", text: "Move launch to 12 Nov; key is ghp_abcdefghijklmnopqrstuvwxyz123456" }] } }),
  line({ type: "response_item", payload: { type: "reasoning", summary: [], encrypted_content: "x" } }),
  line({ type: "response_item", timestamp: "2026-10-26T10:00:03Z", payload: { type: "message", role: "assistant", phase: "commentary", content: [{ type: "output_text", text: "I'll look at the checklist." }] } }),
  line({ type: "response_item", payload: { type: "custom_tool_call", name: "apply_patch", input: "*** Begin Patch\n*** Update File: docs/launch.md\n@@\n-x\n+y\n*** Add File: src/new.ts\n+a\n*** End Patch" } }),
  line({ type: "response_item", payload: { type: "function_call_output", output: "Success" } }),
  line({ type: "response_item", timestamp: "2026-10-26T10:00:09Z", payload: { type: "message", role: "assistant", phase: "final_answer", content: [{ type: "output_text", text: "Done: launch moved to 12 Nov." }] } }),
  line({ type: "event_msg", payload: { type: "task_complete" } }),
  "not json",
].join("\n");

test("parseRollout keeps what the person typed and the final answers", () => {
  const { turns, branch, cwd, files } = parseRollout(ROLLOUT);
  assert.equal(branch, "main");
  assert.equal(cwd, "/w/app");
  assert.deepEqual(turns.map((t) => [t.role, t.content]), [
    ["user", "Move launch to 12 Nov; key is [redacted]"],
    ["assistant", "Done: launch moved to 12 Nov."],
  ]);
  assert.equal(turns[0].at, "2026-10-26T10:00:02Z");
  assert.deepEqual(files, ["docs/launch.md", "src/new.ts"]);
});

test("a mentioned file's content stays; the request after it is kept", () => {
  const raw = [
    line({ type: "response_item", payload: { type: "message", role: "user", content: [{ type: "input_text", text: "<recommended_plugins>\nx\n</recommended_plugins>" }, { type: "input_text", text: "# Files mentioned by the user:\n\n## notes.md: /w/notes.md\nSECRET PLAN CONTENT\n\n## My request:\nSummarize the notes." }] } }),
    line({ type: "response_item", payload: { type: "message", role: "user", content: [{ type: "input_text", text: "<image name=[Image #1] path=\"/tmp/a.png\">" }, { type: "input_image", image_url: "data:..." }, { type: "input_text", text: "</image>" }, { type: "input_text", text: "What is in this screenshot?" }] } }),
  ].join("\n");
  assert.deepEqual(parseRollout(raw).turns.map((t) => t.content), ["Summarize the notes.", "What is in this screenshot?"]);
});

test("a subagent's rollout yields nothing", () => {
  const sub = [line({ type: "session_meta", payload: { cwd: "/w", source: { subagent: { other: "guardian" } }, thread_source: "guardian_review" } }), ROLLOUT.split("\n").slice(1).join("\n")].join("\n");
  assert.deepEqual(parseRollout(sub).turns, []);
});

test("patchedFiles reads adds, updates, deletes and moves", () => {
  assert.deepEqual(patchedFiles("*** Update File: a.py\n*** Move to: b.py\n*** Delete File: c.py\n"), ["a.py", "b.py", "c.py"]);
});

// ------------------------------------------------------------------ the installer, end to end

function run(args, env, input) {
  return new Promise((resolve) => {
    const child = execFile(process.execPath, [SCRIPT, ...args], { env: { ...process.env, ...env, MEMVINE_API_URL: "", MEMVINE_API_KEY: "", MEMVINE_IDENTITY: "" } }, (error, stdout, stderr) =>
      resolve({ code: error ? error.code : 0, stdout, stderr }));
    if (input !== undefined) child.stdin.end(input);
  });
}

function fakeServer() {
  const seen = [];
  const server = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url, auth: req.headers.authorization, ua: req.headers["user-agent"], body: body ? JSON.parse(body) : null });
      if (req.headers.authorization !== "Bearer mv_sk_good123") { res.writeHead(401); res.end("{}"); return; }
      res.writeHead(200, { "Content-Type": "application/json" });
      if (req.url.endsWith("/kept")) {
        res.end(JSON.stringify({ tending: false, kept: [{ id: "11111111-1111-1111-1111-111111111111", category: "decision", statement: "Launch is 12 Nov." }],
          superseded: 0, contradictions: 0, summary: "Memory kept 1 decision." }));
        return;
      }
      res.end(JSON.stringify(req.url.startsWith("/v1/recall") ? { briefing: "=== memvine · recalled ===\nLaunch is 12 Nov.", abstained: false } : { memories: [] }));
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, seen, url: `http://127.0.0.1:${server.address().port}` })));
}

test("install against a temp HOME: verifies the key, edits both files, is idempotent; uninstall undoes it", async () => {
  const { server, seen, url } = await fakeServer();
  try {
    const home = mkdtempSync(join(tmpdir(), "mv-codex-"));
    const codexHome = join(home, ".codex");
    mkdirSync(codexHome);
    writeFileSync(join(codexHome, "config.toml"), USER_TOML, { mode: 0o644 });
    writeFileSync(join(codexHome, "hooks.json"), JSON.stringify(OTHER, null, 2) + "\n");
    const env = { HOME: home, CODEX_HOME: codexHome };

    const bad = await run(["install", "mv_sk_wrong123", "me@x.dev", url], env);
    assert.equal(bad.code, 1);
    assert.match(bad.stderr, /Could not reach .* \(401\)\. Nothing changed\./);
    assert.equal(readFileSync(join(codexHome, "config.toml"), "utf8"), USER_TOML);

    const first = await run(["install", "mv_sk_good123", "me@x.dev", url], env);
    assert.equal(first.code, 0, first.stderr);
    assert.match(first.stdout, /\/hooks/);
    const toml1 = readFileSync(join(codexHome, "config.toml"), "utf8");
    const hooks1 = readFileSync(join(codexHome, "hooks.json"), "utf8");
    assert.ok(toml1.startsWith(USER_TOML));
    assert.ok(toml1.includes(`url = "${url}/mcp"`));
    assert.equal(statSync(join(codexHome, "config.toml")).mode & 0o777, 0o600);
    assert.equal(statSync(join(home, ".memvine", "config.json")).mode & 0o777, 0o600);
    assert.ok(existsSync(join(home, ".memvine", "codex", "bin", "memvine-codex.mjs")));
    assert.ok(existsSync(join(home, ".memvine", "codex", "lib", "memvine-hook.mjs")));
    assert.ok(hooks1.includes(join(home, ".memvine", "codex", "bin", "memvine-codex.mjs")));

    // Again, with the key now taken from ~/.memvine/config.json: nothing changes.
    const second = await run(["install"], env);
    assert.equal(second.code, 0, second.stderr);
    assert.equal(readFileSync(join(codexHome, "config.toml"), "utf8"), toml1);
    assert.equal(readFileSync(join(codexHome, "hooks.json"), "utf8"), hooks1);

    // The installed copy runs as a hook: SessionStart recall comes back as additionalContext, and
    // what the previous session in this folder kept as a line for the person.
    writeFileSync(join(home, ".memvine", "state.json"), JSON.stringify({ sessions: { s0: { cwd: home, closedAt: new Date().toISOString() } } }));
    const installed = join(home, ".memvine", "codex", "bin", "memvine-codex.mjs");
    const brief = await new Promise((resolve) => {
      const child = execFile(process.execPath, [installed, "brief"], { env: { ...process.env, ...env, MEMVINE_API_URL: "", MEMVINE_API_KEY: "" } }, (e, stdout) => resolve(stdout));
      child.stdin.end(JSON.stringify({ hook_event_name: "SessionStart", source: "startup", session_id: "s1", cwd: home }));
    });
    assert.equal(JSON.parse(brief).hookSpecificOutput.hookEventName, "SessionStart");
    assert.match(JSON.parse(brief).hookSpecificOutput.additionalContext, /Launch is 12 Nov\./);
    assert.match(JSON.parse(brief).systemMessage, /^memvine · last session here: Memory kept 1 decision\. To undo one, say "forget the last one"/);
    assert.match(JSON.parse(brief).hookSpecificOutput.additionalContext, /\n1\. \[decision\] Launch is 12 Nov\n.*run `node ".*memvine-codex\.mjs" forget <number>`/);

    // A flush sends the rollout's turns as agent "codex", once.
    const rollout = join(home, "rollout.jsonl");
    writeFileSync(rollout, ROLLOUT);
    for (let i = 0; i < 2; i++) {
      const r = await new Promise((resolve) => execFile(process.execPath, [installed, "flush", "s1", rollout, home], { env: { ...process.env, ...env, MEMVINE_API_URL: "", MEMVINE_API_KEY: "" } }, (e, stdout, stderr) => resolve({ e, stderr })));
      assert.equal(r.e, null, r.stderr);
    }
    const sends = seen.filter((s) => s.url === "/v1/sessions/s1/events");
    assert.equal(sends.length, 1);
    assert.equal(sends[0].body.agent, "codex");
    assert.match(sends[0].ua, /^memvine-codex\//);
    assert.match(sends[0].body.label, /^Codex · /);
    assert.deepEqual(sends[0].body.events.map((e) => e.role), ["user", "assistant"]);

    const status = await run(["status"], env);
    assert.match(status.stdout, /MCP server in .*: yes/);
    assert.match(status.stdout, /Hooks in .*: 5\/5/);

    const out = await run(["uninstall"], env);
    assert.equal(out.code, 0, out.stderr);
    assert.equal(readFileSync(join(codexHome, "config.toml"), "utf8"), USER_TOML);
    assert.deepEqual(JSON.parse(readFileSync(join(codexHome, "hooks.json"), "utf8")), OTHER);
    assert.ok(!existsSync(join(home, ".memvine", "codex")));
    assert.ok(existsSync(join(home, ".memvine", "config.json")));
  } finally {
    server.close();
  }
});

test("install into a fresh Codex home creates both files; uninstall removes hooks.json it made", async () => {
  const home = mkdtempSync(join(tmpdir(), "mv-codex-"));
  const env = { HOME: home, CODEX_HOME: join(home, ".codex") };
  const r = await run(["install", "mv_sk_good123", "me@x.dev", "http://127.0.0.1:9", "--no-verify"], env);
  assert.equal(r.code, 0, r.stderr);
  assert.ok(readFileSync(join(home, ".codex", "config.toml"), "utf8").startsWith(BLOCK_START));
  await run(["uninstall"], env);
  assert.equal(readFileSync(join(home, ".codex", "config.toml"), "utf8"), "");
  assert.ok(!existsSync(join(home, ".codex", "hooks.json")));
});

test("an unparseable hooks.json stops the install before anything is written", async () => {
  const home = mkdtempSync(join(tmpdir(), "mv-codex-"));
  mkdirSync(join(home, ".codex"));
  writeFileSync(join(home, ".codex", "config.toml"), USER_TOML);
  writeFileSync(join(home, ".codex", "hooks.json"), "{ nope");
  const r = await run(["install", "mv_sk_good123", "me@x.dev", "http://127.0.0.1:9", "--no-verify"], { HOME: home, CODEX_HOME: join(home, ".codex") });
  assert.equal(r.code, 1);
  assert.match(r.stderr, /not valid JSON/);
  assert.equal(readFileSync(join(home, ".codex", "config.toml"), "utf8"), USER_TOML);
});

test("share and team say what to do when no team space is connected", async () => {
  const home = mkdtempSync(join(tmpdir(), "mv-codex-team-"));
  mkdirSync(join(home, ".memvine"));
  writeFileSync(join(home, ".memvine", "config.json"), JSON.stringify({ apiUrl: "http://127.0.0.1:9", apiKey: "mv_sk_own_key_0000", identity: "sam@northwind.example" }));
  const script = fileURLToPath(new URL("./memvine-codex.mjs", import.meta.url));
  const env = { ...process.env, HOME: home, MEMVINE_API_URL: "", MEMVINE_API_KEY: "", MEMVINE_TEAM_API_KEY: "", MEMVINE_TEAM_API_URL: "" };
  const run = (...args) => new Promise((resolve) => execFile(process.execPath, [script, ...args], { env, cwd: home }, (e, stdout) => resolve(stdout)));
  assert.match(await run("share"), /^No team space is connected\. Connect one first \(node ".*memvine-codex\.mjs" team <team-space-key>\)\.\n$/);
  assert.match(await run("team"), /^No team space connected\. Usage: node ".*memvine-codex\.mjs" team <team-space-key starting with mv_sk_>/);
  assert.match(await run("status"), /No team space connected: what memory keeps stays in your own project/);
});

// Codex installs Memvine from this repository's own marketplace (`codex plugin marketplace add
// memvine/memvine`, then `codex plugin add memvine@memvine`), so nobody has to clone it.
test("the Codex marketplace points at a plugin that holds everything install needs", async () => {
  const repo = fileURLToPath(new URL("../../../", import.meta.url));
  const market = JSON.parse(readFileSync(join(repo, ".agents", "plugins", "marketplace.json"), "utf8"));
  const entry = market.plugins.find((p) => p.name === "memvine");
  assert.equal(market.name, "memvine");
  assert.deepEqual(entry.source, { source: "local", path: "./plugins" });
  const root = join(repo, entry.source.path);
  const manifest = JSON.parse(readFileSync(join(root, ".codex-plugin", "plugin.json"), "utf8"));
  const { VERSION } = await import("../../claude-code/bin/memvine-hook.mjs");
  assert.equal(manifest.name, "memvine");
  assert.equal(manifest.version, VERSION, "the Codex manifest's version follows the plugin's");
  const npmPackage = JSON.parse(readFileSync(join(root, "codex", "package.json"), "utf8"));
  assert.equal(npmPackage.version, VERSION, "the npm package memvine-codex has the plugin's version");
  // Codex copies the plugin folder into its cache: both scripts must be inside it.
  assert.ok(existsSync(join(root, "codex", "bin", "memvine-codex.mjs")));
  assert.ok(existsSync(join(root, "claude-code", "bin", "memvine-hook.mjs")));
});
