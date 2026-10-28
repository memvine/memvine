// A memory's history (GET /v1/memories/{id}/history) as a timeline, and the labels the Activity page shares.
import Link from "next/link";
import type { MemoryEventItem, MemoryEventKind } from "@/lib/types";
import { formatDate } from "./ui";

export const EVENT_LABEL: Record<MemoryEventKind, string> = {
  created: "Learned",
  restated: "Said again",
  superseded: "Replaced",
  supersedes: "Replaces an older fact",
  contradicted: "Contradicted",
  extended: "Extended",
  confirmed: "Confirmed as true",
  forgotten: "Forgotten",
  restored: "Restored",
  expired: "Expired",
  corrected: "Corrected as wrong",
  merged: "Merged",
  anchored: "Anchored to code",
  stale: "Marked possibly outdated",
  revalidated: "Current again",
  category_changed: "Category set",
  refused: "Not learned again",
  deleted: "Deleted with its source",
  project_settings: "Memory rules changed",
  unmerged: "Un-merged",
  flagged: "Flagged for review",
  unflagged: "Flag cleared",
};

/** Events that end or question a memory's currency, drawn in the warning color. */
const WARN: MemoryEventKind[] = ["superseded", "contradicted", "forgotten", "expired", "corrected", "stale", "deleted", "refused", "flagged"];

const FLAG_LABEL: Record<string, string> = {
  no_longer_passes: "may no longer pass the bar for memory",
  possible_duplicate: "may repeat another memory",
  possible_outdated: "a newer memory may replace it",
};

export function actorLabel(actor: string): string {
  if (actor === "synthesis") return "synthesis";
  if (actor === "system") return "Memvine";
  if (actor === "consolidation") return "consolidation";
  if (actor.startsWith("person:")) return actor.slice("person:".length);
  if (actor === "key:operator") return "operator key";
  if (actor.startsWith("key:")) return `key “${actor.slice(4)}”`;
  return actor;
}

const ROLE_LABEL: Record<string, string> = { by: "by", of: "replaces", with: "against", into: "into", from: "from", related: "" };

/** One line of detail beyond the label: a reason, files, a category change. */
export function eventNote(e: MemoryEventItem): string | null {
  const d = e.detail;
  if (typeof d.reason === "string" && e.kind !== "refused") return d.reason;
  if (e.kind === "refused") return "a source said it again after it was forgotten";
  if (e.kind === "anchored" && Array.isArray(d.files)) return (d.files as string[]).join(", ");
  if (e.kind === "project_settings") {
    if (d.field === "memory_instructions") return `instructions ${d.after_len ? `set (${d.after_len} characters)` : "cleared"}`;
    if (d.field === "category") return `category ${d.slug} ${d.action}`;
  }
  if (e.kind === "flagged" && typeof d.flag === "string") return FLAG_LABEL[d.flag] ?? d.flag;
  if (e.kind === "unflagged") return d.reason === "kept" ? "kept by a person" : "dismissed";
  if (e.kind === "category_changed") return `${d.before ?? "none"} → ${d.after ?? "none"}`;
  if (e.kind === "restated" && typeof d.confidence === "string") return `confidence now ${d.confidence}`;
  if (d.backfill) return "reconstructed from the memory's record";
  return null;
}

export function MemoryTimeline({ events, linkFor }: { events: MemoryEventItem[]; linkFor: (id: string) => string }) {
  if (events.length === 0) return <p className="text-[13px] text-text-3">No recorded history yet.</p>;
  return (
    <ol>
      {events.map((e, i) => {
        const note = eventNote(e);
        return (
          <li key={e.id ?? `computed-${i}`} className="relative flex gap-3 pb-4 last:pb-0">
            <div className="flex flex-col items-center pt-1.5">
              <span className={`size-2 rounded-full ${WARN.includes(e.kind) ? "bg-warn" : e.kind === "created" ? "bg-good" : "bg-border-strong"}`} />
              {i < events.length - 1 && <span className="mt-1 w-px flex-1 bg-border" />}
            </div>
            <div className="min-w-0">
              <p className="text-[13px] text-text">
                <span className="font-medium">{EVENT_LABEL[e.kind] ?? e.kind}</span>
                <span className="text-text-3"> · {actorLabel(e.actor)}</span>
              </p>
              <p className="mt-0.5 text-[12px] text-text-3">{formatDate(e.at, true)}</p>
              {note && <p className="mt-1 text-[12.5px] text-text-2">{note}</p>}
              {e.quotes.map((q) => (
                <p key={q} className="mt-1.5 border-l-2 border-border-strong pl-2.5 text-[12.5px] text-text-2">“{q}”</p>
              ))}
              {e.related.map((r) => (
                <p key={r.role + r.id} className="mt-1 text-[12.5px] text-text-2">
                  {ROLE_LABEL[r.role] ? <span className="text-text-3">{ROLE_LABEL[r.role]} </span> : null}
                  <Link href={linkFor(r.id)} scroll={false} className="hover:underline">{r.statement}</Link>
                  <span className="text-text-3"> ({r.status})</span>
                </p>
              ))}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
