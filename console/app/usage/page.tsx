import { ChartColumn } from "lucide-react";
import { Card, CardBody, CardHeader, PageHeader, Stat, Table, TBody, Td, Th, THead, Tr } from "@/components/ui";
import { UsageChart } from "@/components/UsageChart";
import { currentProject, getUsage, listProjects } from "@/lib/api";

const fmt = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 1 });

export default async function UsagePage() {
  const [project, projects] = await Promise.all([currentProject(), listProjects()]);
  const usage = await getUsage(project.slug);
  const ingestCredits = usage.reduce((s, d) => s + d.ingestCredits, 0);
  const recallCredits = usage.reduce((s, d) => s + d.recallCredits, 0);
  const orgMonth = projects.reduce((s, p) => s + p.monthCredits, 0);
  const cap = project.monthlyCreditCap;

  return (
    <>
      <PageHeader
        icon={ChartColumn}
        title="Usage"
        description="Usage over the last 30 days, counted in units. Sending conversation costs 1 unit per 1,000 characters (a re-send of the same text is free); each recall costs 0.1. Units only count usage: nothing is charged."
      />

      <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <Stat label={`${project.name}, this month`} value={fmt(project.monthCredits)}
              hint={cap === null ? "No monthly cap" : `of ${fmt(cap)} cap${project.monthCredits >= cap ? " · reached" : ""}`} />
        <Stat label="Ingest, 30 days" value={fmt(ingestCredits)} hint={`${usage.reduce((s, d) => s + d.ingest, 0).toLocaleString()} sends`} />
        <Stat label="Recall, 30 days" value={fmt(recallCredits)} hint={`${usage.reduce((s, d) => s + d.recall, 0).toLocaleString()} recalls`} />
        <Stat label="Organization, this month" value={fmt(orgMonth)} hint={`${projects.length} project${projects.length === 1 ? "" : "s"}`} />
      </div>

      <Card className="mt-6">
        <CardHeader title="Units per day" description={project.name} />
        <CardBody>
          <UsageChart unit="units" days={usage.map((d) => ({ date: d.date, ingest: d.ingestCredits, recall: d.recallCredits }))} />
        </CardBody>
      </Card>

      <h2 className="mt-8 mb-2 text-[13.5px] font-medium text-text">By project, this month</h2>
      <Table minWidth={420}>
        <THead>
          <Th>Project</Th>
          <Th align="right">Units</Th>
          <Th align="right">Monthly cap</Th>
        </THead>
        <TBody>
          {projects.map((p) => (
            <Tr key={p.slug}>
              <Td>{p.name}</Td>
              <Td align="right">{fmt(p.monthCredits)}</Td>
              <Td align="right" muted>{p.monthlyCreditCap === null ? "uncapped" : fmt(p.monthlyCreditCap)}</Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </>
  );
}
