#!/usr/bin/env node
// Memvine for Claude Code: one script, switched on the hook event.
//
//   brief      SessionStart      what memory knows, before the first prompt (after a compaction:
//                                this session's own working state first); and, once tended, what
//                                the previous session in this folder left in memory (keptNotice)
//   prompt     UserPromptSubmit  memory relevant to the prompt
//   stop       Stop              send the turns since the last send to the session's live state
//   precompact PreCompact        send everything pending, so the post-compaction brief is current
//   end        SessionEnd        send what is pending and close the session; its turns become
//                                long-term memory
//   flush      (internal)        the detached sender the hooks hand off to
//   connect | status | cut | search | backfill | disconnect | forget   the slash commands
//
// What leaves the machine: the person's prompts and the assistant's replies, with secrets
// redacted, and between them one compact "tool" line per stretch of tool work: which tools ran,
// which commands failed or passed, a short redacted tail of a failure or of a test/build result
// (see outcomeLine). That lets the server see a lesson learned the hard way (failed, then fixed;
// read, then applied; verified by a passing test) rather than only the assistant's say-so. Full
// tool output, file contents, diffs and subagent threads stay here; `"toolOutcomes": false` in
// the config sends no tool lines.
import { execFileSync, spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, isAbsolute, join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const VERSION = "0.7.3";
const HOME = join(homedir(), ".memvine");
export const CONFIG_PATH = join(HOME, "config.json");
const STATE_PATH = join(HOME, "state.json");
const SELF = fileURLToPath(import.meta.url);
export const DEFAULT_API_URL = "https://api.memvine.dev";
const AGENT = "claude-code";
export const MARKER = "=== memvine";
export const BRIEF_BUDGET = 2200;
export const PROMPT_BUDGET = 4000;
export const MIN_PROMPT_CHARS = 12;
const BACKFILL_BATCH = 400;
const MAX_CODE_FILES = 300;
// Tools whose input names a file the session read or changed.
const FILE_INPUTS = ["file_path", "notebook_path"];
const MAX_EVENT_CHARS = 100_000;

// Claude Code writes several kinds of synthetic turn into the transcript. None is something a
// person said, so none belongs in memory.
const SYNTHETIC = [
  "<system-reminder>", "<task-notification>", "<user-prompt-submit-hook>", "<persisted-output>",
  "<command-name>", "<command-message>", "<command-args>", "<local-command-stdout>",
  "<local-command-stderr>", "<bash-input>", "<bash-stdout>", "<bash-stderr>", "[SYSTEM",
];

// ------------------------------------------------------------------ config and state

export function readJson(path, fallback) {
  try { return JSON.parse(readFileSync(path, "utf8")); } catch { return fallback; }
}

export function writePrivate(path, data) {
  mkdirSync(HOME, { recursive: true, mode: 0o700 });
  writeFileSync(path, JSON.stringify(data, null, 2) + "\n", { mode: 0o600 });
}

/** The binding for a folder: the longest `projects` entry whose path contains it, else the
 *  machine-wide settings. Each repository can send to its own Memvine project. */
export function bindingFor(file, cwd) {
  const entries = Object.entries(file.projects || {}).filter(([path]) => cwd && (cwd === path || cwd.startsWith(path.replace(/\/?$/, "/"))));
  entries.sort((a, b) => b[0].length - a[0].length);
  return entries.length ? { ...file, ...entries[0][1], boundTo: entries[0][0] } : file;
}

export function loadConfig(env = process.env, cwd = "") {
  const file = bindingFor(readJson(CONFIG_PATH, {}), cwd);
  // A folder disconnected with /memvine:disconnect sends nothing and recalls nothing.
  if (file.disconnected) return { apiUrl: "", apiKey: "", identity: "", deny: [], boundTo: file.boundTo || "", disconnected: true };
  return {
    apiUrl: (env.MEMVINE_API_URL || file.apiUrl || DEFAULT_API_URL).replace(/\/+$/, ""),
    apiKey: env.MEMVINE_API_KEY || file.apiKey || "",
    identity: env.MEMVINE_IDENTITY || file.identity || "",
    deny: Array.isArray(file.deny) ? file.deny : [],
    boundTo: file.boundTo || "",
    // Compact tool outcome lines (see outcomeLine); `"toolOutcomes": false` keeps them here.
    toolOutcomes: env.MEMVINE_TOOL_OUTCOMES ? env.MEMVINE_TOOL_OUTCOMES !== "0" : file.toolOutcomes !== false,
  };
}

export const loadState = () => readJson(STATE_PATH, { sessions: {} });
export const saveState = (state) => writePrivate(STATE_PATH, state);

// ------------------------------------------------------------------ transcript

export const SECRET_PATTERNS = [
  /\b(?:mv_sk|mv_mk|sk-ant|sk-|ghp_|gho_|ghu_|ghs_|github_pat|xox[baprs]|AKIA|ASIA|glpat)-?[A-Za-z0-9_\-]{12,}/g,
  /\bBearer\s+[A-Za-z0-9._\-]{20,}/gi,
  /\beyJ[A-Za-z0-9._\-]{20,}/g,
  /\b([A-Z0-9_]*(?:SECRET|PASSWORD|TOKEN|API_?KEY|PRIVATE_?KEY|CREDENTIAL)[A-Z0-9_]*)\s*[=:]\s*\S+/g,
  /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g,
];

export function redact(text) {
  let out = text;
  for (const pattern of SECRET_PATTERNS) {
    out = out.replace(pattern, (match, name) => (typeof name === "string" ? `${name}=[redacted]` : "[redacted]"));
  }
  return out;
}

function textOf(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.filter((b) => b && b.type === "text" && typeof b.text === "string").map((b) => b.text).join("\n\n");
}

function isSynthetic(text) {
  const start = text.trimStart();
  return start.startsWith(MARKER) || SYNTHETIC.some((prefix) => start.startsWith(prefix));
}

// ------------------------------------------------------------------ tool outcomes
// A compact signal of what the tools did, so extraction can tell an approach verified by a passing
// test, or a fix that followed a failure, from a mere claim. Never file contents.
const OUTCOME_MAX = 1200;  // one tool line: all of a stretch's tools together
const FAIL_TAIL = 300;     // what a failure says, from the end of its output
const PASS_TAIL = 160;     // what a passing check says ("17 passed in 0.92s")
const COMMAND_HEAD = 120;  // a failed or checking command
const OTHER_HEAD = 60;     // any other command that ran fine: named, not explained
const MAX_LISTED = 6;      // paths, patterns or commands named per group
// A command segment that verifies something: tests, builds, type checks, linters.
const CHECK_COMMAND = new RegExp(
  "^(?:(?:npm|pnpm|yarn|bun)\\s+(?:run\\s+)?(?:test|build|lint|typecheck|check|verify)\\S*"
  + "|(?:npx\\s+|uv\\s+run\\s+|poetry\\s+run\\s+|python3?\\s+-m\\s+)?(?:pytest|jest|vitest|mocha|tsc|eslint|ruff|mypy|pyright|rspec|phpunit|ctest)"
  + "|node\\s+--test|cargo\\s+(?:test|build|check|clippy)|go\\s+(?:test|build|vet)|make|gradle|mvn|dotnet\\s+(?:test|build)|swift\\s+(?:test|build))\\b",
  "i");
// Results that are the harness talking, not the code: a blocked or denied call teaches nothing about the project.
const HARNESS_NOISE = /^(<tool_use_error>|Permission for this action was denied|The user doesn't want to proceed|Error: Blocked|Blocked:)/i;
// The agent's own bookkeeping (plans, todo lists, tool lookup, waiting): nothing about the project.
const BOOKKEEPING_TOOLS = /^(ToolSearch|TodoWrite|TodoRead|Task\w*|Monitor|AskUserQuestion|EnterPlanMode|ExitPlanMode|SendMessage|PushNotification|Skill|Sleep|EnterWorktree|ExitWorktree)$/;
// Places whose paths are one run's scratch, never knowledge: temp folders and agent scratchpads.
const SCRATCH_PATH = /(?:\/private)?\/(?:tmp|var\/folders)\/[^\s'"`;|&)]*/g;

const squash = (text) => String(text || "").replace(/\s+/g, " ").trim();
const head = (text, max) => { const t = squash(text); return t.length > max ? `${t.slice(0, max - 1)}…` : t; };
const tail = (text, max) => { const t = squash(text); return t.length > max ? `…${t.slice(-(max - 1))}` : t; };

/** Paths made short and run-independent: the working folder dropped, the home folder as ~,
 *  temp and scratchpad paths as <tmp>. */
export function shortPaths(text, cwd = "") {
  let out = String(text || "");
  const base = cwd ? cwd.replace(/\/?$/, "/") : "";
  if (base) {
    const escaped = base.slice(0, -1).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    out = out.replace(new RegExp(`${escaped}/`, "g"), "").replace(new RegExp(`${escaped}(?![\\w./-])`, "g"), ".");
  }
  out = out.replace(SCRATCH_PATH, "<tmp>");
  const home = homedir();
  if (home && home.length > 1) out = out.split(home).join("~");
  return out;
}

/** What a shell command did, without the setup around it: its first line, with leading
 *  `cd <dir> &&` hops and VAR=value assignments dropped. */
export function commandGist(command, cwd = "") {
  let first = String(command || "").split("\n")[0];
  for (let i = 0; i < 6; i += 1) {
    const next = first.replace(/^\s*(?:cd\s+(?:"[^"]*"|'[^']*'|\S+)|[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|\S*))\s*(?:&&|;)\s*/, "");
    if (next === first) break;
    first = next;
  }
  return squash(shortPaths(first, cwd));
}

const ENV_PREFIX = /^(?:[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|\S*)\s+)+/;
const isCheck = (gist) => gist.split(/&&|\|\||;|\|/).some((segment) => CHECK_COMMAND.test(segment.trim().replace(ENV_PREFIX, "")));

function resultText(block) {
  const c = block && block.content;
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.filter((b) => b && b.type === "text").map((b) => b.text).join("\n");
  return "";
}

const outputOf = (text, cwd) => shortPaths(String(text || "").replace(/^Exit code \d+\s*/, "").replace(/^Shell cwd was reset to .*$/gm, ""), cwd);

/** One tool call and its result as {tool, target, status, detail, check}, or null for harness
 *  noise. Bash: what the command did and whether it failed, with an output tail for a failure
 *  or a check (a test, build or lint run). Other tools: the path or pattern they worked on. */
export function outcomeOf(use, result, cwd = "") {
  const name = (use && use.name) || "tool";
  const input = (use && use.input) || {};
  const text = resultText(result);
  const failed = Boolean(result && result.is_error);
  if (failed && HARNESS_NOISE.test(text.trim())) return null;
  if (BOOKKEEPING_TOOLS.test(name)) return null;
  if (!use && !failed) return null;  // a result whose call is not in the transcript says nothing
  if (name === "Bash") {
    const gist = commandGist(input.command, cwd);
    const check = isCheck(gist);
    if (failed) {
      const exit = (text.match(/^Exit code (\d+)/) || [])[1];
      return { tool: name, target: head(gist, COMMAND_HEAD), status: `failed${exit ? ` (exit ${exit})` : ""}`, detail: tail(outputOf(text, cwd), FAIL_TAIL), check };
    }
    // "exit 0", not "passed": a check piped into tail or grep exits 0 whatever it found; the tail says.
    if (check) return { tool: name, target: head(gist, COMMAND_HEAD), status: "exit 0", detail: tail(outputOf(text, cwd), PASS_TAIL), check };
    return { tool: name, target: head(gist, OTHER_HEAD), status: "ok", detail: "", check };
  }
  let target = input.file_path || input.notebook_path || input.path || input.pattern || input.url || input.query || input.description || "";
  if (typeof target !== "string") target = "";
  return { tool: name, target: head(shortPaths(target, cwd), 100), status: failed ? "error" : "ok", detail: failed ? tail(outputOf(text, cwd), FAIL_TAIL) : "", check: false };
}

/** A stretch of tool work as one redacted line. A failure or a check stands on its own with its
 *  outcome and output tail; everything that simply worked is grouped by tool and only named. */
export function outcomeLine(outcomes) {
  const parts = [];
  const grouped = new Map();
  for (const o of outcomes) {
    if (o.status !== "ok") {
      parts.push(`${o.tool}${o.target ? ` \`${o.target}\`` : ""} ${o.status}${o.detail ? `: ${o.detail}` : ""}`);
      continue;
    }
    if (!grouped.has(o.tool)) { grouped.set(o.tool, []); parts.push({ group: o.tool }); }
    grouped.get(o.tool).push(o.target);
  }
  const rendered = parts.map((p) => {
    if (typeof p === "string") return p;
    const all = grouped.get(p.group);
    const named = [...new Set(all.filter(Boolean))];
    const listed = named.slice(0, MAX_LISTED).map((t) => (p.group === "Bash" ? `\`${t}\`` : t)).join(", ");
    const more = all.length > Math.min(named.length, MAX_LISTED) ? ` (${all.length} in all)` : "";
    return `${p.group} ok${listed ? `: ${listed}` : ""}${more}`;
  });
  const text = redact(`[tools] ${rendered.join("; ")}`);
  return text.length > OUTCOME_MAX ? `${text.slice(0, OUTCOME_MAX - 1)}…` : text;
}

/** The conversation's turns from a Claude Code transcript: who said what, when; between them,
 *  unless `outcomes` is false, one "tool" turn per stretch of tool work (outcomeLine); and the
 *  files its tools read or changed (paths only: their content never leaves the machine). */
export function parseTranscript(raw, { outcomes = true } = {}) {
  const turns = [];
  const files = new Set();
  const uses = new Map();
  let pending = [], pendingAt = null;
  let branch = "", cwd = "";
  const flushOutcomes = () => {
    if (outcomes && pending.length) turns.push({ role: "tool", at: pendingAt, content: outcomeLine(pending) });
    pending = []; pendingAt = null;
  };
  for (const line of raw.split("\n")) {
    if (!line) continue;
    let entry;
    try { entry = JSON.parse(line); } catch { continue; }
    if (entry.type !== "user" && entry.type !== "assistant") continue;
    // A sidechain is a subagent's thread.
    if (entry.isSidechain || entry.isMeta) continue;
    const content = entry.message && entry.message.content;
    if (entry.type === "assistant" && Array.isArray(content)) {
      for (const block of content) {
        if (!block || block.type !== "tool_use" || !block.input) continue;
        if (block.id) uses.set(block.id, block);
        for (const key of FILE_INPUTS) if (typeof block.input[key] === "string") files.add(block.input[key]);
      }
    }
    // A tool result is machine output wearing a user turn: it feeds the outcome line, never a turn.
    if (entry.toolUseResult !== undefined || (Array.isArray(content) && content.some((b) => b && b.type === "tool_result"))) {
      for (const block of Array.isArray(content) ? content : []) {
        if (!block || block.type !== "tool_result") continue;
        const outcome = outcomeOf(uses.get(block.tool_use_id), block, entry.cwd || cwd);
        if (outcome) { pending.push(outcome); pendingAt = entry.timestamp || pendingAt; }
      }
      continue;
    }
    const text = textOf(content);
    if (!text || text.trim().length < 2 || isSynthetic(text)) continue;
    if (entry.gitBranch) branch = entry.gitBranch;
    if (entry.cwd) cwd = entry.cwd;
    flushOutcomes();
    turns.push({ role: entry.type, at: entry.timestamp || null, content: redact(text.trim()) });
  }
  flushOutcomes();
  return { turns, branch, cwd, files: [...files] };
}

/** Where to resume sending in `turns`. `sentThrough` indexes the list as it was parsed when last
 *  sent: with tool turns (fmt 2) or without (no fmt: an older plugin, or toolOutcomes off). When
 *  the format changed since, resume after the same number of prompts and replies (`said`). */
export function resumeIndex(turns, entry, outcomes = true) {
  if (!entry || !entry.sentThrough) return 0;
  if ((entry.fmt === 2) === outcomes) return entry.sentThrough;
  const target = entry.fmt === 2 ? entry.said ?? entry.sentThrough : entry.sentThrough;
  if (!target) return 0;
  let said = 0;
  for (let i = 0; i < turns.length; i += 1) {
    if (turns[i].role === "tool") continue;
    said += 1;
    if (said === target) return i + 1;
  }
  return turns.length;
}

/** The bookkeeping for "sent through the end of `turns`". */
export const sentMark = (turns, outcomes = true) => ({
  sentThrough: turns.length, said: turns.filter((t) => t.role !== "tool").length, ...(outcomes ? { fmt: 2 } : {}),
});

export function readTranscript(path, options = {}) {
  if (!path || !existsSync(path)) return { turns: [], branch: "", cwd: "", files: [] };
  try { return parseTranscript(readFileSync(path, "utf8"), options); } catch { return { turns: [], branch: "", cwd: "", files: [] }; }
}

// ------------------------------------------------------------------ code (for staleness)

function git(cwd, args, input) {
  try {
    return execFileSync("git", args, { cwd, input, encoding: "utf8", timeout: 5000, stdio: ["pipe", "pipe", "ignore"] }).trim();
  } catch { return ""; }
}

/** A repository's name that every clone shares: its origin, as host/owner/name; else the folder name. */
export function repoId(remote, top) {
  const r = (remote || "").trim().replace(/\.git$/, "");
  const ssh = r.match(/^[\w.-]+@([^:]+):(.+)$/);
  if (ssh) return `${ssh[1]}/${ssh[2]}`.toLowerCase();
  const url = r.match(/^[a-z+]+:\/\/(?:[^@/]+@)?([^/]+)\/(.+)$/i);
  if (url) return `${url[1]}/${url[2]}`.toLowerCase();
  return basename(top || "");
}

/** The branch checked out in a folder, so recall can put that branch's thread first; undefined
 *  outside git and on a detached HEAD, where no branch names the work. */
export function currentBranch(cwd) {
  const branch = cwd ? git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"]) : "";
  return branch && branch !== "HEAD" ? branch.slice(0, 200) : undefined;
}

/** The repository a folder is in: its top folder and shared name; null outside git. */
export function repoOf(cwd) {
  const top = git(cwd, ["rev-parse", "--show-toplevel"]);
  if (!top) return null;
  return { top, repo: repoId(git(top, ["config", "--get", "remote.origin.url"]), top) };
}

/** Git blob hashes of repo-relative paths as they are on disk now; null for a missing file. */
export function hashFiles(top, paths) {
  const present = paths.filter((p) => existsSync(join(top, p)));
  const out = Object.fromEntries(paths.map((p) => [p, null]));
  if (!present.length) return out;
  const hashes = git(top, ["hash-object", "--stdin-paths"], present.join("\n") + "\n").split("\n");
  present.forEach((p, i) => { if (/^[0-9a-f]{40,64}$/.test(hashes[i] || "")) out[p] = hashes[i]; });
  return out;
}

/** What a session touched, for the server to anchor facts to: {repo, commit, files: {path: blob}}. */
export function codeContext(cwd, files) {
  const where = repoOf(cwd);
  if (!where || !files.length) return undefined;
  const paths = [...new Set(files.map((f) => relative(where.top, isAbsolute(f) ? f : join(cwd, f))))]
    .filter((p) => p && !p.startsWith("..") && !isAbsolute(p)).slice(0, MAX_CODE_FILES);
  const hashed = Object.fromEntries(Object.entries(hashFiles(where.top, paths)).filter(([, blob]) => blob));
  if (!Object.keys(hashed).length) return undefined;
  return { repo: where.repo, commit: git(where.top, ["rev-parse", "HEAD"]) || undefined, files: hashed };
}

/** Before a session's brief: report the current versions of the files memory is anchored to, so
 *  memories about code that changed come back marked as possibly outdated. */
export async function checkCode(config, cwd) {
  const where = repoOf(cwd);
  if (!where) return;
  const anchors = await api(config, "GET", `/v1/code/anchors?repo=${encodeURIComponent(where.repo)}`, undefined, 2500);
  if (!anchors || anchors.error || !Array.isArray(anchors.paths) || !anchors.paths.length) return;
  const result = await api(config, "POST", "/v1/code/check", { repo: where.repo, files: hashFiles(where.top, anchors.paths) }, 2500);
  if (result && result.error) recordError(result, "code check");
}

const hashOf = (text) => createHash("sha256").update(text).digest("hex").slice(0, 16);

/** Claude Code keeps each folder's transcripts in ~/.claude/projects/<the path, every
 *  non-alphanumeric character a dash>/<session id>.jsonl. */
export const transcriptDirName = (path) => path.replace(/[^a-zA-Z0-9]/g, "-");

/** Earlier sessions run in this folder or a folder inside it, oldest first. The dash encoding is
 *  ambiguous (app-x and app/x look alike), so each transcript's own recorded folder decides. */
export function findTranscripts(cwd, root = join(homedir(), ".claude", "projects"), options = {}) {
  const prefix = transcriptDirName(cwd);
  let dirs = [];
  try { dirs = readdirSync(root).filter((d) => d === prefix || d.startsWith(prefix + "-")); } catch { return []; }
  const found = [];
  for (const dir of dirs) {
    let files = [];
    try { files = readdirSync(join(root, dir)).filter((f) => f.endsWith(".jsonl")); } catch { continue; }
    for (const file of files) {
      const path = join(root, dir, file);
      const parsed = readTranscript(path, options);
      if (!parsed.cwd || !(parsed.cwd === cwd || parsed.cwd.startsWith(cwd.replace(/\/?$/, "/")))) continue;
      if (!parsed.turns.length) continue;
      found.push({ sessionId: basename(file, ".jsonl"), path, ...parsed, startedAt: parsed.turns[0].at || "" });
    }
  }
  return found.sort((a, b) => a.startedAt.localeCompare(b.startedAt));
}

// ------------------------------------------------------------------ the API

export async function api(config, method, path, body, timeoutMs) {
  if (!config.apiKey) return null;
  const control = new AbortController();
  const timer = setTimeout(() => control.abort(), timeoutMs);
  try {
    const response = await fetch(`${config.apiUrl}${path}`, {
      method,
      headers: { Authorization: `Bearer ${config.apiKey}`, "Content-Type": "application/json", "User-Agent": `memvine-${config.agent || AGENT}/${VERSION}` },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: control.signal,
    });
    if (!response.ok) return { error: response.status, body: await response.text().catch(() => "") };
    return response.status === 204 ? {} : await response.json().catch(() => ({}));
  } catch (error) {
    return { error: "unreachable", body: String(error && error.message) };
  } finally {
    clearTimeout(timer);
  }
}

export function recordError(result, where) {
  if (!result || !result.error) return;
  const state = loadState();
  state.lastError = { at: new Date().toISOString(), where, status: result.error, detail: String(result.body || "").slice(0, 300) };
  saveState(state);
}

// ------------------------------------------------------------------ what memory kept
// A session's sources are synthesized in the background after it closes, and a SessionEnd hook
// has no screen left to print on, so the line about what a session left in memory is shown at the
// next session start in the same folder, once the server says synthesis is done
// (GET /v1/sessions/{id}/kept). Until then nothing is shown and the next start asks again.

const KEPT_MAX_AGE_DAYS = 7;

/** Earlier sessions in this folder whose kept line has not been shown yet, newest first. */
export function unreportedSessions(state, cwd, currentId, now = Date.now()) {
  const since = now - KEPT_MAX_AGE_DAYS * 864e5;
  const when = (e) => Date.parse(e.closedAt || e.lastSentAt || "") || 0;
  return Object.entries(state.sessions || {})
    .filter(([id, e]) => id !== currentId && e && e.cwd === cwd && !e.keptShown && !e.backfilled && when(e) >= since)
    .sort((a, b) => when(b[1]) - when(a[1]));
}

/** What the agent is told beside the user's line, so "forget the last one" has something to act on. */
export function keptContext(items, forgetCommand) {
  const lines = items.map((k, i) => `${i + 1}. [${k.category}] ${k.statement.replace(/\.$/, "")}${k.previous ? ` (superseded: ${k.previous.replace(/\.$/, "")})` : ""}`);
  return [`${MARKER} · what memory kept from the previous session in this folder ===`, ...lines,
    `If the user asks to forget one of these ("forget the last one" is number ${items.length}), run \`${forgetCommand} <number>\` and tell them what was forgotten.`].join("\n");
}

/** At session start: what memory kept from the previous session here, once tending is done:
 *  {user: the line for the person, context: the list for the agent}, or null. */
export async function keptNotice(config, cwd, currentId, forgetCommand) {
  const candidates = unreportedSessions(loadState(), cwd, currentId);
  if (!candidates.length) return null;
  const [sessionId] = candidates[0];
  const kept = await api(config, "GET", `/v1/sessions/${encodeURIComponent(sessionId)}/kept`, undefined, 2500);
  if (!kept || (kept.error && kept.error !== 404)) { recordError(kept, "kept"); return null; }
  if (!kept.error && kept.tending) return null;  // still being synthesized: ask again next time
  const state = loadState();
  for (const [id] of candidates) if (state.sessions[id]) state.sessions[id].keptShown = true;
  const items = (!kept.error && Array.isArray(kept.kept) ? kept.kept : [])
    .map((k) => ({ id: k.id, category: k.category, statement: k.statement, previous: k.previous || undefined }));
  if (items.length) state.kept = { ...(state.kept || {}), [cwd]: { sessionId, at: new Date().toISOString(), items } };
  saveState(state);
  if (!items.length) return null;  // nothing new: nothing worth interrupting for
  return {
    user: `memvine · last session here: ${kept.summary} To undo one, say "forget the last one" (or name it).`,
    context: keptContext(items, forgetCommand),
  };
}

/** `forget [n | last | memory-id]`: retire a memory the previous session kept (the list shown at
 *  session start), or any memory by id. With no argument, lists what can be forgotten. */
export async function forgetKept(config, cwd, argv, commandName = "forget") {
  const state = loadState();
  const entry = (state.kept || {})[cwd]
    || Object.values(state.kept || {}).sort((a, b) => String(b.at).localeCompare(String(a.at)))[0];
  const items = entry ? entry.items : [];
  const live = items.map((k, i) => ({ ...k, n: i + 1 })).filter((k) => !k.forgotten);
  const arg = argv.filter((a) => !a.startsWith("--")).join(" ").trim().toLowerCase().replace(/^#/, "");
  if (!arg) {
    if (!live.length) { console.log("Nothing from the last session to forget. Pass a memory id to forget any memory."); return; }
    console.log("What memory kept from the last session:");
    for (const k of live) console.log(`  ${k.n}. [${k.category}] ${k.statement}`);
    console.log(`Forget one with: ${commandName} <number> (or "last")`);
    return;
  }
  let target;
  if (/^(the )?last( one)?$/.test(arg)) target = live[live.length - 1];
  else if (/^\d+$/.test(arg)) target = live.find((k) => k.n === Number(arg));
  else if (/^[0-9a-f-]{36}$/.test(arg)) target = items.find((k) => k.id === arg) || { id: arg };
  if (!target) { console.log(`No memory ${arg} in the last session's list; run ${commandName} with no argument to see it.`); return; }
  const result = await api(config, "PATCH", `/v1/memories/${encodeURIComponent(target.id)}`, { status: "retired" }, 10000);
  if (!result || result.error) {
    recordError(result, "forget");
    console.log(`Could not forget it (${result ? result.error : "not connected"}). Nothing changed.`);
    return;
  }
  if (result.status !== "retired") { console.log(`Not forgotten: "${result.statement}" is ${result.status}, not current.`); return; }
  const k = items.find((x) => x.id === target.id);
  if (k) { k.forgotten = true; saveState(state); }
  console.log(`Forgotten: ${result.statement}${k && k.previous ? `\nCurrent again: ${k.previous}` : ""}`);
}

// ------------------------------------------------------------------ sending

/** Sends the turns not yet sent for this session; closes it when asked. Other agents' plugins
 *  (plugins/codex) pass their own transcript reader, agent name and label; a reader gets
 *  `{outcomes}` and adds "tool" turns (outcomeLine) only when it is true. */
export async function flush(config, sessionId, transcriptPath, cwd, { close = false, read = readTranscript, agent = AGENT, labelPrefix = "Claude Code" } = {}) {
  const outcomes = config.toolOutcomes !== false;
  const { turns, branch, files } = read(transcriptPath, { outcomes });
  const state = loadState();
  const entry = state.sessions[sessionId] || { sentThrough: 0 };
  const pending = turns.slice(resumeIndex(turns, entry, outcomes));
  const project = basename(cwd || "");
  if (pending.length) {
    const result = await api(config, "POST", `/v1/sessions/${encodeURIComponent(sessionId)}/events`, {
      events: pending.map((t) => ({ role: t.role, content: t.content, at: t.at || undefined })),
      code: codeContext(cwd, files),
      agent,
      identity: config.identity || undefined,
      label: `${labelPrefix} · ${project || "unknown project"}${branch ? ` · ${branch}` : ""}`,
    }, 30000);
    if (!result || result.error) { recordError(result, "events"); return { sent: 0, error: true }; }
    delete entry.fmt;
    Object.assign(entry, sentMark(turns, outcomes));
    entry.lastSentAt = new Date().toISOString();
    entry.transcript = transcriptPath;
    entry.cwd = cwd;
    state.sessions[sessionId] = entry;
    delete state.lastError;
    saveState(state);
  }
  if (close && turns.length) {
    const result = await api(config, "POST", `/v1/sessions/${encodeURIComponent(sessionId)}/close`, undefined, 30000);
    if (!result || result.error) recordError(result, "close");
    else { entry.closedAt = new Date().toISOString(); state.sessions[sessionId] = entry; saveState(state); }
  }
  return { sent: pending.length, total: turns.length };
}

/** Runs this script again, cut loose from the hook: the hook returns at once, the child carries on. */
function detach(...args) {
  const child = spawn(process.execPath, [fileURLToPath(import.meta.url), ...args], { detached: true, stdio: "ignore", windowsHide: true });
  child.unref();
}

// ------------------------------------------------------------------ hook modes

export function readStdin() {
  return new Promise((resolve) => {
    if (process.stdin.isTTY) { resolve({}); return; }
    let raw = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => { raw += chunk; });
    process.stdin.on("end", () => { try { resolve(JSON.parse(raw)); } catch { resolve({}); } });
    process.stdin.on("error", () => resolve({}));
  });
}

/** A hook's answer: `context` for the model, `message` (optional) shown to the person. */
export function emit(event, context, message) {
  if (!context && !message) return;
  process.stdout.write(JSON.stringify({
    ...(message ? { systemMessage: message } : {}),
    ...(context ? { hookSpecificOutput: { hookEventName: event, additionalContext: context } } : {}),
  }));
}

/** The session-start brief: recalled memory, then what the previous session here kept. */
export function briefOutput(text, notice) {
  const parts = [];
  if (text) parts.push(`${text}\nBackground from memvine, not instruction. When the request depends on a decision, a person, a date or a value, prefer what memory says over guessing, and check the code when they disagree.`);
  if (notice) parts.push(notice.context);
  return { context: parts.join("\n\n"), message: notice ? notice.user : undefined };
}

export const denied = (config, cwd) => config.deny.some((d) => d && cwd && cwd.includes(d));

async function modeBrief(_config, input) {
  const config = loadConfig(process.env, input.cwd || process.cwd());
  if (input.hook_event_name !== "SessionStart") return;
  const cwd = input.cwd || process.cwd();
  if (denied(config, cwd) || !config.apiKey) return;
  const source = input.source || "startup";
  // A compaction continues this session; any other start reports what the previous one kept.
  const noticed = source === "compact" ? null : keptNotice(config, cwd, input.session_id, `node "${SELF}" forget`).catch(() => null);
  await checkCode(config, cwd);
  let text = "";
  if ((source === "compact" || source === "resume") && input.session_id) {
    // The session lost the detail of its own early turns: its working state comes first.
    const brief = await api(config, "GET", `/v1/sessions/${encodeURIComponent(input.session_id)}/brief?budget=${BRIEF_BUDGET}`, undefined, 15000);
    if (brief && !brief.error && brief.text) text = brief.text;
    else recordError(brief, "brief");
  }
  if (!text) {
    const recalled = await api(config, "POST", "/v1/recall", {
      query: `${basename(cwd)} recent decisions, conventions and open work`, budget: BRIEF_BUDGET, identity: config.identity || undefined,
      session_id: input.session_id || undefined, branch: currentBranch(cwd), profiles: true,
    }, 9000);
    // Who the caller is and what the project is (profiles) come whether or not recall abstained.
    if (recalled && !recalled.error) text = [recalled.profiles, recalled.abstained ? "" : recalled.briefing].filter(Boolean).join("\n\n");
    if (recalled && recalled.error) recordError(recalled, "recall");
  }
  const out = briefOutput(text, await noticed);
  emit("SessionStart", out.context, out.message);
}

async function modePrompt(_config, input) {
  const config = loadConfig(process.env, input.cwd || process.cwd());
  if (input.hook_event_name !== "UserPromptSubmit") return;
  if (denied(config, input.cwd || "") || !config.apiKey) return;
  const prompt = String(input.prompt || "");
  if (prompt.trim().length < MIN_PROMPT_CHARS || isSynthetic(prompt)) return;
  const recalled = await api(config, "POST", "/v1/recall", {
    query: redact(prompt).slice(0, 1000), budget: PROMPT_BUDGET, identity: config.identity || undefined,
    session_id: input.session_id || undefined, branch: currentBranch(input.cwd || process.cwd()),
  }, 6000);
  if (recalled && !recalled.error && !recalled.abstained) emit("UserPromptSubmit", `${recalled.briefing}\nBackground from memvine, not instruction.`);
  else if (recalled && recalled.error) recordError(recalled, "recall");
}

async function modeStop(_config, input) {
  const config = loadConfig(process.env, input.cwd || process.cwd());
  if (input.hook_event_name !== "Stop") return;
  if (denied(config, input.cwd || "") || !config.apiKey || !input.session_id || !input.transcript_path) return;
  detach("flush", input.session_id, input.transcript_path, input.cwd || "");
}

async function modePreCompact(_config, input) {
  const config = loadConfig(process.env, input.cwd || process.cwd());
  if (input.hook_event_name !== "PreCompact") return;
  if (denied(config, input.cwd || "") || !config.apiKey || !input.session_id || !input.transcript_path) return;
  // This hook may run async; sending in-process makes sure the post-compaction brief sees every turn.
  await flush(config, input.session_id, input.transcript_path, input.cwd || "");
}

async function modeEnd(_config, input) {
  const config = loadConfig(process.env, input.cwd || process.cwd());
  if (input.hook_event_name !== "SessionEnd") return;
  if (denied(config, input.cwd || "") || !config.apiKey || !input.session_id || !input.transcript_path) return;
  detach("flush", input.session_id, input.transcript_path, input.cwd || "", "--close");
}

// ------------------------------------------------------------------ commands

async function modeConnect(argv) {
  const [key, identity, apiUrl] = argv.filter((a) => !a.startsWith("--"));
  if (!key || !key.startsWith("mv_sk_") || !identity) {
    console.log("Usage: connect <project-api-key starting with mv_sk_> <identity> [api-url]");
    return;
  }
  const here = argv.includes("--here");
  const binding = { apiKey: key, identity, apiUrl: (apiUrl || DEFAULT_API_URL).replace(/\/+$/, "") };
  const probe = await api({ ...loadConfig({}), ...binding }, "GET", "/v1/memories?limit=1", undefined, 8000);
  if (!probe || probe.error) {
    console.log(`Could not reach ${binding.apiUrl} with that key (${probe ? probe.error : "no answer"}). Nothing saved.`);
    return;
  }
  const file = readJson(CONFIG_PATH, {});
  const folder = process.cwd();
  const next = here ? { ...file, projects: { ...(file.projects || {}), [folder]: binding } } : { ...file, ...binding };
  writePrivate(CONFIG_PATH, next);
  console.log(here
    ? `Connected ${folder} as ${identity} to ${binding.apiUrl}. Sessions in this folder use this project; other folders keep their own settings.`
    : `Connected this machine as ${identity} to ${binding.apiUrl}. The key is in ${CONFIG_PATH}, readable only by you.`);
  console.log("From now on each session's prompts and replies are sent as it runs, and every session starts briefed.");
}

/** Stops this folder (and the folders inside it) from sending to or recalling from Memvine,
 *  whatever a parent folder or the machine is connected to. --undo removes that again. */
function modeDisconnect(argv) {
  const file = readJson(CONFIG_PATH, {});
  const folder = process.cwd();
  const projects = { ...(file.projects || {}) };
  if (argv.includes("--undo")) {
    if (projects[folder] && projects[folder].disconnected) delete projects[folder];
    writePrivate(CONFIG_PATH, { ...file, projects });
    console.log(`${folder} follows its parent folder's or this machine's Memvine settings again.`);
    return;
  }
  projects[folder] = { disconnected: true };
  writePrivate(CONFIG_PATH, { ...file, projects });
  console.log(`Disconnected ${folder} and the folders inside it: sessions here are no longer sent to Memvine or briefed from it. Other folders keep their settings. Undo with /memvine:disconnect --undo.`);
  console.log("What was already sent stays on the server; an admin can delete those sessions in the console (Sessions).");
}

function modeStatus(_config) {
  const config = loadConfig(process.env, process.cwd());
  if (config.disconnected) {
    console.log(`memvine ${VERSION} · disconnected here (${config.boundTo}): nothing is sent from this folder.`);
    return;
  }
  const state = loadState();
  const sessions = Object.values(state.sessions || {});
  console.log(`memvine ${VERSION} · ${config.apiKey ? `connected to ${config.apiUrl} as ${config.identity || "(no identity)"}` : "not connected: run /memvine:connect"}`);
  console.log(config.boundTo ? `This folder uses the project bound at ${config.boundTo}.` : "This folder uses the machine-wide settings.");
  console.log(`${sessions.length} session(s) seen on this machine, ${sessions.filter((s) => s.closedAt).length} closed.`);
  if (state.lastError) console.log(`Last error: ${state.lastError.where} ${state.lastError.status} at ${state.lastError.at}: ${state.lastError.detail}`);
}

function modeCut(argv) {
  const sessionId = argv.find((a) => !a.startsWith("--")) || "";
  const state = loadState();
  const entry = state.sessions[sessionId];
  const transcript = entry && entry.transcript;
  const outcomes = loadConfig(process.env, (entry && entry.cwd) || process.cwd()).toolOutcomes !== false;
  const { turns } = readTranscript(transcript, { outcomes });
  const pending = turns.slice(resumeIndex(turns, entry, outcomes));
  console.log(`Session ${sessionId || "?"}: ${turns.length} turn(s) in the transcript, ${pending.length} not yet sent.`);
  const who = { user: "Developer", assistant: "Claude", tool: "Tools" };
  for (const t of pending) console.log(`\n## ${who[t.role] || t.role}${t.at ? ` · ${t.at}` : ""}\n${t.content}`);
  if (!transcript) console.log("No transcript recorded for this session yet; the first Stop records it.");
}

/** Sends this folder's earlier Claude Code sessions, the ones never sent live. Without --send it
 *  only lists them, with the size and credits it would cost. */
async function modeBackfill(argv) {
  const cwd = process.cwd();
  const config = loadConfig(process.env, cwd);
  const flag = (name) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : undefined; };
  const send = argv.includes("--send");
  const skip = new Set([flag("--skip")].filter(Boolean));
  const days = Number(flag("--days") || 0);
  const since = days > 0 ? new Date(Date.now() - days * 864e5).toISOString() : "";
  if (!config.apiKey) { console.log("Not connected: run /memvine:connect first."); return; }
  if (denied(config, cwd)) { console.log(`${cwd} is on the deny list in ${CONFIG_PATH}; nothing is sent from it.`); return; }

  const state = loadState();
  const outcomes = config.toolOutcomes !== false;
  const candidates = findTranscripts(cwd, undefined, { outcomes }).filter((t) =>
    !skip.has(t.sessionId) && !state.sessions[t.sessionId] && t.turns.filter((x) => x.role !== "tool").length >= 2
    && (!since || t.startedAt >= since));
  const chars = candidates.reduce((n, t) => n + t.turns.reduce((m, turn) => m + turn.content.length, 0), 0);
  if (!candidates.length) { console.log(`No earlier sessions of ${cwd} left to send.`); return; }
  console.log(`${candidates.length} earlier session(s) of ${cwd}${since ? ` from the last ${days} days` : ""}, `
    + `${candidates.reduce((n, t) => n + t.turns.length, 0)} turns, ${chars.toLocaleString("en-US")} characters (about ${Math.max(1, Math.round(chars / 1000))} credits):`);
  for (const t of candidates) {
    const first = (t.turns.find((x) => x.role === "user") || t.turns[0]).content.replace(/\s+/g, " ").slice(0, 70);
    console.log(`  ${t.startedAt.slice(0, 10) || "undated   "}  ${String(t.turns.length).padStart(4)} turns  ${first}`);
  }
  if (!send) {
    console.log(`\nNothing sent yet. Prompts and replies${outcomes ? ", plus a one-line outcome per stretch of tool work (which commands failed or passed)" : " only"}, secrets redacted; full tool output and files stay here. To send: backfill --send${days ? ` --days ${days}` : ""}`);
    return;
  }

  let sent = 0;
  for (const t of candidates) {
    const label = `Claude Code · ${basename(t.cwd || cwd)}${t.branch ? ` · ${t.branch}` : ""} (imported)`;
    let failed = false;
    for (let i = 0; i < t.turns.length && !failed; i += BACKFILL_BATCH) {
      const result = await api(config, "POST", `/v1/sessions/${encodeURIComponent(t.sessionId)}/events`, {
        events: t.turns.slice(i, i + BACKFILL_BATCH).map((x) => ({ role: x.role, content: x.content.slice(0, MAX_EVENT_CHARS), at: x.at || undefined })),
        agent: AGENT, identity: config.identity || undefined, label, live: false,
        code: i === 0 ? codeContext(t.cwd || cwd, t.files || []) : undefined,
      }, 60000);
      if (!result || result.error) { recordError(result, "backfill"); failed = true; console.log(`  ${t.sessionId}: failed (${result ? result.error : "no answer"}): ${String(result && result.body).slice(0, 200)}`); }
    }
    if (failed) break;
    const closed = await api(config, "POST", `/v1/sessions/${encodeURIComponent(t.sessionId)}/close`, undefined, 60000);
    if (!closed || closed.error) { recordError(closed, "backfill close"); console.log(`  ${t.sessionId}: sent, but closing failed; the server closes it after 30 idle minutes.`); }
    state.sessions[t.sessionId] = { ...sentMark(t.turns, outcomes), lastSentAt: new Date().toISOString(), transcript: t.path, cwd: t.cwd, closedAt: new Date().toISOString(), backfilled: true };
    saveState(state);
    sent += 1;
  }
  console.log(`Sent ${sent} of ${candidates.length} session(s). The server turns them into memory over the next minutes; the console's Sessions page shows them.`);
}

async function modeSearch(config, argv) {
  const query = argv.join(" ").trim();
  if (!query) { console.log("Usage: search <question>"); return; }
  const recalled = await api(config, "POST", "/v1/recall", { query: redact(query).slice(0, 1000), budget: PROMPT_BUDGET, identity: config.identity || undefined }, 10000);
  if (!recalled || recalled.error) { console.log(`Recall failed: ${recalled ? recalled.error : "no answer"}`); return; }
  console.log(recalled.abstained ? "Nothing in memory matches." : recalled.briefing);
}

// ------------------------------------------------------------------ dispatch

async function main() {
  const [mode, ...argv] = process.argv.slice(2);
  // Set by the Memvine server on its own `claude -p` calls: never record the server's work.
  if (process.env.MEMVINE_HOOKS_DISABLED === "1" && ["brief", "prompt", "stop", "precompact", "end", "flush"].includes(mode)) {
    process.exit(0);
  }
  const config = loadConfig();
  try {
    if (mode === "brief") await modeBrief(config, await readStdin());
    else if (mode === "prompt") await modePrompt(config, await readStdin());
    else if (mode === "stop") await modeStop(config, await readStdin());
    else if (mode === "precompact") await modePreCompact(config, await readStdin());
    else if (mode === "end") await modeEnd(config, await readStdin());
    else if (mode === "flush") await flush(loadConfig(process.env, argv[2] || ""), argv[0], argv[1], argv[2] || "", { close: argv.includes("--close") });
    else if (mode === "connect") await modeConnect(argv);
    else if (mode === "status") modeStatus(config);
    else if (mode === "cut") modeCut(argv);
    else if (mode === "search") await modeSearch(loadConfig(process.env, process.cwd()), argv);
    else if (mode === "backfill") await modeBackfill(argv);
    else if (mode === "disconnect") modeDisconnect(argv);
    else if (mode === "forget") await forgetKept(loadConfig(process.env, process.cwd()), process.cwd(), argv, "/memvine:forget");
    else console.log("Usage: memvine-hook <brief|prompt|stop|precompact|end|flush|connect|status|cut|search|backfill|disconnect|forget>");
  } catch {
    // A hook that throws must not break the session; /memvine:status is where a person finds out.
  }
  process.stdout.write("", () => process.stderr.write("", () => process.exit(0)));
}

// Node resolves symlinks in import.meta.url, so the path it was started with is resolved too: a
// plugin under a symlinked folder (macOS /var -> /private/var, a linked home) must still run.
const invoked = (() => { try { return pathToFileURL(realpathSync(process.argv[1] || "")).href; } catch { return ""; } })();
if (invoked && import.meta.url === invoked) await main();

export const _internal = { hashOf, isSynthetic };
