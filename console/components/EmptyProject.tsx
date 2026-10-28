// What the home shows for a project with no memory yet, in place of what memory holds: the paste screen.
// One transcript in, many facts out, within the request; under it, the three steps that connect an
// agent (key, install, backfill) and the sample-data offer (docs/memory-graph-plan.md, workstream 3).
import Link from "next/link";
import { Check, Plug } from "lucide-react";
import { PasteBox } from "./PasteBox";
import { LoadSampleButton } from "./SampleData";
import { Card, CardHeader, cx } from "./ui";

export interface SetupStep { done: boolean; label: string; href: string; detail?: string }

export function SetupSteps({ steps }: { steps: SetupStep[] }) {
  return (
    <ol className="p-2">
      {steps.map((s, i) => (
        <li key={s.label}>
          <Link href={s.href} className="flex items-start gap-3 rounded-lg px-3 py-2.5 text-[13px] transition-colors hover:bg-surface-2">
            <span className={cx("mt-px grid size-5 shrink-0 place-items-center rounded-full text-[11px] font-semibold",
              s.done ? "bg-accent text-accent-text" : "border border-border-strong text-text-3")}>
              {s.done ? <Check className="size-3" strokeWidth={3} aria-hidden /> : i + 1}
            </span>
            <span className="min-w-0">
              <span className={cx("block", s.done ? "text-text-3 line-through decoration-text-3/60" : "text-text")}>{s.label}</span>
              {s.detail && (s.detail.startsWith("/")
                ? <code className="mt-0.5 block truncate font-mono text-[11.5px] text-text-3">{s.detail}</code>
                : <span className="mt-0.5 block text-[12px] text-text-3">{s.detail}</span>)}
            </span>
          </Link>
        </li>
      ))}
    </ol>
  );
}

export function EmptyProject({ connect, tending }: { connect: SetupStep[]; tending: number }) {
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      {tending > 0 && (
        <p className="rounded-lg border border-border bg-surface px-4 py-2.5 text-[13px] text-text-2">
          Memory is tending {tending} source{tending === 1 ? "" : "s"}; their facts appear here when it is done.
        </p>
      )}
      <PasteBox mode="first-run" />
      <div className="grid gap-6 md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card>
          <CardHeader icon={Plug} title="Connect your agent" description="So memory grows from real sessions, not only what you paste." />
          <SetupSteps steps={connect} />
        </Card>
        <Card>
          <CardHeader title="Or explore a sample" description="A made-up product team's memory: a changed decision, a contradiction, threads." />
          <div className="flex flex-col items-start gap-2 px-4 py-4 sm:px-5">
            <LoadSampleButton variant="secondary" />
            <p className="text-[12px] text-text-3">Instant and free; delete it in one click.</p>
          </div>
        </Card>
      </div>
    </div>
  );
}
