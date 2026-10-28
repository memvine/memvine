import { Waypoints } from "lucide-react";
import { ThreadKindTag } from "@/components/thread-bits";
import {
  FilterBar, Mono, PageHeader, RowLink, SearchField, Select, Tabs, Table, TableEmpty, TBody, Td, Th, THead, ThreadStatusBadge, THREAD_KIND_LABEL,
  timeAgo, Tr,
} from "@/components/ui";
import { currentProject, listThreads } from "@/lib/api";
import type { ThreadKind, ThreadStatus } from "@/lib/types";
import { href } from "@/lib/url";

const STATUSES: ThreadStatus[] = ["open", "blocked", "done", "abandoned"];
const KINDS: ThreadKind[] = ["release", "task", "pr", "ticket", "topic"];
const STATUS_LABEL: Record<ThreadStatus, string> = { open: "Open", blocked: "Blocked", done: "Done", abandoned: "Abandoned" };

export default async function ThreadsPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const params = await searchParams;
  const status = STATUSES.find((s) => s === params.status);
  const kind = KINDS.find((k) => k === params.kind);
  const q = params.q?.trim() || undefined;
  const project = await currentProject();
  const filtered = Boolean(status || kind || q);
  const [all, threads] = await Promise.all([
    listThreads(project.slug),
    filtered ? listThreads(project.slug, { status, kind, q }) : null,
  ]);
  const shown = threads ?? all;
  const count = (s?: ThreadStatus) => all.filter((t) => !s || t.status === s).length;

  return (
    <>
      <PageHeader
        icon={Waypoints}
        title="Threads"
        count={all.length}
        description="The work your agents are part of: releases, tasks, pull requests and tickets, each followed across sessions and kept current."
      />

      <Tabs
        label="Status"
        items={[
          { href: href("/threads", { kind, q }), label: "All", count: count(), active: !status },
          ...STATUSES.map((s) => ({ href: href("/threads", { status: s, kind, q }), label: STATUS_LABEL[s], count: count(s), active: status === s })),
        ]}
      />

      <FilterBar clearHref="/threads" filtered={filtered}>
        {status && <input type="hidden" name="status" value={status} />}
        <SearchField defaultValue={q} placeholder="Search titles, keys and summaries" label="Search threads" className="basis-full sm:basis-auto" />
        <Select name="kind" defaultValue={kind ?? ""} aria-label="Kind" wrapperClassName="w-[150px]" className="h-9">
          <option value="">All kinds</option>
          {KINDS.map((k) => <option key={k} value={k}>{THREAD_KIND_LABEL[k]}</option>)}
        </Select>
      </FilterBar>

      <Table minWidth={820} footer={shown.length ? `${shown.length} of ${all.length} · open one for its status page` : undefined}>
        <THead>
          <Th className="w-[110px]">Status</Th>
          <Th>Thread</Th>
          <Th className="w-[130px]">Kind</Th>
          <Th className="w-[120px]">Last activity</Th>
          <Th align="right" className="w-[90px]">Sessions</Th>
          <Th align="right" className="w-[100px]">Knowledge</Th>
        </THead>
        <TBody>
          {shown.length === 0 && (
            <TableEmpty colSpan={6}>
              {all.length === 0
                ? "No threads yet. They form as sessions mention releases, tasks, pull requests or tickets."
                : "No threads match these filters."}
            </TableEmpty>
          )}
          {shown.map((t) => (
            <Tr key={t.id} interactive>
              <Td><ThreadStatusBadge status={t.status} /></Td>
              <Td className="max-w-0 py-3">
                <div className="flex min-w-0 items-baseline gap-2">
                  <RowLink href={`/threads/${t.id}`} className="truncate font-medium">{t.title}</RowLink>
                  <Mono className="shrink-0 text-text-3">{t.key}</Mono>
                </div>
                <p className="mt-0.5 truncate text-[12.5px] text-text-3">{t.summary}</p>
              </Td>
              <Td><ThreadKindTag kind={t.kind} /></Td>
              <Td muted nowrap>{timeAgo(t.lastActivityAt)}</Td>
              <Td align="right" muted>{t.sessionCount}</Td>
              <Td align="right" muted>{t.knowledgeCount}</Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </>
  );
}
