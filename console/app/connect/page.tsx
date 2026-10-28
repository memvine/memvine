import { PlugZap } from "lucide-react";
import { PageHeader } from "@/components/ui";
import { currentProject, listKeys, listProjects, publicApiUrl, viewerEmail } from "@/lib/api";
import { ConnectFlow } from "./ConnectFlow";

// "Connect your agent": pick the project, get your own key, paste the steps for your client,
// and watch for the first session. Written so a teammate can set up without help.
export default async function ConnectPage() {
  const [project, projects, email] = await Promise.all([currentProject(), listProjects(), viewerEmail()]);
  const keys = await listKeys(project.slug);
  const live = keys.filter((k) => !k.revokedAt);
  // Your key: the newest live key you created. Keys made before keys had creators (or by an
  // organization key) belong to nobody, so a teammate is offered a key of their own.
  const mine = live.find((k) => k.createdByEmail && k.createdByEmail.toLowerCase() === email.toLowerCase()) ?? null;

  return (
    <>
      <PageHeader
        icon={PlugZap}
        title="Connect your agent"
        description="Four steps, about two minutes. Your agent's sessions become shared memory for this project, and every new session starts briefed from it."
      />
      <ConnectFlow
        key={project.slug}
        project={{ slug: project.slug, name: project.name }}
        projects={projects.map((p) => ({ slug: p.slug, name: p.name }))}
        apiUrl={publicApiUrl()}
        email={email}
        mine={mine && { id: mine.id, name: mine.name, masked: mine.masked, createdAt: mine.createdAt }}
        otherKeys={live.length - (mine ? 1 : 0)}
      />
    </>
  );
}
