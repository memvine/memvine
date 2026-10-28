import { Activity } from "lucide-react";
import { Badge, Mono, PageHeader, shortDate, Table, TableEmpty, TBody, Td, Th, THead, Tr } from "@/components/ui";
import { currentProject, listRequests } from "@/lib/api";

export default async function RequestsPage() {
  const project = await currentProject();
  const requests = await listRequests(project.slug);
  const refused = requests.filter((r) => r.status >= 400).length;
  const abstained = requests.filter((r) => r.detail?.startsWith("abstained")).length;

  return (
    <>
      <PageHeader
        icon={Activity}
        title="Requests"
        count={requests.length}
        description="Every API call to this project, accepted or refused, newest first. A recall says what it served, or why it abstained."
        meta={refused || abstained ? (
          <>
            {refused > 0 && <Badge tone="warn" dot>{refused} refused</Badge>}
            {abstained > 0 && <Badge tone="neutral" dot>{abstained} recalls abstained</Badge>}
          </>
        ) : undefined}
      />
      <Table minWidth={640} footer={requests.length ? `${requests.length} most recent calls` : undefined}>
        <THead>
          <Th className="w-[160px]">Time (UTC)</Th>
          <Th>Call</Th>
          <Th className="w-[90px]">Status</Th>
          <Th align="right" className="w-[100px]">Latency</Th>
          <Th className="w-[140px]">Key</Th>
        </THead>
        <TBody>
          {requests.length === 0 && <TableEmpty colSpan={5}>No API calls yet.</TableEmpty>}
          {requests.map((r) => (
            <Tr key={r.id}>
              <Td muted nowrap>{shortDate(r.at, true)}</Td>
              <Td className="max-w-0">
                <span className="mr-2 inline-block w-10 font-mono text-[11.5px] text-text-3">{r.method}</span><Mono className="text-text">{r.path}</Mono>
                {r.detail && (
                  <p className={`mt-0.5 truncate pl-12 text-[12px] ${r.detail.startsWith("abstained") ? "text-warn" : "text-text-3"}`} title={r.detail}>{r.detail}</p>
                )}
              </Td>
              <Td><Badge tone={r.status < 400 ? "good" : r.status < 500 ? "warn" : "bad"} dot>{r.status}</Badge></Td>
              <Td align="right" muted>{r.latencyMs} ms</Td>
              <Td nowrap>{r.keyName ?? <span className="text-text-3">console</span>}</Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </>
  );
}
