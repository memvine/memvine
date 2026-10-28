import { Users } from "lucide-react";
import { deleteAudience } from "@/app/actions";
import {
  Badge, button, Card, CardBody, CardHeader, Mono, PageHeader, plural, Table, TableEmpty, TBody, Td, Th, THead, timeAgo, Tr,
} from "@/components/ui";
import { canManage, currentProject, listAudiences, listIdentities } from "@/lib/api";
import type { AudienceCondition } from "@/lib/types";
import { AudienceForm } from "./AudienceForm";

function describe(c: AudienceCondition): string {
  const value = Array.isArray(c.value) ? c.value.join(", ") : c.value;
  const op = { is: "is", is_not: "is not", is_one_of: "is one of", is_not_one_of: "is not one of", exists: "is set", does_not_exist: "is not set" }[c.op];
  return `${c.trait} ${op}${value ? ` ${value}` : ""}`;
}

export default async function AudiencesPage() {
  const project = await currentProject();
  const [audiences, identities, manage] = await Promise.all([listAudiences(project.slug), listIdentities(project.slug), canManage()]);

  return (
    <>
      <PageHeader
        icon={Users}
        title="Audiences"
        count={audiences.length}
        description="Who may read which memory. Memory sent without an audience is visible to everyone in the project; memory sent to an audience is recalled only by the identities it reaches."
      />

      {manage && (
        <Card className="mb-6">
          <CardHeader title="Create or replace an audience" description="Saving an existing slug replaces it." />
          <CardBody><AudienceForm /></CardBody>
        </Card>
      )}

      <h2 className="mb-2 text-[13.5px] font-medium text-text">{plural(audiences.length, "audience")}</h2>
      <Table minWidth={600} className="mb-8">
        <THead>
          <Th className="w-[200px]">Audience</Th>
          <Th>Who it reaches</Th>
          <Th align="right" className="w-[110px]">Memories</Th>
          {manage && <Th align="right" className="w-[100px]"><span className="sr-only">Actions</span></Th>}
        </THead>
        <TBody>
          {audiences.length === 0 && <TableEmpty colSpan={manage ? 4 : 3}>No audiences yet. Everything in this project is visible to every identity.</TableEmpty>}
          {audiences.map((a) => (
            <Tr key={a.slug}>
              <Td><span className="flex items-center gap-2"><Mono className="text-text">{a.slug}</Mono><Badge tone="outline">{a.kind}</Badge></span></Td>
              <Td muted className="text-[12.5px]">
                {a.kind === "fixed" ? (a.members.length ? a.members.join(", ") : "No members yet") : a.conditions.map(describe).join(" and ")}
              </Td>
              <Td align="right" muted>{a.memoryCount}</Td>
              {manage && (
                <Td align="right">
                  <form action={deleteAudience}>
                    <input type="hidden" name="slug" value={a.slug} />
                    <button className={button("danger", "sm")} disabled={a.memoryCount > 0} title={a.memoryCount ? "Memories still use this audience" : undefined}>Delete</button>
                  </form>
                </Td>
              )}
            </Tr>
          ))}
        </TBody>
      </Table>

      <h2 className="mb-2 text-[13.5px] font-medium text-text">{plural(identities.length, "identity", "identities")}</h2>
      <Table minWidth={600}>
        <THead>
          <Th>Identity</Th>
          <Th>Traits</Th>
          <Th className="w-[100px]">Origin</Th>
          <Th className="w-[120px]">Last seen</Th>
        </THead>
        <TBody>
          {identities.length === 0 && <TableEmpty colSpan={4}>No identities yet. One is created the first time an identity sends or recalls.</TableEmpty>}
          {identities.map((i) => (
            <Tr key={i.identity}>
              <Td><Mono className="text-text">{i.identity}</Mono></Td>
              <Td>
                {Object.keys(i.traits).length ? (
                  <span className="flex flex-wrap gap-1">{Object.entries(i.traits).map(([k, v]) => <Badge key={k}>{k}: {v}</Badge>)}</span>
                ) : <span className="text-text-3">none</span>}
              </Td>
              <Td muted>{i.origin}</Td>
              <Td muted nowrap>{timeAgo(i.lastSeenAt)}</Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </>
  );
}
