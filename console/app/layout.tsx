import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import { FolderPlus } from "lucide-react";
import { ProjectGate } from "@/components/ProjectGate";
import { SampleBanner } from "@/components/SampleData";
import { Sidebar } from "@/components/Sidebar";
import { ButtonLink, Empty } from "@/components/ui";
import { headers } from "next/headers";
import { currentProject, isLive, listProjects, viewer } from "@/lib/api";
import "./globals.css";

export const metadata: Metadata = {
  title: "Memvine Console",
  description: "Keys, memories, sessions and usage for your Memvine projects.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f4f5" },
    { media: "(prefers-color-scheme: dark)", color: "#070708" },
  ],
};

const fonts = `${GeistSans.variable} ${GeistMono.variable}`;

function FirstProject({ canCreate }: { canCreate: boolean }) {
  return (
    <div className="mx-auto mt-16 max-w-md rounded-xl border border-border bg-surface">
      <Empty
        icon={FolderPlus}
        title={canCreate ? "Create your first project" : "No projects yet"}
        action={<ButtonLink href="/projects" variant="primary">Go to Projects</ButtonLink>}
      >
        {canCreate
          ? "A project holds the memory of one app, repository or team. Create one, copy its API key, and connect your agent."
          : "A project holds the memory of one app, repository or team. Ask an owner or admin of this organization to create one."}
      </Empty>
    </div>
  );
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  // The sign-in page renders without the app around it (and before any key exists).
  const incoming = await headers();
  if (incoming.get("x-memvine-login") === "1") {
    return (
      <html lang="en" className={fonts}>
        <body><main className="min-h-screen px-4">{children}</main></body>
      </html>
    );
  }
  const [projects, project, who] = await Promise.all([listProjects(), currentProject(), viewer()]);
  const credits = project.slug ? { used: project.monthCredits, cap: project.monthlyCreditCap } : null;
  return (
    <html lang="en" className={fonts}>
      <body>
        <div className="md:flex md:h-screen">
          <Sidebar projects={projects} current={project.slug} live={isLive} viewer={who} credits={credits} />
          <main className="scroll-quiet min-w-0 flex-1 bg-panel md:my-2 md:mr-2 md:overflow-y-auto md:rounded-xl md:border md:border-border">
            <div className="mx-auto max-w-[1180px] px-4 pt-6 pb-16 sm:px-6 md:px-8 md:pt-8">
              <ProjectGate hasProjects={projects.length > 0} fallback={<FirstProject canCreate={who?.canManage ?? true} />}>
                {project.slug && (project.sampleSourceCount ?? 0) > 0 && <SampleBanner sources={project.sampleSourceCount ?? 0} />}
                {children}
              </ProjectGate>
            </div>
          </main>
        </div>
      </body>
    </html>
  );
}
