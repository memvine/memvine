import { FolderKanban } from "lucide-react";
import { Callout, Empty, FilterBar, formatDate, PageHeader, SearchField, Select, timeAgo } from "@/components/ui";
import { canManage, currentProject, listProjects, publicApiUrl, viewerEmail } from "@/lib/api";
import { NewProject } from "./NewProject";
import { ProjectRow } from "./ProjectRow";

export default async function ProjectsPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const { q = "", tag = "", missing = "" } = await searchParams;
  const [projects, current, manage, email] = await Promise.all([listProjects(), currentProject(), canManage(), viewerEmail()]);
  const needle = q.trim().toLowerCase();
  const shown = projects.filter(
    (p) => (!needle || p.name.toLowerCase().includes(needle) || p.slug.includes(needle) || p.tags.some((t) => t.includes(needle)))
      && (!tag || p.tags.includes(tag)),
  );
  const allTags = [...new Set(projects.flatMap((p) => p.tags))].sort();

  return (
    <>
      <PageHeader
        icon={FolderKanban}
        title="Projects"
        count={projects.length}
        description="Every project of this organization. A project is an isolation boundary: its memory, sources, sessions and keys never cross into another."
        actions={manage ? <NewProject apiUrl={publicApiUrl()} email={email} /> : undefined}
      />

      {missing && (
        <div className="mb-4">
          <Callout tone="warn" title={`No project “${missing}” in this organization`}>
            The link names a project that does not exist here, was deleted, or belongs to another organization. Pick one below;
            a project&apos;s own link is <code className="font-mono text-[12px]">/projects/&lt;slug&gt;</code>.
          </Callout>
        </div>
      )}

      <FilterBar clearHref="/projects" filtered={Boolean(q || tag)}>
        <SearchField defaultValue={q} placeholder="Search projects by name, slug or tag" label="Search projects" className="basis-full sm:basis-auto" />
        {allTags.length > 0 && (
          <Select name="tag" defaultValue={tag} aria-label="Filter by tag" wrapperClassName="w-[150px]" className="h-9">
            <option value="">All tags</option>
            {allTags.map((t) => <option key={t} value={t}>{t}</option>)}
          </Select>
        )}
      </FilterBar>

      <div className="rounded-xl border border-border bg-surface">
        <div className="hidden h-9 items-center gap-x-4 border-b border-border px-4 text-[11px] 2xl:flex font-medium uppercase tracking-[0.06em] text-text-3 sm:px-5">
          <span className="min-w-48 flex-1">Project</span>
          <span className="min-w-32">Tags</span>
          <span className="w-20 text-right">Memories</span>
          <span className="w-16 text-right">Sources</span>
          <span className="w-12 text-right">Keys</span>
          <span className="w-32 text-right">Credits / cap</span>
          <span className="w-20 text-right">Retention</span>
          <span className="w-28 text-right">Last API call</span>
          <span className="w-8" />
        </div>
        {shown.length === 0 ? (
          <Empty>{projects.length ? "No project matches." : "No projects yet."}</Empty>
        ) : (
          <ul className="divide-y divide-border">
            {shown.map((p) => (
              <ProjectRow
                key={p.slug}
                project={p}
                current={p.slug === current.slug}
                lastActivity={p.lastActivityAt ? timeAgo(p.lastActivityAt) : "no calls yet"}
                created={formatDate(p.createdAt)}
                canManage={manage}
              />
            ))}
          </ul>
        )}
        <div className="border-t border-border px-4 py-2.5 text-[12px] text-text-3 sm:px-5">{shown.length} of {projects.length} projects</div>
      </div>
      <p className="mt-4 max-w-3xl text-[12.5px] leading-relaxed text-text-3">
        A project at its monthly cap stops accepting new conversations until the month resets (UTC); recall keeps working.
        Retention drops the raw text of older sources; the memories learned from them are kept.
      </p>
    </>
  );
}
