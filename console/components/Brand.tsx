import { cx } from "./ui";

/** The memvine mark: a sprout on an inverted tile. */
export function Logo({ className = "size-7" }: { className?: string }) {
  return (
    <span className={cx("grid shrink-0 place-items-center rounded-lg bg-inverse text-inverse-text", className)} aria-hidden>
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className="size-[62%]">
        <path d="M10 17.5V9" />
        <path d="M10 11C10 7.4 7.6 5 4 5c0 3.6 2.4 6 6 6Z" />
        <path d="M10 9c0-3.3 2.2-5.5 5.6-5.5 0 3.3-2.2 5.5-5.6 5.5Z" />
      </svg>
    </span>
  );
}

export function Brand({ className = "" }: { className?: string }) {
  return (
    <div className={cx("flex items-center gap-2.5", className)}>
      <Logo className="size-8" />
      <span className="text-[17px] font-semibold tracking-[-0.02em]">memvine</span>
    </div>
  );
}
