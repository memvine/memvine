import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronRight, FileText, Quote } from "lucide-react";
import { ReceiptList } from "@/components/Receipt";
import { ThreadKindTag } from "@/components/thread-bits";
import {
  Card, CardHeader, Empty, Eyebrow, formatDate, Mono, PageHeader, plural, shortDate, ThreadStatusBadge, timeAgo,
} from "@/components/ui";
import { currentProject, getThread } from "@/lib/api";
import type { ThreadSection } from "@/lib/types";

function SectionList({ section, sourceLabel }: { section: ThreadSection; sourceLabel: (episodeId: string) => string }) {
  return (
    <section className="py-5 first:pt-0 last:pb-0">
      <Eyebrow className="mb-2.5 flex items-center gap-2">
        {section.label}
        <span className="tabular-nums normal-case tracking-normal text-text-3/80">{section.items.length}</span>
      </Eyebrow>
      <ul className="space-y-0.5">
        {section.items.map((item, i) => {
          const row = (
            <>
              <span className="mt-[7px] size-1.5 shrink-0 rounded-full bg-text-3/70" aria-hidden />
              <span className="min-w-0 flex-1 text-[14px] leading-6 text-text">{item.text}</span>
              {item.evidence.length > 0 && (
                <Quote className="mt-1 size-3.5 shrink-0 text-text-3 opacity-0 transition-opacity group-hover:opacity-100 group-open:opacity-100" aria-hidden />
              )}
              <span className="w-14 shrink-0 pt-px text-right text-[12px] leading-6 tabular-nums text-text-3">{item.date ? shortDate(item.date) : ""}</span>
            </>
          );
          if (!item.evidence.length) {
            return <li key={i} className="flex items-start gap-3 rounded-md px-2 py-1">{row}</li>;
          }
          return (
            <li key={i}>
              <details className="group rounded-md">
                <summary className="flex cursor-pointer items-start gap-3 rounded-md px-2 py-1 transition-colors hover:bg-surface-2" title="Show what the source said">
                  {row}
                </summary>
                <div className="mt-1 mb-2 ml-[22px] space-y-2 border-l-2 border-border-strong pl-3">
                  {item.evidence.map((e, j) => (
                    <figure key={j}>
                      <blockquote className="text-[13px] leading-relaxed text-text-2">“{e.quote}”</blockquote>
                      <figcaption className="mt-0.5 text-[12px] text-text-3">
                        <Link href={`/sources/${encodeURIComponent(e.episodeId)}`} className="hover:text-text hover:underline">{sourceLabel(e.episodeId)}</Link>
                      </figcaption>
                    </figure>
                  ))}
                </div>
              </details>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export default async function ThreadPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const project = await currentProject();
  const thread = await getThread(project.slug, decodeURIComponent(id));
  if (!thread) notFound();
  const sourceLabel = (episodeId: string) => {
    const s = thread.sources.find((x) => x.episodeId === episodeId);
    return s ? `${s.label} · ${formatDate(s.occurredAt, true)}` : episodeId;
  };

  return (
    <>
      <PageHeader
        back={{ href: "/threads", label: "Threads" }}
        title={thread.title}
        meta={
          <>
            <ThreadStatusBadge status={thread.status} />
            <ThreadKindTag kind={thread.kind} />
            <Mono className="text-text-3">{thread.key}</Mono>
          </>
        }
      />

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0">
          <p className="max-w-[68ch] text-[15.5px] leading-[1.65] text-text">{thread.summary}</p>
          <p className="mt-3 text-[12.5px] text-text-3">
            Last activity {timeAgo(thread.lastActivityAt)} · first seen {formatDate(thread.firstSeenAt)} · {plural(thread.sessionCount, "session")}
          </p>

          <Card className="mt-6 px-4 py-5 sm:px-5">
            {thread.sections.length === 0 ? (
              <p className="text-[13px] text-text-3">Nothing recorded about this thread yet.</p>
            ) : (
              <div className="divide-y divide-border">
                {thread.sections.map((s) => <SectionList key={s.label} section={s} sourceLabel={sourceLabel} />)}
              </div>
            )}
          </Card>

          <Card className="mt-6">
            <CardHeader title="Knowledge from this thread" description="Decisions, rules and gotchas that outlive the work." />
            {thread.knowledge.length === 0 ? (
              <Empty>Nothing durable yet. Decisions, rules and gotchas made here show up as they are learned.</Empty>
            ) : (
              <ReceiptList items={thread.knowledge} showCategory hrefFor={Object.fromEntries(thread.knowledge.map((m) => [m.id, `/memories?open=${encodeURIComponent(m.id)}`]))} />
            )}
          </Card>
        </div>

        <aside className="min-w-0 space-y-6">
          <section>
            <Eyebrow className="mb-2">Related</Eyebrow>
            {thread.related.length === 0 ? (
              <p className="text-[13px] text-text-3">No related threads.</p>
            ) : (
              <ul className="-mx-2">
                {thread.related.map((r) => (
                  <li key={r.id}>
                    <Link href={`/threads/${r.id}`} className="group flex items-center gap-2 rounded-lg px-2 py-2 transition-colors hover:bg-surface-2">
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] text-text">{r.title}</span>
                        <span className="mt-0.5 flex items-center gap-2"><ThreadStatusBadge status={r.status} /><Mono className="truncate text-text-3">{r.key}</Mono></span>
                      </span>
                      <ChevronRight className="size-4 shrink-0 text-text-3 opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <Eyebrow className="mb-2">Sources</Eyebrow>
            {thread.sources.length === 0 ? (
              <p className="text-[13px] text-text-3">No sources recorded.</p>
            ) : (
              <ul className="-mx-2">
                {thread.sources.map((s) => (
                  <li key={s.episodeId}>
                    <Link href={`/sources/${encodeURIComponent(s.episodeId)}`} className="flex items-start gap-2.5 rounded-lg px-2 py-2 transition-colors hover:bg-surface-2">
                      <FileText className="mt-0.5 size-4 shrink-0 text-text-3" strokeWidth={1.75} aria-hidden />
                      <span className="min-w-0">
                        <span className="block truncate text-[13px] text-text">{s.label}</span>
                        <span className="block text-[12px] text-text-3">{formatDate(s.occurredAt, true)}</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>
    </>
  );
}
