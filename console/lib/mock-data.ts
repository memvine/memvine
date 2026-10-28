// Seed data for the mock backend: two demo projects with realistic memory,
// sessions, requests and usage. Used until the Memvine server exists.
import type { FileAnchor, MemoryLink } from "./graph";
import type { ApiKey, Audience, Episode, Identity, Member, MemoryItem, Project, RequestLog, SessionSummary, ThreadDetail, UsageDay } from "./types";

/** A thread as the mock stores it; related threads, knowledge and sources are resolved on read. */
export interface MockThread extends Omit<ThreadDetail, "related" | "knowledge" | "knowledgeCount" | "sources"> {
  relatedIds: string[];
  sourceIds: string[];
}

export interface ProjectData {
  keys: ApiKey[];
  memories: MemoryItem[];
  episodes: Episode[];
  threads: MockThread[];
  /** Episode ids whose synthesis job failed, so the Sources screen can show Retry. */
  failedEpisodes: Set<string>;
  /** Episodes still being synthesized: [synthesis starts, done], in seconds after the mock store is seeded. */
  pendingEpisodes?: Record<string, [number, number]>;
  /** Standing instructions, included in every recall. */
  standing?: string[];
  /** Files memories are anchored to (memory_anchors), for the graph's anchored edges. */
  anchors?: FileAnchor[];
  /** extends and derived links between memories (memory_links). */
  links?: MemoryLink[];
  sessions: SessionSummary[];
  audiences: Audience[];
  identities: Identity[];
  requests: RequestLog[];
  usage: UsageDay[];
}

export const seedProjects: Project[] = [
  { slug: "coding-team", name: "Coding team", createdAt: "2026-10-01T09:30:00Z", memoryCount: 0, tags: ["engineering"], sourceCount: 0, keyCount: 0, lastActivityAt: "2026-10-26T07:12:03Z", monthlyCreditCap: null, retentionDays: null, monthCredits: 0,
    description: "The billing app team: releases, review fixes and the gotchas of our payments sandbox and CI setup.",
    memoryInstructions: "Also keep: on-call runbook steps for the Northwind billing service.\nNever keep: customer names or ticket text from the support desk.",
    categories: [{ slug: "runbook", name: "Runbook step", description: "What on-call does when a billing alert fires.", createdAt: "2026-10-02T09:00:00Z" }] },
  { slug: "support-agent", name: "Support agent", createdAt: "2026-09-02T10:00:00Z", memoryCount: 0, tags: ["production", "support"], sourceCount: 0, keyCount: 0, lastActivityAt: "2026-10-25T08:41:12Z", monthlyCreditCap: 20000, retentionDays: 90, monthCredits: 0,
    description: "The customer support bot: refund policy, escalations and the public launch." },
  { slug: "docs-bot", name: "Docs bot", createdAt: "2026-08-12T09:00:00Z", memoryCount: 0, tags: ["support"], sourceCount: 0, keyCount: 0, lastActivityAt: "2026-10-26T06:58:00Z", monthlyCreditCap: null, retentionDays: null, monthCredits: 0,
    description: "The docs site's chat widget: answers about SSO, rate limits and webhooks." },
];

export const seedMembers: Member[] = [
  { id: "u_1", name: "Vishal Gupta", email: "hello@memvine.dev", role: "owner", joinedAt: "2026-09-01T00:00:00Z", lastLoginAt: null },
  { id: "u_2", name: "Priya Raman", email: "priya@example.com", role: "admin", joinedAt: "2026-09-10T00:00:00Z", lastLoginAt: null },
  { id: "u_3", name: "Sam Okafor", email: "sam@example.com", role: "member", joinedAt: "2026-10-04T00:00:00Z", lastLoginAt: null },
];

function usageSeries(days: number, ingestBase: number, recallBase: number, seed: number): UsageDay[] {
  const out: UsageDay[] = [];
  const end = Date.parse("2026-10-25T00:00:00Z");
  let x = seed;
  for (let i = days - 1; i >= 0; i--) {
    // Deterministic pseudo-random wobble so the chart is stable between renders.
    x = (x * 9301 + 49297) % 233280;
    const wobble = x / 233280;
    const weekday = new Date(end - i * 86_400_000).getUTCDay();
    const weekend = weekday === 0 || weekday === 6 ? 0.35 : 1;
    out.push({
      date: new Date(end - i * 86_400_000).toISOString().slice(0, 10),
      ingest: Math.round(ingestBase * weekend * (0.6 + wobble * 0.8)),
      recall: Math.round(recallBase * weekend * (0.7 + wobble * 0.6)),
      ingestCredits: Math.round(ingestBase * 12 * weekend * (0.6 + wobble * 0.8)),
      recallCredits: Math.round(recallBase * weekend * (0.7 + wobble * 0.6)) / 10,
    });
  }
  return out;
}

const supportAgent: ProjectData = {
  keys: [
    { id: "key_01", name: "production", masked: "mv_sk_••••7f2a", createdAt: "2026-09-02T10:05:00Z", lastUsedAt: "2026-10-25T08:41:00Z", revokedAt: null, lastUserAgent: "memvine-sdk-ts/0.1.0" },
    { id: "key_02", name: "staging", masked: "mv_sk_••••c91e", createdAt: "2026-09-12T15:20:00Z", lastUsedAt: "2026-10-24T19:02:00Z", revokedAt: null, lastUserAgent: "memvine-claude-code/0.6.4" },
    { id: "key_03", name: "old laptop", masked: "mv_sk_••••0b44", createdAt: "2026-09-03T08:00:00Z", lastUsedAt: "2026-09-20T11:00:00Z", revokedAt: "2026-09-21T09:00:00Z" },
  ],
  episodes: [
    {
      id: "ep_1001", identity: "priya@example.com", occurredAt: "2026-10-03T14:02:00Z",
      label: "Claude Code · support-agent · main",
      content: "## Developer · 2026-10-03T14:02\nLegal pushed back on the refund copy, so we're moving launch from 29 Oct to 12 Nov.\n\n## Claude · 2026-10-03T14:03\nUnderstood. I'll update the release checklist to 12 Nov and flag the pricing page copy as blocked on legal.",
    },
    {
      id: "ep_0990", identity: "priya@example.com", occurredAt: "2026-09-18T10:11:00Z",
      label: "Claude Code · support-agent · main",
      content: "## Developer · 2026-09-18T10:11\nTarget launch is 29 Oct. Refunds over $500 always need a human approver, the bot can only draft them.\n\n## Claude · 2026-09-18T10:12\nNoted: launch 29 Oct, and refunds above $500 route to a human approver.",
    },
    {
      id: "ep_0975", identity: "priya@example.com", occurredAt: "2026-09-14T09:00:00Z",
      label: "Team preferences",
      content: "Priya prefers async review over meetings. No meetings on Fridays.",
    },
    {
      id: "ep_1010", identity: "sam@example.com", occurredAt: "2026-10-21T16:40:00Z",
      label: "Claude Code · support-agent · feat/escalation",
      content: "## Developer · 2026-10-21T16:40\nEscalations should go to the #support-tier2 channel, not email. Zendesk webhook retries 3 times with a 10s backoff.\n\n## Claude · 2026-10-21T16:41\nSwitched escalation to #support-tier2. Implementation complete, all tests pass.\n\n## Developer · 2026-10-21T16:44\nThe 10s backoff is there because Zendesk rate-limits webhook bursts. On weekends escalations wait in the channel until Monday.",
    },
    {
      id: "ep_1015", identity: "priya@example.com", occurredAt: "2026-10-24T09:20:00Z",
      label: "Support review · weekend coverage",
      content: "## Priya · 2026-10-24T09:20\nWeekend escalations should page the on-call engineer. A customer waited two days last month.",
    },
  ],
  memories: [
    {
      id: "mem_launch_2", category: "decision", threadId: "thr_launch", why: "Legal pushed back on the refund copy.", kind: "semantic", statement: "Launch date is 12 Nov 2026, moved from 29 Oct because legal pushed back on the refund copy.",
      subjects: ["launch", "legal"], validFrom: "2026-10-03T14:02:00Z", validTo: null, recordedAt: "2026-10-03T14:02:00Z",
      status: "active", supersedes: "mem_launch_1", supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_1001", quote: "we're moving launch from 29 Oct to 12 Nov" }],
    },
    {
      id: "mem_launch_1", category: "decision", threadId: "thr_launch", why: null, kind: "semantic", statement: "Launch date is 29 Oct 2026.",
      subjects: ["launch"], validFrom: "2026-09-18T10:11:00Z", validTo: "2026-10-03T14:02:00Z", recordedAt: "2026-09-18T10:11:00Z",
      status: "superseded", supersedes: null, supersededBy: "mem_launch_2", assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_0990", quote: "Target launch is 29 Oct." }],
    },
    {
      id: "mem_refund", category: "rule", threadId: null, why: "Large refunds carry fraud and chargeback risk.", kind: "procedural", statement: "Refunds over $500 always need a human approver; the bot may only draft them.",
      subjects: ["refund policy"], validFrom: "2026-09-18T10:11:00Z", validTo: null, recordedAt: "2026-09-18T10:11:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_0990", quote: "Refunds over $500 always need a human approver, the bot can only draft them." }],
    },
    {
      id: "mem_priya_pref", category: "preference", threadId: null, why: null, kind: "preference", statement: "Priya prefers async review over meetings and keeps Fridays meeting-free.",
      subjects: ["priya raman"], validFrom: "2026-09-14T09:00:00Z", validTo: null, recordedAt: "2026-09-14T09:00:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_0975", quote: "Priya prefers async review over meetings. No meetings on Fridays." }],
    },
    {
      id: "mem_pricing_open", threadId: "thr_launch", kind: "prospective", statement: "Pricing page copy is blocked on legal review; update it before the 12 Nov launch.",
      subjects: ["launch", "legal"], validFrom: "2026-10-03T14:03:00Z", validTo: null, recordedAt: "2026-10-03T14:03:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "medium", audience: "project",
      evidence: [{ episodeId: "ep_1001", quote: "flag the pricing page copy as blocked on legal" }],
    },
    {
      id: "mem_escalation", category: "rule", threadId: "thr_escalation", why: "Tier-2 watches Slack; email sat unread over weekends.", kind: "procedural", statement: "Escalations go to the #support-tier2 Slack channel, not email.",
      subjects: ["escalations"], validFrom: "2026-10-21T16:40:00Z", validTo: null, recordedAt: "2026-10-21T16:40:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_1010", quote: "Escalations should go to the #support-tier2 channel, not email." }],
    },
    {
      id: "mem_zendesk_retry", category: "fact", threadId: "thr_escalation", why: null, kind: "semantic", statement: "The Zendesk webhook retries 3 times with a 10-second backoff.",
      subjects: ["zendesk webhook"], validFrom: "2026-10-21T16:40:00Z", validTo: null, recordedAt: "2026-10-21T16:40:00Z",
      status: "stale", staleReason: "src/escalation/webhook.ts changed since 21 Oct 2026", supersedes: null, supersededBy: null, assertion: "stated", confidence: "medium", audience: "project",
      evidence: [{ episodeId: "ep_1010", quote: "Zendesk webhook retries 3 times with a 10s backoff." }],
    },
    {
      id: "mem_tests_claim", kind: "episodic", statement: "The agent claimed the escalation change was complete with all tests passing (not verified).",
      subjects: ["escalations"], validFrom: "2026-10-21T16:41:00Z", validTo: null, recordedAt: "2026-10-21T16:41:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "claimed", confidence: "low", audience: "project",
      evidence: [{ episodeId: "ep_1010", quote: "Implementation complete, all tests pass." }],
    },
    {
      id: "mem_backoff_why", category: "fact", threadId: "thr_escalation", why: null, kind: "semantic",
      statement: "The 10-second webhook backoff exists because Zendesk rate-limits webhook bursts.",
      subjects: ["zendesk webhook"], validFrom: "2026-10-21T16:44:00Z", validTo: null, recordedAt: "2026-10-21T16:44:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "medium", audience: "project",
      evidence: [{ episodeId: "ep_1010", quote: "The 10s backoff is there because Zendesk rate-limits webhook bursts." }],
    },
    {
      id: "mem_weekend_wait", category: "rule", threadId: "thr_escalation", why: null, kind: "procedural",
      statement: "Weekend escalations wait in #support-tier2 until Monday; nobody is paged.",
      subjects: ["escalations"], validFrom: "2026-10-21T16:44:00Z", validTo: null, recordedAt: "2026-10-21T16:44:00Z", conflictsWith: ["mem_weekend_page"],
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "medium", audience: "project",
      evidence: [{ episodeId: "ep_1010", quote: "On weekends escalations wait in the channel until Monday." }],
    },
    {
      id: "mem_weekend_page", category: "rule", threadId: "thr_escalation", why: "A customer waited two days last month.", kind: "procedural",
      statement: "Weekend escalations page the on-call engineer right away.",
      subjects: ["escalations"], validFrom: "2026-10-24T09:20:00Z", validTo: null, recordedAt: "2026-10-24T09:21:00Z", conflictsWith: ["mem_weekend_wait"],
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_1015", quote: "Weekend escalations should page the on-call engineer." }],
    },
    {
      id: "mem_priya_legal", category: "fact", threadId: "thr_launch", why: null, kind: "semantic",
      statement: "Priya is the one chasing legal sign-off for the launch.",
      subjects: ["launch", "priya raman"], validFrom: "2026-10-03T14:40:00Z", validTo: null, recordedAt: "2026-10-22T08:00:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "inferred", confidence: "low", audience: "project",
      evidence: [{ episodeId: "ep_1001", quote: "Legal pushed back on the refund copy" }, { episodeId: "ep_0990", quote: "Target launch is 29 Oct." }],
    },
  ],
  threads: [
    {
      id: "thr_launch", kind: "release", key: "launch", title: "Public launch", status: "open",
      summary: "Launch moved from 29 Oct to 12 Nov after legal pushed back on the refund copy. Pricing page copy is still blocked on legal review.",
      firstSeenAt: "2026-09-18T10:11:00Z", lastActivityAt: "2026-10-03T14:03:00Z", sessionCount: 2, relatedIds: ["thr_escalation"], sourceIds: ["ep_1001", "ep_0990"],
      sections: [
        { label: "Decisions", items: [
          { text: "Launch on 12 Nov (was 29 Oct)", date: "2026-10-03", evidence: [{ episodeId: "ep_1001", quote: "we're moving launch from 29 Oct to 12 Nov" }] },
        ] },
        { label: "Open questions", items: [
          { text: "When does legal sign off on the pricing page copy?", date: "2026-10-03", evidence: [{ episodeId: "ep_1001", quote: "flag the pricing page copy as blocked on legal" }] },
        ] },
      ],
    },
    {
      id: "thr_escalation", kind: "task", key: "feat/escalation", title: "Route tier-2 escalations to Slack", status: "open",
      summary: "Escalations now go to #support-tier2 instead of email. The agent says it is done with tests passing; nobody has verified that yet.",
      firstSeenAt: "2026-10-21T16:40:00Z", lastActivityAt: "2026-10-25T08:41:00Z", sessionCount: 1, relatedIds: ["thr_launch"], sourceIds: ["ep_1010"],
      sections: [
        { label: "Decisions", items: [
          { text: "Use the #support-tier2 channel, not email", date: "2026-10-21", evidence: [{ episodeId: "ep_1010", quote: "Escalations should go to the #support-tier2 channel, not email." }] },
        ] },
        { label: "Open questions", items: [
          { text: "Should weekend escalations page on-call?", date: "2026-10-25", evidence: [] },
        ] },
      ],
    },
  ],
  sessions: [
    {
      id: "ses_a2", agent: "claude-code", identity: "sam@example.com", label: "Claude Code · support-agent · feat/escalation", status: "live",
      startedAt: "2026-10-25T07:58:00Z", lastEventAt: "2026-10-25T08:41:00Z", turns: 14, stateThrough: 12,
      state: {
        goal: "Route tier-2 escalations to Slack instead of email",
        decisions: ["Use the #support-tier2 channel", "Keep the Zendesk webhook retry policy as is"],
        openQuestions: ["Should weekend escalations page on-call?"],
        inPlay: ["src/escalation/router.ts", "Zendesk webhook"],
        recentFacts: ["Zendesk webhook retries 3 times with a 10 s backoff"],
        progressClaims: ["Implementation complete, all tests pass"],
      },
    },
    {
      id: "ses_a1", agent: "claude-code", identity: "priya@example.com", label: "Claude Code · support-agent · main", status: "synthesized",
      startedAt: "2026-10-03T13:40:00Z", lastEventAt: "2026-10-03T14:30:00Z", turns: 22, stateThrough: 22,
      state: {
        goal: "Update the release checklist for the new launch date",
        decisions: ["Launch moves to 12 Nov"], openQuestions: ["Pricing page copy waiting on legal"], inPlay: ["docs/release-checklist.md"],
        recentFacts: [], progressClaims: [],
      },
    },
  ],
  audiences: [
    { slug: "support-leads", kind: "fixed", members: ["priya@example.com", "sam@example.com"], conditions: [], memoryCount: 0 },
    { slug: "acme", kind: "rule", members: [], conditions: [{ trait: "company", op: "is", value: "acme" }], memoryCount: 0 },
  ],
  identities: [
    { identity: "priya@example.com", traits: { role: "admin", team: "support" }, origin: "ingest", lastSeenAt: "2026-10-03T14:30:00Z" },
    { identity: "sam@example.com", traits: { team: "support" }, origin: "ingest", lastSeenAt: "2026-10-25T08:41:00Z" },
    { identity: "alice@acme.com", traits: { company: "acme" }, origin: "api", lastSeenAt: null },
  ],
  failedEpisodes: new Set(["ep_1010"]),
  anchors: [
    { memoryId: "mem_zendesk_retry", repo: "support-agent", path: "src/escalation/webhook.ts", version: "9b21e4d", currentVersion: "d41e7b0", anchoredAt: "2026-10-21T16:40:00Z" },
    { memoryId: "mem_escalation", repo: "support-agent", path: "src/escalation/router.ts", version: "4c8d0aa", currentVersion: "4c8d0aa", anchoredAt: "2026-10-21T16:41:00Z" },
    { memoryId: "mem_refund", repo: "support-agent", path: "src/refunds/policy.ts", version: "e12f9b3", currentVersion: "e12f9b3", anchoredAt: "2026-09-18T10:12:00Z" },
  ],
  links: [
    { from: "mem_backoff_why", to: "mem_zendesk_retry", kind: "extends" },
    { from: "mem_priya_legal", to: "mem_launch_2", kind: "derived" },
    { from: "mem_priya_legal", to: "mem_pricing_open", kind: "derived" },
    { from: "mem_priya_legal", to: "mem_priya_pref", kind: "derived" },
  ],
  standing: ["Never quote a refund amount to a customer before a human approver signs off."],
  requests: [
    { id: 9001, at: "2026-10-25T08:41:12Z", method: "POST", path: "/v1/recall", status: 200, latencyMs: 412, keyName: "production", detail: "served 3 items, 284 tokens (best relevance 0.712, threshold 0.30)" },
    { id: 9000, at: "2026-10-25T08:40:55Z", method: "POST", path: "/v1/ingest", status: 202, latencyMs: 38, keyName: "production" },
    { id: 8990, at: "2026-10-24T19:02:31Z", method: "POST", path: "/v1/ingest", status: 202, latencyMs: 96, keyName: "staging" },
    { id: 8988, at: "2026-10-24T18:59:02Z", method: "POST", path: "/v1/recall", status: 401, latencyMs: 4, keyName: "old laptop" },
    { id: 8970, at: "2026-10-21T16:52:40Z", method: "GET", path: "/v1/memories", status: 200, latencyMs: 21, keyName: "production" },
    { id: 8950, at: "2026-10-03T14:31:10Z", method: "POST", path: "/v1/ingest", status: 202, latencyMs: 102, keyName: "production" },
  ],
  usage: usageSeries(30, 14, 60, 7),
};

const LEAD = "priya@example.com";
const DEV = "maria@example.com";

const codingTeam: ProjectData = {
  keys: [
    { id: "key_11", name: "team plugin", masked: "mv_sk_••••a3d0", createdAt: "2026-10-01T09:35:00Z", lastUsedAt: "2026-10-26T07:12:00Z", revokedAt: null, lastUserAgent: "memvine-claude-code/0.7.1" },
    { id: "key_12", name: "ci", masked: "mv_sk_••••5be1", createdAt: "2026-10-08T12:00:00Z", lastUsedAt: "2026-10-25T21:40:00Z", lastUsedIp: "10.20.4.18", revokedAt: null, lastUserAgent: "memvine-sdk-python/0.1.0" },
  ],
  episodes: [
    {
      id: "ep_3007", identity: LEAD, occurredAt: "2026-10-25T16:05:00Z",
      label: "Claude Code · billing · release/4.2.0",
      content: "## Developer · 2026-10-25T16:05\nCI is green on release/4.2.0 except the coverage gate, and that one fails on every branch right now. Does master's branch protection block the merge with the coverage gate red? Ask before we try.\n\n## Claude · 2026-10-25T16:06\nI'll leave the merge to you. After it lands: create the payment provider API keys and webhook secrets in each environment, have customers re-confirm their saved cards, and remove the legacy-pdf-export feature flag from QA and staging.",
    },
    {
      id: "ep_3006", identity: DEV, occurredAt: "2026-10-24T11:30:00Z",
      label: "Claude Code · billing · fix/review-447",
      content: "## Developer · 2026-10-24T11:30\nThe preview still shows paid invoices as open. Our payments sandbox does that sometimes; re-send the invoice webhooks from the sandbox console and it syncs.\n\n## Claude · 2026-10-24T11:31\nRe-sent the webhooks; the preview now shows all review fixes #441 through #447.\n\n## Developer · 2026-10-24T11:34\nIt's the sandbox's 24-hour webhook queue; re-sending makes a fresh event.",
    },
    {
      id: "ep_3005", identity: LEAD, occurredAt: "2026-10-23T09:12:00Z",
      label: "Claude Code · billing · release/4.2.0",
      content: "## Developer · 2026-10-23T09:12\nWe're out of time for three things: the invoice footer copy fix A, the 'CHF' currency code fix, and the reminder timezone bug. Push them to 4.2.1.\n\n## Claude · 2026-10-23T09:13\nMoved to 4.2.1: invoice footer copy fix A, 'CHF' currency code fix, reminder timezone bug. 4.2.0 keeps #429, #430/#437, #434, #448 and review fixes #441–#447.",
    },
    {
      id: "ep_3004", identity: LEAD, occurredAt: "2026-10-21T14:40:00Z",
      label: "Claude Code · billing · fix/invoice-field-aliases",
      content: "## Developer · 2026-10-21T14:40\nDrop the InvoiceFieldAliases class. The prompt already maps 'bill to' to 'customer name' and the table keeps drifting from it. Rely on the LLM prompt.\n\n## Claude · 2026-10-21T14:41\nRemoved InvoiceFieldAliases and its tests in #442; field aliases now live only in the invoice prompt.",
    },
    {
      id: "ep_3003", identity: LEAD, occurredAt: "2026-10-20T10:02:00Z",
      label: "Claude Code · billing · release/4.2.0",
      content: "## Developer · 2026-10-20T10:02\nScope for 4.2.0: PDF invoice export (#429), multi-currency totals (#430 and #437), payment reminder emails (#434), and tax region from the billing account (#448). The Slack invoice bot (INV-243) is not launching, and multi-language invoices (INV-211) wait. The reminder timezone fix rides along in 4.2.0 too.\nRelease work never pushes to master or staging; only I merge.\n\n## Claude · 2026-10-20T10:03\nUnderstood. I'll work only on release/4.2.0 and feature branches, and open PRs for you to merge.",
    },
    {
      id: "ep_3002", identity: DEV, occurredAt: "2026-10-18T15:20:00Z",
      label: "Claude Code · billing · feat/tax-region",
      content: "## Developer · 2026-10-18T15:20\nTax region should come from the billing account, not the invoice. The billing account is the source of truth for tax.\n\n## Claude · 2026-10-18T15:22\nTax region now reads billing_account.tax_region (#448).",
    },
    {
      id: "ep_3008", identity: DEV, occurredAt: "2026-10-25T17:10:00Z",
      label: "Claude Code · billing · main",
      content: "## Developer · 2026-10-25T17:10\nThe coverage gate only fails on release branches; main was green this morning.",
    },
    {
      id: "ep_3001", identity: LEAD, occurredAt: "2026-10-14T08:45:00Z",
      label: "Claude Code · billing · main",
      content: "## Developer · 2026-10-14T08:45\nFor review fixes, I like one squashed commit per fix with the PR number in the subject. Keeps the release notes honest.",
    },
    {
      id: "ep_2001", identity: LEAD, occurredAt: "2026-10-12T11:20:00Z",
      label: "Claude Code · billing · main",
      content: "## Developer · 2026-10-12T11:20\nWe tried Node 22 for the build in March and it broke the linter, so we rolled back. Don't retry without upgrading eslint first.",
    },
  ],
  memories: [
    {
      id: "mem_rule_merge", kind: "procedural", category: "rule", threadId: "thr_420",
      statement: "Release work never pushes to master or staging; only the lead merges.",
      why: "Both branches deploy to shared environments, so one person decides what lands there.",
      subjects: ["release process"], validFrom: "2026-10-20T10:02:00Z", validTo: null, recordedAt: "2026-10-20T10:04:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_3003", quote: "Release work never pushes to master or staging; only I merge." }],
    },
    {
      id: "mem_gotcha_sandbox", kind: "procedural", category: "gotcha", threadId: "thr_420",
      statement: "The payments sandbox can leave paid invoices showing as open; re-sending the invoice webhooks from the sandbox console syncs them.",
      why: "The 4.2.0 preview showed paid invoices as open during review until the webhooks were re-sent.",
      subjects: ["payments sandbox"], validFrom: "2026-10-24T11:30:00Z", validTo: null, recordedAt: "2026-10-24T11:33:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "observed", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_3006", quote: "re-send the invoice webhooks from the sandbox console and it syncs" }],
    },
    {
      id: "mem_dec_aliases", kind: "semantic", category: "decision", threadId: "thr_442",
      statement: "Drop the InvoiceFieldAliases class and rely on the LLM prompt for invoice field aliases.",
      why: "The alias table kept drifting from the prompt, which already maps the same fields.",
      subjects: ["invoice parsing"], validFrom: "2026-10-21T14:40:00Z", validTo: null, recordedAt: "2026-10-21T14:42:00Z",
      status: "active", supersedes: "mem_dec_aliases_old", supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_3004", quote: "Drop the InvoiceFieldAliases class." }, { episodeId: "ep_3004", quote: "Rely on the LLM prompt." }],
    },
    {
      id: "mem_dec_aliases_old", kind: "semantic", category: "decision", threadId: "thr_442",
      statement: "Keep InvoiceFieldAliases as a fallback for invoice fields the prompt misses.",
      why: null,
      subjects: ["invoice parsing"], validFrom: "2026-10-09T10:00:00Z", validTo: "2026-10-21T14:40:00Z", recordedAt: "2026-10-09T10:00:00Z",
      status: "superseded", supersedes: null, supersededBy: "mem_dec_aliases", assertion: "stated", confidence: "medium", audience: "project",
      evidence: [{ episodeId: "ep_3004", quote: "the table keeps drifting from it" }],
    },
    {
      id: "mem_dec_bot", kind: "semantic", category: "decision", threadId: "thr_inv243",
      statement: "The Slack invoice bot (INV-243) is not launching.",
      why: "Product pulled it from the roadmap; there is no launch date.",
      subjects: ["slack invoice bot"], validFrom: "2026-10-20T10:02:00Z", validTo: null, recordedAt: "2026-10-20T10:04:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_3003", quote: "The Slack invoice bot (INV-243) is not launching" }],
    },
    {
      id: "mem_fact_tax_region", kind: "semantic", category: "fact", threadId: "thr_420",
      statement: "Tax region comes from the billing account, which is the source of truth for tax.",
      why: null,
      subjects: ["billing account"], validFrom: "2026-10-18T15:20:00Z", validTo: null, recordedAt: "2026-10-18T15:23:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_3002", quote: "The billing account is the source of truth for tax." }],
    },
    {
      id: "mem_fact_coverage", kind: "semantic", category: "fact", threadId: "thr_coverage",
      statement: "The coverage gate currently fails on every branch, not only on release branches.",
      why: null,
      subjects: ["ci"], validFrom: "2026-10-25T16:05:00Z", validTo: null, recordedAt: "2026-10-25T16:07:00Z", conflictsWith: ["mem_coverage_release"],
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "medium", audience: "project",
      evidence: [{ episodeId: "ep_3007", quote: "that one fails on every branch right now" }],
    },
    {
      id: "mem_fact_keys", kind: "prospective", category: "fact", threadId: "thr_420",
      statement: "After the 4.2.0 merge each environment needs payment provider API keys and webhook secrets, and customers must re-confirm their saved cards.",
      why: null,
      subjects: ["payment provider"], validFrom: "2026-10-25T16:06:00Z", validTo: null, recordedAt: "2026-10-25T16:07:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_3007", quote: "create the payment provider API keys and webhook secrets in each environment, have customers re-confirm their saved cards" }],
    },
    {
      id: "mem_pref_commits", kind: "preference", category: "preference", threadId: null,
      statement: "The lead wants one squashed commit per review fix, with the PR number in the subject.",
      why: "It keeps the release notes honest.",
      subjects: ["release process"], validFrom: "2026-10-14T08:45:00Z", validTo: null, recordedAt: "2026-10-14T08:46:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_3001", quote: "one squashed commit per fix with the PR number in the subject" }],
    },
    {
      id: "mem_node22", kind: "episodic", category: "gotcha", threadId: "thr_node22",
      statement: "Node 22 broke the linter when tried for the build in March 2026; upgrade eslint before retrying.",
      why: "It was rolled back once already.",
      subjects: ["build toolchain"], validFrom: "2026-10-12T11:20:00Z", validTo: null, recordedAt: "2026-10-12T11:20:00Z",
      status: "stale", staleReason: "package.json changed since 12 Oct 2026", supersedes: null, supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_2001", quote: "We tried Node 22 for the build in March and it broke the linter, so we rolled back." }],
    },
    {
      id: "mem_claim_fixes", kind: "episodic", category: null, threadId: "thr_420",
      statement: "The agent said review fixes #441–#447 all pass locally (not verified).",
      why: null,
      subjects: ["release 4.2.0"], validFrom: "2026-10-24T11:31:00Z", validTo: null, recordedAt: "2026-10-24T11:33:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "claimed", confidence: "low", audience: "project",
      evidence: [{ episodeId: "ep_3006", quote: "the preview now shows all review fixes #441 through #447" }],
    },
    {
      id: "mem_tz_420", kind: "semantic", category: "decision", threadId: "thr_420",
      statement: "The reminder timezone bug fix ships in 4.2.0.",
      why: null,
      subjects: ["reminder timezone bug"], validFrom: "2026-10-20T10:02:00Z", validTo: "2026-10-23T09:12:00Z", recordedAt: "2026-10-20T10:04:00Z",
      status: "superseded", supersedes: null, supersededBy: "mem_tz_421", assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_3003", quote: "The reminder timezone fix rides along in 4.2.0 too." }],
    },
    {
      id: "mem_tz_421", kind: "semantic", category: "decision", threadId: "thr_421",
      statement: "The reminder timezone bug fix moved to 4.2.1; 4.2.0 ran out of time.",
      why: "4.2.0 ran out of time for it.",
      subjects: ["reminder timezone bug"], validFrom: "2026-10-23T09:12:00Z", validTo: null, recordedAt: "2026-10-23T09:14:00Z",
      status: "active", supersedes: "mem_tz_420", supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_3005", quote: "the reminder timezone bug. Push them to 4.2.1." }],
    },
    {
      // Time-bound: it said when it ends, and that day has passed, so recall no longer serves it (shown as Expired).
      id: "mem_scratch_org", kind: "semantic", category: "fact", threadId: null,
      statement: "The shared QA scratch org expires on 26 Oct 2026; after that, park test data in the new payments sandbox.",
      why: "Test data left in the scratch org is lost when it expires.",
      subjects: ["qa scratch org"], validFrom: "2026-10-14T08:45:00Z", validTo: "2026-10-26T23:59:59Z", recordedAt: "2026-10-14T08:46:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_3001", quote: "the shared QA scratch org expires on 26 Oct" }],
      lastRecalledAt: "2026-10-22T09:00:00Z", recallCount: 4,
    },
    {
      // Unused: learned long ago and no recall has served it since (listed for review on Home and Memories).
      id: "mem_vpn_profile", kind: "procedural", category: "gotcha", threadId: null,
      statement: "The staging VPN profile is shared with the partner integration team; rotating it locks them out until they re-import it.",
      why: "A Friday rotation once left the partner team without staging for the weekend.",
      subjects: ["staging vpn"], validFrom: "2026-08-11T10:00:00Z", validTo: null, recordedAt: "2026-08-11T10:02:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_2001", quote: "the staging VPN profile is shared with the partner integration team" }],
      lastRecalledAt: null, recallCount: 0,
    },
    {
      id: "mem_billing_flags", kind: "semantic", category: "fact", threadId: null,
      statement: "Billing feature flags live in the ops dashboard, not in the repository config.",
      why: "Changing the repo config has no effect on billing flags.",
      subjects: ["billing flags"], validFrom: "2026-08-04T15:00:00Z", validTo: null, recordedAt: "2026-08-04T15:01:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "medium", audience: "project",
      evidence: [{ episodeId: "ep_2001", quote: "billing flags live in the ops dashboard" }],
      lastRecalledAt: "2026-08-20T11:30:00Z", recallCount: 2,
    },
    {
      id: "mem_coverage_release", kind: "semantic", category: "fact", threadId: "thr_coverage",
      statement: "The coverage gate only fails on release branches; main is green.",
      why: null,
      subjects: ["ci"], validFrom: "2026-10-25T17:10:00Z", validTo: null, recordedAt: "2026-10-25T17:11:00Z", conflictsWith: ["mem_fact_coverage"],
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "medium", audience: "project",
      evidence: [{ episodeId: "ep_3008", quote: "The coverage gate only fails on release branches; main was green this morning." }],
    },
    {
      id: "mem_sandbox_queue", kind: "semantic", category: "fact", threadId: "thr_420",
      statement: "The sync lag is the payments sandbox's 24-hour webhook queue; re-sending an invoice webhook makes a fresh event.",
      why: null,
      subjects: ["payments sandbox"], validFrom: "2026-10-24T11:34:00Z", validTo: null, recordedAt: "2026-10-24T11:35:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "medium", audience: "project",
      evidence: [{ episodeId: "ep_3006", quote: "It's the sandbox's 24-hour webhook queue; re-sending makes a fresh event." }],
    },
    {
      id: "mem_lead_owns", kind: "semantic", category: "fact", threadId: null,
      statement: "The lead owns what lands from release work: they merge it, and they set the commit format.",
      why: null,
      subjects: ["release process"], validFrom: "2026-10-20T10:02:00Z", validTo: null, recordedAt: "2026-10-25T18:00:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "inferred", confidence: "low", audience: "project",
      evidence: [{ episodeId: "ep_3003", quote: "only I merge" }, { episodeId: "ep_3001", quote: "one squashed commit per fix with the PR number in the subject" }],
    },
  ],
  threads: [
    {
      id: "thr_420", kind: "release", key: "4.2.0", title: "Release 4.2.0", status: "open",
      summary: "Ships PDF invoice export, multi-currency totals, payment reminder emails and tax regions from the billing account, plus review fixes #441–#447. CI is green except the repo-wide coverage gate; the merge waits on whether master's branch protection blocks it.",
      firstSeenAt: "2026-10-20T10:02:00Z", lastActivityAt: "2026-10-25T16:06:00Z", sessionCount: 6,
      relatedIds: ["thr_421", "thr_442", "thr_coverage", "thr_inv243"],
      sourceIds: ["ep_3007", "ep_3006", "ep_3005", "ep_3004", "ep_3003", "ep_3002"],
      sections: [
        { label: "Contains", items: [
          { text: "PDF invoice export (#429)", date: "2026-10-20", evidence: [{ episodeId: "ep_3003", quote: "PDF invoice export (#429)" }] },
          { text: "Multi-currency totals (#430, #437)", date: "2026-10-20", evidence: [{ episodeId: "ep_3003", quote: "multi-currency totals (#430 and #437)" }] },
          { text: "Payment reminder emails (#434)", date: "2026-10-20", evidence: [{ episodeId: "ep_3003", quote: "payment reminder emails (#434)" }] },
          { text: "Tax region from the billing account (#448)", date: "2026-10-18", evidence: [{ episodeId: "ep_3002", quote: "Tax region now reads billing_account.tax_region (#448)." }] },
          { text: "Review fixes #441–#447", date: "2026-10-24", evidence: [{ episodeId: "ep_3006", quote: "the preview now shows all review fixes #441 through #447" }] },
        ] },
        { label: "Left out", items: [
          { text: "Slack invoice bot (INV-243): not launching", date: "2026-10-20", evidence: [{ episodeId: "ep_3003", quote: "The Slack invoice bot (INV-243) is not launching" }] },
          { text: "Multi-language invoices (INV-211)", date: "2026-10-20", evidence: [{ episodeId: "ep_3003", quote: "multi-language invoices (INV-211) wait" }] },
        ] },
        { label: "Deferred to 4.2.1", items: [
          { text: "Invoice footer copy fix A", date: "2026-10-23", evidence: [{ episodeId: "ep_3005", quote: "the invoice footer copy fix A" }] },
          { text: "'CHF' currency code fix", date: "2026-10-23", evidence: [{ episodeId: "ep_3005", quote: "the 'CHF' currency code fix" }] },
          { text: "Reminder timezone bug", date: "2026-10-23", evidence: [{ episodeId: "ep_3005", quote: "the reminder timezone bug. Push them to 4.2.1." }] },
        ] },
        { label: "Decisions", items: [
          { text: "Drop the InvoiceFieldAliases class; rely on the LLM prompt (#442)", date: "2026-10-21", evidence: [{ episodeId: "ep_3004", quote: "Drop the InvoiceFieldAliases class. [...] Rely on the LLM prompt." }] },
        ] },
        { label: "State", items: [
          { text: "CI green except the repo-wide coverage gate", date: "2026-10-25", evidence: [{ episodeId: "ep_3007", quote: "CI is green on release/4.2.0 except the coverage gate, and that one fails on every branch right now." }] },
        ] },
        { label: "Open questions", items: [
          { text: "Does master's branch protection block the merge while the coverage gate fails?", date: "2026-10-25", evidence: [{ episodeId: "ep_3007", quote: "Does master's branch protection block the merge with the coverage gate red?" }] },
        ] },
        { label: "Next steps after merge", items: [
          { text: "Create the payment provider API keys and webhook secrets in each environment", date: null, evidence: [{ episodeId: "ep_3007", quote: "create the payment provider API keys and webhook secrets in each environment" }] },
          { text: "Have customers re-confirm their saved cards", date: null, evidence: [{ episodeId: "ep_3007", quote: "have customers re-confirm their saved cards" }] },
          { text: "Remove the legacy-pdf-export feature flag in QA and staging", date: null, evidence: [{ episodeId: "ep_3007", quote: "remove the legacy-pdf-export feature flag from QA and staging" }] },
        ] },
      ],
    },
    {
      id: "thr_421", kind: "release", key: "4.2.1", title: "Release 4.2.1", status: "open",
      summary: "Collects what 4.2.0 deferred: the invoice footer copy fix A, the 'CHF' currency code fix and the reminder timezone bug. Nothing is started yet.",
      firstSeenAt: "2026-10-23T09:12:00Z", lastActivityAt: "2026-10-23T09:13:00Z", sessionCount: 1,
      relatedIds: ["thr_420"], sourceIds: ["ep_3005"],
      sections: [
        { label: "Contains", items: [
          { text: "Invoice footer copy fix A", date: "2026-10-23", evidence: [{ episodeId: "ep_3005", quote: "the invoice footer copy fix A" }] },
          { text: "'CHF' currency code fix", date: "2026-10-23", evidence: [{ episodeId: "ep_3005", quote: "the 'CHF' currency code fix" }] },
          { text: "Reminder timezone bug", date: "2026-10-23", evidence: [{ episodeId: "ep_3005", quote: "the reminder timezone bug" }] },
        ] },
      ],
    },
    {
      id: "thr_442", kind: "pr", key: "#442", title: "Drop InvoiceFieldAliases; rely on the LLM prompt", status: "done",
      summary: "Removed the InvoiceFieldAliases class and its tests. Field aliases now live only in the invoice prompt. Merged into release/4.2.0.",
      firstSeenAt: "2026-10-21T14:40:00Z", lastActivityAt: "2026-10-21T14:41:00Z", sessionCount: 1,
      relatedIds: ["thr_420"], sourceIds: ["ep_3004"],
      sections: [
        { label: "Decisions", items: [
          { text: "Rely on the LLM prompt instead of an alias table", date: "2026-10-21", evidence: [{ episodeId: "ep_3004", quote: "Rely on the LLM prompt." }] },
        ] },
        { label: "State", items: [
          { text: "Merged into release/4.2.0", date: "2026-10-21", evidence: [{ episodeId: "ep_3004", quote: "Removed InvoiceFieldAliases and its tests in #442" }] },
        ] },
      ],
    },
    {
      id: "thr_coverage", kind: "task", key: "coverage-gate", title: "Coverage gate fails repo-wide", status: "blocked",
      summary: "The coverage gate fails on every branch, so it says nothing about 4.2.0. Blocked on whether branch protection on master lets a merge through with it red.",
      firstSeenAt: "2026-10-25T16:05:00Z", lastActivityAt: "2026-10-25T16:06:00Z", sessionCount: 1,
      relatedIds: ["thr_420"], sourceIds: ["ep_3007"],
      sections: [
        { label: "Open questions", items: [
          { text: "Does master's branch protection require the coverage check?", date: "2026-10-25", evidence: [{ episodeId: "ep_3007", quote: "Does master's branch protection block the merge with the coverage gate red?" }] },
        ] },
      ],
    },
    {
      id: "thr_inv243", kind: "ticket", key: "INV-243", title: "Slack invoice bot", status: "abandoned",
      summary: "Not launching. Product pulled it from 4.2.0 with no new date.",
      firstSeenAt: "2026-10-20T10:02:00Z", lastActivityAt: "2026-10-20T10:03:00Z", sessionCount: 1,
      relatedIds: ["thr_420"], sourceIds: ["ep_3003"],
      sections: [
        { label: "Decisions", items: [
          { text: "Not launching", date: "2026-10-20", evidence: [{ episodeId: "ep_3003", quote: "The Slack invoice bot (INV-243) is not launching" }] },
        ] },
      ],
    },
    {
      id: "thr_node22", kind: "topic", key: "node-22", title: "Node 22 upgrade", status: "blocked",
      summary: "Tried in March and rolled back after it broke the linter. Blocked on an eslint upgrade.",
      firstSeenAt: "2026-10-12T11:20:00Z", lastActivityAt: "2026-10-12T11:20:00Z", sessionCount: 1,
      relatedIds: [], sourceIds: ["ep_2001"],
      sections: [
        { label: "State", items: [
          { text: "Rolled back to Node 20 after the linter broke", date: "2026-03-01", evidence: [{ episodeId: "ep_2001", quote: "it broke the linter, so we rolled back" }] },
        ] },
        { label: "Next steps", items: [
          { text: "Upgrade eslint, then retry Node 22", date: null, evidence: [{ episodeId: "ep_2001", quote: "Don't retry without upgrading eslint first." }] },
        ] },
      ],
    },
  ],
  sessions: [
    {
      id: "ses_c3", agent: "claude-code", identity: LEAD, label: "Claude Code · billing · release/4.2.0", status: "live",
      startedAt: "2026-10-25T15:40:00Z", lastEventAt: "2026-10-26T07:12:00Z", turns: 31, stateThrough: 28,
      state: {
        goal: "Get release 4.2.0 merged",
        decisions: ["Defer three items to 4.2.1", "Leave the merge to the lead"],
        openQuestions: ["Does branch protection on master block the merge while the coverage gate fails?"],
        inPlay: ["release/4.2.0", "Coverage gate"],
        recentFacts: ["CI is green except the repo-wide coverage gate"],
        progressClaims: ["Review fixes #441–#447 all pass locally"],
      },
    },
    {
      id: "ses_c2", agent: "claude-code", identity: DEV, label: "Claude Code · billing · fix/review-447", status: "synthesized",
      startedAt: "2026-10-24T10:50:00Z", lastEventAt: "2026-10-24T11:40:00Z", turns: 18, stateThrough: 18,
      state: {
        goal: "Land review fixes #441–#447",
        decisions: ["One squashed commit per fix"], openQuestions: [], inPlay: ["fix/review-447"],
        recentFacts: ["Re-sending invoice webhooks syncs a stale sandbox preview"], progressClaims: [],
      },
    },
  ],
  audiences: [],
  identities: [
    { identity: LEAD, traits: { role: "lead", team: "billing" }, origin: "ingest", lastSeenAt: "2026-10-26T07:12:00Z" },
    { identity: DEV, traits: { team: "billing" }, origin: "ingest", lastSeenAt: "2026-10-24T11:40:00Z" },
  ],
  failedEpisodes: new Set(),
  standing: ["Open a pull request for every change; never push to master directly."],
  anchors: [
    { memoryId: "mem_node22", repo: "billing", path: "package.json", version: "3f9a1c2", currentVersion: "d41e7b0", anchoredAt: "2026-10-12T11:20:00Z" },
    { memoryId: "mem_fact_tax_region", repo: "billing", path: "src/tax/region.ts", version: "a1b4c9e", currentVersion: "a1b4c9e", anchoredAt: "2026-10-18T15:22:00Z" },
    { memoryId: "mem_dec_aliases", repo: "billing", path: "src/invoices/prompt.ts", version: "77d02e1", currentVersion: "77d02e1", anchoredAt: "2026-10-21T14:41:00Z" },
    { memoryId: "mem_fact_coverage", repo: "billing", path: "codecov.yml", version: "c0ffee1", currentVersion: "c0ffee1", anchoredAt: "2026-10-25T16:07:00Z" },
  ],
  links: [
    { from: "mem_sandbox_queue", to: "mem_gotcha_sandbox", kind: "extends" },
    { from: "mem_lead_owns", to: "mem_rule_merge", kind: "derived" },
    { from: "mem_lead_owns", to: "mem_pref_commits", kind: "derived" },
  ],
  requests: [
    { id: 7006, at: "2026-10-26T07:12:03Z", method: "POST", path: "/v1/recall", status: 200, latencyMs: 380, keyName: "team plugin", detail: "abstained: below_threshold (best relevance 0.214, threshold 0.30)" },
    { id: 7005, at: "2026-10-26T07:11:50Z", method: "POST", path: "/v1/sessions/ses_c3/events", status: 202, latencyMs: 24, keyName: "team plugin" },
    { id: 7004, at: "2026-10-25T21:40:12Z", method: "GET", path: "/v1/threads", status: 200, latencyMs: 31, keyName: "ci" },
    { id: 7003, at: "2026-10-25T16:07:40Z", method: "POST", path: "/v1/ingest", status: 202, latencyMs: 88, keyName: "team plugin" },
    { id: 7002, at: "2026-10-24T11:41:02Z", method: "POST", path: "/v1/recall", status: 429, latencyMs: 6, keyName: "team plugin" },
    { id: 7001, at: "2026-10-24T11:40:58Z", method: "POST", path: "/v1/recall", status: 200, latencyMs: 412, keyName: "team plugin", detail: "served 5 items, 1210 tokens (best relevance 0.655, threshold 0.30)" },
  ],
  usage: usageSeries(30, 9, 40, 3),
};

/** A small project with no threads yet: one fact so far. Two of its sources are still being
 *  synthesized when the mock starts. */
const docsBot: ProjectData = {
  keys: [
    { id: "key_31", name: "docs widget", masked: "mv_sk_••••5d10", createdAt: "2026-08-12T09:05:00Z", lastUsedAt: "2026-10-26T06:58:00Z", revokedAt: null },
  ],
  episodes: [
    {
      id: "ep_3001", identity: "visitor-1182", occurredAt: "2026-10-20T13:10:00Z", label: "Docs chat · SSO setup",
      content: "## Visitor · 2026-10-20T13:10\nDoes SSO work with Okta?\n\n## Bot · 2026-10-20T13:10\nYes. SAML SSO supports Okta and Azure AD on the Team plan and above.",
    },
    {
      id: "ep_3002", identity: "visitor-1190", occurredAt: "2026-10-26T06:50:00Z", label: "Docs chat · rate limits",
      content: "## Visitor · 2026-10-26T06:50\nWhat is the API rate limit?\n\n## Bot · 2026-10-26T06:51\n600 requests per minute per key; bursts above that get a 429 with Retry-After.",
    },
    {
      id: "ep_3003", identity: "visitor-1191", occurredAt: "2026-10-26T06:57:00Z", label: "Docs chat · webhooks",
      content: "## Visitor · 2026-10-26T06:57\nAre webhooks signed?\n\n## Bot · 2026-10-26T06:58\nYes, with an HMAC-SHA256 signature in the X-Signature header.",
    },
  ],
  memories: [
    {
      id: "mem_sso", category: "fact", threadId: null, why: null, kind: "semantic", statement: "SAML SSO supports Okta and Azure AD on the Team plan and above.",
      subjects: ["sso"], validFrom: "2026-10-20T13:10:00Z", validTo: null, recordedAt: "2026-10-20T13:10:00Z",
      status: "active", supersedes: null, supersededBy: null, assertion: "stated", confidence: "high", audience: "project",
      evidence: [{ episodeId: "ep_3001", quote: "SAML SSO supports Okta and Azure AD on the Team plan and above." }],
    },
  ],
  threads: [],
  sessions: [],
  audiences: [],
  identities: [],
  failedEpisodes: new Set(),
  pendingEpisodes: { ep_3002: [0, 25], ep_3003: [25, 45] },
  requests: [
    { id: 3003, at: "2026-10-26T06:58:00Z", method: "POST", path: "/v1/ingest", status: 202, latencyMs: 31, keyName: "docs widget" },
  ],
  usage: usageSeries(30, 4, 12, 5),
};

export const seedData: Record<string, ProjectData> = {
  "support-agent": supportAgent,
  "coding-team": codingTeam,
  "docs-bot": docsBot,
};
