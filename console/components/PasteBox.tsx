"use client";
// One transcript becomes many facts: paste a few lines, memory reads them within the request
// (POST /v1/ingest, instant) and the facts it kept appear one by one, each as a receipt, with what
// it supersedes, extends or contradicts said in words under it. An empty project's home opens on it; the Ask page has it as "Add".
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useActionState, useEffect, useMemo, useState } from "react";
import { ArrowRight, CircleAlert, LoaderCircle, RotateCcw, Sparkles } from "lucide-react";
import { addToMemory, type AddState } from "@/app/actions";
import { edgeVerb } from "@/lib/graph";
import { PASTE_SNIPPET } from "@/lib/paste-snippet";
import { INSTANT_MAX_CHARS, type AddResult, type MemoryRelated, type RelatedEdge } from "@/lib/types";
import { Receipt } from "./Receipt";
import { button, ButtonLink, Card, cx, FormError, inputClass } from "./ui";

const REVEAL_MS = 650;
const MEMORY_EDGES = new Set(["supersedes", "contradicts", "extends", "derived"]);

export function PasteBox({ mode }: { mode: "first-run" | "add" }) {
  const [state, action, pending] = useActionState<AddState, FormData>(addToMemory, {});
  const [text, setText] = useState(mode === "first-run" ? PASTE_SNIPPET : "");
  const over = text.length > INSTANT_MAX_CHARS;

  return (
    <div className="space-y-4">
      <Card>
        <form action={action}>
          <div className="px-4 pt-4 sm:px-5">
            <label htmlFor={`paste-${mode}`} className="block text-[14px] font-medium text-text">
              Paste a few lines from a session, a standup, or a PR thread
            </label>
            <p className="mt-1 max-w-2xl text-[12.5px] leading-relaxed text-text-3">
              {mode === "first-run"
                ? "Memory reads it now and keeps what a teammate would want next week: decisions, rules, gotchas and facts, each with its quote, linked to what it builds on. Try the example below, or paste your own."
                : "Feed memory by hand: it reads the text now, keeps the decisions, rules, gotchas and facts in it, and links each to what memory already knows."}
            </p>
            <textarea
              id={`paste-${mode}`}
              name="content"
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={mode === "first-run" ? 11 : 7}
              spellCheck={false}
              placeholder={"Priya: We decided to ship the export fix in 4.2.0.\nSam: Then it needs the migration first, because …"}
              className={cx(inputClass, "mt-3 resize-y font-mono text-[12.5px] leading-relaxed")}
            />
          </div>
          <div className="flex flex-wrap items-center gap-2 px-4 py-3 sm:px-5">
            <button type="submit" disabled={pending || over || text.trim().length < 20} className={button("primary")}>
              {pending ? <LoaderCircle className="animate-spin" aria-hidden /> : <Sparkles aria-hidden />}
              {pending ? "Reading it…" : mode === "first-run" ? "Turn it into memory" : "Add to memory"}
            </button>
            {text !== PASTE_SNIPPET && !pending && (
              <button type="button" onClick={() => setText(PASTE_SNIPPET)} className={button("ghost", "sm")}>
                <RotateCcw aria-hidden /> {mode === "first-run" ? "Back to the example" : "Use an example"}
              </button>
            )}
            <span className={cx("ml-auto text-[12px] tabular-nums", over ? "text-bad" : "text-text-3")}>
              {text.length.toLocaleString("en")} / {INSTANT_MAX_CHARS.toLocaleString("en")}
            </span>
          </div>
          {state.error && !pending && <div className="px-4 pb-3 sm:px-5"><FormError>{state.error}</FormError></div>}
        </form>
      </Card>

      {pending && <Reading />}
      {!pending && state.result && <Learned key={state.result.sourceId} result={state.result} mode={mode} />}
    </div>
  );
}

/** While the request runs: what memory is doing, said plainly. */
function Reading() {
  return (
    <Card className="drawer-fade px-4 py-4 sm:px-5" aria-live="polite">
      <p className="flex items-center gap-2 text-[13px] text-text-2">
        <LoaderCircle className="size-4 animate-spin text-text-3" aria-hidden />
        Reading the text, picking out what lasts, and checking each fact against what memory already knows…
      </p>
      <ul className="mt-4 space-y-3" aria-hidden>
        {[78, 64, 70].map((w) => (
          <li key={w} className="space-y-1.5">
            <div className="h-3 animate-pulse rounded bg-surface-3" style={{ width: `${w}%` }} />
            <div className="h-2.5 w-1/3 animate-pulse rounded bg-surface-2" />
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** What one source taught, revealed a fact at a time. */
function Learned({ result, mode }: { result: AddResult; mode: "first-run" | "add" }) {
  const router = useRouter();
  const total = result.memories.length;
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (shown >= total) return;
    const t = setTimeout(() => setShown((n) => n + 1), shown === 0 ? 120 : REVEAL_MS);
    return () => clearTimeout(t);
  }, [shown, total]);
  const visible = useMemo(() => result.memories.slice(0, shown), [result, shown]);
  const done = shown >= total;

  if (result.status === "queued") {
    return (
      <Card className="drawer-fade px-4 py-3.5 sm:px-5">
        <p className="flex items-start gap-2 text-[13px] text-text-2">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
          <span>
            {result.reason ?? "The text is queued."} Its facts appear when memory has tended it;{" "}
            <Link href="/sources" className="underline underline-offset-2 hover:text-text">Sources</Link> shows its progress.
          </span>
        </p>
      </Card>
    );
  }
  if (total === 0) {
    return (
      <Card className="drawer-fade px-4 py-3.5 sm:px-5">
        <p className="text-[13px] text-text-2">
          Nothing in that text will matter a week from now, so memory kept no fact from it. It keeps decisions, rules,
          gotchas and how things work; progress and status go on thread pages, then expire.
        </p>
      </Card>
    );
  }

  const ids = new Set(visible.map((m) => m.memory.id));
  // Each memory edge once, from the end that points (contradicts has no direction: once per pair).
  const kinds = new Map<string, number>();
  const seen = new Set<string>();
  for (const m of result.memories) {
    for (const e of m.edges) {
      if (!e.item || !MEMORY_EDGES.has(e.kind) || e.direction === "in") continue;
      const key = [e.kind, ...(e.kind === "contradicts" ? [m.memory.id, e.item.id].sort() : [m.memory.id, e.item.id])].join("|");
      if (!seen.has(key)) kinds.set(e.kind, (kinds.get(e.kind) ?? 0) + 1);
      seen.add(key);
    }
  }
  const threads = [...new Map(result.memories.flatMap((m) => m.edges).filter((e) => e.thread).map((e) => [e.thread!.id, e.thread!.title])).values()];
  const summary = [
    ...[...kinds].map(([kind, n]) => `${edgeVerb(kind as RelatedEdge["kind"], "out")} ${n} fact${n === 1 ? "" : "s"}`),
    threads.length ? `decided in ${threads.join(", ")}` : null,
  ].filter(Boolean).join(" · ");

  return (
    <Card className="overflow-hidden" aria-live="polite">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-4 py-3 sm:px-5">
        <h2 className="text-[14px] font-medium text-text">
          {result.outcome === "unchanged" ? "Memory already had this text" : `Learned ${total} fact${total === 1 ? "" : "s"} from it`}
        </h2>
        <p className="text-[12.5px] text-text-3">{done ? summary : `${shown} of ${total}…`}</p>
      </div>
      <ol className="divide-y divide-border">
        {visible.map((m) => (
          <li key={m.memory.id} className="fact-in px-4 py-3 sm:px-5" data-learned={m.memory.id}>
            <Receipt m={m.memory} showCategory readOnly={m.memory.status === "superseded"} />
            <Edges item={m} shown={ids} />
          </li>
        ))}
      </ol>
      {done && (
        <div className="drawer-fade flex flex-wrap items-center gap-2 border-t border-border bg-surface-2/40 px-4 py-3 sm:px-5">
          {mode === "first-run" ? (
            <>
              <button type="button" onClick={() => router.refresh()} className={button("primary")}>
                See your memory <ArrowRight aria-hidden />
              </button>
              <p className="text-[12.5px] text-text-3">Then connect an agent, so memory grows from your real sessions.</p>
            </>
          ) : (
            <>
              <ButtonLink href={`/memories?open=${encodeURIComponent(result.memories.at(-1)!.memory.id)}`} variant="secondary">
                Open in Memories <ArrowRight aria-hidden />
              </ButtonLink>
              <ButtonLink href="/playground" variant="ghost">Ask about it</ButtonLink>
            </>
          )}
        </div>
      )}
    </Card>
  );
}

/** A fact's edges under its receipt: those pointing at what memory already knew, and those from
 *  facts already on screen, so each edge appears with the later of its two ends. */
function Edges({ item, shown }: { item: MemoryRelated; shown: Set<string> }) {
  const edges = item.edges.filter((e: RelatedEdge) =>
    e.kind === "decided_in" || (e.item && MEMORY_EDGES.has(e.kind) && (e.direction !== "in" || shown.has(e.item.id))));
  if (edges.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1">
      {edges.map((e) => (
        <li key={`${e.kind}-${e.direction}-${e.item?.id ?? e.thread?.id}`} className="fact-in flex min-w-0 items-center gap-2 text-[12px] text-text-3" data-edge-kind={e.kind}>
          <span className="shrink-0 text-text-2">{edgeVerb(e.kind, e.direction)}:</span>
          {e.thread ? (
            <Link href={`/threads/${e.thread.id}`} className="min-w-0 truncate text-text-2 hover:text-text hover:underline">{e.thread.title}</Link>
          ) : (
            <span className={cx("min-w-0 truncate", e.item?.status === "superseded" ? "text-text-3 line-through" : "text-text-2")}>{e.item?.statement}</span>
          )}
        </li>
      ))}
    </ul>
  );
}
