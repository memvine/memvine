/**
 * The memvine MCP server: how coding agents talk to the store.
 *
 * Design principle: memvine never calls an LLM itself. Judgment calls
 * (is this a duplicate? does it contradict an existing memory?) are made
 * by the CALLING agent — the tool descriptions instruct it to check before
 * writing. The agent that's already running pays for its own thinking;
 * memvine stays pure git + files, free forever.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { KINDS, MemoryKind } from "./schema.js";
import { Store } from "./store.js";
import { findStale, markStale } from "./staleness.js";
import { headCommit } from "./git.js";

function fmt(result: ReturnType<Store["recall"]>): string {
  const { memories, omitted, sources } = result;
  if (memories.length === 0) return "No memories found.";
  const body = memories
    .map(
      (m) =>
        `[${m.meta.id}] (${m.meta.kind}, ${m.meta.status}, confidence=${m.meta.confidence}` +
        (m.meta.verified ? "" : ", UNVERIFIED candidate") +
        (m.meta.tags.length ? `, tags=${m.meta.tags.join(",")}` : "") +
        (m.meta.scope.length ? `, scope=${m.meta.scope.join(",")}` : "") +
        `, via=${sources[m.meta.id]}` +
        `, learned@${m.meta.learned_commit})\n${m.body}`,
    )
    .join("\n\n---\n\n");
  return omitted > 0
    ? `${body}\n\n---\n\n(${omitted} lower-ranked memor${omitted === 1 ? "y" : "ies"} omitted to fit the recall budget — narrow your query or pass a path to see more.)`
    : body;
}

export async function serve(store: Store): Promise<void> {
  const server = new McpServer({ name: "memvine", version: "0.1.0" });

  server.tool(
    "recall",
    "Recall project memories relevant to what you're working on. Call this at the START of a task, and again when entering an unfamiliar part of the codebase. Returns memories with provenance (id, kind, status, the commit they were learned at). Treat memories with status=stale with suspicion: the code they describe has changed since they were learned — verify them, then either revise them (revise tool) or confirm them (mark them active again via revise).",
    {
      query: z
        .string()
        .describe(
          "Free-text description of what you're doing, e.g. 'auth integration tests failing'",
        ),
      path: z
        .string()
        .optional()
        .describe(
          "Repo-relative file path you're working on, to scope results, e.g. 'src/auth/login.ts'",
        ),
      limit: z
        .number()
        .int()
        .positive()
        .optional()
        .describe(
          "Max memories to return (default 10). Results are also capped by a token budget, so fewer may come back.",
        ),
    },
    async ({ query, path, limit }) => ({
      content: [
        { type: "text", text: fmt(store.recall(query, path, { limit })) },
      ],
    }),
  );

  server.tool(
    "remember",
    "Store a durable project memory for future sessions (yours, other machines', and teammates' agents). BEFORE storing: call recall to check for duplicates or contradictions. If this new knowledge CONTRADICTS an existing memory, do not just add it — pass supersedes with the old memory's id so the old one is retired with a pointer to its replacement. Memory kinds mirror human memory — pick by asking what sentence you're storing: what HAPPENED (an event, an attempt, an outcome) → episodic; what IS TRUE about this codebase (a fact, decision, convention, quirk) → semantic; HOW TO do something here (a runbook, workflow, sequence of steps) → procedural; something to do LATER when a condition arrives → prospective. VERIFICATION: by default a memory is stored as an unverified CANDIDATE (kept local, not shared, down-ranked) — this is correct for a hunch or an unconfirmed result. Set verified=true ONLY when you have actually confirmed it (a test passed, you read the code, a PR merged, the user confirmed) and pass evidence saying how; that promotes it to committed team knowledge. Do NOT store secrets, credentials, or anything you'd not commit to the repo.",
    {
      body: z.string().describe("The memory itself, in plain markdown. Be specific and self-contained."),
      kind: z.enum(KINDS as [MemoryKind, ...MemoryKind[]]).describe("episodic = what happened · semantic = what is true · procedural = how to · prospective = do later"),
      tags: z.array(z.string()).optional().describe("Freeform domain labels, e.g. ['test','auth','deploy']"),
      scope: z
        .array(z.string())
        .optional()
        .describe(
          "Path globs this memory is about, e.g. ['src/auth/**']. IMPORTANT for staleness detection: scoped semantic/procedural memories get flagged when their code changes (episodic memories are history and never stale). Omit only for truly repo-wide knowledge.",
        ),
      confidence: z.enum(["high", "medium", "low"]).optional(),
      verified: z
        .boolean()
        .optional()
        .describe(
          "true ONLY if you actually confirmed this (test/code/PR/user). Default false = unverified candidate, kept local and down-ranked until validated.",
        ),
      evidence: z
        .string()
        .optional()
        .describe("How it was confirmed, e.g. 'tests green at a1b4c9e', 'PR #123', 'user confirmed'. Required in spirit whenever verified=true."),
      supersedes: z
        .string()
        .optional()
        .describe("id of an existing memory this replaces (use when new knowledge contradicts old)"),
      local: z
        .boolean()
        .optional()
        .describe("true = personal memory (gitignored, not shared with the team)"),
    },
    async (args) => {
      const m = store.add({
        body: args.body,
        kind: args.kind,
        tags: args.tags,
        scope: args.scope,
        confidence: args.confidence,
        verified: args.verified,
        evidence: args.evidence,
        supersedes: args.supersedes,
        local: args.local,
        agent: "mcp",
      });
      const shared = m.meta.verified && !args.local;
      return {
        content: [
          {
            type: "text",
            text:
              `Stored ${m.meta.id} (${m.meta.kind}, learned@${m.meta.learned_commit}) as ` +
              (shared
                ? "verified team memory (committed)."
                : "an unverified candidate (kept local — validate it once confirmed to share).") +
              `${args.supersedes ? ` Superseded ${args.supersedes}.` : ""}`,
          },
        ],
      };
    },
  );

  server.tool(
    "revise",
    "Update an existing memory after revalidating it. Use when: a stale memory turned out still true (set status=active), the content needs correcting (pass new body), or the memory no longer applies at all (set status=archived).",
    {
      id: z.string().describe("Memory id, e.g. mem_ab12cd34"),
      body: z.string().optional().describe("Corrected content (omit to keep current)"),
      status: z.enum(["active", "archived"]).optional(),
    },
    async ({ id, body, status }) => {
      const found = store.get(id);
      if (!found) {
        return { content: [{ type: "text", text: `No memory with id ${id}.` }] };
      }
      if (body !== undefined) found.memory.body = body.trim();
      if (status !== undefined) {
        found.memory.meta.status = status;
        if (status === "active") delete found.memory.meta.stale_since;
      }
      // A revise means the agent re-checked this memory against the current
      // code, so advance validated_commit to HEAD: staleness is measured from
      // here, and re-confirming clears the flag until the code changes AGAIN.
      // learned_commit stays put — it's immutable provenance of first learning.
      found.memory.meta.validated_commit = headCommit(store.root);
      found.memory.meta.learned_at = new Date().toISOString();
      store.write(found.memory, found.local);
      return {
        content: [
          {
            type: "text",
            text: `Revised ${id} (revalidated@${found.memory.meta.validated_commit}).`,
          },
        ],
      };
    },
  );

  server.tool(
    "validate",
    "Promote an unverified candidate memory to verified team knowledge, AFTER you have confirmed it is actually true — a test passed, you read the code, a PR merged, or the user confirmed. This moves it from the local candidate store into the committed store so `git push` shares it with the team. Pass evidence describing how you confirmed it. Only validate memories you have genuinely checked; that promise is the whole point of the verified store.",
    {
      id: z.string().describe("Memory id to validate, e.g. mem_ab12cd34"),
      evidence: z
        .string()
        .optional()
        .describe("How you confirmed it, e.g. 'integration suite green at a1b4c9e', 'PR #123', 'user confirmed'."),
    },
    async ({ id, evidence }) => {
      const res = store.validate(id, evidence);
      if (!res) {
        return { content: [{ type: "text", text: `No memory with id ${id}.` }] };
      }
      return {
        content: [
          {
            type: "text",
            text: `Validated ${id} (confirmed@${res.memory.meta.validated_commit})${res.promoted ? " — promoted to the committed team store; commit .memvine/ to share it." : " — already committed; refreshed its evidence."}`,
          },
        ],
      };
    },
  );

  server.tool(
    "check_stale",
    "Scan for semantic/procedural memories whose scoped files have changed since they were learned, and mark them stale (episodic memories are historical facts and are never staled). Run at session start. Returns the list of newly-stale memories so you can revalidate the relevant ones as you encounter their territory.",
    {},
    async () => {
      const reports = findStale(store);
      const n = markStale(store, reports);
      const text =
        n === 0
          ? "No memories need revalidation."
          : `${n} memor${n === 1 ? "y" : "ies"} need revalidation — their scoped code changed since they were last confirmed:\n\n` +
            reports
              .map(
                (r) =>
                  `[${r.memory.meta.id}] needs revalidation — changed since ${r.memory.meta.validated_commit}: ${r.changedFiles.join(", ")}\n${r.memory.body.slice(0, 200)}`,
              )
              .join("\n\n");
      return { content: [{ type: "text", text }] };
    },
  );

  const transport = new StdioServerTransport();
  await server.connect(transport);
}
