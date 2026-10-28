// The console's home: what the current project's memory holds, in plain text. What it learned
// lately, what changed, what may be outdated, and how recall answered agents. (The graph home it
// replaced is on hold; its code stays in app/graph/, reachable at /graph.)
import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowRight, MessageCircleQuestion } from "lucide-react";
import { AutoRefresh } from "@/components/client";
import { EmptyProject, SetupSteps, type SetupStep } from "@/components/EmptyProject";
import { Receipt } from "@/components/Receipt";
import { SynthesisBanner } from "@/components/SynthesisBanner";
import { Badge, ButtonLink, Card, CardHeader, cx, shortDate, timeAgo } from "@/components/ui";
import { ProfileLines, ProfileMeta, RebuildButton } from "@/components/ProfileLines";
import { canManage, currentProject, getProjectProfile, getUsage, listEpisodes, listKeys, listMemories, listRequests, listSessions, listThreads } from "@/lib/api";
import { SAMPLE_PREFIX } from "@/lib/sample-data";
import type { ConsolidationStats, MemoryItem, RequestLog } from "@/lib/types";

const LEARNED = 8;
const CHANGED = 5;
const OUTDATED = 6;
const RECALLS = 5;
const REVIEW = 6;
/** listMemories asks for at most this many per status. */
const PAGE = 200;

const memoryHref = (id: string) => `/memories?open=${encodeURIComponent(id)}`;
const newest = (a: MemoryItem, b: MemoryItem) => b.recordedAt.localeCompare(a.recordedAt);

export default async function HomePage() {
  const project = await currentProject();
  const [unreviewed, flagged, active, stale, superseded, episodes, keys, requests, sessions, usage, threads, profile, manage] = await Promise.all([
    listMemories(project.slug, { review: "new" }),
    listMemories(project.slug, { flag: "any" }),
    listMemories(project.slug, { status: "active" }),
    listMemories(project.slug, { status: "stale" }),
    listMemories(project.slug, { status: "superseded" }),
    listEpisodes(project.slug),
    listKeys(project.slug),
    listRequests(project.slug),
    listSessions(project.slug),
    getUsage(project.slug),
    listThreads(project.slug),
    getProjectProfile(project.slug),
    canManage(),
  ]);

  const live = [...active, ...stale];
  const byId = new Map([...live, ...superseded].map((m) => [m.id, m]));
  const threadTitle = new Map(threads.map((t) => [t.id, t.title]));
  const hasMemory = byId.size > 0;
  const tending = episodes.filter((e) => e.jobStatus === "queued" || e.jobStatus === "running").length;
  const tendedAt = [...byId.values()].reduce<string | null>((max, m) => (!max || m.recordedAt > max ? m.recordedAt : max), null);
  const recalls = requests.filter((r) => r.path === "/v1/recall");

  // Recently changed: a current value with the one it replaced, and active contradictions as pairs.
  const supersedes = live
    .filter((m) => m.supersedes && byId.has(m.supersedes))
    .sort((a, b) => b.validFrom.localeCompare(a.validFrom))
    .slice(0, CHANGED);
  const pairs: [MemoryItem, MemoryItem][] = [];
  const paired = new Set<string>();
  for (const m of [...live].sort(newest)) {
    for (const other of m.conflictsWith ?? []) {
      const o = byId.get(other);
      const key = [m.id, other].sort().join("|");
      if (!o || paired.has(key) || (o.status !== "active" && o.status !== "stale")) continue;
      paired.add(key);
      pairs.push([m, o]);
    }
  }

  const since = new Date(Date.now() - 30 * 86_400_000).toISOString().slice(0, 10);
  const recalls30 = usage.filter((d) => d.date >= since).reduce((n, d) => n + d.recall, 0);

  const steps: SetupStep[] = [
    { done: keys.some((k) => !k.revokedAt), label: "Create a project API key", href: "/connect" },
    { done: episodes.length > 0, label: "Send your first conversation", href: "/connect" },
    { done: hasMemory, label: "Wait for memory to be tended", href: "/sources" },
    { done: recalls30 > 0 || recalls.some((r) => r.status < 400), label: "Ask memory for the first time", href: "/playground" },
  ];
  // The empty project's three connect steps. Sources an agent sent are neither pasted nor sample;
  // the plugin's backfill labels the sessions it imports "(imported)".
  const sent = episodes.filter((e) => !e.id.startsWith("paste-") && !e.id.startsWith(SAMPLE_PREFIX));
  const connect: SetupStep[] = [
    { done: keys.some((k) => !k.revokedAt), label: "Create a project API key", href: "/connect", detail: "Agents send and recall with it." },
    { done: sent.length > 0, label: "Install the plugin in Claude Code or Codex", href: "/connect", detail: "/plugin install memvine@memvine" },
    { done: sent.some((e) => e.label?.endsWith("(imported)")), label: "Backfill your past sessions", href: "/integrations", detail: "/memvine:backfill" },
  ];

  const receipt = (m: MemoryItem, reviewing = false) => (
    <Receipt m={m} href={memoryHref(m.id)} showCategory reviewing={reviewing}
             thread={m.threadId && threadTitle.has(m.threadId) ? { id: m.threadId, title: threadTitle.get(m.threadId)! } : null} />
  );
  // Review: what consolidation flagged (with its reason) first, then new memories no one has looked at.
  const flaggedIds = new Set(flagged.map((m) => m.id));
  const review = [...[...flagged].sort(newest), ...[...unreviewed].sort(newest).filter((m) => !flaggedIds.has(m.id))];
  const reviewTotal = Math.max((project.reviewCount ?? 0) + flagged.filter((m) => m.review !== "new").length, review.length);
  const tidied = project.consolidatedAt && project.lastConsolidation ? tidiedLine(project.consolidatedAt, project.lastConsolidation) : null;

  return (
    <>
      <header className="mb-5 flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="w-full min-w-0 sm:w-auto sm:flex-1">
          <h1 className="text-[19px] font-semibold tracking-[-0.01em] text-text">{project.name}</h1>
          {project.description && <p className="mt-1 max-w-2xl text-[13.5px] leading-relaxed text-text-2">{project.description}</p>}
          <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-text-3">
            <span>{tendedAt ? `tended ${timeAgo(tendedAt)}` : "not tended yet"}</span>
            {tidied && (
              <>
                <span aria-hidden>·</span>
                <Link href="/activity?kind=merged" className="hover:text-text-2 hover:underline" title="Background consolidation: duplicates merged, missed updates linked, doubtful memories flagged for review. Nothing is deleted.">{tidied}</Link>
              </>
            )}
            {tending > 0 && (
              <>
                <span aria-hidden>·</span>
                <AutoRefresh active label={`tending ${tending} session${tending === 1 ? "" : "s"}`} seconds={8} />
              </>
            )}
          </p>
        </div>
        <ButtonLink href="/playground" variant="primary"><MessageCircleQuestion aria-hidden /> Ask memory</ButtonLink>
      </header>

      {project.slug && <SynthesisBanner project={project.slug} />}

      {!hasMemory ? (
        <EmptyProject connect={connect} tending={tending} />
      ) : (
        <>
          <dl className="mb-6 grid grid-cols-2 overflow-hidden rounded-xl border border-border bg-surface sm:grid-cols-4">
            <Count label="Active memories" value={active.length >= PAGE ? `${PAGE}+` : active.length.toLocaleString()} href="/memories?status=active" />
            <Count label="Sources" value={project.sourceCount.toLocaleString()} href="/sources" />
            <Count label="Sessions" value={sessions.length.toLocaleString()} href="/sessions" />
            <Count label="Recalls, 30 days" value={recalls30.toLocaleString()} href="/usage" />
          </dl>

          <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
            <div className="min-w-0 space-y-6">
              {review.length > 0 && (
                <Section
                  title={`Review · ${reviewTotal.toLocaleString()}`}
                  description={flagged.length > 0
                    ? "Memories the last tidy-up doubted, with its reason, then new ones no one has looked at yet. Keep what is worth knowing on another task, forget the rest, or dismiss a doubt. Agents are served them meanwhile."
                    : "New memories no one has looked at yet. Keep what is worth knowing on another task, forget the rest, or say what is true instead. Agents are served them meanwhile."}
                  more={reviewTotal > REVIEW ? { href: "/memories?review=new", label: `All ${reviewTotal.toLocaleString()}` } : undefined}
                >
                  <ul className="divide-y divide-border">
                    {review.slice(0, Math.max(REVIEW, Math.min(flagged.length, 12))).map((m) => <Row key={m.id}>{receipt(m, true)}</Row>)}
                  </ul>
                </Section>
              )}

              <Section title="Recently learned" description="The newest memories agents are served now." more={{ href: "/memories", label: "All memories" }}>
                {active.length === 0 ? <Quiet>No active memories.</Quiet> : (
                  <ul className="divide-y divide-border">
                    {[...active].sort(newest).slice(0, LEARNED).map((m) => <Row key={m.id}>{receipt(m)}</Row>)}
                  </ul>
                )}
              </Section>

              <Section title="Recently changed" description="Values that replaced an older one, and memories that disagree.">
                {supersedes.length === 0 && pairs.length === 0 ? <Quiet>Nothing has changed or conflicted yet.</Quiet> : (
                  <ul className="divide-y divide-border">
                    {supersedes.map((m) => {
                      const was = byId.get(m.supersedes!)!;
                      return (
                        <Row key={m.id}>
                          {receipt(m)}
                          <p className="mt-1.5 text-[12.5px] leading-snug text-text-3">
                            <span className="text-text-2">was:</span>{" "}
                            <Link href={memoryHref(was.id)} className="line-through decoration-text-3/50 hover:text-text-2">{was.statement}</Link>
                            {" · "}changed {shortDate(was.validTo ?? m.validFrom)}
                          </p>
                        </Row>
                      );
                    })}
                    {pairs.map(([a, b]) => (
                      <Row key={`${a.id}|${b.id}`}>
                        <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-warn">These two disagree</p>
                        <div className="space-y-2 border-l-2 border-warn/40 pl-3">
                          {receipt(a)}
                          <p className="text-[12px] text-text-3">contradicts</p>
                          {receipt(b)}
                        </div>
                      </Row>
                    ))}
                  </ul>
                )}
              </Section>
            </div>

            <div className="min-w-0 space-y-6">
              {profile && (profile.lines.length > 0 || profile.builtAt) && (
                <Card>
                  <CardHeader
                    title="Project profile"
                    description={<>What every agent session here starts with. <ProfileMeta profile={profile} /></>}
                    action={manage ? <RebuildButton subjectKind="project" /> : undefined}
                  />
                  <div className="px-4 pb-4 sm:px-5"><ProfileLines profile={{ ...profile, description: null }} /></div>
                </Card>
              )}

              {(project.unusedCount ?? 0) > 0 && <UnusedCard count={project.unusedCount!} days={project.unusedDays ?? 60} />}

              <Section title="Possibly outdated" description="Memories whose ground moved; confirm, correct or forget them."
                       more={stale.length > OUTDATED ? { href: "/memories?status=stale", label: `All ${stale.length}` } : undefined}>
                {stale.length === 0 ? <Quiet>Nothing looks outdated.</Quiet> : (
                  <ul className="divide-y divide-border">
                    {[...stale].sort(newest).slice(0, OUTDATED).map((m) => <Row key={m.id}>{receipt(m)}</Row>)}
                  </ul>
                )}
              </Section>

              <Section title="Recent recalls" description="What agents asked for, and whether memory answered." more={{ href: "/requests", label: "Requests" }}>
                {recalls.length === 0 ? <Quiet>{recalls30 > 0 ? "No recall in the recent request log." : "No agent has recalled yet."} <Link href="/playground" className="underline underline-offset-2 hover:text-text">Ask memory</Link> to try it.</Quiet> : (
                  <ul className="divide-y divide-border">
                    {recalls.slice(0, RECALLS).map((r) => <RecallRow key={r.id} r={r} />)}
                  </ul>
                )}
              </Section>

              {!steps.every((s) => s.done) && (
                <Card>
                  <CardHeader title="Get started" description={`${steps.filter((s) => s.done).length} of ${steps.length} done`} />
                  <SetupSteps steps={steps} />
                </Card>
              )}
            </div>
          </div>
        </>
      )}
    </>
  );
}

/** "Last tidied 3h ago: 2 merged, 1 linked, 1 flagged", from the last consolidation run. */
function tidiedLine(at: string, s: ConsolidationStats): string {
  if (s.skipped) return `last tidied ${timeAgo(at)}`;
  return `last tidied ${timeAgo(at)}: ${s.merged ?? 0} merged, ${s.linked ?? 0} linked, ${s.flagged ?? 0} flagged`;
}

/** Memories no agent's recall served lately: a nudge to review them, never hidden or deleted for it. */
function UnusedCard({ count, days }: { count: number; days: number }) {
  return (
    <Card>
      <Link href="/memories?unused=true" className="flex items-center justify-between gap-3 px-4 py-3.5 transition-colors hover:bg-surface-2/50 sm:px-5">
        <p className="text-[13px] leading-snug text-text-2">
          <span className="font-semibold tabular-nums text-text">{count.toLocaleString()}</span>{" "}
          {count === 1 ? "memory hasn't" : "memories haven't"} been used in {days} days
        </p>
        <span className="inline-flex shrink-0 items-center gap-1 text-[12.5px] text-text-3">Review <ArrowRight className="size-3.5" aria-hidden /></span>
      </Link>
    </Card>
  );
}

function Count({ label, value, href }: { label: string; value: string; href: string }) {
  return (
    <Link href={href} className="border-border px-4 py-3 transition-colors hover:bg-surface-2/60 sm:px-5 [&:nth-child(odd)]:border-r sm:[&:not(:last-child)]:border-r [&:nth-child(-n+2)]:border-b sm:[&:nth-child(-n+2)]:border-b-0">
      <dt className="text-[12px] text-text-3">{label}</dt>
      <dd className="mt-0.5 text-[20px] font-semibold leading-tight tracking-[-0.01em] tabular-nums text-text">{value}</dd>
    </Link>
  );
}

function Section({ title, description, more, children }: { title: string; description: string; more?: { href: string; label: string }; children: ReactNode }) {
  return (
    <Card>
      <CardHeader
        title={title}
        description={description}
        action={more && (
          <Link href={more.href} className="inline-flex items-center gap-1 text-[12.5px] text-text-3 transition-colors hover:text-text">
            {more.label} <ArrowRight className="size-3.5" aria-hidden />
          </Link>
        )}
      />
      {children}
    </Card>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <li className="px-4 py-3 transition-colors hover:bg-surface-2/50 sm:px-5">{children}</li>;
}

function Quiet({ children }: { children: ReactNode }) {
  return <p className="px-4 py-4 text-[13px] text-text-3 sm:px-5">{children}</p>;
}

/** One /v1/recall call from the request log: when, by which key, and what it served or why it abstained. */
function RecallRow({ r }: { r: RequestLog }) {
  const abstained = r.detail?.startsWith("abstained");
  const [tone, word] = r.status >= 400 ? (["warn", `refused ${r.status}`] as const)
    : abstained ? (["neutral", "abstained"] as const) : (["good", "served"] as const);
  // "abstained: below_threshold (best …)" reads as "below threshold (best …)" under its badge.
  const detail = r.detail?.replace(/^abstained:\s*/, "").replace(/_/g, " ");
  return (
    <li>
      <Link href="/requests" className="block px-4 py-3 transition-colors hover:bg-surface-2/50 sm:px-5">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Badge tone={tone} dot>{word}</Badge>
          <span className="text-[12px] text-text-3">{timeAgo(r.at)}{r.keyName ? ` · ${r.keyName}` : ""} · {r.latencyMs} ms</span>
        </div>
        {detail && <p className={cx("mt-1 text-[12.5px] leading-snug", abstained ? "text-text-3" : "text-text-2")}>{detail}</p>}
      </Link>
    </li>
  );
}
