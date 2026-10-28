import Link from "next/link";
import { notFound } from "next/navigation";
import { Brain } from "lucide-react";
import { CopyButton } from "@/components/client";
import { MemoryActions, MemoryFacts, MemoryHistory } from "@/components/MemoryPanel";
import { MemoryTimeline } from "@/components/MemoryTimeline";
import { Callout, Card, CardBody, CardHeader, formatDate, Mono, PageHeader, SourceQuote, StatusBadge } from "@/components/ui";
import { currentProject, getMemory, listThreads } from "@/lib/api";

export default async function MemoryPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const project = await currentProject();
  const [detail, threads] = await Promise.all([getMemory(project.slug, id), listThreads(project.slug)]);
  if (!detail) notFound();
  const { memory: m, chain, conflicts, episodes } = detail;
  const thread = threads.find((t) => t.id === m.threadId) ?? null;

  return (
    <>
      <PageHeader
        back={{ href: "/memories", label: "Memories" }}
        icon={Brain}
        title={m.statement}
        meta={<><StatusBadge status={m.status} /><Mono className="text-text-3">{m.id}</Mono><CopyButton value={m.id} label="Copy id" /></>}
        actions={<MemoryActions m={m} />}
      />

      <div className="space-y-3">
        {m.status === "stale" && m.staleReason && (
          <Callout tone="warn" title="May be outdated">
            {m.staleReason}. Agents see this warning with the memory. Confirm it if it still holds (it is then checked against the current version), or retire it.
          </Callout>
        )}
        {m.assertion === "claimed" && (
          <Callout tone="warn" title="Unverified agent claim">
            An agent said this about its own work. Recall ranks it below observed and stated facts until someone confirms it.
          </Callout>
        )}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-6">
          <Card>
            <CardHeader title="Evidence" description={`${m.evidence.length} quote${m.evidence.length === 1 ? "" : "s"} from the source`} />
            <CardBody className="space-y-3">
              {m.evidence.map((e) => {
                const episode = episodes.find((ep) => ep.id === e.episodeId);
                return (
                  <SourceQuote key={e.episodeId + e.quote} quote={e.quote}>
                    {episode ? (
                      <details>
                        <summary className="cursor-pointer hover:text-text">
                          {episode.label} · {episode.identity} · {formatDate(episode.occurredAt, true)} <span className="text-text-3">· show conversation</span>
                        </summary>
                        <pre className="mt-2 max-h-80 overflow-auto rounded-md border border-border bg-surface p-3 font-mono text-[12px] leading-relaxed whitespace-pre-wrap text-text-2">{episode.content}</pre>
                        <Link href={`/sources/${encodeURIComponent(episode.id)}`} className="mt-2 inline-block hover:text-text hover:underline">Open source</Link>
                      </details>
                    ) : <Mono className="text-text-3">{e.episodeId}</Mono>}
                  </SourceQuote>
                );
              })}
            </CardBody>
          </Card>

          {conflicts.length > 0 && (
            <Card className="border-warn/40">
              <CardHeader title="Contradicted by" description="Both stay active, and recall shows them together." />
              <ul className="divide-y divide-border">
                {conflicts.map((c) => (
                  <li key={c.id} className="px-5 py-3">
                    <Link href={`/memories/${c.id}`} className="text-[13px] hover:underline">{c.statement}</Link>
                    <p className="mt-0.5 text-[12px] text-text-3">said {formatDate(c.recordedAt)}</p>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {chain.length > 1 && (
            <Card>
              <CardHeader title="Values of this fact" />
              <CardBody><MemoryHistory chain={chain} current={m.id} linkFor={(cid) => `/memories/${cid}`} /></CardBody>
            </Card>
          )}

          <Card>
            <CardHeader title="History" description="Everything that happened to this memory, and who did it." />
            <CardBody><MemoryTimeline events={detail.history} linkFor={(cid) => `/memories/${cid}`} /></CardBody>
          </Card>
        </div>

        <div className="min-w-0 space-y-6">
          <Card>
            <CardHeader title="Details" />
            <div className="px-5 py-1"><MemoryFacts m={m} thread={thread} /></div>
          </Card>
        </div>
      </div>
    </>
  );
}
