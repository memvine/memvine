// A memory's detail, shown in the right drawer on Memories and Knowledge and on its own page.
import Link from "next/link";
import { ArrowUpRight, Check, RotateCcw, Archive } from "lucide-react";
import { confirmMemory, setMemoryStatus } from "@/app/actions";
import type { MemoryDetail, MemoryItem, ThreadSummary } from "@/lib/types";
import { CopyButton, Drawer } from "./client";
import { MemoryTimeline } from "./MemoryTimeline";
import {
  AssertionBadge, Badge, button, Callout, CATEGORY_LABEL, CategoryBadge, Eyebrow, formatDate, KeyValue, KeyValueList, Mono, SourceQuote,
  StatusBadge, ThreadStatusBadge,
} from "./ui";

/** Confirm, retire or restore, as the memory's state allows. `next` returns to a list instead of the memory's page. */
export function MemoryActions({ m, next, size = "md" }: { m: MemoryItem; next?: string; size?: "sm" | "md" }) {
  const back = next ? <input type="hidden" name="next" value={next} /> : null;
  return (
    <>
      {((m.assertion !== "observed" && m.status === "active") || m.status === "stale") && (
        <form action={confirmMemory}>
          <input type="hidden" name="id" value={m.id} />
          {back}
          <button className={button("primary", size)}><Check aria-hidden /> Confirm as true</button>
        </form>
      )}
      {(m.status === "active" || m.status === "stale") && (
        <form action={setMemoryStatus}>
          <input type="hidden" name="id" value={m.id} />
          <input type="hidden" name="status" value="retired" />
          {back}
          <button className={button("danger", size)}><Archive aria-hidden /> Retire</button>
        </form>
      )}
      {m.status === "retired" && (
        <form action={setMemoryStatus}>
          <input type="hidden" name="id" value={m.id} />
          <input type="hidden" name="status" value="active" />
          {back}
          <button className={button("secondary", size)}><RotateCcw aria-hidden /> Restore</button>
        </form>
      )}
    </>
  );
}

export function MemoryFacts({ m, thread }: { m: MemoryItem; thread?: ThreadSummary | null }) {
  return (
    <KeyValueList>
      <KeyValue label="Status"><StatusBadge status={m.status} /></KeyValue>
      {m.category && <KeyValue label="Category"><CategoryBadge category={m.category} /></KeyValue>}
      {m.why && <KeyValue label="Why">{m.why}</KeyValue>}
      {(thread || m.threadId) && (
        <KeyValue label="Thread">
          {thread ? (
            <span className="flex items-start gap-2">
              <ThreadStatusBadge status={thread.status} />
              <Link href={`/threads/${thread.id}`} className="pt-0.5 hover:underline">{thread.title}</Link>
            </span>
          ) : <Mono>{m.threadId}</Mono>}
        </KeyValue>
      )}
      <KeyValue label="Source type"><AssertionBadge assertion={m.assertion} /></KeyValue>
      <KeyValue label="Kind"><Badge tone="outline">{m.kind}</Badge></KeyValue>
      <KeyValue label="Confidence">{m.confidence}</KeyValue>
      <KeyValue label="Valid">{formatDate(m.validFrom)} <span className="text-text-3">→</span> {m.validTo ? formatDate(m.validTo) : "still true"}</KeyValue>
      <KeyValue label="Learned">{formatDate(m.recordedAt, true)}</KeyValue>
      <KeyValue label="Last recalled">
        {m.lastRecalledAt ? `${formatDate(m.lastRecalledAt, true)} · ${m.recallCount ?? 0} time${m.recallCount === 1 ? "" : "s"}` : "never"}
      </KeyValue>
      {m.subjects.length > 0 && (
        <KeyValue label="About"><span className="flex flex-wrap gap-1">{m.subjects.map((s) => <Badge key={s}>{s}</Badge>)}</span></KeyValue>
      )}
      <KeyValue label="Audience">{m.audience === "project" ? "Every identity in the project" : <Badge>{m.audience}</Badge>}</KeyValue>
    </KeyValueList>
  );
}

export function MemoryHistory({ chain, current, linkFor }: { chain: MemoryItem[]; current: string; linkFor: (id: string) => string }) {
  if (chain.length < 2) return null;
  return (
    <ol className="space-y-0">
      {chain.map((c, i) => (
        <li key={c.id} className="relative flex gap-3 pb-4 last:pb-0">
          <div className="flex flex-col items-center pt-1.5">
            <span className={`size-2 rounded-full ${c.status === "active" ? "bg-good" : "bg-border-strong"}`} />
            {i < chain.length - 1 && <span className="mt-1 w-px flex-1 bg-border" />}
          </div>
          <div className="min-w-0">
            <p className={`text-[13px] ${c.id === current ? "font-medium text-text" : "text-text-2"}`}>
              {c.id === current ? c.statement : <Link href={linkFor(c.id)} scroll={false} className="hover:underline">{c.statement}</Link>}
            </p>
            <p className="mt-0.5 text-[12px] text-text-3">{formatDate(c.validFrom)} – {c.validTo ? formatDate(c.validTo) : "now"}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function MemoryDrawer({ detail, closeHref, linkFor, thread }: {
  detail: MemoryDetail;
  closeHref: string;
  /** The URL that opens another memory in this same drawer. */
  linkFor: (id: string) => string;
  thread?: ThreadSummary | null;
}) {
  const { memory: m, chain, conflicts, episodes } = detail;
  const [first, ...rest] = m.evidence;
  const episodeOf = (id: string) => episodes.find((e) => e.id === id);
  const firstEpisode = first ? episodeOf(first.episodeId) : undefined;
  const heading = m.category ? CATEGORY_LABEL[m.category].one : "Memory";

  return (
    <Drawer
      closeHref={closeHref}
      label={m.statement}
      header={
        <div className="pr-10">
          <Eyebrow>{heading} · {m.kind}</Eyebrow>
          <h2 className="mt-2 text-[17px] leading-snug font-semibold tracking-[-0.01em] text-text">{m.statement}</h2>
          <div className="mt-2.5 flex items-center gap-1.5">
            <Mono className="truncate text-text-3">{m.id}</Mono>
            <CopyButton value={m.id} label="Copy id" />
          </div>
        </div>
      }
      footer={
        <>
          <MemoryActions m={m} next={closeHref} size="md" />
          <Link href={`/memories/${m.id}`} className={button("ghost", "md", "ml-auto")}>Open page <ArrowUpRight aria-hidden /></Link>
        </>
      }
    >
      <div className="space-y-5">
        {m.expired && (
          <Callout tone="warn" title="Expired">
            It said it holds until {m.validTo ? formatDate(m.validTo) : "a date that has passed"}. Recall no longer serves it as current; a question about the past still finds it.
          </Callout>
        )}
        {m.status === "stale" && m.staleReason && (
          <Callout tone="warn" title="May be outdated">{m.staleReason}. Confirm it if it still holds, or retire it.</Callout>
        )}
        {m.assertion === "claimed" && (
          <Callout tone="warn" title="Unverified agent claim">An agent said this about its own work. Recall ranks it last until someone confirms it.</Callout>
        )}
        {first && (
          <SourceQuote quote={first.quote}>
            {firstEpisode ? (
              <Link href={`/sources/${encodeURIComponent(first.episodeId)}`} className="hover:text-text hover:underline">
                {firstEpisode.label ?? first.episodeId} · {formatDate(firstEpisode.occurredAt, true)}
              </Link>
            ) : <Mono className="text-text-3">{first.episodeId}</Mono>}
          </SourceQuote>
        )}
        {rest.length > 0 && (
          <ul className="space-y-2">
            {rest.map((e) => (
              <li key={e.episodeId + e.quote} className="border-l-2 border-border-strong pl-3 text-[13px] text-text-2">“{e.quote}”</li>
            ))}
          </ul>
        )}

        <MemoryFacts m={m} thread={thread} />

        {conflicts.length > 0 && (
          <section>
            <Eyebrow className="mb-2">Contradicted by</Eyebrow>
            <ul className="space-y-2">
              {conflicts.map((c) => (
                <li key={c.id}>
                  <Link href={linkFor(c.id)} scroll={false} className="text-[13px] hover:underline">{c.statement}</Link>
                  <p className="text-[12px] text-text-3">said {formatDate(c.recordedAt)}; both stay active and recall shows them together</p>
                </li>
              ))}
            </ul>
          </section>
        )}
        {chain.length > 1 && (
          <section>
            <Eyebrow className="mb-3">Values of this fact</Eyebrow>
            <MemoryHistory chain={chain} current={m.id} linkFor={linkFor} />
          </section>
        )}
        <section>
          <Eyebrow className="mb-3">History</Eyebrow>
          <MemoryTimeline events={detail.history} linkFor={linkFor} />
        </section>
      </div>
    </Drawer>
  );
}
