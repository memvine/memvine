import { ArrowRight, KeyRound } from "lucide-react";
import { revokeKey } from "@/app/actions";
import { Badge, button, ButtonLink, Card, CardBody, CardHeader, formatDate, Mono, PageHeader, Table, TableEmpty, TBody, Td, Th, THead, timeAgo, Tr } from "@/components/ui";
import { canManage, currentProject, listKeys, publicApiUrl, viewerEmail } from "@/lib/api";
import { describeAgent } from "@/lib/agent";
import { CreateKeyForm } from "./CreateKeyForm";

function agentLabel(userAgent: string | null | undefined): string {
  const a = describeAgent(userAgent);
  return a ? `via ${a.label}${a.version ? ` ${a.version}` : ""}` : "";
}

export default async function KeysPage() {
  const project = await currentProject();
  const [keys, manage, email] = await Promise.all([listKeys(project.slug), canManage(), viewerEmail()]);
  const live = keys.filter((k) => !k.revokedAt).length;

  return (
    <>
      <PageHeader
        icon={KeyRound}
        title="API keys"
        count={live}
        description={`Project keys let agents, plugins and your backend ingest and recall memory in ${project.name}. A key belongs to one project and never moves.`}
        actions={<ButtonLink href="/connect" variant="secondary">Connect your agent, step by step <ArrowRight aria-hidden /></ButtonLink>}
      />

      <Card className="mb-6">
        <CardHeader title="Create a key" description="Name it after where it will run." />
        <CardBody><CreateKeyForm apiUrl={publicApiUrl()} email={email} /></CardBody>
      </Card>

      <Table minWidth={680} footer="Revoking a key stops every plugin and service that uses it on its next call. Organization keys live under Settings.">
        <THead>
          <Th>Name</Th>
          <Th>Key</Th>
          <Th>Created</Th>
          <Th>Last used</Th>
          <Th align="right"><span className="sr-only">Actions</span></Th>
        </THead>
        <TBody>
          {keys.length === 0 && <TableEmpty colSpan={5}>No keys yet. Create one above to connect an agent.</TableEmpty>}
          {keys.map((k) => (
            <Tr key={k.id} className={k.revokedAt ? "text-text-3" : ""}>
              <Td className={k.revokedAt ? "" : "font-medium"}>{k.name}</Td>
              <Td><Mono>{k.masked}</Mono></Td>
              <Td muted nowrap>{formatDate(k.createdAt)}</Td>
              <Td muted nowrap>
                {timeAgo(k.lastUsedAt)}
                {k.lastUsedIp && <span className="block font-mono text-[11.5px] text-text-3">from {k.lastUsedIp}</span>}
                {describeAgent(k.lastUserAgent) && <span className="block text-[11.5px] text-text-3">{agentLabel(k.lastUserAgent)}</span>}
              </Td>
              <Td align="right">
                {k.revokedAt ? (
                  <Badge>revoked {formatDate(k.revokedAt)}</Badge>
                ) : manage && (
                  <form action={revokeKey}>
                    <input type="hidden" name="id" value={k.id} />
                    <button type="submit" className={button("danger", "sm")}>Revoke</button>
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
