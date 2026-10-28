import Link from "next/link";
import { notFound } from "next/navigation";
import { FileText } from "lucide-react";
import { CopyButton } from "@/components/client";
import { AssertionBadge, Card, CardHeader, Empty, formatDate, Mono, PageHeader, StatusBadge } from "@/components/ui";
import { currentProject, getEpisode, listMemories } from "@/lib/api";

export default async function SourcePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const project = await currentProject();
  const episode = await getEpisode(project.slug, decodeURIComponent(id));
  if (!episode) notFound();
  const memories = (await listMemories(project.slug)).filter((m) => m.evidence.some((e) => e.episodeId === episode.id));

  return (
    <>
      <PageHeader
        back={{ href: "/sources", label: "Sources" }}
        icon={FileText}
        title={episode.label || episode.id}
        description={`${episode.identity ?? "Unknown identity"} · ${formatDate(episode.occurredAt, true)}`}
        meta={<><Mono className="text-text-3">{episode.id}</Mono><CopyButton value={episode.id} label="Copy source id" /></>}
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="min-w-0">
          <CardHeader title="Content as sent" action={<CopyButton value={episode.content} label="Copy content" />} />
          <pre className="scroll-quiet max-h-[70vh] overflow-auto px-5 py-4 font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap text-text-2">{episode.content}</pre>
        </Card>
        <Card className="min-w-0">
          <CardHeader title={`${memories.length} ${memories.length === 1 ? "memory" : "memories"} from this source`} />
          {memories.length === 0 ? (
            <Empty>Nothing yet. Synthesis may still be running, or the text held no durable facts.</Empty>
          ) : (
            <ul className="divide-y divide-border">
              {memories.map((m) => (
                <li key={m.id}>
                  <Link href={`/memories?open=${m.id}`} className="flex items-start gap-3 px-5 py-3 transition-colors hover:bg-surface-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-[13.5px] text-text">{m.statement}</p>
                      <p className="mt-1 border-l-2 border-border-strong pl-2 text-[12.5px] text-text-3">“{m.evidence.find((e) => e.episodeId === episode.id)?.quote}”</p>
                    </div>
                    <div className="hidden shrink-0 gap-1 sm:flex"><StatusBadge status={m.status} /><AssertionBadge assertion={m.assertion} /></div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
