"use client";
import { useState, type ReactNode } from "react";
import { Check, Copy, KeyRound } from "lucide-react";
import { button } from "./ui";

/** A value shown once (an API key, an invite link): copy it, then Done reloads the page. */
export function SecretReveal({ title, note, secret, copyLabel = "Copy", children, actions }: {
  title: string;
  note: ReactNode;
  secret: string;
  copyLabel?: string;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded-xl border border-accent/30 bg-accent-soft p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-surface text-accent"><KeyRound className="size-4" aria-hidden /></span>
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-medium text-text">{title}</p>
          <p className="mt-0.5 text-[13px] text-text-2">{note}</p>
        </div>
      </div>
      {children && <div className="mt-3">{children}</div>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 basis-64 truncate rounded-lg border border-border bg-surface px-3 py-[7px] font-mono text-[12.5px] text-text">{secret}</code>
        <button
          type="button"
          className={button("primary")}
          onClick={async () => {
            await navigator.clipboard.writeText(secret);
            setCopied(true);
          }}
        >
          {copied ? <Check aria-hidden /> : <Copy aria-hidden />} {copied ? "Copied" : copyLabel}
        </button>
        {actions}
        <button type="button" className={button("secondary")} onClick={() => location.reload()}>Done</button>
      </div>
    </div>
  );
}

/** A command to paste, in a code block. */
export function CommandBlock({ children }: { children: string }) {
  return <pre className="scroll-quiet overflow-x-auto rounded-lg border border-border bg-surface px-3 py-2.5 font-mono text-[12px] leading-relaxed text-text">{children}</pre>;
}
