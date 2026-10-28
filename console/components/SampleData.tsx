"use client";
// Sample data: a button that fills an empty project with a small sample memory, and the banner
// shown while sample sources exist, with the one action that deletes them.
import Link from "next/link";
import { useActionState } from "react";
import { MessageCircleQuestion, Sparkles, Trash2 } from "lucide-react";
import { deleteSampleData, loadSampleData, type SampleState } from "@/app/actions";
import { button, FormError } from "@/components/ui";

export function LoadSampleButton({ variant = "primary" }: { variant?: "primary" | "secondary" }) {
  const [state, action, pending] = useActionState<SampleState, FormData>(loadSampleData, {});
  return (
    <form action={action} className="inline-flex flex-col items-center gap-2">
      <button type="submit" disabled={pending} className={button(variant)}>
        <Sparkles aria-hidden /> {pending ? "Loading sample…" : "Load sample data"}
      </button>
      {state.error && <FormError>{state.error}</FormError>}
    </form>
  );
}

/** What an empty project offers: try recall on a sample now, or connect an agent. */
export function SampleOffer() {
  return (
    <div className="flex flex-col items-center gap-2">
      <LoadSampleButton />
      <p className="max-w-sm text-[12px] text-text-3">
        A few sources and memories from a made-up product team, including a changed decision and a contradiction.
        Instant and free; delete it in one click.
      </p>
    </div>
  );
}

export function SampleBanner({ sources }: { sources: number }) {
  const [state, action, pending] = useActionState<SampleState, FormData>(deleteSampleData, {});
  return (
    <div className="mb-6 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-info/30 bg-info-soft px-4 py-3 text-[13px]">
      <div className="min-w-0 flex-1">
        <p className="font-medium text-info">This project contains sample data</p>
        <p className="mt-0.5 text-text-2">
          {sources} sample source{sources === 1 ? "" : "s"} (labelled “Sample”) and the memories drawn from them.{" "}
          <Link href="/playground" className="underline underline-offset-2 hover:text-text">Ask memory a question</Link>, then delete it when you connect your own agent.
        </p>
        {state.error && <div className="mt-1"><FormError>{state.error}</FormError></div>}
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        <Link href="/playground" className={button("secondary", "sm")}><MessageCircleQuestion aria-hidden /> Ask memory</Link>
        <form action={action}>
          <button type="submit" disabled={pending} className={button("secondary", "sm")}>
            <Trash2 aria-hidden /> {pending ? "Deleting…" : "Delete sample data"}
          </button>
        </form>
      </div>
    </div>
  );
}
