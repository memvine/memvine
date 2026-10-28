import { Brain } from "lucide-react";
import { MemoryDrawer } from "@/components/MemoryPanel";
import { ReceiptList } from "@/components/Receipt";
import { SampleOffer } from "@/components/SampleData";
import {
  Card, categoryLabel, FilterBar, PageHeader, SearchField, Select,
} from "@/components/ui";
import { currentProject, getMemory, listMemories, listThreads } from "@/lib/api";
import { BUILTIN_CATEGORIES, type KnowledgeCategory, type MemoryKind, type MemoryStatus } from "@/lib/types";
import { href } from "@/lib/url";

const KINDS: MemoryKind[] = ["semantic", "episodic", "procedural", "prospective", "preference"];
const STATUSES: (MemoryStatus | "expired")[] = ["active", "superseded", "retired", "stale", "expired"];

export default async function MemoriesPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const params = await searchParams;
  const kind = KINDS.find((k) => k === params.kind);
  const status = STATUSES.find((s) => s === params.status);
  const unused = params.unused === "true" ? "true" : undefined;
  const review = params.review === "new" || params.review === "flagged" ? params.review : undefined;
  const project = await currentProject();
  const custom = project.categories ?? [];
  const categories: KnowledgeCategory[] = [...BUILTIN_CATEGORIES, ...custom.map((c) => c.slug)];
  const category = categories.find((c) => c === params.category);
  const unusedDays = project.unusedDays ?? 60;
  const [memories, all, threads, detail] = await Promise.all([
    listMemories(project.slug, { q: params.q, kind, status, category, unused: Boolean(unused),
                                 ...(review === "flagged" ? { flag: "any" as const } : { review }) }),
    params.q || kind || status || category || unused || review ? listMemories(project.slug) : null,
    listThreads(project.slug),
    params.open ? getMemory(project.slug, params.open) : null,
  ]);
  const filtered = Boolean(params.q || kind || status || category || unused || review);
  const base = { q: params.q, kind, status, category, unused, review };
  const link = (id: string) => href("/memories", { ...base, open: id });
  const total = all?.length ?? memories.length;

  return (
    <>
      <PageHeader
        icon={Brain}
        title="Memories"
        count={total}
        description="Everything Memvine has learned in this project, each with the source it came from. Superseded values stay, so agents can answer what changed and when."
      />

      <FilterBar clearHref="/memories" filtered={filtered}>
        <SearchField defaultValue={params.q} placeholder="Search what a memory says" label="Search memories" className="basis-full sm:basis-auto" />
        <Select name="kind" defaultValue={kind ?? ""} aria-label="Kind" wrapperClassName="w-[150px]" className="h-9">
          <option value="">All kinds</option>
          {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
        </Select>
        <Select name="category" defaultValue={category ?? ""} aria-label="Category" wrapperClassName="w-[160px]" className="h-9">
          <option value="">Any category</option>
          {categories.map((c) => <option key={c} value={c}>{categoryLabel(c, custom)}</option>)}
        </Select>
        <Select name="status" defaultValue={status ?? ""} aria-label="Status" wrapperClassName="w-[140px]" className="h-9">
          <option value="">Any status</option>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
        <Select name="unused" defaultValue={unused ?? ""} aria-label="Use" wrapperClassName="w-[190px]" className="h-9">
          <option value="">Any use</option>
          <option value="true">Not used in {unusedDays} days</option>
        </Select>
        <Select name="review" defaultValue={review ?? ""} aria-label="Review" wrapperClassName="w-[150px]" className="h-9">
          <option value="">Reviewed or not</option>
          <option value="new">Needs review</option>
          <option value="flagged">Flagged by tidy-up</option>
        </Select>
      </FilterBar>

      {unused && (
        <p className="mb-3 text-[12.5px] leading-relaxed text-text-3">
          No agent&apos;s recall has served these in {unusedDays} days. Keep what still matters, correct or forget the rest; nothing is hidden or deleted for being unused.
        </p>
      )}

      <Card>
        <ReceiptList
          items={memories}
          hrefFor={Object.fromEntries(memories.map((m) => [m.id, link(m.id)]))}
          showCategory
          followParam="open"
          selected={params.open}
          threads={Object.fromEntries(threads.map((t) => [t.id, t.title]))}
          reviewing={Boolean(review)}
          empty={
            <div className="px-5 py-14 text-center text-[13px] text-text-3">
              {review && !params.q && !kind && !status && !category && !unused ? "Nothing to review: every current memory has been kept or forgotten." : filtered ? "No memories match these filters." : (
                <div className="flex flex-col items-center gap-4">
                  <p>No memories yet. They appear a minute or two after the first conversation is sent, or load a sample to try recall right now.</p>
                  {project.sourceCount === 0 && <SampleOffer />}
                </div>
              )}
            </div>
          }
        />
        {memories.length > 0 && (
          <div className="border-t border-border px-4 py-2.5 text-[12px] text-text-3 sm:px-5">{memories.length} of {total} · hover one for its quote; open it to read its source</div>
        )}
      </Card>

      {detail && (
        <MemoryDrawer
          detail={detail}
          closeHref={href("/memories", base)}
          linkFor={link}
          thread={threads.find((t) => t.id === detail.memory.threadId) ?? null}
        />
      )}
    </>
  );
}
