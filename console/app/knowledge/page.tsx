import { BookMarked } from "lucide-react";
import { MemoryDrawer } from "@/components/MemoryPanel";
import { ReceiptList } from "@/components/Receipt";
import {
  Card, CATEGORY_LABEL, FilterBar, PageHeader, SearchField, Tabs,
} from "@/components/ui";
import { currentProject, getMemory, listMemories, listThreads } from "@/lib/api";
import type { KnowledgeCategory } from "@/lib/types";
import { href } from "@/lib/url";

const CATEGORIES: KnowledgeCategory[] = ["decision", "rule", "gotcha", "fact", "preference"];

export default async function KnowledgePage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const params = await searchParams;
  const category = CATEGORIES.find((c) => c === params.category);
  const q = params.q?.trim().toLowerCase() || "";
  const project = await currentProject();
  const [memories, threads, detail] = await Promise.all([
    listMemories(project.slug),
    listThreads(project.slug),
    params.open ? getMemory(project.slug, params.open) : null,
  ]);
  // Thread-only knowledge (what one release's cherry-picks did) lives on its thread page; the
  // list shows what concerns the whole project unless asked for everything.
  const everything = memories.filter((m) => m.category);
  const threadOnly = everything.filter((m) => m.scope === "thread").length;
  const all = params.scope === "all";
  const knowledge = all ? everything : everything.filter((m) => m.scope !== "thread");
  const count = (c?: KnowledgeCategory) => knowledge.filter((m) => !c || m.category === c).length;
  const shown = knowledge
    .filter((m) => !category || m.category === category)
    .filter((m) => !q || `${m.statement} ${m.why ?? ""}`.toLowerCase().includes(q))
    // Current knowledge first; what it replaced sinks to the bottom.
    .sort((a, b) => Number(a.status === "superseded") - Number(b.status === "superseded") || b.recordedAt.localeCompare(a.recordedAt));
  const threadOf = (id?: string | null) => threads.find((t) => t.id === id) ?? null;
  const base = { category, q: params.q, scope: all ? "all" : undefined };
  const link = (id: string) => href("/knowledge", { ...base, open: id });

  return (
    <>
      <PageHeader
        icon={BookMarked}
        title="Knowledge"
        count={knowledge.length}
        description="What your team has settled and should not relearn: decisions and why they were made, rules, gotchas, facts and preferences."
      />

      <Tabs
        label="Category"
        items={[
          { href: href("/knowledge", { q: params.q, scope: all ? "all" : undefined }), label: "All", count: count(), active: !category },
          ...CATEGORIES.map((c) => ({ href: href("/knowledge", { category: c, q: params.q, scope: all ? "all" : undefined }), label: CATEGORY_LABEL[c].many, count: count(c), active: category === c })),
        ]}
      />

      {threadOnly > 0 && (
        <p className="mb-3 text-xs text-text-3">
          {all ? "Showing all knowledge, including what only matters inside one thread. " : `Showing project-wide knowledge. ${threadOnly} thread-only item${threadOnly === 1 ? "" : "s"} are on their thread pages. `}
          <a className="text-text-2 underline-offset-2 hover:text-text hover:underline" href={href("/knowledge", { category, q: params.q, scope: all ? undefined : "all" })}>
            {all ? "Project-wide only" : "Show them here too"}
          </a>
        </p>
      )}

      <FilterBar clearHref={href("/knowledge", { category, scope: all ? "all" : undefined })} filtered={Boolean(q)}>
        {all && <input type="hidden" name="scope" value="all" />}
        {category && <input type="hidden" name="category" value={category} />}
        <SearchField defaultValue={params.q} placeholder="Search statements and reasons" label="Search knowledge" />
      </FilterBar>

      <Card>
        <ReceiptList
          items={shown}
          hrefFor={Object.fromEntries(shown.map((m) => [m.id, link(m.id)]))}
          showCategory={!category}
          followParam="open"
          selected={params.open}
          threads={Object.fromEntries(threads.map((t) => [t.id, t.title]))}
          empty={
            <p className="px-5 py-14 text-center text-[13px] text-text-3">
              {knowledge.length === 0
                ? "No knowledge yet. Decisions, rules and gotchas appear here as sessions settle them."
                : q ? "Nothing matches that search." : `No ${category ? CATEGORY_LABEL[category].many.toLowerCase() : "knowledge"} yet.`}
            </p>
          }
        />
        {shown.length > 0 && (
          <div className="border-t border-border px-4 py-2.5 text-[12px] text-text-3 sm:px-5">{shown.length} of {knowledge.length} · hover one for its quote; open it for its sources</div>
        )}
      </Card>

      {detail && (
        <MemoryDrawer detail={detail} closeHref={href("/knowledge", base)} linkFor={link} thread={threadOf(detail.memory.threadId)} />
      )}
    </>
  );
}
