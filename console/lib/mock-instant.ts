// The mock of POST /v1/ingest with `instant: true`: what the server's extraction and reconcile
// would make of a pasted source, without a model. The prefilled snippet gets the reading a real
// model should give it (the same one server/tests/test_instant.py fakes): five facts, one extending
// another, and a thread page. Any other text is read by a simple rule: sentences that decide, set a
// rule or state how something works become facts; a fact sharing most of its words with one memory
// already holds extends it, or supersedes it when the sentence says the value moved or changed.
import "server-only";
import { randomBytes } from "node:crypto";
import { relatedFromGraph } from "./graph";
import type { MockThread, ProjectData } from "./mock-data";
import { isPasteSnippet } from "./paste-snippet";
import type { KnowledgeCategory, MemoryGraph, MemoryItem, MemoryRelated } from "./types";

interface Reading {
  statement: string;
  category: KnowledgeCategory;
  why: string | null;
  quote: string;
  subjects: string[];
  /** Index of an earlier reading this one extends. */
  extends?: number;
  inThread?: boolean;
}

const SNIPPET_READING: Reading[] = [
  { statement: "Invoice export retries are capped at 3 per batch.", category: "decision",
    why: "more retries only queue behind the billing API's rate limit", quote: "capped export retries at 3 per batch",
    subjects: ["export retries"], inThread: true },
  { statement: "Each invoice export retry waits 30 seconds, so the 3 retries per batch stay under the billing API rate limit.",
    category: "fact", why: "the billing API allows 100 requests a minute", quote: "each export retry now waits 30 seconds before it runs",
    subjects: ["export retries"], extends: 0, inThread: true },
  { statement: "The release moved from Thu 29 Oct to Tue 3 Nov 2026 so finance can check invoices from the new export.",
    category: "decision", why: "finance wants a week with the new export", quote: "the release moves from Thu 29 Oct to Tue 3 Nov",
    subjects: ["release"] },
  { statement: "The old CSV exporter stays as a fallback until 3 Nov 2026 because two customers still pull from it.",
    category: "decision", why: "two customers still pull from it", quote: "keep the old CSV exporter as a fallback until 3 Nov",
    subjects: ["csv exporter"] },
  { statement: "Any change that touches billing needs a review from Dana before merge.", category: "rule", why: null,
    quote: "any change that touches billing gets a review from Dana before merge", subjects: ["billing"] },
];

const STOP = new Set(("the a an is are was were be been to of in on at for and or but so it its this that with from by as we i you he she they " +
  "our my your their them us will would just now then than also any all some not no do does did have has had into out up about").split(" "));
const words = (text: string) => text.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length > 1 && !STOP.has(w));
const MOVED = /\b(moved?|moves|changed?|changes|instead|no longer|now)\b/i;
const DURABLE = /\b(decided|decide|will|won't|always|never|must|from now on|because|keep|keeps|moved?|moves|cap(ped)?|uses?|needs?|requires?|depends?)\b/i;

/** Sentences worth keeping from text that is not the snippet, as the rule above reads them. */
function readText(content: string): Reading[] {
  const sentences = content
    .split(/\n+/)
    .map((line) => line.replace(/^\s*[\w .'-]{1,40}:\s+/, "").trim()) // "Priya: ..." -> "..."
    .flatMap((line) => line.split(/(?<=[.!?;])\s+/))
    .map((s) => s.replace(/^[-*•]\s*/, "").trim())
    .filter((s) => s.split(/\s+/).length >= 5 && DURABLE.test(s) && !s.endsWith("?"));
  return sentences.slice(0, 5).map((s) => {
    const statement = s.charAt(0).toUpperCase() + s.slice(1).replace(/[;,]$/, ".").replace(/([^.!])$/, "$1.");
    const category: KnowledgeCategory = /\b(always|never|must|from now on)\b/i.test(s) ? "rule"
      : /\b(decided|will|keep|moved?|moves)\b/i.test(s) ? "decision" : "fact";
    const because = s.match(/\bbecause\b\s+(.*)$/i)?.[1]?.replace(/[.;]$/, "") ?? null;
    return { statement, category, why: because, quote: s.slice(0, 200), subjects: words(s).slice(0, 2) };
  });
}

/** How many of a's content words b shares, as a share of the smaller set. */
function overlap(a: string, b: string): number {
  const x = new Set(words(a)), y = new Set(words(b));
  if (!x.size || !y.size) return 0;
  return [...x].filter((w) => y.has(w)).length / Math.min(x.size, y.size);
}

export function simulateInstant(data: ProjectData, content: string, graphOf: (d: ProjectData) => MemoryGraph):
  { sourceId: string; memories: MemoryRelated[] } {
  const now = new Date().toISOString();
  const sourceId = `paste-${randomBytes(4).toString("hex")}`;
  data.episodes.push({ id: sourceId, identity: null, occurredAt: now, label: "Pasted in the console", content });
  const snippet = isPasteSnippet(content);
  const readings = snippet ? SNIPPET_READING : readText(content);

  // The snippet's work gets a thread page, as the thread step would write it.
  let thread: MockThread | null = null;
  if (snippet) {
    thread = data.threads.find((t) => t.key === "topic:invoice-export") ?? null;
    if (!thread) {
      thread = {
        id: `thr_${randomBytes(6).toString("hex")}`, kind: "topic", key: "topic:invoice-export", title: "Invoice export reliability",
        status: "open", summary: "The nightly invoice export timed out on large accounts; retries are now capped at 3 per batch and spaced 30 seconds apart.",
        firstSeenAt: now, lastActivityAt: now, sessionCount: 1, relatedIds: [], sourceIds: [],
        sections: [{ label: "Decisions", items: [{ text: "Cap export retries at 3 per batch", date: now.slice(0, 10),
                                                   evidence: [{ episodeId: sourceId, quote: "capped export retries at 3 per batch" }] }] }],
      };
      data.threads.push(thread);
    }
    thread.sourceIds.push(sourceId);
    thread.lastActivityAt = now;
  }

  const touched: string[] = [];
  const made: MemoryItem[] = [];
  data.links ??= [];
  for (const r of readings) {
    const evidence = { episodeId: sourceId, quote: r.quote, identity: null };
    // Said before in the same words: the old memory gains this source (reconcile's "same").
    const same = data.memories.find((m) => (m.status === "active" || m.status === "stale") && m.statement === r.statement);
    if (same) {
      same.evidence.push(evidence);
      touched.push(same.id);
      made.push(same);
      continue;
    }
    const item: MemoryItem = {
      id: `mem_${randomBytes(6).toString("hex")}`, kind: r.category === "rule" ? "procedural" : "semantic", statement: r.statement,
      subjects: r.subjects, validFrom: now, validTo: null, recordedAt: now, status: "active", supersedes: null, supersededBy: null,
      assertion: "stated", confidence: "high", audience: "project", evidence: [evidence], conflictsWith: [], staleReason: null,
      scope: "project", category: r.category, why: r.why, threadId: r.inThread && thread ? thread.id : null,
    };
    let target: MemoryItem | undefined = r.extends !== undefined ? made[r.extends] : undefined;
    let relation: "extends" | "supersedes" = "extends";
    if (!target && !snippet) {
      // The closest live memory, when it shares most of its words with this one.
      const best = data.memories
        .filter((m) => m.status === "active" || m.status === "stale")
        .map((m) => ({ m, score: overlap(r.statement, m.statement) }))
        .sort((a, b) => b.score - a.score)[0];
      if (best && best.score >= 0.5) {
        target = best.m;
        relation = MOVED.test(r.statement) ? "supersedes" : "extends";
      }
    }
    if (target && relation === "supersedes") {
      Object.assign(item, { supersedes: target.id, subjects: target.subjects, threadId: item.threadId ?? target.threadId ?? null });
      Object.assign(target, { status: "superseded", supersededBy: item.id, validTo: now });
    } else if (target) {
      data.links.push({ from: item.id, to: target.id, kind: "extends" });
    }
    data.memories.push(item);
    touched.push(item.id);
    made.push(item);
  }

  const graph = graphOf(data);
  const memories = [...new Set(touched)].map((id) => relatedFromGraph(graph, id)).filter((m): m is MemoryRelated => Boolean(m));
  return { sourceId, memories: orderForDrawing(memories) };
}

/** A memory after those it supersedes or extends, as the server orders them. */
function orderForDrawing(memories: MemoryRelated[]): MemoryRelated[] {
  const byId = new Map(memories.map((m) => [m.memory.id, m]));
  const out: MemoryRelated[] = [];
  const placed = new Set<string>();
  const place = (m: MemoryRelated, path: Set<string>) => {
    if (placed.has(m.memory.id) || path.has(m.memory.id)) return;
    for (const e of m.edges) {
      const other = e.item && byId.get(e.item.id);
      if (e.direction === "out" && other) place(other, new Set([...path, m.memory.id]));
    }
    placed.add(m.memory.id);
    out.push(m);
  };
  for (const m of memories) place(m, new Set());
  return out;
}
