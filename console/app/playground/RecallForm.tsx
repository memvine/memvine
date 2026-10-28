"use client";
import Link from "next/link";
import { useActionState, type ReactNode } from "react";
import { CircleSlash, Pin, Play } from "lucide-react";
import { runRecall, type RecallState } from "@/app/actions";
import { CopyButton } from "@/components/client";
import { AssertionBadge, Badge, button, Card, CardHeader, cx, Eyebrow, FormError, inputClass, Select, StatusBadge } from "@/components/ui";
import { edgeVerb } from "@/lib/graph";
import type { RecallResult } from "@/lib/types";

const EXAMPLES = [
  "What is in release 4.2.0?", "Who merges release work?", "Why did we drop InvoiceFieldAliases?", "When is the launch?", "How do escalations work?",
  // Deliberately unrelated: recall should abstain rather than send filler.
  "What is the weather in Paris?",
];

const relevance = (n: number | null | undefined) => (n === null || n === undefined ? "n/a" : n.toFixed(3));

/** The server's abstain_reason codes, said plainly. */
const ABSTAIN_REASON: Record<string, string> = {
  empty_store: "This project has no memory yet, so there was nothing to search.",
  no_candidates: "Memory exists, but none of it is visible to this question: everything was excluded, outside the reader's audiences, or recorded after the as-of time.",
  below_threshold: "The closest memory is not relevant enough to the question to be served.",
};

function Figure({ label, children, tone }: { label: string; children: ReactNode; tone?: "bad" | "good" }) {
  return (
    <div className="min-w-0">
      <Eyebrow>{label}</Eyebrow>
      <div className={cx("mt-1 text-[15px] font-semibold tabular-nums tracking-[-0.01em]", tone === "bad" ? "text-warn" : "text-text")}>{children}</div>
    </div>
  );
}

/** Abstained or not, tokens used and the relevance figures, in one strip. */
function Summary({ result, budget }: { result: RecallResult; budget?: number }) {
  return (
    <Card>
      <div className="grid grid-cols-2 gap-4 px-4 py-3.5 sm:grid-cols-5 sm:px-5">
        <Figure label="Abstained" tone={result.abstained ? "bad" : undefined}>{result.abstained ? "Yes" : "No"}</Figure>
        <Figure label="Tokens used">
          {result.tokens.toLocaleString()}
          {budget ? <span className="text-[12.5px] font-normal text-text-3"> of {budget.toLocaleString()}</span> : null}
        </Figure>
        <Figure label="Memories">{result.items.length}</Figure>
        <Figure label="Best match">{relevance(result.topRelevance)}</Figure>
        <Figure label="Needed to serve">{relevance(result.minRelevance)}</Figure>
      </div>
    </Card>
  );
}

function Standing({ standing }: { standing: string[] }) {
  return (
    <Card className="min-w-0">
      <CardHeader icon={Pin} title="Standing instructions (always on)"
                  description="Sent with every recall, whatever the question, and even when recall abstains." />
      {standing.length === 0 ? (
        <p className="px-5 py-4 text-[13px] text-text-3">None in this project.</p>
      ) : (
        <ul className="divide-y divide-border">
          {standing.map((s) => <li key={s} className="px-5 py-2.5 text-[13px] text-text">{s}</li>)}
        </ul>
      )}
    </Card>
  );
}

function Abstained({ result }: { result: RecallResult }) {
  const below = result.topRelevance !== null && result.topRelevance !== undefined && result.minRelevance !== null && result.minRelevance !== undefined;
  return (
    <Card className="min-w-0">
      <div className="flex flex-col items-center px-6 py-10 text-center">
        <div className="mb-3 grid size-10 place-items-center rounded-xl border border-border bg-surface-2">
          <CircleSlash className="size-[18px] text-text-3" strokeWidth={1.75} aria-hidden />
        </div>
        <p className="text-[14px] font-medium text-text">No relevant memory</p>
        <p className="mt-1 max-w-md text-[13px] text-text-2">
          {(result.abstainReason && (ABSTAIN_REASON[result.abstainReason] ?? result.abstainReason))
            || "Nothing in this project's memory is relevant to the question."}
        </p>
        {result.abstainReason && <code className="mt-1.5 font-mono text-[11.5px] text-text-3">{result.abstainReason}</code>}
        {below && (
          <p className="mt-2 text-[12.5px] text-text-3">
            Best match {relevance(result.topRelevance)}; a memory needs {relevance(result.minRelevance)} to be served.
          </p>
        )}
        <p className="mt-3 max-w-md text-[12.5px] text-text-3">
          Memvine abstains rather than send filler: the briefing is empty
          {result.standing.length ? ", and only the standing instructions below still go to the agent." : ", and the agent receives nothing."}
        </p>
      </div>
    </Card>
  );
}

export function RecallForm() {
  const [state, action, pending] = useActionState<RecallState, FormData>(runRecall, {});
  const result = state.result;

  return (
    <div className="space-y-6">
      <Card>
        <form action={action} className="p-4 sm:p-5">
          <label htmlFor="query" className="mb-1.5 block text-[12.5px] font-medium text-text-2">Question</label>
          <textarea id="query" name="query" rows={3} defaultValue={state.query} placeholder="Ask the way an agent would, e.g. what did we decide about the launch?" className={`${inputClass} resize-y leading-relaxed`} />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <label htmlFor="budget" className="text-[12.5px] text-text-2">Token budget</label>
            <Select id="budget" name="budget" defaultValue="2000" wrapperClassName="w-[110px]">
              <option value="500">500</option>
              <option value="2000">2,000</option>
              <option value="8000">8,000</option>
            </Select>
            <button type="submit" disabled={pending} className={button("primary", "md", "ml-auto")}><Play aria-hidden /> {pending ? "Recalling…" : "Run recall"}</button>
          </div>
          {state.error && <div className="mt-2"><FormError>{state.error}</FormError></div>}
        </form>
        <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3 sm:px-5">
          <Eyebrow className="mr-1">Try</Eyebrow>
          {EXAMPLES.map((e) => (
            <form key={e} action={action}>
              <input type="hidden" name="query" value={e} />
              <input type="hidden" name="budget" value="2000" />
              <button className="h-7 rounded-full border border-border px-3 text-[12.5px] text-text-2 transition-colors hover:border-border-strong hover:bg-surface-2 hover:text-text">{e}</button>
            </form>
          ))}
        </div>
      </Card>

      {result && (
        <>
          <Summary result={result} budget={state.budget} />
          <div className="grid gap-6 lg:grid-cols-2">
            <div className="min-w-0 space-y-6">
              {result.abstained && <Abstained result={result} />}
              {result.briefing && (
                <Card className="min-w-0">
                  <CardHeader
                    title="What the agent receives"
                    description={`${result.tokens.toLocaleString()} tokens`}
                    action={<CopyButton value={result.briefing} label="Copy briefing" />}
                  />
                  <pre className="scroll-quiet overflow-x-auto px-5 py-4 font-mono text-[12px] leading-relaxed whitespace-pre-wrap text-text">{result.briefing}</pre>
                </Card>
              )}
              <Standing standing={result.standing} />
            </div>

            <div className="min-w-0 space-y-6">
              {result.pages.length > 0 && (
                <Card className="min-w-0">
                  <CardHeader title="Thread pages" description="Background pages give context around the answer; the others are the answer." />
                  <ul className="divide-y divide-border">
                    {result.pages.map((p) => {
                      return (
                        <li key={p.threadId}>
                          <Link href={`/threads/${p.threadId}`} className="block px-5 py-3 transition-colors hover:bg-surface-2">
                            <span className="flex items-center gap-2">
                              <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-text">{p.title}</span>
                              {p.background ? <Badge tone="outline">Background</Badge> : <Badge tone="info">Relevant</Badge>}
                            </span>
                            <span className="mt-0.5 line-clamp-2 block text-[12.5px] text-text-3">{p.summary}</span>
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                </Card>
              )}
              {result.items.length > 0 && (
                <Card className="min-w-0">
                  <CardHeader title="Memories, in rank order" description={`${result.items.length} included · score is recall's ranking score`} />
                  <ol className="divide-y divide-border">
                    {result.items.map((m, i) => (
                      <li key={m.id}>
                        <Link href={`/memories?open=${m.id}`} className="flex items-start gap-3 px-5 py-3 transition-colors hover:bg-surface-2">
                          <span className="w-4 shrink-0 pt-px text-[12px] tabular-nums text-text-3">{i + 1}</span>
                          <span className="min-w-0 flex-1 text-[13px] text-text">
                            {m.statement}
                            {m.related && m.related.length > 0 && (
                              <span className="mt-1 block space-y-0.5">
                                {m.related.map((r) => (
                                  <span key={`${r.kind}-${r.direction}-${r.id}`} className="block truncate text-[12px] text-text-3">
                                    <span className={cx("font-medium", r.kind === "contradicts" ? "text-bad" : "text-text-2")}>{edgeVerb(r.kind, r.direction)}</span> {r.statement}
                                  </span>
                                ))}
                              </span>
                            )}
                          </span>
                          <span className="shrink-0 pt-px font-mono text-[12px] tabular-nums text-text-2" title="score">
                            {m.score === null || m.score === undefined ? "–" : m.score.toFixed(3)}
                          </span>
                          <span className="hidden shrink-0 gap-1 sm:flex"><StatusBadge status={m.status} /><AssertionBadge assertion={m.assertion} /></span>
                        </Link>
                      </li>
                    ))}
                  </ol>
                </Card>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
