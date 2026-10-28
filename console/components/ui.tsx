// The console's design system: presentational building blocks shared by every screen.
// Server-safe (no hooks); interactive pieces (Drawer, CopyButton) live in ./client.tsx.
import Link from "next/link";
import type { ComponentProps, ComponentType, ReactNode } from "react";
import { ChevronDown, Search, SlidersHorizontal } from "lucide-react";
import type { Assertion, KnowledgeCategory, MemoryStatus, ThreadKind, ThreadStatus } from "@/lib/types";

export type Icon = ComponentType<{ className?: string; strokeWidth?: number; "aria-hidden"?: boolean }>;

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

// ---------------------------------------------------------------- page structure

export function PageHeader({ icon: IconC, title, count, description, actions, back, meta }: {
  icon?: Icon;
  title: ReactNode;
  count?: number | string;
  description?: ReactNode;
  actions?: ReactNode;
  back?: { href: string; label: string };
  meta?: ReactNode;
}) {
  return (
    <header className="mb-6">
      {back && (
        <Link href={back.href} className="mb-3 inline-flex items-center gap-1 text-[13px] text-text-3 transition-colors hover:text-text">
          <ChevronDown className="size-3.5 rotate-90" aria-hidden /> {back.label}
        </Link>
      )}
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="w-full min-w-0 sm:w-auto sm:flex-1">
          <h1 className="flex min-w-0 items-center gap-2.5 text-[19px] font-semibold tracking-[-0.01em] text-text">
            {IconC && <IconC className="size-[18px] shrink-0 text-text-2" strokeWidth={1.75} aria-hidden />}
            <span className="min-w-0 break-words">{title}</span>
            {count !== undefined && <span className="font-normal tabular-nums text-text-3">{count}</span>}
          </h1>
          {description && <p className="mt-1.5 max-w-2xl text-[13.5px] leading-relaxed text-text-2">{description}</p>}
          {meta && <div className="mt-3 flex flex-wrap items-center gap-2">{meta}</div>}
        </div>
        {actions && <div className="flex max-w-full shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}

export function Card({ children, className = "", ...rest }: { children: ReactNode; className?: string } & Omit<ComponentProps<"div">, "className" | "children">) {
  return <div className={cx("rounded-xl border border-border bg-surface", className)} {...rest}>{children}</div>;
}

export function CardHeader({ title, description, action, icon: IconC }: { title: ReactNode; description?: ReactNode; action?: ReactNode; icon?: Icon }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border px-4 py-3 sm:px-5">
      <div className="min-w-0">
        <h2 className="flex items-center gap-2 text-[13.5px] font-medium text-text">
          {IconC && <IconC className="size-4 text-text-3" strokeWidth={1.75} aria-hidden />}
          {title}
        </h2>
        {description && <p className="mt-0.5 text-[12.5px] text-text-3">{description}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export function CardBody({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={cx("px-4 py-4 sm:px-5", className)}>{children}</div>;
}

export function CardLink({ href, children }: { href: string; children: ReactNode }) {
  return <Link href={href} className="text-[12.5px] text-text-3 transition-colors hover:text-text">{children}</Link>;
}

/** Small uppercase label used for groups, column headers and section names. */
export function Eyebrow({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={cx("text-[11px] font-medium uppercase tracking-[0.06em] text-text-3", className)}>{children}</div>;
}

export function Stat({ label, value, hint, icon: IconC, href }: { label: string; value: string; hint?: ReactNode; icon?: Icon; href?: string }) {
  const body = (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12.5px] text-text-2">{label}</span>
        {IconC && <IconC className="size-4 text-text-3" strokeWidth={1.75} aria-hidden />}
      </div>
      <div className="mt-2 text-[26px] font-semibold leading-none tracking-[-0.02em] tabular-nums">{value}</div>
      {hint && <div className="mt-2 truncate text-[12px] text-text-3">{hint}</div>}
    </>
  );
  const cls = "block rounded-xl border border-border bg-surface p-4 sm:p-5";
  return href
    ? <Link href={href} className={cx(cls, "transition-colors hover:border-border-strong")}>{body}</Link>
    : <div className={cls}>{body}</div>;
}

// ---------------------------------------------------------------- badges and pills

export type Tone = "neutral" | "good" | "warn" | "bad" | "info" | "outline";
const tones: Record<Tone, string> = {
  neutral: "bg-surface-3 text-text-2",
  good: "bg-good-soft text-good",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
  info: "bg-info-soft text-info",
  outline: "border border-border text-text-2",
};
const dots: Record<Tone, string> = {
  neutral: "bg-text-3", good: "bg-good", warn: "bg-warn", bad: "bg-bad", info: "bg-info", outline: "bg-text-3",
};

export function Badge({ children, tone = "neutral", dot = false, className = "", title }: { children: ReactNode; tone?: Tone; dot?: boolean; className?: string; title?: string }) {
  return (
    <span title={title} className={cx("inline-flex h-[22px] shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-2 text-[11.5px] font-medium leading-none", tones[tone], className)}>
      {dot && <span className={cx("size-1.5 rounded-full", dots[tone])} aria-hidden />}
      {children}
    </span>
  );
}

const statusTone: Record<MemoryStatus, Tone> = { active: "good", superseded: "neutral", retired: "neutral", stale: "warn" };
export function StatusBadge({ status }: { status: MemoryStatus }) {
  return <Badge tone={statusTone[status]} dot>{status}</Badge>;
}

const assertionTone: Record<Assertion, Tone> = { observed: "outline", stated: "outline", claimed: "warn", inferred: "outline" };
export function AssertionBadge({ assertion }: { assertion: Assertion }) {
  return <Badge tone={assertionTone[assertion]}>{assertion}</Badge>;
}

const threadTone: Record<ThreadStatus, Tone> = { open: "info", blocked: "warn", done: "good", abandoned: "neutral" };
export function ThreadStatusBadge({ status }: { status: ThreadStatus }) {
  return <Badge tone={threadTone[status]} dot>{status}</Badge>;
}

export const CATEGORY_LABEL: Record<KnowledgeCategory, { one: string; many: string }> = {
  decision: { one: "Decision", many: "Decisions" },
  rule: { one: "Rule", many: "Rules" },
  gotcha: { one: "Gotcha", many: "Gotchas" },
  fact: { one: "Fact", many: "Facts" },
  preference: { one: "Preference", many: "Preferences" },
};
export function CategoryBadge({ category }: { category: KnowledgeCategory }) {
  return <Badge tone="outline">{CATEGORY_LABEL[category].one}</Badge>;
}

export const THREAD_KIND_LABEL: Record<ThreadKind, string> = { release: "Release", task: "Task", pr: "Pull request", ticket: "Ticket", topic: "Topic" };

/** Inline code-ish token, e.g. an id, a key or a path. */
export function Mono({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={cx("font-mono text-[12px] text-text-2", className)}>{children}</span>;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-border bg-surface-2 px-1 font-mono text-[10.5px] text-text-3">{children}</kbd>;
}

// ---------------------------------------------------------------- buttons and fields

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "accent";
export type ButtonSize = "sm" | "md";

const buttonBase =
  "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-colors disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-4 [&_svg]:shrink-0";
const buttonVariants: Record<ButtonVariant, string> = {
  primary: "bg-inverse text-inverse-text hover:opacity-90",
  accent: "bg-accent text-accent-text hover:opacity-90",
  secondary: "border border-border-strong bg-surface text-text hover:bg-surface-2",
  ghost: "text-text-2 hover:bg-surface-2 hover:text-text",
  danger: "border border-border-strong bg-surface text-bad hover:border-bad/40 hover:bg-bad-soft",
};
const buttonSizes: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 text-[12.5px] [&_svg]:size-3.5",
  md: "h-8 px-3 text-[13px]",
};

export function button(variant: ButtonVariant = "secondary", size: ButtonSize = "md", extra = ""): string {
  return cx(buttonBase, buttonVariants[variant], buttonSizes[size], extra);
}

export const buttonClass = buttonBase;
export const primaryButton = button("primary");
export const secondaryButton = button("secondary");
export const ghostButton = button("ghost");
export const dangerButton = button("danger");

export function Button({ variant = "secondary", size = "md", className = "", ...rest }: { variant?: ButtonVariant; size?: ButtonSize } & ComponentProps<"button">) {
  return <button className={button(variant, size, className)} {...rest} />;
}

export function ButtonLink({ variant = "secondary", size = "md", className = "", ...rest }: { variant?: ButtonVariant; size?: ButtonSize } & ComponentProps<typeof Link>) {
  return <Link className={button(variant, size, className)} {...rest} />;
}

export const inputClass =
  "block w-full rounded-lg border border-border-strong bg-surface px-3 py-[7px] text-[13px] text-text placeholder:text-text-3 transition-colors hover:border-text-3/50 focus:border-ring focus:outline-none focus:ring-2 focus:ring-ring/20 disabled:opacity-60";

export const selectClass = cx(inputClass, "appearance-none pr-8");

/** A native select with a consistent chevron. */
export function Select({ className = "", wrapperClassName = "", children, ...rest }: ComponentProps<"select"> & { wrapperClassName?: string }) {
  return (
    <div className={cx("relative", wrapperClassName)}>
      <select className={cx(selectClass, className)} {...rest}>{children}</select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-2.5 size-3.5 -translate-y-1/2 text-text-3" aria-hidden />
    </div>
  );
}

/** A search box for a GET form: a magnifier, the field, and room for filters beside it. */
export function SearchField({ name = "q", defaultValue, placeholder, label, className = "" }: { name?: string; defaultValue?: string; placeholder: string; label: string; className?: string }) {
  return (
    <div className={cx("relative min-w-0 flex-1", className)}>
      <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-text-3" strokeWidth={1.75} aria-hidden />
      <input type="search" name={name} defaultValue={defaultValue} placeholder={placeholder} aria-label={label}
             className={cx(inputClass, "h-9 pl-9")} />
    </div>
  );
}

/** A filter bar: search field, optional selects, a submit, and a clear link when filtered. */
export function FilterBar({ children, clearHref, filtered }: { children: ReactNode; clearHref: string; filtered: boolean }) {
  return (
    <form role="search" className="mb-4 flex flex-wrap items-center gap-2">
      {children}
      <button type="submit" className={button("secondary", "md", "h-9")} aria-label="Apply filters">
        <SlidersHorizontal aria-hidden /> <span className="hidden sm:inline">Filter</span>
      </button>
      {filtered && <Link href={clearHref} className={button("ghost", "md", "h-9")}>Clear</Link>}
    </form>
  );
}

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-[12.5px] font-medium text-text-2">{label}</label>
      {children}
      {hint && <p className="text-[12px] text-text-3">{hint}</p>}
    </div>
  );
}

export function FormError({ children }: { children: ReactNode }) {
  return <p role="alert" className="text-[12.5px] text-bad">{children}</p>;
}

// ---------------------------------------------------------------- tables

/** A bordered, horizontally scrollable table with an optional footer line. */
export function Table({ children, footer, className = "", minWidth = 640 }: { children: ReactNode; footer?: ReactNode; className?: string; minWidth?: number }) {
  return (
    <div className={cx("overflow-hidden rounded-xl border border-border bg-surface", className)}>
      <div className="scroll-quiet overflow-x-auto">
        <table className="w-full border-collapse text-left text-[13px]" style={{ minWidth }}>{children}</table>
      </div>
      {footer && <div className="border-t border-border px-4 py-2.5 text-[12px] text-text-3 sm:px-5">{footer}</div>}
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return <thead><tr className="border-b border-border">{children}</tr></thead>;
}

export function Th({ children, className = "", align = "left" }: { children?: ReactNode; className?: string; align?: "left" | "right" }) {
  return (
    <th scope="col" className={cx("h-9 px-4 text-[11px] font-medium uppercase tracking-[0.06em] whitespace-nowrap text-text-3 first:pl-4 sm:first:pl-5 last:pr-4 sm:last:pr-5", align === "right" && "text-right", className)}>
      {children}
    </th>
  );
}

export function TBody({ children }: { children: ReactNode }) {
  return <tbody className="divide-y divide-border">{children}</tbody>;
}

/** A row; with `interactive` it highlights on hover and a RowLink inside makes the whole row clickable. */
export function Tr({ children, interactive = false, className = "", selected = false }: { children: ReactNode; interactive?: boolean; className?: string; selected?: boolean }) {
  return (
    <tr className={cx(interactive && "relative transition-colors hover:bg-surface-2 focus-within:bg-surface-2", selected && "bg-surface-2", className)}>
      {children}
    </tr>
  );
}

export function Td({ children, className = "", align = "left", muted = false, nowrap = false }: { children?: ReactNode; className?: string; align?: "left" | "right"; muted?: boolean; nowrap?: boolean }) {
  return (
    <td className={cx("h-12 px-4 py-2.5 align-middle first:pl-4 sm:first:pl-5 last:pr-4 sm:last:pr-5", align === "right" && "text-right tabular-nums", muted && "text-text-2", nowrap && "whitespace-nowrap", className)}>
      {children}
    </td>
  );
}

/** The link in a row's main cell; its hit area covers the whole row. */
export function RowLink({ href, children, className = "", scroll }: { href: string; children: ReactNode; className?: string; scroll?: boolean }) {
  return (
    <Link href={href} scroll={scroll} className={cx("text-text outline-none before:absolute before:inset-0 before:content-[''] focus-visible:before:outline-2 focus-visible:before:-outline-offset-2 focus-visible:before:outline-ring", className)}>
      {children}
    </Link>
  );
}

export function TableEmpty({ colSpan, children }: { colSpan: number; children: ReactNode }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-5 py-14 text-center text-[13px] text-text-3">{children}</td>
    </tr>
  );
}

// ---------------------------------------------------------------- lists, key/values, empty states

export function KeyValueList({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <dl className={cx("divide-y divide-border", className)}>{children}</dl>;
}

export function KeyValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(96px,34%)_1fr] items-baseline gap-4 py-3">
      <dt className="text-[13px] text-text-3">{label}</dt>
      <dd className="min-w-0 break-words text-[13px] text-text">{children}</dd>
    </div>
  );
}

export function Empty({ children, icon: IconC, title, action }: { children: ReactNode; icon?: Icon; title?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center">
      {IconC && (
        <div className="mb-3 grid size-10 place-items-center rounded-xl border border-border bg-surface-2">
          <IconC className="size-[18px] text-text-3" strokeWidth={1.75} aria-hidden />
        </div>
      )}
      {title && <p className="text-[14px] font-medium text-text">{title}</p>}
      <p className={cx("max-w-sm text-[13px] text-text-3", title && "mt-1")}>{children}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** The quoted evidence block: what the source actually said. */
export function SourceQuote({ quote, children, label = "From your source" }: { quote: string; children?: ReactNode; label?: string }) {
  return (
    <figure className="rounded-lg border border-border bg-surface-2 px-4 py-3.5">
      <Eyebrow>{label}</Eyebrow>
      <blockquote className="mt-2 text-[13.5px] leading-relaxed text-text">“{quote}”</blockquote>
      {children && <figcaption className="mt-2 text-[12px] text-text-3">{children}</figcaption>}
    </figure>
  );
}

export function Callout({ tone = "warn", title, children }: { tone?: "warn" | "bad" | "info" | "good"; title: string; children: ReactNode }) {
  const map = {
    warn: "border-warn/30 bg-warn-soft",
    bad: "border-bad/30 bg-bad-soft",
    info: "border-info/30 bg-info-soft",
    good: "border-good/30 bg-good-soft",
  } as const;
  const text = { warn: "text-warn", bad: "text-bad", info: "text-info", good: "text-good" } as const;
  return (
    <div className={cx("rounded-lg border px-4 py-3 text-[13px]", map[tone])}>
      <p className={cx("font-medium", text[tone])}>{title}</p>
      <div className="mt-0.5 text-text-2">{children}</div>
    </div>
  );
}

/** Link-based tabs: each tab is a URL, so the server renders the selected one. */
export function Tabs({ items, label }: { items: { href: string; label: string; count?: number; active: boolean }[]; label: string }) {
  return (
    <nav aria-label={label} className="scroll-quiet -mx-1 mb-4 flex gap-1 overflow-x-auto border-b border-border px-1">
      {items.map((t) => (
        <Link
          key={t.href}
          href={t.href}
          aria-current={t.active ? "page" : undefined}
          className={cx(
            "relative -mb-px inline-flex h-9 shrink-0 items-center gap-1.5 border-b-2 px-2.5 text-[13px] whitespace-nowrap transition-colors",
            t.active ? "border-text font-medium text-text" : "border-transparent text-text-2 hover:text-text",
          )}
        >
          {t.label}
          {t.count !== undefined && <span className="tabular-nums text-text-3">{t.count}</span>}
        </Link>
      ))}
    </nav>
  );
}

/** A CSS-only tooltip; the trigger must be focusable for keyboard users. */
export function Tooltip({ content, children }: { content: string; children: ReactNode }) {
  return (
    <span className="group/tip relative inline-flex">
      {children}
      <span role="tooltip" className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-1.5 -translate-x-1/2 rounded-md border border-border bg-surface-3 px-2 py-1 text-[11.5px] whitespace-nowrap text-text opacity-0 shadow-panel transition-opacity group-focus-within/tip:opacity-100 group-hover/tip:opacity-100">
        {content}
      </span>
    </span>
  );
}

// ---------------------------------------------------------------- formatting

export function formatDate(iso: string | null, withTime = false): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
    timeZone: "UTC",
  });
}

/** "21 Oct" (this year) or "21 Oct 2025"; for dense tables. */
export function shortDate(iso: string | null, withTime = false): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  const sameYear = d.getUTCFullYear() === new Date().getUTCFullYear();
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
    timeZone: "UTC",
  });
}

export function timeAgo(iso: string | null, now = Date.now()): string {
  if (!iso) return "never";
  const minutes = Math.round((now - Date.parse(iso)) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}
