import { Cog, RotateCcw } from "lucide-react";
import { retryJob } from "@/app/actions";
import { AutoRefresh } from "@/components/client";
import { Badge, button, Mono, PageHeader, RowLink, shortDate, Table, TableEmpty, TBody, Td, Th, THead, Tr } from "@/components/ui";
import { currentProject, listEpisodes } from "@/lib/api";
import type { JobStatus } from "@/lib/types";

const TONE: Record<JobStatus, "neutral" | "info" | "good" | "bad"> = { queued: "neutral", running: "info", done: "good", failed: "bad" };

/** The synthesis jobs behind "tending": one per source, with attempts, errors and retry. */
export default async function JobsPage() {
  const project = await currentProject();
  const jobs = (await listEpisodes(project.slug)).filter((e) => e.jobId !== null);
  const pending = jobs.filter((j) => j.jobStatus === "queued" || j.jobStatus === "running").length;
  return (
    <>
      <PageHeader
        icon={Cog}
        title="Jobs"
        count={jobs.length}
        description="The background work that tends memory: one synthesis job per source. Failed jobs can be retried."
        meta={<AutoRefresh active={pending > 0} label="Updating every 5 s" />}
      />
      <Table minWidth={720} footer={jobs.length ? `${jobs.length} jobs` : undefined}>
        <THead>
          <Th className="w-[80px]">Job</Th>
          <Th>Source</Th>
          <Th className="w-[110px]">Status</Th>
          <Th className="w-[90px]" align="right">Attempts</Th>
          <Th className="w-[110px]">Sent</Th>
          <Th className="w-[90px]"><span className="sr-only">Retry</span></Th>
        </THead>
        <TBody>
          {jobs.length === 0 && <TableEmpty colSpan={6}>No jobs yet. Each source sent to this project queues one.</TableEmpty>}
          {jobs.map((j) => (
            <Tr key={j.id} interactive>
              <Td><Mono>#{j.jobId}</Mono></Td>
              <Td className="max-w-0">
                <RowLink href={`/sources/${encodeURIComponent(j.id)}`} className="block truncate">{j.label ?? j.id}</RowLink>
                {j.jobError && <p className="mt-0.5 truncate font-mono text-[11.5px] text-bad">{j.jobError.split("\n")[0]}</p>}
              </Td>
              <Td>{j.jobStatus ? <Badge tone={TONE[j.jobStatus]} dot>{j.jobStatus}</Badge> : <span className="text-text-3">—</span>}</Td>
              <Td align="right" muted>{j.jobAttempts ?? 0}</Td>
              <Td muted nowrap>{shortDate(j.occurredAt, true)}</Td>
              <Td>
                {j.jobStatus === "failed" && (
                  <form action={retryJob} className="relative z-10">
                    <input type="hidden" name="jobId" value={j.jobId!} />
                    <button className={button("secondary", "sm")}><RotateCcw aria-hidden /> Retry</button>
                  </form>
                )}
              </Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </>
  );
}
