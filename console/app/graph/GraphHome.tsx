// The console's home: the project's memory as a graph, with the selected node's panel beside it.
import { MessageCircleQuestion } from "lucide-react";
import { AutoRefresh } from "@/components/client";
import { EmptyProject, SetupSteps, type SetupStep } from "@/components/EmptyProject";
import { ButtonLink, Card, CardHeader, timeAgo } from "@/components/ui";
import { currentProject, getGraph, listEpisodes, listKeys, listRequests } from "@/lib/api";
import { weekAgo } from "@/lib/graph";
import { SAMPLE_PREFIX } from "@/lib/sample-data";
import { GraphView } from "./GraphView";
import { GrowthPanel, NodePanel } from "./NodePanel";
import { parseGraphParams } from "./params";

export async function GraphHome({ search, base }: { search: Record<string, string | undefined>; base: string }) {
  const params = parseGraphParams(search);
  const project = await currentProject();
  const [graph, episodes, keys, requests] = await Promise.all([
    // "Everything" asks the server for every thread, not only the open work it starts from.
    getGraph(project.slug, { since: weekAgo(), all: params.view === "all" }),
    listEpisodes(project.slug),
    listKeys(project.slug),
    listRequests(project.slug),
  ]);

  const facts = graph.nodes.filter((n) => n.type === "fact");
  const tending = episodes.filter((e) => e.jobStatus === "queued" || e.jobStatus === "running").length;
  const tendedAt = facts.reduce<string | null>((max, n) => (n.memory && (!max || n.memory.recordedAt > max) ? n.memory.recordedAt : max), null);
  const steps: SetupStep[] = [
    { done: keys.some((k) => !k.revokedAt), label: "Create a project API key", href: "/keys" },
    { done: episodes.length > 0, label: "Send your first conversation", href: "/integrations" },
    { done: facts.length > 0, label: "Wait for memory to be tended", href: "/sources" },
    { done: requests.some((r) => (r.path === "/v1/recall" || r.path === "/v1/ask") && r.status < 400), label: "Ask memory for the first time", href: "/playground" },
  ];
  const setUp = steps.every((s) => s.done);
  // The empty project's three connect steps. Sources an agent sent are neither pasted nor sample;
  // the plugin's backfill labels the sessions it imports "(imported)".
  const sent = episodes.filter((e) => !e.id.startsWith("paste-") && !e.id.startsWith(SAMPLE_PREFIX));
  const connect: SetupStep[] = [
    { done: keys.some((k) => !k.revokedAt), label: "Create a project API key", href: "/keys", detail: "Agents send and recall with it." },
    { done: sent.length > 0, label: "Install the plugin in Claude Code or Codex", href: "/integrations", detail: "/plugin install memvine@memvine" },
    { done: sent.some((e) => e.label?.endsWith("(imported)")), label: "Backfill your past sessions", href: "/integrations", detail: "/memvine:backfill" },
  ];

  return (
    <>
      <header className="mb-5 flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="w-full min-w-0 sm:w-auto sm:flex-1">
          <h1 className="text-[19px] font-semibold tracking-[-0.01em] text-text">{project.name}</h1>
          {project.description && <p className="mt-1 max-w-2xl text-[13.5px] leading-relaxed text-text-2">{project.description}</p>}
          <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-text-3">
            <span>{tendedAt ? `tended ${timeAgo(tendedAt)}` : "not tended yet"}</span>
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

      {facts.length === 0 ? (
        <EmptyProject connect={connect} tending={tending} />
      ) : (
        <>
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
            <div className="min-w-0 lg:sticky lg:top-0">
              <GraphView graph={graph} params={params} base={base} />
            </div>
            <aside className="min-w-0">
              {params.node
                ? <NodePanel project={project.slug} graph={graph} params={params} base={base} />
                : <GrowthPanel graph={graph} params={params} base={base} />}
            </aside>
          </div>
          {!setUp && (
            <Card className="mt-6 max-w-xl">
              <CardHeader title="Get started" description={`${steps.filter((s) => s.done).length} of ${steps.length} done`} />
              <SetupSteps steps={steps} />
            </Card>
          )}
        </>
      )}
    </>
  );
}
