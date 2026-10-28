"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  Activity, BookMarked, Brain, ChartColumn, ChevronsUpDown, Cog, FileText, FolderKanban, KeyRound,
  House, LogOut, Menu, MessageCircleQuestion, MessagesSquare, Plug, PlugZap, Settings, Users, Waypoints, X,
} from "lucide-react";
import { switchProject } from "@/app/actions";
import type { Project } from "@/lib/types";
import { Logo } from "./Brand";
import { cx, type Icon } from "./ui";

const NAV: { group: string; items: { href: string; label: string; icon: Icon }[] }[] = [
  { group: "Project", items: [
    { href: "/", label: "Home", icon: House },
    { href: "/playground", label: "Ask", icon: MessageCircleQuestion },
    { href: "/threads", label: "Threads", icon: Waypoints },
    { href: "/knowledge", label: "Knowledge", icon: BookMarked },
    { href: "/memories", label: "Memories", icon: Brain },
    { href: "/sessions", label: "Sessions", icon: MessagesSquare },
    { href: "/sources", label: "Sources", icon: FileText },
  ] },
  { group: "Connect", items: [
    { href: "/connect", label: "Connect your agent", icon: PlugZap },
    { href: "/integrations", label: "Integrations", icon: Plug },
    { href: "/audiences", label: "Audiences", icon: Users },
  ] },
  { group: "Developer", items: [
    { href: "/requests", label: "Requests", icon: Activity },
    { href: "/usage", label: "Usage", icon: ChartColumn },
    { href: "/keys", label: "API keys", icon: KeyRound },
    { href: "/jobs", label: "Jobs", icon: Cog },
  ] },
  { group: "Organization", items: [
    { href: "/projects", label: "Projects", icon: FolderKanban },
    { href: "/settings", label: "Settings", icon: Settings },
  ] },
];

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

function initials(text: string) {
  const name = text.split("@")[0].replace(/[._-]+/g, " ").trim();
  const parts = name.split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

export interface SidebarViewer { label: string; detail?: string | null; canSignOut: boolean }

export function Sidebar({ projects, current, live, viewer, credits, reviewCount = 0 }: {
  /** Current memories awaiting review: a badge on Home. */
  reviewCount?: number;
  projects: Project[];
  current: string;
  live: boolean;
  viewer: SidebarViewer | null;
  credits: { used: number; cap: number | null } | null;
}) {
  const pathname = usePathname();
  const form = useRef<HTMLFormElement>(null);
  const [open, setOpen] = useState(false);
  const currentName = projects.find((p) => p.slug === current)?.name ?? "No project";

  // A navigation closes the mobile menu.
  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const who = viewer?.label ?? "Demo organization";
  const detail = viewer ? viewer.detail : "mock data";

  const panel = (
    <div className="flex h-full flex-col">
      <div className="flex h-14 shrink-0 items-center gap-2.5 px-4">
        <Logo />
        <span className="text-[15px] font-semibold tracking-[-0.02em]">memvine</span>
        {!live && <span className="ml-1 rounded-full border border-border px-1.5 py-px text-[10.5px] font-medium text-text-3">mock data</span>}
        <button type="button" onClick={() => setOpen(false)} aria-label="Close menu"
                className="ml-auto grid size-8 place-items-center rounded-lg text-text-3 hover:bg-surface-2 hover:text-text md:hidden">
          <X className="size-4" aria-hidden />
        </button>
      </div>

      <form ref={form} action={switchProject} className="px-3 pb-2">
        <label htmlFor="project" className="sr-only">Project</label>
        <div className="relative">
          <FolderKanban className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-text-3" strokeWidth={1.75} aria-hidden />
          <select
            id="project"
            name="project"
            defaultValue={current}
            key={current}
            onChange={() => form.current?.requestSubmit()}
            disabled={projects.length === 0}
            className="h-9 w-full cursor-pointer appearance-none truncate rounded-lg border border-border bg-surface-2 pr-8 pl-8 text-[13px] font-medium text-text transition-colors hover:border-border-strong focus:border-ring focus:outline-none disabled:cursor-default"
          >
            {projects.length === 0 && <option value="">No project yet</option>}
            {projects.map((p) => (
              <option key={p.slug} value={p.slug}>{p.name}</option>
            ))}
          </select>
          <ChevronsUpDown className="pointer-events-none absolute top-1/2 right-2.5 size-3.5 -translate-y-1/2 text-text-3" aria-hidden />
        </div>
      </form>

      <nav aria-label="Console" className="scroll-quiet min-h-0 flex-1 overflow-y-auto px-3 pt-2 pb-4">
        {NAV.map((section) => (
          <div key={section.group} className="mb-4">
            <div className="px-2 pb-1.5 text-[11px] font-medium uppercase tracking-[0.06em] text-text-3">{section.group}</div>
            <ul className="space-y-px">
              {section.items.map((item) => {
                const active = isActive(pathname, item.href);
                const ItemIcon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={cx(
                        "flex h-8 items-center gap-2.5 rounded-lg px-2 text-[13.5px] transition-colors",
                        active ? "bg-surface-3 font-medium text-text" : "text-text-2 hover:bg-surface-2 hover:text-text",
                      )}
                    >
                      <ItemIcon className={cx("size-4 shrink-0", active ? "text-text" : "text-text-3")} strokeWidth={1.75} aria-hidden />
                      {item.label}
                      {item.href === "/" && reviewCount > 0 && (
                        <span className="ml-auto rounded-full bg-accent-soft px-1.5 py-px text-[11px] font-medium tabular-nums text-accent"
                              title={`${reviewCount} new ${reviewCount === 1 ? "memory" : "memories"} to review`}>
                          {reviewCount > 99 ? "99+" : reviewCount}
                          <span className="sr-only"> to review</span>
                        </span>
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="shrink-0 space-y-2 border-t border-border p-3">
        {credits && (
          <Link href="/usage" className="block rounded-lg border border-border bg-surface-2 px-3 py-2.5 transition-colors hover:border-border-strong">
            <div className="flex items-center justify-between text-[12px]">
              <span className="font-medium text-text">Credits this month</span>
              <span className="tabular-nums text-text-3">
                <span className="text-text">{Math.round(credits.used).toLocaleString()}</span>
                {credits.cap !== null && ` / ${credits.cap.toLocaleString()}`}
              </span>
            </div>
            {credits.cap !== null ? (
              <div className="mt-2 h-1 overflow-hidden rounded-full bg-surface-3">
                <div
                  className={cx("h-full rounded-full", credits.used >= credits.cap ? "bg-bad" : "bg-accent")}
                  style={{ width: `${Math.min(100, (credits.used / Math.max(1, credits.cap)) * 100)}%` }}
                />
              </div>
            ) : (
              <div className="mt-1 text-[11.5px] text-text-3">No monthly cap</div>
            )}
          </Link>
        )}
        <div className="flex items-center gap-2.5 rounded-lg px-1 py-1">
          <span className="grid size-8 shrink-0 place-items-center rounded-full bg-surface-3 text-[11.5px] font-semibold text-text-2" aria-hidden>{initials(who)}</span>
          <div className="min-w-0 flex-1 leading-tight">
            <div className="truncate text-[13px] font-medium text-text" title={who}>{who}</div>
            {detail && <div className="truncate text-[11.5px] text-text-3" title={detail}>{detail}</div>}
          </div>
          {viewer?.canSignOut && (
            <a href="/logout" aria-label="Sign out" title="Sign out"
               className="grid size-8 shrink-0 place-items-center rounded-lg text-text-3 transition-colors hover:bg-surface-2 hover:text-text">
              <LogOut className="size-4" aria-hidden />
            </a>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <>
      {/* Phone and tablet: a top bar with a menu button; the panel slides over the page. */}
      <div className="sticky top-0 z-40 flex h-14 items-center gap-3 border-b border-border bg-bg/90 px-4 backdrop-blur md:hidden">
        <button type="button" onClick={() => setOpen(true)} aria-label="Open menu" aria-expanded={open}
                className="-ml-1.5 grid size-9 place-items-center rounded-lg text-text-2 hover:bg-surface-2 hover:text-text">
          <Menu className="size-5" aria-hidden />
        </button>
        <Logo className="size-6" />
        <span className="truncate text-[14px] font-medium">{currentName}</span>
      </div>
      {open && <button type="button" aria-label="Close menu" tabIndex={-1} onClick={() => setOpen(false)} className="drawer-fade fixed inset-0 z-40 bg-overlay md:hidden" />}

      <aside
        className={cx(
          "fixed inset-y-0 left-0 z-50 w-[264px] border-r border-border bg-surface transition-[transform,visibility] duration-200 md:sticky md:top-0 md:z-auto md:h-screen md:w-[248px] md:shrink-0 md:translate-x-0 md:bg-bg",
          open ? "visible translate-x-0 shadow-panel" : "invisible -translate-x-full md:visible",
        )}
        aria-label="Sidebar"
      >
        {panel}
      </aside>
    </>
  );
}
