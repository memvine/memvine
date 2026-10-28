// The side panel of the graph: the selected node's receipt, its history, file, thread, sources and
// actions; or, with nothing selected, what grew this week.
import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowUpRight, FileCode2, FileText, Sparkles, X } from "lucide-react";
import { MemoryHistory } from "@/components/MemoryPanel";
import { Receipt } from "@/components/Receipt";
import { ThreadKindTag } from "@/components/thread-bits";
import { Badge, CATEGORY_LABEL, Eyebrow, formatDate, Mono, plural, shortDate, ThreadStatusBadge, timeAgo } from "@/components/ui";
import { getMemory, getRelated } from "@/lib/api";
import { edgeVerb, threadIdOf, threadNodeId } from "@/lib/graph";
import { personName } from "@/lib/trust";
import type { MemoryGraph, MemoryItem, RelatedEdge } from "@/lib/types";
import { graphHref, type GraphParams } from "./params";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-border px-4 py-4 sm:px-5">
      <Eyebrow className="mb-2.5">{title}</Eyebrow>
      {children}
    </section>
  );
}

function PanelShell({ eyebrow, title, close, children }: { eyebrow: ReactNode; title?: ReactNode; close: string; children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface" data-panel>
      <div className="relative px-4 pt-4 pb-3 sm:px-5">
        <Eyebrow className="pr-10">{eyebrow}</Eyebrow>
        {title && <div className="mt-2 pr-8">{title}</div>}
        <Link href={close} scroll={false} aria-label="Close" className="absolute top-3 right-3 grid size-8 place-items-center rounded-lg text-text-3 transition-colors hover:bg-surface-2 hover:text-text">
          <X className="size-4" aria-hidden />
        </Link>
      </div>
      {children}
    </div>
  );
}

/** Facts on the graph tied to a node by one edge kind, as receipts. */
function FactList({ facts, link }: { facts: MemoryItem[]; link: (id: string) => string }) {
  if (facts.length === 0) return <p className="text-[13px] text-text-3">None.</p>;
  return (
    <ul className="space-y-3">
      {facts.map((m) => <li key={m.id}><Receipt m={m} href={link(m.id)} showCategory followParam="node" readOnly={m.status === "superseded"} /></li>)}
    </ul>
  );
}

async function FactPanel({ project, id, base, params, graph }: { project: string; id: string; base: string; params: GraphParams; graph: MemoryGraph }) {
  const [detail, related] = await Promise.all([getMemory(project, id), getRelated(project, id)]);
  const close = graphHref(base, { ...params, node: undefined });
  if (!detail) {
    return <PanelShell eyebrow="Not found" close={close}><p className="px-5 pb-5 text-[13px] text-text-3">This memory is gone, or belongs to another project.</p></PanelShell>;
  }
  const m = detail.memory;
  const link = (nid: string) => graphHref(base, { ...params, node: nid });
  const edges: RelatedEdge[] = related?.edges ?? [];
  const files = edges.flatMap((e) => (e.kind === "anchored" && e.file ? [e.file] : []));
  const threads = edges.flatMap((e) => (e.kind === "decided_in" && e.thread ? [e.thread] : []));
  const links = edges.flatMap((e) => (e.item && (e.kind === "contradicts" || e.kind === "extends" || e.kind === "derived")
    ? [{ kind: e.kind, direction: e.direction, item: e.item }] : []));
  const onGraph = (nid: string) => graph.nodes.some((n) => n.id === nid);

  return (
    <PanelShell eyebrow={<>{m.category ? CATEGORY_LABEL[m.category].one : "Memory"} · {m.kind}</>} close={close}>
      <div className="px-4 pb-4 sm:px-5">
        <Receipt m={m} size="lg" open followParam="node" readOnly={m.status === "superseded"} />
        {m.status === "superseded" && m.supersededBy && (
          <p className="mt-3 text-[12.5px] text-text-3">
            Replaced on {formatDate(m.validTo)}. <Link href={link(m.supersededBy)} scroll={false} className="text-text-2 underline-offset-2 hover:text-text hover:underline">See what holds now</Link>
          </p>
        )}
      </div>

      {detail.chain.length > 1 && (
        <Section title="History">
          <MemoryHistory chain={detail.chain} current={m.id} linkFor={link} />
        </Section>
      )}

      {files.length > 0 && (
        <Section title={files.length === 1 ? "File" : "Files"}>
          <ul className="space-y-2">
            {files.map((f) => (
              <li key={`${f.repo}:${f.path}`} className="flex items-start gap-2.5">
                <FileCode2 className={`mt-0.5 size-4 shrink-0 ${f.current === false ? "text-warn" : "text-text-3"}`} strokeWidth={1.75} aria-hidden />
                <div className="min-w-0">
                  <Mono className="block break-all text-text">{f.path}</Mono>
                  <p className="mt-0.5 text-[12px] text-text-3">
                    {f.repo && <>{f.repo} · </>}
                    {f.version ? <>anchored at <Mono className="text-text-3">{f.version.slice(0, 7)}</Mono></> : "anchored"}
                    {f.anchoredAt && <> · {shortDate(f.anchoredAt)}</>}
                    {f.current === false && f.currentVersion && <> · now <Mono className="text-text-3">{f.currentVersion.slice(0, 7)}</Mono></>}
                  </p>
                  <div className="mt-1">
                    {f.current === true && <Badge tone="good" dot>current</Badge>}
                    {f.current === false && <Badge tone="warn" dot>{f.currentVersion ? "changed since" : "deleted since"}</Badge>}
                    {f.current === null && <Badge tone="neutral" dot>not checked since</Badge>}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {threads.length > 0 && (
        <Section title="Thread">
          {threads.map((t) => (
            <div key={t.id} className="space-y-1.5">
              <div className="flex flex-wrap items-center gap-2">
                <ThreadStatusBadge status={t.status} />
                <ThreadKindTag kind={t.kind} />
                <Mono className="text-text-3">{t.key}</Mono>
              </div>
              <Link href={`/threads/${t.id}`} className="block text-[13.5px] font-medium text-text hover:underline">{t.title}</Link>
              <p className="line-clamp-3 text-[12.5px] leading-relaxed text-text-2">{t.summary}</p>
              {onGraph(threadNodeId(t.id)) && <Link href={link(threadNodeId(t.id))} scroll={false} className="text-[12.5px] text-text-3 hover:text-text hover:underline">Show its graph</Link>}
            </div>
          ))}
        </Section>
      )}

      {links.length > 0 && (
        <Section title="Related">
          <ul className="space-y-3.5">
            {links.map((e) => (
              <li key={`${e.kind}-${e.direction}-${e.item.id}`}>
                <p className={`mb-1 text-[11.5px] font-medium ${e.kind === "contradicts" ? "text-bad" : "text-text-3"}`}>{edgeVerb(e.kind, e.direction)}</p>
                <Receipt m={e.item} href={link(e.item.id)} followParam="node" readOnly={e.item.status === "superseded"} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      {detail.episodes.length > 0 && (
        <Section title={detail.episodes.length === 1 ? "Source" : "Sources"}>
          <ul className="-mx-2">
            {detail.episodes.map((ep) => (
              <li key={ep.id}>
                <Link href={`/sources/${encodeURIComponent(ep.id)}`} className="flex items-start gap-2.5 rounded-lg px-2 py-1.5 transition-colors hover:bg-surface-2">
                  <FileText className="mt-0.5 size-4 shrink-0 text-text-3" strokeWidth={1.75} aria-hidden />
                  <span className="min-w-0">
                    <span className="block truncate text-[13px] text-text">{ep.label ?? ep.id}</span>
                    <span className="block text-[12px] text-text-3">{ep.identity ? `${personName(ep.identity)} · ` : ""}{formatDate(ep.occurredAt, true)}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <div className="flex items-center border-t border-border px-4 py-3 sm:px-5">
        <Link href={`/memories/${m.id}`} className="inline-flex items-center gap-1 text-[12.5px] text-text-3 hover:text-text">Open its page <ArrowUpRight className="size-3.5" aria-hidden /></Link>
      </div>
    </PanelShell>
  );
}

/** The facts on the graph joined to `id` by an edge of one kind. */
function factsBy(graph: MemoryGraph, id: string, kind: string): MemoryItem[] {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  return graph.edges
    .filter((e) => e.kind === kind && e.to === id)
    .flatMap((e) => byId.get(e.from)?.memory ?? [])
    .sort((a, b) => Number(a.status === "superseded") - Number(b.status === "superseded") || b.recordedAt.localeCompare(a.recordedAt));
}

export async function NodePanel({ project, graph, params, base }: { project: string; graph: MemoryGraph; params: GraphParams; base: string }) {
  const id = params.node!;
  const node = graph.nodes.find((n) => n.id === id);
  const close = graphHref(base, { ...params, node: undefined });
  const link = (nid: string) => graphHref(base, { ...params, node: nid });
  if (!node || node.type === "fact") return <FactPanel project={project} id={id} base={base} params={params} graph={graph} />;

  if (node.type === "thread" && node.thread) {
    const t = node.thread;
    const facts = factsBy(graph, id, "decided_in");
    return (
      <PanelShell eyebrow="Thread" close={close} title={<h2 className="text-[16px] font-semibold leading-snug text-text">{t.title}</h2>}>
        <div className="space-y-2 px-4 pb-4 sm:px-5">
          <div className="flex flex-wrap items-center gap-2"><ThreadStatusBadge status={t.status} /><ThreadKindTag kind={t.kind} /><Mono className="text-text-3">{t.key}</Mono></div>
          <p className="text-[13px] leading-relaxed text-text-2">{t.summary}</p>
          <Link href={`/threads/${threadIdOf(t.id)}`} className="inline-flex items-center gap-1 text-[12.5px] text-text-2 hover:text-text hover:underline">Open the thread page <ArrowUpRight className="size-3.5" aria-hidden /></Link>
        </div>
        <Section title={`Decided here · ${facts.length}`}><FactList facts={facts} link={link} /></Section>
      </PanelShell>
    );
  }

  if (node.type === "file" && node.file) {
    const f = node.file;
    const anchors = graph.edges.filter((e) => e.kind === "anchored" && e.to === id);
    const changed = f.deleted || anchors.some((e) => e.current === false);
    const facts = factsBy(graph, id, "anchored");
    return (
      <PanelShell eyebrow="File" close={close} title={<Mono className="block break-all text-[14px] text-text">{f.path}</Mono>}>
        <div className="space-y-2 px-4 pb-4 sm:px-5">
          <p className="text-[12.5px] text-text-3">{f.repo && <>{f.repo} · </>}{f.version ? <>latest version <Mono className="text-text-3">{f.version.slice(0, 7)}</Mono></> : "no version reported"}</p>
          {f.deleted ? <Badge tone="warn" dot>deleted</Badge>
            : changed ? <Badge tone="warn" dot>changed since some facts below were learned</Badge>
            : <Badge tone="good" dot>current</Badge>}
        </div>
        <Section title={`Facts that depend on it · ${facts.length}`}><FactList facts={facts} link={link} /></Section>
      </PanelShell>
    );
  }

  const identity = id.replace(/^person:/, "");
  const facts = graph.nodes.flatMap((n) => (n.memory && (n.saidBy.includes(identity) || n.memory.evidence.some((e) => e.identity === identity)) ? [n.memory] : []));
  return (
    <PanelShell eyebrow="Person" close={close} title={<h2 className="text-[16px] font-semibold text-text">{personName(identity)}</h2>}>
      <div className="px-4 pb-4 sm:px-5"><Mono className="text-text-3">{identity}</Mono></div>
      <Section title={`Said · ${facts.length}`}><FactList facts={facts} link={link} /></Section>
    </PanelShell>
  );
}
/** With nothing selected: how to read the graph, and what grew this week. */
export function GrowthPanel({ graph, params, base }: { graph: MemoryGraph; params: GraphParams; base: string }) {
  const grown = graph.nodes
    .flatMap((n) => (n.memory && n.grew && n.memory.status !== "superseded" ? [n.memory] : []))
    .sort((a, b) => b.recordedAt.localeCompare(a.recordedAt));
  const link = (nid: string) => graphHref(base, { ...params, node: nid });
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface">
      <div className="px-4 pt-4 pb-3 sm:px-5">
        <Eyebrow className="flex items-center gap-1.5"><Sparkles className="size-3.5" aria-hidden /> Grew this week · {grown.length}</Eyebrow>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-text-3">Tap a node to see its receipt, its history and where it came from. Drag to pan; pinch or scroll to zoom.</p>
      </div>
      {grown.length === 0 ? (
        <p className="border-t border-border px-5 py-6 text-[13px] text-text-3">Nothing new this week.</p>
      ) : (
        <ul className="divide-y divide-border border-t border-border">
          {grown.slice(0, 6).map((m) => (
            <li key={m.id} className="px-4 py-3 sm:px-5"><Receipt m={m} href={link(m.id)} showCategory followParam="node" /></li>
          ))}
        </ul>
      )}
      {grown.length > 6 && (
        <div className="border-t border-border px-4 py-2.5 text-[12.5px] sm:px-5">
          <Link href={graphHref(base, { ...params, grew: true })} scroll={false} className="text-text-3 hover:text-text">Show only growth on the graph</Link>
        </div>
      )}
    </div>
  );
}
