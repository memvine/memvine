// How a fact's provenance reads to a person ("Shared decisions" in docs/memory-graph-plan.md):
// `assertion` renders as plain words, never as a badge alone.
import type { MemoryItem } from "./types";

/** "priya@example.com" -> "Priya"; "sam.okafor" -> "Sam Okafor"; "Dana" -> "Dana". */
export function personName(identity: string): string {
  const local = identity.split("@")[0].replace(/[._-]+/g, " ").trim();
  if (!local) return identity;
  return local.split(/\s+/).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

/** The first identity among a memory's evidence: who said it. */
export function speakerOf(m: Pick<MemoryItem, "evidence">): string | null {
  return m.evidence.find((e) => e.identity)?.identity ?? null;
}

/** observed -> "seen in a run"; stated -> "Priya said"; claimed -> "assistant said, unverified"; inferred -> "inferred". */
export function trustWords(m: Pick<MemoryItem, "assertion" | "evidence">): string {
  switch (m.assertion) {
    case "observed":
      return "seen in a run";
    case "stated": {
      const who = speakerOf(m);
      return who ? `${personName(who)} said` : "stated";
    }
    case "claimed":
      return "assistant said, unverified";
    case "inferred":
      return "inferred";
  }
}

/** Two initials for a person node. */
export function initials(identity: string): string {
  const parts = personName(identity).split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}
