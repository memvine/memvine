import Link from "next/link";
import { MessageCircleQuestion, Plus } from "lucide-react";
import { PasteBox } from "@/components/PasteBox";
import { LoadSampleButton } from "@/components/SampleData";
import { cx, PageHeader } from "@/components/ui";
import { currentProject } from "@/lib/api";
import { RecallForm } from "./RecallForm";

/** Ask memory a question, or Add to it by hand: the paste box the empty home opens on. */
export default async function PlaygroundPage({ searchParams }: { searchParams: Promise<{ tab?: string }> }) {
  const project = await currentProject();
  const add = (await searchParams).tab === "add";
  const empty = Boolean(project.slug) && project.memoryCount === 0 && project.sourceCount === 0;
  return (
    <>
      <PageHeader
        icon={add ? Plus : MessageCircleQuestion}
        title={add ? "Add to memory" : "Ask memory"}
        description={add
          ? "Paste what was said and see what memory keeps from it, fact by fact, with the edges to what it already knew."
          : "Ask what an agent would ask, and see exactly what it would receive: current facts first, previous values when the question asks about history, unverified agent claims ranked last, and each fact's edges."}
      />
      <nav aria-label="Ask or add" className="mb-6 inline-flex rounded-lg border border-border p-0.5">
        {([["Ask", "/playground", !add, MessageCircleQuestion], ["Add", "/playground?tab=add", add, Plus]] as const).map(([label, href, on, Icon]) => (
          <Link key={label} href={href} aria-current={on ? "page" : undefined}
                className={cx("inline-flex h-7 items-center gap-1.5 rounded-md px-3 text-[12.5px] font-medium transition-colors",
                  on ? "bg-surface-3 text-text" : "text-text-3 hover:text-text")}>
            <Icon className="size-3.5" aria-hidden /> {label}
          </Link>
        ))}
      </nav>
      {add ? (
        <div className="max-w-4xl"><PasteBox mode="add" /></div>
      ) : (
        <>
          {empty && (
            <div className="mb-6 flex flex-wrap items-center gap-x-4 gap-y-3 rounded-lg border border-border bg-surface px-4 py-3 text-[13px]">
              <div className="min-w-0 flex-1">
                <p className="font-medium text-text">This project has no memory yet</p>
                <p className="mt-0.5 text-text-2">
                  Load the sample to try the questions below against it: a moved launch date, a dropped class, and two people
                  disagreeing about weekend escalations. Or <Link href="/playground?tab=add" className="underline underline-offset-2 hover:text-text">add</Link> a few lines of your own.
                </p>
              </div>
              <LoadSampleButton variant="secondary" />
            </div>
          )}
          <RecallForm />
        </>
      )}
    </>
  );
}
