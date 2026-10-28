"use client";
// Interactive pieces of the design system. Everything else in ./ui.tsx renders on the server.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, Copy, X } from "lucide-react";
import { cx } from "./ui";

/** Copies a value to the clipboard; shows a check for a moment after. */
export function CopyButton({ value, label = "Copy", className = "", showLabel = false }: { value: string; label?: string; className?: string; showLabel?: boolean }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);
  return (
    <button
      type="button"
      aria-label={copied ? "Copied" : label}
      title={copied ? "Copied" : label}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
        } catch {
          /* clipboard refused: nothing to show */
        }
      }}
      className={cx(
        "inline-flex items-center gap-1.5 rounded-md text-text-3 transition-colors hover:text-text",
        showLabel ? "h-8 border border-border-strong bg-surface px-3 text-[13px] font-medium text-text hover:bg-surface-2" : "size-6 justify-center hover:bg-surface-2",
        className,
      )}
    >
      {copied ? <Check className="size-3.5 text-good" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
      {showLabel && (copied ? "Copied" : label)}
    </button>
  );
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

/** A right-side panel over the page. Its open state lives in the URL, so the server renders the
 *  content; closing navigates to `closeHref` (Esc, the close button or the backdrop). */
export function Drawer({ closeHref, label, header, footer, children }: {
  closeHref: string;
  label: string;
  header: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}) {
  const router = useRouter();
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(null);

  useEffect(() => {
    opener.current = document.activeElement;
    panel.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        router.push(closeHref, { scroll: false });
      } else if (e.key === "Tab" && panel.current) {
        // Keep keyboard focus inside the panel while it is open.
        const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1];
        if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      const back = opener.current;
      if (back instanceof HTMLElement && back.isConnected) back.focus({ preventScroll: true });
    };
  }, [closeHref, router]);

  return (
    <div className="fixed inset-0 z-50">
      <Link href={closeHref} scroll={false} aria-label="Close panel" tabIndex={-1} className="drawer-fade absolute inset-0 bg-overlay" />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        className="drawer-in absolute inset-y-0 right-0 flex w-full max-w-[560px] flex-col border-l border-border bg-surface shadow-panel outline-none"
      >
        <div className="relative shrink-0 border-b border-border px-5 pt-5 pb-4 sm:px-6">
          {header}
          <Link href={closeHref} scroll={false} aria-label="Close" className="absolute top-4 right-4 grid size-8 place-items-center rounded-lg text-text-3 transition-colors hover:bg-surface-2 hover:text-text">
            <X className="size-4" aria-hidden />
          </Link>
        </div>
        <div className="scroll-quiet min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">{children}</div>
        {footer && <div className="flex shrink-0 flex-wrap items-center gap-2 border-t border-border bg-surface px-5 py-3.5 sm:px-6">{footer}</div>}
      </div>
    </div>
  );
}

/** Re-renders the page from the server every few seconds while `active`, e.g. while synthesis
 *  is still running, and stops once nothing is pending. Paused while the tab is hidden. */
export function AutoRefresh({ active, seconds = 5, label }: { active: boolean; seconds?: number; label?: string }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") router.refresh();
    }, seconds * 1000);
    return () => window.clearInterval(id);
  }, [active, seconds, router]);
  if (!active || !label) return null;
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] text-text-3" aria-live="polite">
      <span className="size-1.5 animate-pulse rounded-full bg-info" aria-hidden />
      {label}
    </span>
  );
}
