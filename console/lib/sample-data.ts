// The sample memory for mock mode: the same sources and memories the server writes for
// POST /v1/sample (server/memvine_server/sample.py), with dates relative to now. Sample source ids
// start with "sample:", which is how the mock tells them apart.
import type { MockThread } from "./mock-data";
import type { Episode, KnowledgeCategory, MemoryItem, MemoryKind, ThreadKind, ThreadStatus } from "./types";

export const SAMPLE_PREFIX = "sample:";

/** As server/memvine_server/sample.py: the threads of work the sample's knowledge belongs to. */
const SAMPLE_THREADS: Record<string, [ThreadKind, string, ThreadStatus, string]> = {
  "topic:public-launch": ["topic", "Public launch", "open", "The public launch date and what moved it: legal pushed back on the refund copy."],
  "release:4.2.0": ["release", "Release 4.2.0", "open", "Scope of release 4.2.0 and how release work is merged."],
  "topic:invoice-parsing": ["topic", "Invoice parsing", "done", "How invoice fields the prompt misses are mapped; InvoiceFieldAliases was kept, then dropped."],
  "topic:support-escalations": ["topic", "Support escalations", "open", "Where escalations go and what happens on weekends; Sam and Dana disagree."],
};
const THREAD_OF: Record<string, string> = {
  launch_old: "topic:public-launch", launch_new: "topic:public-launch", release: "release:4.2.0", merges: "release:4.2.0",
  synonyms_old: "topic:invoice-parsing", synonyms_new: "topic:invoice-parsing",
  escalations: "topic:support-escalations", weekend_wait: "topic:support-escalations", weekend_page: "topic:support-escalations",
};

interface SampleMemory {
  key: string;
  statement: string;
  kind: MemoryKind;
  category: KnowledgeCategory;
  subjects: string[];
  quote: string;
  why?: string;
  standing?: boolean;
  supersedes?: string;
  conflictsWith?: string;
}

interface SampleSource {
  id: string;
  daysAgo: number;
  label: string;
  turns: [string, string][];
  memories: SampleMemory[];
}

function sources(now: Date): SampleSource[] {
  const day = (d: number) => new Date(now.getTime() + d * 86_400_000).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  const launchOld = day(4), launchNew = day(18);
  return [
    {
      id: "sample:kickoff", daysAgo: 21, label: "Sample · kickoff with Priya",
      turns: [
        ["Priya", `Target public launch is ${launchOld}. Refunds over $500 always need a human approver; the support bot may only draft them. And never deploy on Fridays. I prefer async review over meetings.`],
        ["Agent", `Noted: launch on ${launchOld}, refunds above $500 go to a human approver, no Friday deploys, and reviews happen async.`],
      ],
      memories: [
        { key: "launch_old", statement: `The public launch is planned for ${launchOld}.`, kind: "semantic", category: "decision", subjects: ["launch"], quote: `Target public launch is ${launchOld}.` },
        { key: "refunds", statement: "Refunds over $500 always need a human approver; the support bot may only draft them.", kind: "procedural", category: "rule",
          subjects: ["refunds"], quote: "Refunds over $500 always need a human approver", why: "Large refunds carry fraud and chargeback risk." },
        { key: "fridays", statement: "Never deploy on Fridays.", kind: "procedural", category: "rule", subjects: ["deploys"], quote: "never deploy on Fridays", standing: true },
        { key: "async", statement: "Priya prefers async review over meetings.", kind: "preference", category: "preference", subjects: ["priya"], quote: "I prefer async review over meetings." },
      ],
    },
    {
      id: "sample:invoice-parsing-1", daysAgo: 17, label: "Sample · invoice parsing",
      turns: [
        ["Priya", "The prompt misses some invoice fields. Keep the InvoiceFieldAliases class as a fallback for now."],
        ["Agent", "Kept InvoiceFieldAliases; it maps field names the prompt does not recognize."],
      ],
      memories: [
        { key: "synonyms_old", statement: "Keep the InvoiceFieldAliases class as a fallback for invoice fields the prompt misses.", kind: "semantic", category: "decision",
          subjects: ["invoice parsing"], quote: "Keep the InvoiceFieldAliases class as a fallback for now." },
      ],
    },
    {
      id: "sample:launch-update", daysAgo: 12, label: "Sample · launch update",
      turns: [
        ["Priya", `Legal pushed back on the refund copy, so we're moving the launch from ${launchOld} to ${launchNew}.`],
        ["Agent", `Updated the release checklist to ${launchNew} and flagged the pricing page copy as blocked on legal.`],
      ],
      memories: [
        { key: "launch_new", statement: `The public launch moved to ${launchNew} because legal pushed back on the refund copy.`, kind: "semantic", category: "decision",
          subjects: ["launch"], quote: `we're moving the launch from ${launchOld} to ${launchNew}`, why: "Legal pushed back on the refund copy.", supersedes: "launch_old" },
      ],
    },
    {
      id: "sample:release-4.2.0", daysAgo: 6, label: "Sample · release/4.2.0",
      turns: [
        ["Priya", "Scope for 4.2.0: PDF invoice export (#429), multi-currency totals (#430 and #437) and payment reminder emails (#434). Multi-language invoices wait for 4.2.1. Release work never pushes to main; only I merge release branches."],
        ["Agent", "Understood. I'll work on release/4.2.0 and feature branches and open pull requests for you to merge."],
      ],
      memories: [
        { key: "release", statement: "Release 4.2.0 ships PDF invoice export (#429), multi-currency totals (#430, #437) and payment reminder emails (#434); multi-language invoices wait for 4.2.1.",
          kind: "semantic", category: "decision", subjects: ["release 4.2.0"], quote: "Scope for 4.2.0: PDF invoice export (#429)" },
        { key: "merges", statement: "Only Priya merges release work; agents open pull requests and never push release branches to main.", kind: "procedural", category: "rule",
          subjects: ["releases", "priya"], quote: "Release work never pushes to main; only I merge release branches." },
      ],
    },
    {
      id: "sample:invoice-parsing-2", daysAgo: 5, label: "Sample · invoice parsing",
      turns: [
        ["Priya", "Drop the InvoiceFieldAliases class. The prompt already maps 'bill to' to 'customer name' and the table keeps drifting from it. Rely on the prompt."],
        ["Agent", "Removed InvoiceFieldAliases and its tests in #442; field aliases now live only in the invoice prompt."],
      ],
      memories: [
        { key: "synonyms_new", statement: "Drop InvoiceFieldAliases (done in #442): invoice field aliases live only in the LLM prompt, because the alias table kept drifting from it.",
          kind: "semantic", category: "decision", subjects: ["invoice parsing"], quote: "Drop the InvoiceFieldAliases class.", why: "The alias table kept drifting from the prompt.",
          supersedes: "synonyms_old" },
      ],
    },
    {
      id: "sample:escalations", daysAgo: 3, label: "Sample · support escalations",
      turns: [
        ["Sam", "Escalations should go to the #support-tier2 Slack channel, not email: tier 2 watches Slack and email sat unread. On weekends they wait in the channel until Monday; nobody gets paged."],
        ["Agent", "Switched escalation routing to #support-tier2."],
      ],
      memories: [
        { key: "escalations", statement: "Escalations go to the #support-tier2 Slack channel, not email.", kind: "procedural", category: "rule", subjects: ["escalations"],
          quote: "Escalations should go to the #support-tier2 Slack channel, not email", why: "Tier 2 watches Slack; email sat unread." },
        { key: "weekend_wait", statement: "Weekend escalations wait in #support-tier2 until Monday; nobody is paged.", kind: "procedural", category: "rule", subjects: ["escalations"],
          quote: "On weekends they wait in the channel until Monday" },
      ],
    },
    {
      id: "sample:on-call", daysAgo: 1, label: "Sample · on-call review",
      turns: [
        ["Dana", "Weekend escalations page the on-call engineer right away. A customer waited two days last month."],
        ["Agent", "Noted."],
      ],
      memories: [
        { key: "weekend_page", statement: "Weekend escalations page the on-call engineer right away.", kind: "procedural", category: "rule", subjects: ["escalations"],
          quote: "Weekend escalations page the on-call engineer right away.", conflictsWith: "weekend_wait" },
      ],
    },
  ];
}

/** The sample's sources, memories (with their supersede chain and contradiction) and standing rules. */
export function buildSample(now = new Date()): { episodes: Episode[]; memories: MemoryItem[]; standing: string[]; threads: MockThread[] } {
  const base = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 10));
  const episodes: Episode[] = [];
  const byKey = new Map<string, MemoryItem>();
  const standing: string[] = [];
  const threads = new Map<string, MockThread>();
  /** The sample thread `key`, created on first use; each source that feeds it is one of its sources. */
  const threadFor = (key: string, episodeId: string, iso: string): string => {
    let t = threads.get(key);
    if (!t) {
      const [kind, title, status, summary] = SAMPLE_THREADS[key];
      t = { id: `thr_${SAMPLE_PREFIX.replace(":", "")}_${key.replace(/[^a-z0-9]+/gi, "_")}`, kind, key, title, status, summary,
            firstSeenAt: iso, lastActivityAt: iso, sessionCount: 0, sections: [], relatedIds: [], sourceIds: [] };
      threads.set(key, t);
    }
    if (!t.sourceIds.includes(episodeId)) { t.sourceIds.push(episodeId); t.sessionCount += 1; }
    if (iso > t.lastActivityAt) t.lastActivityAt = iso;
    return t.id;
  };
  for (const src of sources(base)) {
    const at = new Date(base.getTime() - src.daysAgo * 86_400_000);
    const iso = at.toISOString();
    episodes.push({
      // Who said it: the source's first speaker, as the server records it.
      id: src.id, identity: src.turns[0][0], occurredAt: iso, label: src.label,
      content: src.turns.map(([who, text], i) => `## ${who} · ${new Date(at.getTime() + i * 60_000).toISOString().slice(0, 16)}\n${text}`).join("\n\n"),
    });
    for (const m of src.memories) {
      const item: MemoryItem = {
        id: `mem_${src.id.replace(/[^a-z0-9]+/gi, "_")}_${m.key}`, kind: m.kind, statement: m.statement, subjects: m.subjects,
        validFrom: iso, validTo: null, recordedAt: iso, status: "active", supersedes: null, supersededBy: null,
        assertion: "stated", confidence: "high", audience: "project", evidence: [{ episodeId: src.id, quote: m.quote, identity: src.turns[0][0] }],
        conflictsWith: [], category: m.category, why: m.why ?? null, scope: "project",
        threadId: THREAD_OF[m.key] ? threadFor(THREAD_OF[m.key], src.id, iso) : null,
      };
      const old = m.supersedes ? byKey.get(m.supersedes) : undefined;
      if (old) {
        item.supersedes = old.id;
        Object.assign(old, { status: "superseded", supersededBy: item.id, validTo: iso });
      }
      const other = m.conflictsWith ? byKey.get(m.conflictsWith) : undefined;
      if (other) {
        item.conflictsWith = [other.id];
        other.conflictsWith = [...(other.conflictsWith ?? []), item.id];
      }
      if (m.standing) standing.push(m.statement);
      byKey.set(m.key, item);
    }
  }
  return { episodes, memories: [...byKey.values()], standing, threads: [...threads.values()] };
}
