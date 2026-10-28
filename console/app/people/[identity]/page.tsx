import { UserRound } from "lucide-react";
import { ProfileLines, ProfileMeta, RebuildButton } from "@/components/ProfileLines";
import { Card, CardBody, CardHeader, PageHeader } from "@/components/ui";
import { canManage, currentProject, getMemory, getProfile } from "@/lib/api";

export default async function PersonPage({ params }: { params: Promise<{ identity: string }> }) {
  const identity = decodeURIComponent((await params).identity);
  const project = await currentProject();
  const [profile, manage] = await Promise.all([getProfile(project.slug, identity), canManage()]);
  const ids = [...new Set(profile?.lines.flatMap((l) => l.memoryIds) ?? [])];
  const memories = await Promise.all(ids.map((id) => getMemory(project.slug, id)));
  const statements = Object.fromEntries(memories.filter((d) => d !== null).map((d) => [d.memory.id, d.memory.statement]));

  return (
    <>
      <PageHeader
        back={{ href: "/people", label: "People" }}
        icon={UserRound}
        title={identity}
        description="Restated from memory by the fast model; a line that cites no memory, or a memory it was not given, is dropped. Forgetting or replacing a memory takes its line out at once, and the profile is rebuilt within ten minutes."
        meta={profile ? <ProfileMeta profile={profile} /> : undefined}
        actions={manage ? <RebuildButton subjectKind="person" subject={identity} /> : undefined}
      />
      <Card className="mb-6">
        <CardHeader title="Profile" description="Each line links to the memories it restates." />
        <CardBody>{profile ? <ProfileLines profile={profile} statements={statements} /> : <p className="text-[13px] text-text-3">No profile.</p>}</CardBody>
      </Card>
      {profile?.text && (
        <Card>
          <CardHeader title="As an agent sees it" description="At the start of a session working as this person, before the project profile." />
          <CardBody><pre className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-text-2">{profile.text}</pre></CardBody>
        </Card>
      )}
    </>
  );
}
