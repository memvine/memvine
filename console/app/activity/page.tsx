import Link from "next/link";
import { History } from "lucide-react";
import { actorLabel, EVENT_LABEL, eventNote } from "@/components/MemoryTimeline";
import { FilterBar, PageHeader, Select, shortDate, Table, TableEmpty, TBody, Td, Th, THead, Tr, button } from "@/components/ui";
import { currentProject, listEvents } from "@/lib/api";
import { MEMORY_EVENT_KINDS } from "@/lib/types";
import { href } from "@/lib/url";

const SINCE: Record<string, { label: string; hours: number }> = {
  day: { label: "Last 24 hours", hours: 24 },
  week: { label: "Last 7 days", hours: 24 * 7 },
  month: { label: "Last 30 days", hours: 24 * 30 },
};

export default async function ActivityPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const params = await searchParams;
  const kind = MEMORY_EVENT_KINDS.find((k) => k === params.kind);
  const since = params.since && SINCE[params.since] ? params.since : undefined;
  const memoryId = params.memory || undefined;
  const project = await currentProject();
  const page = await listEvents(project.slug, {
    kind, memoryId, cursor: params.cursor,
    since: since ? new Date(Date.now() - SINCE[since].hours * 3600_000).toISOString() : undefined,
  });
  const base = { kind, since, memory: memoryId };
  const filtered = Boolean(kind || since || memoryId);

  return (
    <>
      <PageHeader
        icon={History}
        title="Activity"
        description="What happened to this project's memory, newest first: learned, said again, replaced, confirmed, forgotten, gone stale. Each change names who made it."
      />

      <FilterBar clearHref="/activity" filtered={filtered}>
        <Select name="kind" defaultValue={kind ?? ""} aria-label="Event" wrapperClassName="w-[220px]" className="h-9">
          <option value="">Every event</option>
          {MEMORY_EVENT_KINDS.filter((k) => k !== "expired").map((k) => <option key={k} value={k}>{EVENT_LABEL[k]}</option>)}
        </Select>
        <Select name="since" defaultValue={since ?? ""} aria-label="When" wrapperClassName="w-[170px]" className="h-9">
          <option value="">Any time</option>
          {Object.entries(SINCE).map(([value, s]) => <option key={value} value={value}>{s.label}</option>)}
        </Select>
        {memoryId && <input type="hidden" name="memory" value={memoryId} />}
      </FilterBar>

      {memoryId && (
        <p className="mb-3 text-[12.5px] text-text-3">
          One memory&apos;s events. <Link href={href("/activity", { kind, since })} className="text-text-2 hover:underline">Show every memory</Link>
        </p>
      )}

      <Table minWidth={720}>
        <THead>
          <Th className="w-[150px]">Time (UTC)</Th>
          <Th className="w-[190px]">Event</Th>
          <Th>Memory</Th>
          <Th className="w-[200px]">By</Th>
        </THead>
        <TBody>
          {page.events.length === 0 && <TableEmpty colSpan={4}>{filtered ? "No events match these filters." : "Nothing has happened to this project's memory yet."}</TableEmpty>}
          {page.events.map((e) => {
            const note = eventNote(e);
            return (
              <Tr key={e.id ?? `${e.kind}-${e.at}`}>
                <Td muted nowrap>{shortDate(e.at, true)}</Td>
                <Td nowrap>
                  <Link href={href("/activity", { ...base, kind: e.kind })} className="hover:underline">{EVENT_LABEL[e.kind] ?? e.kind}</Link>
                </Td>
                <Td className="max-w-0">
                  {e.memoryId && e.statement ? (
                    <Link href={href("/memories", { open: e.memoryId })} className="block truncate text-text hover:underline" title={e.statement}>{e.statement}</Link>
                  ) : <span className="text-text-3">{e.kind === "project_settings" ? "this project's memory rules" : e.kind === "profile_rebuilt" ? "a profile, rebuilt from memory" : "a memory since deleted"}</span>}
                  {note && <p className="mt-0.5 truncate text-[12px] text-text-3" title={note}>{note}</p>}
                </Td>
                <Td nowrap className="max-w-[200px] truncate">{actorLabel(e.actor)}</Td>
              </Tr>
            );
          })}
        </TBody>
      </Table>

      {(params.cursor || page.nextCursor) && (
        <div className="mt-4 flex gap-2">
          {params.cursor && <Link href={href("/activity", base)} className={button("secondary", "sm")}>Newest</Link>}
          {page.nextCursor && <Link href={href("/activity", { ...base, cursor: page.nextCursor })} className={button("secondary", "sm")}>Older</Link>}
        </div>
      )}
    </>
  );
}
