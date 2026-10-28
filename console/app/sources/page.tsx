import { FileText, RotateCcw } from "lucide-react";
import { retryJob } from "@/app/actions";
import { AutoRefresh } from "@/components/client";
import { Badge, button, Mono, PageHeader, plural, RowLink, shortDate, Table, TableEmpty, TBody, Td, Th, THead, Tr } from "@/components/ui";
import { currentProject, listEpisodes } from "@/lib/api";
import type { EpisodeSummary } from "@/lib/types";

/** What a source's synthesis is doing: its label, tone, and the line under it. */
function progress(e: EpisodeSummary): { label: string; tone: "good" | "neutral" | "info" | "bad" | "warn"; note: string | null } {
  switch (e.jobStatus) {
    case "queued":
      return e.jobError
        ? { label: "Queued · retrying", tone: "warn",
            note: `Attempt ${e.jobAttempts ?? 1} failed${e.jobRetryAt ? `; next try ${shortDate(e.jobRetryAt, true)} UTC` : ""}` }
        : { label: "Queued", tone: "neutral", note: "Waiting for the synthesis worker" };
    case "running":
      return { label: "Synthesizing", tone: "info", note: "Extracting memory from this source now" };
    case "done": {
      const made = [plural(e.memoryCount, "memory", "memories"), plural(e.threadCount ?? 0, "thread page")];
      const r = e.jobResult;
      const extra = r && [
        r.dropped_no_quote ? `${r.dropped_no_quote} dropped (no quote)` : "",
        r.relations ? Object.entries(r.relations).map(([k, v]) => `${v} ${k}`).join(", ") : "",
      ].filter(Boolean).join(" · ");
      return { label: "Done", tone: "good", note: `${made.join(" · ")}${extra ? ` · ${extra}` : ""}` };
    }
    case "failed":
      return { label: "Failed", tone: "bad", note: null };
    default:
      return { label: "Not queued", tone: "neutral", note: null };
  }
}

export default async function SourcesPage() {
  const project = await currentProject();
  const episodes = await listEpisodes(project.slug);
  const count = (s: EpisodeSummary["jobStatus"]) => episodes.filter((e) => e.jobStatus === s).length;
  const [queued, running, failed] = [count("queued"), count("running"), count("failed")];
  const pending = queued + running;

  return (
    <>
      <PageHeader
        icon={FileText}
        title="Sources"
        count={episodes.length}
        description="Every conversation or document sent to this project, and what synthesis made of it. Delete a source to remove the memories drawn only from it."
        meta={
          <>
            {running > 0 && <Badge tone="info" dot>{running} synthesizing</Badge>}
            {queued > 0 && <Badge tone="neutral" dot>{queued} queued</Badge>}
            {failed > 0 && <Badge tone="bad" dot>{failed} failed</Badge>}
            <AutoRefresh active={pending > 0} label="Updating every 5 s" />
          </>
        }
      />
      <Table minWidth={760} footer={episodes.length ? `${episodes.length} sources · open one to read what was sent` : undefined}>
        <THead>
          <Th>Source</Th>
          <Th className="w-[150px]">Occurred (UTC)</Th>
          <Th className="w-[300px]">Synthesis</Th>
          <Th align="right" className="w-[100px]">Memories</Th>
        </THead>
        <TBody>
          {episodes.length === 0 && <TableEmpty colSpan={4}>Nothing sent yet. Install a plugin or call the ingest API from Integrations.</TableEmpty>}
          {episodes.map((e) => {
            const p = progress(e);
            const error = e.jobError && (e.jobStatus === "failed" || e.jobStatus === "queued") ? e.jobError : null;
            return (
              <Tr key={e.id} interactive>
                <Td className="max-w-0 py-3">
                  <RowLink href={`/sources/${encodeURIComponent(e.id)}`} className="block truncate font-medium">{e.label || e.id}</RowLink>
                  <div className="mt-0.5 truncate text-[12px] text-text-3">
                    {e.identity && `${e.identity} · `}<Mono className="text-[11.5px] text-text-3">{e.id}</Mono> · {(e.bytes / 1000).toFixed(1)} kB
                  </div>
                </Td>
                <Td muted nowrap>{shortDate(e.occurredAt, true)}</Td>
                <Td className="py-3">
                  <div className="relative z-10 flex items-center gap-2">
                    <Badge tone={p.tone} dot>{p.label}</Badge>
                    {e.jobStatus === "failed" && e.jobId !== null && (
                      <form action={retryJob}>
                        <input type="hidden" name="jobId" value={e.jobId} />
                        <button className={button("secondary", "sm")}><RotateCcw aria-hidden /> Retry</button>
                      </form>
                    )}
                  </div>
                  {p.note && <p className="mt-1 truncate text-[12px] text-text-3" title={p.note}>{p.note}</p>}
                  {error && (
                    <details className="relative z-10 mt-1 max-w-[290px] text-[12px]">
                      <summary className={`cursor-pointer truncate ${e.jobStatus === "failed" ? "text-bad" : "text-warn"}`} title={error.split("\n")[0]}>
                        {error.split("\n")[0]}
                      </summary>
                      <pre className="scroll-quiet mt-1 max-h-48 overflow-auto rounded-md border border-border bg-surface-2 p-2 font-mono text-[11px] whitespace-pre-wrap text-text-2">{error}</pre>
                    </details>
                  )}
                </Td>
                <Td align="right" muted>{e.jobStatus === "done" ? e.memoryCount : "–"}</Td>
              </Tr>
            );
          })}
        </TBody>
      </Table>
    </>
  );
}
