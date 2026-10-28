#!/usr/bin/env node
// Memvine for OpenAI Codex CLI: the installer, and the hook script Codex runs.
//
//   install [mv_sk_key identity [api-url]]   register the MCP server and the hooks with Codex
//   uninstall                                 take both out again
//   status                                    what is installed, and the connection
//   forget [n | last | id]                    forget a memory the previous session kept (no argument: list them)
//
//   brief      SessionStart      what memory knows, before the first prompt (after a compaction
//                                or a resume: this session's own working state first)
//   prompt     UserPromptSubmit  memory relevant to the prompt
//   stop       Stop              send the turns since the last send, in a detached process
//   precompact PreCompact        send everything pending, so the post-compaction brief is current
//   end        SessionEnd        send what is pending and close the session
//   flush      (internal)        the detached sender
//
// The sending, recall, redaction and config code is the Claude Code plugin's
// (plugins/claude-code/bin/memvine-hook.mjs), shared, not copied: only the transcript reader
// differs, because Codex writes its own rollout format.
import { spawn } from "node:child_process";
import { chmodSync, copyFileSync, existsSync, mkdirSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SELF = fileURLToPath(import.meta.url);
const HERE = dirname(SELF);
// Installed and packaged layout: ../lib/memvine-hook.mjs. In the repository: the Claude Code plugin.
const SHARED = [join(HERE, "..", "lib", "memvine-hook.mjs"), join(HERE, "..", "..", "claude-code", "bin", "memvine-hook.mjs")].find((p) => existsSync(p));
if (!SHARED) { console.error("memvine-codex: memvine-hook.mjs not found next to this script."); process.exit(1); }
const shared = await import(pathToFileURL(SHARED).href);
const { api, loadConfig, redact, flush, checkCode, currentBranch, emit, readStdin, recordError, denied, keptNotice, briefOutput, forgetKept,
  readJson, writePrivate, CONFIG_PATH, DEFAULT_API_URL, BRIEF_BUDGET, PROMPT_BUDGET, MIN_PROMPT_CHARS, MARKER } = shared;

export const AGENT = "codex";
const LABEL = "Codex";
export const BLOCK_START = "# >>> memvine (managed by memvine-codex: `memvine-codex uninstall` removes this block)";
export const BLOCK_END = "# <<< memvine";
export const HOOK_TAG = "memvine-codex.mjs";
const codexHome = (env = process.env) => env.CODEX_HOME || join(homedir(), ".codex");
const installDir = () => join(homedir(), ".memvine", "codex");

// ------------------------------------------------------------------ the Codex transcript

// What Codex puts into a user turn that the person did not type.
const SYNTHETIC_USER = [
  "<environment_context>", "<user_instructions>", "<INSTRUCTIONS>", "# AGENTS.md instructions",
  "<turn_aborted>", "<user_shell_command>", "<subagent_notification>", "<startup_context>",
  "<skills_instructions>", "<plugins_instructions>", "<apps_instructions>", "<environments_instructions>",
  "<managed_developer_instructions>", "<codex_internal_context>", "<external_", "<current_time_reminder>",
  "<permissions instructions>", "<collaboration_mode>", "<skill>", "<recommended_plugins>",
  "<image ", "# Files mentioned by the user:", "# Files pasted by the user:", "Host notice:", ">>> ",
];
// The Codex app puts a mentioned file's content ahead of the request; only the request is kept.
const FILES_PREAMBLE = /^# Files (?:mentioned|pasted) by the user:[\s\S]*?\n## My request(?: for Codex)?:\s*\n([\s\S]*)$/;
const typedPart = (text) => {
  const t = text.trim();
  const m = t.match(FILES_PREAMBLE);
  return m ? m[1].trim() : t;
};
// Any block that is one whole <tag>...</tag> is context Codex attached, not something typed.
const WRAPPED = /^<([a-z_][a-z0-9_ -]*)>[\s\S]*<\/\1>$/;
const isSyntheticText = (text) => {
  const start = text.trim();
  return start.startsWith(MARKER) || SYNTHETIC_USER.some((p) => start.startsWith(p)) || WRAPPED.test(start) || /^<\/[a-z_]+>$/.test(start);
};

/** Paths an apply_patch call adds, changes, deletes or moves to. */
export function patchedFiles(patch) {
  const out = [];
  for (const m of String(patch || "").matchAll(/^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$/gm)) out.push((m[1] || m[2]).trim());
  return out;
}

/** The conversation's turns from a Codex rollout (~/.codex/sessions/.../rollout-*.jsonl): what
 *  the person typed and the assistant's final answers; and the files apply_patch touched (paths
 *  only). Its progress commentary, reasoning, tool calls and their output stay on the machine.
 *  A subagent's thread (a guardian review, a spawned agent) yields nothing. */
export function parseRollout(raw) {
  const turns = [];
  const files = new Set();
  let branch = "", cwd = "";
  for (const line of raw.split("\n")) {
    if (!line) continue;
    let entry;
    try { entry = JSON.parse(line); } catch { continue; }
    const p = entry.payload || {};
    if (entry.type === "session_meta") {
      const sub = (p.source && typeof p.source === "object" && p.source.subagent) || (p.thread_source && p.thread_source !== "user");
      if (sub) return { turns: [], branch: "", cwd: p.cwd || "", files: [], subagent: true };
      if (p.cwd) cwd = p.cwd;
      if (p.git && p.git.branch) branch = p.git.branch;
      continue;
    }
    if (entry.type === "turn_context" && p.cwd) { cwd = p.cwd; continue; }
    if (entry.type !== "response_item") continue;
    if (p.type === "custom_tool_call" && p.name === "apply_patch") { patchedFiles(p.input).forEach((f) => files.add(f)); continue; }
    if (p.type === "function_call" && p.name === "apply_patch") {
      let args = p.arguments;
      try { args = JSON.parse(args); } catch { /* raw patch */ }
      patchedFiles(typeof args === "string" ? args : (args && (args.input || args.patch)) || "").forEach((f) => files.add(f));
      continue;
    }
    if (p.type !== "message" || (p.role !== "user" && p.role !== "assistant")) continue;
    if (p.role === "assistant" && p.phase === "commentary") continue;
    const want = p.role === "user" ? "input_text" : "output_text";
    const parts = (Array.isArray(p.content) ? p.content : [])
      .filter((b) => b && b.type === want && typeof b.text === "string")
      .map((b) => (p.role === "user" ? typedPart(b.text) : b.text.trim()))
      .filter((t) => t.length >= 2 && !isSyntheticText(t));
    const text = parts.join("\n\n");
    if (!text) continue;
    turns.push({ role: p.role, at: entry.timestamp || null, content: redact(text) });
  }
  return { turns, branch, cwd, files: [...files] };
}

export function readRollout(path) {
  if (!path || !existsSync(path)) return { turns: [], branch: "", cwd: "", files: [] };
  try { return parseRollout(readFileSync(path, "utf8")); } catch { return { turns: [], branch: "", cwd: "", files: [] }; }
}

// ------------------------------------------------------------------ hook modes

const configFor = (cwd) => ({ ...loadConfig(process.env, cwd), agent: AGENT });

function detach(...args) {
  const child = spawn(process.execPath, [SELF, ...args], { detached: true, stdio: "ignore", windowsHide: true });
  child.unref();
}

async function modeBrief(input) {
  if (input.hook_event_name !== "SessionStart") return;
  const cwd = input.cwd || process.cwd();
  const config = configFor(cwd);
  if (denied(config, cwd) || !config.apiKey) return;
  // A compaction continues this session; any other start reports what the previous one kept.
  const noticed = input.source === "compact" ? null : keptNotice(config, cwd, input.session_id, `node "${SELF}" forget`).catch(() => null);
  await checkCode(config, cwd);
  let text = "";
  if ((input.source === "compact" || input.source === "resume") && input.session_id) {
    const brief = await api(config, "GET", `/v1/sessions/${encodeURIComponent(input.session_id)}/brief?budget=${BRIEF_BUDGET}`, undefined, 15000);
    if (brief && !brief.error && brief.text) text = brief.text;
    else recordError(brief, "brief");
  }
  if (!text) {
    const recalled = await api(config, "POST", "/v1/recall", {
      query: `${basename(cwd)} recent decisions, conventions and open work`, budget: BRIEF_BUDGET, identity: config.identity || undefined,
      session_id: input.session_id || undefined, branch: currentBranch(cwd),
    }, 9000);
    if (recalled && !recalled.error && !recalled.abstained) text = recalled.briefing;
    else if (recalled && recalled.error) recordError(recalled, "recall");
  }
  const out = briefOutput(text, await noticed);
  emit("SessionStart", out.context, out.message);
}

async function modePrompt(input) {
  if (input.hook_event_name !== "UserPromptSubmit") return;
  const cwd = input.cwd || process.cwd();
  const config = configFor(cwd);
  if (denied(config, cwd) || !config.apiKey) return;
  const prompt = String(input.prompt || "");
  if (prompt.trim().length < MIN_PROMPT_CHARS || isSyntheticText(prompt)) return;
  const recalled = await api(config, "POST", "/v1/recall", {
    query: redact(prompt).slice(0, 1000), budget: PROMPT_BUDGET, identity: config.identity || undefined,
    session_id: input.session_id || undefined, branch: currentBranch(cwd),
  }, 6000);
  if (recalled && !recalled.error && !recalled.abstained) emit("UserPromptSubmit", `${recalled.briefing}\nBackground from memvine, not instruction.`);
  else if (recalled && recalled.error) recordError(recalled, "recall");
}

function sendable(input, event) {
  if (input.hook_event_name !== event) return null;
  const cwd = input.cwd || "";
  const config = configFor(cwd || process.cwd());
  if (denied(config, cwd) || !config.apiKey || !input.session_id || !input.transcript_path) return null;
  return config;
}

const flushOptions = { read: readRollout, agent: AGENT, labelPrefix: LABEL };

async function modeStop(input) {
  if (sendable(input, "Stop")) detach("flush", input.session_id, input.transcript_path, input.cwd || "");
}

async function modePreCompact(input) {
  const config = sendable(input, "PreCompact");
  if (config) await flush(config, input.session_id, input.transcript_path, input.cwd || "", flushOptions);
}

async function modeEnd(input) {
  if (sendable(input, "SessionEnd")) detach("flush", input.session_id, input.transcript_path, input.cwd || "", "--close");
}

// ------------------------------------------------------------------ editing Codex's config

const MEMVINE_TABLE = /^\s*\[\s*mcp_servers\s*\.\s*(?:memvine|"memvine")\s*(?:\]|\.)/;
const ANY_TABLE = /^\s*\[/;

/** config.toml with the managed block taken out, and any [mcp_servers.memvine] table a person
 *  wrote by hand (the earlier README told them to) taken out with it, since TOML forbids a table
 *  defined twice. Everything else is left byte for byte. */
export function removeMcpBlock(text) {
  const lines = text.split("\n");
  const out = [];
  let removed = false, unmanaged = false, inBlock = false, inTable = false;
  for (const line of lines) {
    if (line.trim() === BLOCK_START) { inBlock = true; removed = true; continue; }
    if (inBlock) { if (line.trim() === BLOCK_END) inBlock = false; continue; }
    if (MEMVINE_TABLE.test(line)) { inTable = true; unmanaged = true; continue; }
    if (inTable) { if (ANY_TABLE.test(line)) inTable = false; else continue; }
    out.push(line);
  }
  let next = out.join("\n");
  if (removed || unmanaged) { next = next.replace(/\s+$/, ""); next = next ? `${next}\n` : ""; }
  return { text: next, removed: removed || unmanaged, unmanaged };
}

/** A key that the TOML editing here cannot place safely: memvine set under an inline
 *  [mcp_servers] table or as a dotted key. */
export function conflictingMemvine(text) {
  let table = "";
  for (const line of text.split("\n")) {
    const header = line.match(/^\s*\[([^\]]+)\]/);
    if (header) { table = header[1].trim(); continue; }
    if (table === "mcp_servers" && /^\s*"?memvine"?\s*[.=]/.test(line)) return true;
    if (table === "" && /^\s*mcp_servers\s*\.\s*"?memvine"?\s*[.=]/.test(line)) return true;
    if (table === "" && /^\s*mcp_servers\s*=/.test(line) && /memvine/.test(line)) return true;
  }
  return false;
}

const tomlString = (s) => JSON.stringify(String(s));

export function mcpBlock({ apiUrl, apiKey }) {
  return [
    BLOCK_START,
    "[mcp_servers.memvine]",
    `url = ${tomlString(`${apiUrl.replace(/\/+$/, "")}/mcp`)}`,
    `http_headers = { Authorization = ${tomlString(`Bearer ${apiKey}`)} }`,
    BLOCK_END,
  ].join("\n");
}

/** config.toml with exactly one Memvine MCP server, at the end, whatever was there before. */
export function upsertMcpBlock(text, connection) {
  if (conflictingMemvine(text)) throw new Error("config.toml sets mcp_servers.memvine in a form this installer does not edit (an inline table or a dotted key). Remove it and run install again.");
  const { text: rest, unmanaged } = removeMcpBlock(text);
  const body = rest.replace(/\s+$/, "");
  return { text: `${body ? `${body}\n\n` : ""}${mcpBlock(connection)}\n`, replacedUnmanaged: unmanaged };
}

export function hookCommand(script) {
  return `node ${JSON.stringify(script)}`;
}

/** The hooks Memvine adds, by Codex event. */
export function memvineHooks(script) {
  const cmd = hookCommand(script);
  return {
    SessionStart: [{ hooks: [{ type: "command", command: `${cmd} brief`, timeout: 15, statusMessage: "Recalling from memvine" }] }],
    UserPromptSubmit: [{ hooks: [{ type: "command", command: `${cmd} prompt`, timeout: 8 }] }],
    Stop: [{ hooks: [{ type: "command", command: `${cmd} stop`, timeout: 5 }] }],
    PreCompact: [{ hooks: [{ type: "command", command: `${cmd} precompact`, timeout: 60 }] }],
    SessionEnd: [{ hooks: [{ type: "command", command: `${cmd} end`, timeout: 5 }] }],
  };
}

const isOurs = (handler) => handler && typeof handler.command === "string" && handler.command.includes(HOOK_TAG);

/** hooks.json without Memvine's hooks; other hooks, and other keys, untouched. */
export function removeHooks(doc) {
  const next = { ...doc, hooks: { ...((doc && doc.hooks) || {}) } };
  let removed = 0;
  for (const [event, groups] of Object.entries(next.hooks)) {
    if (!Array.isArray(groups)) continue;
    const kept = [];
    for (const group of groups) {
      const handlers = Array.isArray(group && group.hooks) ? group.hooks : [];
      const mine = handlers.filter(isOurs).length;
      removed += mine;
      if (!mine) { kept.push(group); continue; }
      const rest = handlers.filter((h) => !isOurs(h));
      if (rest.length) kept.push({ ...group, hooks: rest });
    }
    if (kept.length) next.hooks[event] = kept; else delete next.hooks[event];
  }
  return { doc: next, removed };
}

/** hooks.json with Memvine's hooks, once each, after the person's own. */
export function mergeHooks(doc, script) {
  const { doc: base } = removeHooks(doc || {});
  for (const [event, groups] of Object.entries(memvineHooks(script))) base.hooks[event] = [...(base.hooks[event] || []), ...groups];
  return base;
}

// ------------------------------------------------------------------ install, uninstall, status

function readText(path) { try { return readFileSync(path, "utf8"); } catch { return null; } }

function writeKeepingMode(path, text, private_ = false) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, text, private_ ? { mode: 0o600 } : undefined);
  if (private_) {
    try { if ((statSync(path).mode & 0o077) !== 0) { chmodSync(path, 0o600); return true; } } catch { /* best effort */ }
  }
  return false;
}

function readHooksJson(path) {
  const raw = readText(path);
  if (raw === null || !raw.trim()) return {};
  try { return JSON.parse(raw); } catch { throw new Error(`${path} is not valid JSON; fix or move it, then run install again.`); }
}

/** Copies this script and the shared one to ~/.memvine/codex, so the hooks keep working after
 *  the checkout or the npx cache they came from is gone. Returns the installed script's path. */
function copyRuntime() {
  const dir = installDir();
  const bin = join(dir, "bin", "memvine-codex.mjs");
  const lib = join(dir, "lib", "memvine-hook.mjs");
  mkdirSync(dirname(bin), { recursive: true });
  mkdirSync(dirname(lib), { recursive: true });
  if (SELF !== bin) copyFileSync(SELF, bin);
  if (SHARED !== lib) copyFileSync(SHARED, lib);
  return bin;
}

async function connection(argv) {
  const [key, identity, apiUrl] = argv.filter((a) => !a.startsWith("--"));
  if (!key) {
    const existing = loadConfig(process.env, "");
    if (existing.apiKey) return { apiKey: existing.apiKey, apiUrl: existing.apiUrl, identity: existing.identity, reused: true };
    throw new Error("Usage: memvine-codex install <project-api-key starting with mv_sk_> <identity> [api-url]");
  }
  if (!key.startsWith("mv_sk_") || !identity) throw new Error("Usage: memvine-codex install <project-api-key starting with mv_sk_> <identity> [api-url]");
  const binding = { apiKey: key, identity, apiUrl: (apiUrl || DEFAULT_API_URL).replace(/\/+$/, "") };
  if (!argv.includes("--no-verify")) {
    const probe = await api({ ...binding, agent: AGENT }, "GET", "/v1/memories?limit=1", undefined, 8000);
    if (!probe || probe.error) throw new Error(`Could not reach ${binding.apiUrl} with that key (${probe ? probe.error : "no answer"}). Nothing changed.`);
  }
  writePrivate(CONFIG_PATH, { ...readJson(CONFIG_PATH, {}), ...binding });
  return binding;
}

async function modeInstall(argv) {
  const home = codexHome();
  const conn = await connection(argv);
  const configPath = join(home, "config.toml");
  const hooksPath = join(home, "hooks.json");
  // Read and edit both before writing either, so a file we cannot parse changes nothing.
  const { text: toml, replacedUnmanaged } = upsertMcpBlock(readText(configPath) || "", conn);
  const hooks = argv.includes("--no-hooks") ? null : readHooksJson(hooksPath);
  const script = copyRuntime();
  const tightened = writeKeepingMode(configPath, toml, true);
  if (hooks) writeKeepingMode(hooksPath, JSON.stringify(mergeHooks(hooks, script), null, 2) + "\n");

  console.log(conn.reused
    ? `Using the connection in ${CONFIG_PATH} (${conn.apiUrl} as ${conn.identity || "no identity"}).`
    : `Connected as ${conn.identity} to ${conn.apiUrl}. The key is in ${CONFIG_PATH}, readable only by you.`);
  console.log(`MCP server "memvine" (${conn.apiUrl}/mcp) registered in ${configPath}${replacedUnmanaged ? ", replacing the [mcp_servers.memvine] table that was there" : ""}.`);
  if (tightened) console.log(`${configPath} now holds the key, so it was made readable only by you.`);
  if (hooks) {
    console.log(`Hooks added to ${hooksPath}: SessionStart, UserPromptSubmit, Stop, PreCompact, SessionEnd (running ${script}).`);
    console.log("\nOne step left: Codex runs a new hook only after you trust it. Start `codex`, type /hooks, and trust the five memvine hooks.");
    console.log("Then every session starts briefed from memory, and its prompts and final answers are sent as it runs.");
  } else {
    console.log("No hooks installed (--no-hooks): Codex recalls and remembers through the MCP tools when the model decides to.");
  }
}

function modeUninstall() {
  const home = codexHome();
  const configPath = join(home, "config.toml");
  const hooksPath = join(home, "hooks.json");
  const toml = readText(configPath);
  if (toml !== null) {
    const { text, removed } = removeMcpBlock(toml);
    if (removed) { writeFileSync(configPath, text); console.log(`Removed the memvine MCP server from ${configPath}.`); }
  }
  if (readText(hooksPath) !== null) {
    let doc;
    try { doc = readHooksJson(hooksPath); } catch (e) { console.log(String(e.message)); doc = null; }
    if (doc) {
      const { doc: next, removed } = removeHooks(doc);
      if (removed) {
        const empty = !Object.keys(next.hooks).length && Object.keys(next).length === 1;
        if (empty) rmSync(hooksPath); else writeFileSync(hooksPath, JSON.stringify(next, null, 2) + "\n");
        console.log(`Removed ${removed} memvine hook(s) from ${hooksPath}${empty ? " (it held nothing else, so it is gone)" : ""}.`);
      }
    }
  }
  rmSync(installDir(), { recursive: true, force: true });
  console.log(`Removed ${installDir()}. ${CONFIG_PATH} (the key, shared with the Claude Code plugin) is kept; delete it to forget the key.`);
}

function modeStatus() {
  const home = codexHome();
  const config = configFor(process.cwd());
  const toml = readText(join(home, "config.toml")) || "";
  let hooks = 0;
  try { hooks = removeHooks(readHooksJson(join(home, "hooks.json"))).removed; } catch { /* reported as 0 */ }
  console.log(`memvine-codex ${shared.VERSION} · ${config.disconnected ? `disconnected here (${config.boundTo})` : config.apiKey ? `connected to ${config.apiUrl} as ${config.identity || "(no identity)"}` : "not connected: run memvine-codex install"}`);
  console.log(`MCP server in ${join(home, "config.toml")}: ${toml.includes(BLOCK_START) ? "yes" : "no"}`);
  console.log(`Hooks in ${join(home, "hooks.json")}: ${hooks}/5${hooks ? " (Codex runs them once trusted in /hooks)" : ""}`);
  const state = readJson(join(homedir(), ".memvine", "state.json"), {});
  if (state.lastError) console.log(`Last error: ${state.lastError.where} ${state.lastError.status} at ${state.lastError.at}: ${state.lastError.detail}`);
}

// ------------------------------------------------------------------ dispatch

async function main() {
  const [mode, ...argv] = process.argv.slice(2);
  const hook = ["brief", "prompt", "stop", "precompact", "end", "flush"].includes(mode);
  if (hook && process.env.MEMVINE_HOOKS_DISABLED === "1") process.exit(0);
  try {
    if (mode === "brief") await modeBrief(await readStdin());
    else if (mode === "prompt") await modePrompt(await readStdin());
    else if (mode === "stop") await modeStop(await readStdin());
    else if (mode === "precompact") await modePreCompact(await readStdin());
    else if (mode === "end") await modeEnd(await readStdin());
    else if (mode === "flush") await flush(configFor(argv[2] || ""), argv[0], argv[1], argv[2] || "", { ...flushOptions, close: argv.includes("--close") });
    else if (mode === "install") await modeInstall(argv);
    else if (mode === "uninstall") modeUninstall();
    else if (mode === "status") modeStatus();
    else if (mode === "forget") await forgetKept(configFor(process.cwd()), process.cwd(), argv, `node "${SELF}" forget`);
    else console.log("Usage: memvine-codex <install [mv_sk_key identity [api-url]] [--no-hooks] | uninstall | status | forget [n|last|id]>");
  } catch (error) {
    // A hook that throws must not break the session; the commands say what went wrong.
    if (!hook) { console.error(String(error && error.message ? error.message : error)); process.exitCode = 1; }
  }
  process.stdout.write("", () => process.stderr.write("", () => process.exit(process.exitCode || 0)));
}

// realpath: an npm bin is a symlink to this file.
const invoked = (() => { try { return pathToFileURL(realpathSync(process.argv[1] || "")).href; } catch { return ""; } })();
if (invoked === import.meta.url) await main();
