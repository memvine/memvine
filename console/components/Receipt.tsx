"use client";
// The receipt: how every fact is shown, everywhere in the console (docs/memory-graph-plan.md,
// "The receipt"). Statement on the left; trust word, date and source on the right; why on a second
// line; the quote, and Still true, Wrong and Forget, on hover (or a tap, on a touch screen).
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useActionState, useEffect, useRef, useState, type ReactNode } from "react";
import { Archive, Check, CircleAlert, RotateCcw, X } from "lucide-react";
import { correctMemory, forgetMemory, stillTrue, type ReceiptState } from "@/app/actions";
import type { MemoryItem } from "@/lib/types";
import { trustWords } from "@/lib/trust";
import { button, CATEGORY_LABEL, cx, FormError, inputClass, shortDate } from "./ui";

export interface ReceiptProps {
  m: MemoryItem;
  /** Where the statement links: the fact's drawer, graph node or page. */
  href?: string;
  /** Show the category (Decision, Rule…) above the statement. */
  showCategory?: boolean;
  /** Hide Still true, Wrong and Forget (e.g. on a fact shown as history). */
  readOnly?: boolean;
  /** After Wrong, set this search parameter to the new fact's id, so the page follows it. */
  followParam?: string;
  /** Larger statement, for the head of a panel. */
  size?: "md" | "lg";
  /** Keep the quote and actions open (a panel's own fact). */
  open?: boolean;
  /** The thread it was decided in, shown under the why (lists that span threads). */
  thread?: { id: string; title: string } | null;
  className?: string;
}

function sourceOf(m: MemoryItem): { label: string; href?: string } | null {
  const ids = [...new Set(m.evidence.map((e) => e.episodeId))];
  if (ids.length === 0) return null;
  if (ids[0] === "console") return { label: "console" };
  return ids.length === 1
    ? { label: "source", href: `/sources/${encodeURIComponent(ids[0])}` }
    : { label: `${ids.length} sources`, href: `/sources/${encodeURIComponent(ids[0])}` };
}

export function Receipt({ m, href, showCategory = false, readOnly = false, followParam, size = "md", open: pinned = false, thread, className = "" }: ReceiptProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(pinned);
  const [wrong, setWrong] = useState(false);
  const [confirmState, confirmAction, confirming] = useActionState<ReceiptState, FormData>(stillTrue, {});
  const [forgetState, forgetAction, forgetting] = useActionState<ReceiptState, FormData>(forgetMemory, {});
  const [wrongState, wrongAction, correcting] = useActionState<ReceiptState, FormData>(correctMemory, {});
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!wrongState.replacedBy) return;
    setWrong(false);
    if (followParam) {
      const next = new URLSearchParams(window.location.search);
      next.set(followParam, wrongState.replacedBy);
      router.replace(`${pathname}?${next}`, { scroll: false });
    }
    // Only a new correction moves the page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wrongState.replacedBy]);
  useEffect(() => {
    if (wrong) input.current?.focus();
  }, [wrong]);

  const faded = m.status === "superseded" || m.status === "retired";
  // Past the end it stated: still listed, no longer served as current.
  const expired = Boolean(m.expired);
  const live = m.status === "active" || m.status === "stale";
  const quote = m.evidence[0]?.quote;
  const source = sourceOf(m);
  const error = confirmState.error ?? forgetState.error ?? wrongState.error;
  const statementCls = cx(
    size === "lg" ? "text-[15.5px] leading-snug font-medium" : "text-[13.5px] leading-snug",
    faded ? "text-text-3 line-through decoration-text-3/50" : expired ? "text-text-3" : "text-text",
  );
  const reveal = open || wrong || Boolean(error);

  return (
    <div
      className={cx("group/receipt @container relative", className)}
      data-receipt={m.id}
      onClick={(e) => {
        // A tap on the receipt itself (not a link, button or field) opens its quote and actions.
        if (!(e.target as HTMLElement).closest("a, button, input, form")) setOpen((o) => pinned || !o);
      }}
    >
      <div className="flex flex-col gap-1 @md:flex-row @md:items-start @md:gap-4">
        <div className="min-w-0 flex-1">
          {showCategory && (
            <span className="mb-0.5 block text-[11px] font-medium uppercase tracking-[0.06em] text-text-3">{m.category ? CATEGORY_LABEL[m.category].one : m.kind}</span>
          )}
          {href ? (
            <p className={statementCls}><Link href={href} scroll={false} className="decoration-text-3/60 underline-offset-2 hover:underline">{m.statement}</Link></p>
          ) : (
            <p className={statementCls}>{m.statement}</p>
          )}
          {m.why && <p className="mt-0.5 text-[12.5px] leading-snug text-text-3">{m.why}</p>}
          {thread && (
            <p className="mt-0.5 text-[12px] text-text-3">
              decided in <Link href={`/threads/${thread.id}`} className="text-text-2 hover:text-text hover:underline">{thread.title}</Link>
            </p>
          )}
          {m.status === "stale" && (
            <p className="mt-1 inline-flex items-center gap-1 text-[12px] text-warn">
              <CircleAlert className="size-3.5 shrink-0" aria-hidden /> May be outdated{m.staleReason ? `: ${m.staleReason}` : ""}
            </p>
          )}
        </div>
        <p className="shrink-0 text-[12px] leading-5 text-text-3 @md:max-w-[40%] @md:text-right">
          <span className={cx(m.assertion === "claimed" && "text-warn", m.assertion === "inferred" && "italic")}>{trustWords(m)}</span>
          {" · "}
          <time dateTime={m.validFrom}>{shortDate(m.validFrom)}</time>
          {source && (
            <>
              {" · "}
              {source.href ? <Link href={source.href} className="hover:text-text hover:underline">{source.label}</Link> : source.label}
            </>
          )}
          {m.status === "superseded" && <span className="block">superseded {m.validTo ? shortDate(m.validTo) : ""}</span>}
          {m.status === "retired" && <span className="block">forgotten</span>}
          {expired && <span className="block">Expired {m.validTo ? shortDate(m.validTo) : ""}</span>}
        </p>
      </div>

      <div
        className={cx(
          "grid transition-[grid-template-rows,opacity] duration-150",
          reveal ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0 group-hover/receipt:grid-rows-[1fr] group-hover/receipt:opacity-100 group-focus-within/receipt:grid-rows-[1fr] group-focus-within/receipt:opacity-100",
        )}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="flex flex-col gap-2 pt-2 @xl:flex-row @xl:items-center @xl:justify-between">
            {quote ? (
              <blockquote className="min-w-0 border-l-2 border-border-strong pl-2.5 text-[12.5px] leading-snug text-text-2">“{quote}”</blockquote>
            ) : <span />}
            {!readOnly && !wrong && (
              <div className="flex shrink-0 flex-wrap gap-1.5">
                {live && (
                  <>
                    <form action={confirmAction}>
                      <input type="hidden" name="id" value={m.id} />
                      <button type="submit" disabled={confirming} className={button("secondary", "sm")} title="It still holds: confirm it, and anchor it to the code as it is now">
                        <Check aria-hidden /> {confirming ? "Saving…" : "Still true"}
                      </button>
                    </form>
                    <button type="button" onClick={() => setWrong(true)} className={button("secondary", "sm")} title="Say what is true instead">
                      <X aria-hidden /> Wrong
                    </button>
                    <form action={forgetAction}>
                      <input type="hidden" name="id" value={m.id} />
                      <button type="submit" disabled={forgetting} className={button("danger", "sm")} title="Retire it: recall stops serving it">
                        <Archive aria-hidden /> {forgetting ? "Forgetting…" : "Forget"}
                      </button>
                    </form>
                  </>
                )}
                {m.status === "retired" && (
                  <form action={forgetAction}>
                    <input type="hidden" name="id" value={m.id} />
                    <input type="hidden" name="restore" value="1" />
                    <button type="submit" disabled={forgetting} className={button("secondary", "sm")}><RotateCcw aria-hidden /> Restore</button>
                  </form>
                )}
              </div>
            )}
          </div>
          {wrong && (
            <form action={wrongAction} className="mt-2.5 space-y-2">
              <input type="hidden" name="id" value={m.id} />
              <label htmlFor={`wrong-${m.id}`} className="block text-[12.5px] font-medium text-text-2">What is true instead?</label>
              <input ref={input} id={`wrong-${m.id}`} name="statement" required minLength={3} maxLength={2000} defaultValue=""
                     placeholder={m.statement} className={inputClass}
                     onKeyDown={(e) => e.key === "Escape" && setWrong(false)} />
              <div className="flex flex-wrap items-center gap-2">
                <button type="submit" disabled={correcting} className={button("primary", "sm")}>{correcting ? "Saving…" : "Save; it supersedes this"}</button>
                <button type="button" onClick={() => setWrong(false)} className={button("ghost", "sm")}>Cancel</button>
              </div>
            </form>
          )}
          {error && <div className="mt-2"><FormError>{error}</FormError></div>}
        </div>
      </div>
    </div>
  );
}

/** A list of receipts with dividers, for a card or a page. */
export function ReceiptList({ items, hrefFor, showCategory = false, followParam, threads, selected, empty }: {
  items: MemoryItem[];
  hrefFor?: Record<string, string>;
  showCategory?: boolean;
  followParam?: string;
  /** Thread titles by id, to say where each fact was decided. */
  threads?: Record<string, string>;
  /** The id whose drawer is open, highlighted. */
  selected?: string;
  empty?: ReactNode;
}) {
  if (items.length === 0) return <>{empty ?? null}</>;
  return (
    <ul className="divide-y divide-border">
      {items.map((m) => (
        <li key={m.id} className={cx("px-4 py-3 transition-colors sm:px-5", selected === m.id ? "bg-surface-2" : "hover:bg-surface-2/50")}>
          <Receipt m={m} href={hrefFor?.[m.id]} showCategory={showCategory} followParam={followParam} readOnly={m.status === "superseded"}
                   thread={m.threadId && threads?.[m.threadId] ? { id: m.threadId, title: threads[m.threadId] } : null} />
        </li>
      ))}
    </ul>
  );
}
