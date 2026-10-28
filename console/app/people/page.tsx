import { UserRound } from "lucide-react";
import { ProfileMeta, RebuildButton } from "@/components/ProfileLines";
import { Mono, PageHeader, RowLink, Table, TableEmpty, TBody, Td, Th, THead, timeAgo, Tr } from "@/components/ui";
import { canManage, currentProject, listIdentities, listProfiles } from "@/lib/api";

export default async function PeoplePage() {
  const project = await currentProject();
  const [identities, profiles, manage] = await Promise.all([listIdentities(project.slug), listProfiles(project.slug), canManage()]);
  const byPerson = new Map(profiles.filter((p) => p.subjectKind === "person").map((p) => [p.subject, p]));
  const people = [...new Set([...identities.map((i) => i.identity), ...byPerson.keys()])];
  const lastSeen = new Map(identities.map((i) => [i.identity, i.lastSeenAt]));

  return (
    <>
      <PageHeader
        icon={UserRound}
        title="People"
        count={people.length}
        description="Each person's profile: their preferences, the rules and decisions they set or own, and what they own, restated from memory with every line citing its memories. An agent working as that person gets it at the start of each session."
        actions={manage ? <RebuildButton /> : undefined}
      />
      <Table minWidth={640}>
        <THead>
          <Th className="w-[220px]">Person</Th>
          <Th>Profile</Th>
          <Th className="w-[200px]">Built</Th>
          <Th className="w-[110px]">Last seen</Th>
        </THead>
        <TBody>
          {people.length === 0 && <TableEmpty colSpan={4}>No people yet. An identity appears the first time an agent sends or recalls as someone.</TableEmpty>}
          {people.map((person) => {
            const p = byPerson.get(person);
            return (
              <Tr key={person} interactive>
                <Td><RowLink href={`/people/${encodeURIComponent(person)}`}><Mono className="text-text">{person}</Mono></RowLink></Td>
                <Td muted className="max-w-0 truncate text-[12.5px]">
                  {p?.lines.length ? `${p.lines[0].text}${p.lines.length > 1 ? ` (+${p.lines.length - 1} more)` : ""}` : "—"}
                </Td>
                <Td>{p ? <ProfileMeta profile={p} /> : <span className="text-[12px] text-text-3">not asked for yet</span>}</Td>
                <Td muted nowrap>{timeAgo(lastSeen.get(person) ?? null)}</Td>
              </Tr>
            );
          })}
        </TBody>
      </Table>
    </>
  );
}
