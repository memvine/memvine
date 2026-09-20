import type { Memory } from "./schema.js";

/** Clip at a Unicode code-point boundary while enforcing a UTF-8 byte limit. */
export function clipBytes(text: string, budget: number): string {
  let result = "";
  let bytes = 0;
  for (const char of text) {
    const size = Buffer.byteLength(char);
    if (bytes + size > budget) break;
    result += char;
    bytes += size;
  }
  return result;
}

export function renderMemory(memory: Memory, source: string): string {
  const m = memory.meta;
  return `[${m.id}] (${m.kind}, ${m.status}, confidence=${m.confidence}` +
    (m.verified ? "" : ", UNVERIFIED candidate") +
    (memory.freshness === "unknown" ? ", freshness UNKNOWN: Git history unavailable" : "") +
    `, scope=${m.scope.join(",") || "repo-wide"}, via=${source}, validated@${m.validated_commit})\n${memory.body}`;
}

export function renderRecall(memories: Memory[], sources: Record<string, string>, omitted: number): string {
  const body = memories.map(m => renderMemory(m, sources[m.meta.id])).join("\n\n---\n\n");
  const notice = omitted ? `\n\n(${omitted} omitted; narrow the query or increase the budget.)` : "";
  return (body || "No memories found.") + notice;
}
