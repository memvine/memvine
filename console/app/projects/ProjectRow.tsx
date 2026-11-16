"use client";
import { useActionState, useEffect, useRef, useState } from "react";
import { Ellipsis } from "lucide-react";
import { deleteProject, openProject, updateProject, type ProjectFormState } from "@/app/actions";
import { Badge, button, cx, FormError, inputClass } from "@/components/ui";
import type { Project } from "@/lib/types";

type Mode = "view" | "edit" | "delete";

const menuItem = "flex w-full items-center rounded-md px-2.5 py-1.5 text-left text-[13px] text-text transition-colors hover:bg-surface-2";

export function ProjectRow({ project, current, lastActivity, created, canManage = true }: { project: Project; current: boolean; lastActivity: string; created: string; canManage?: boolean }) {
  const [mode, setMode] = useState<Mode>("view");
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const [editState, editAction, editing] = useActionState<ProjectFormState, FormData>(async (prev, fd) => {
    const result = await updateProject(prev, fd);
    if (result.saved) setMode("view");
    return result;
  }, {});
  const [deleteState, deleteAction, deleting] = useActionState<ProjectFormState, FormData>(deleteProject, {});

  // The menu closes on a click elsewhere or Esc.
  useEffect(() => {
    if (!menu) return;
    const onDown = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setMenu(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [menu]);

  if (deleteState.deleted) return null;

  const capped = project.monthlyCreditCap !== null && project.monthCredits >= project.monthlyCreditCap;

  return (
    <li className="px-4 py-3 sm:px-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="order-1 min-w-0 flex-1 2xl:order-none 2xl:min-w-48">
          <p className="flex items-center gap-2 text-[13.5px] font-medium text-text">
            {/* /projects/<slug> selects the project and opens its Overview; a link anyone can share. */}
            <a href={`/projects/${encodeURIComponent(project.slug)}`} className="truncate hover:underline">{project.name}</a>
            {current && <Badge tone="good" dot>current</Badge>}
            {project.personal && <Badge title="Only you can open this project, whatever role anyone else in the organization holds">only you</Badge>}
            {project.teamSpace && <Badge title="Holds only memories people shared from projects of their own; refuses sessions">team space</Badge>}
          </p>
          <p className="font-mono text-[11.5px] text-text-3">{project.slug}</p>
        </div>
        {/* Below 2xl the figures wrap under the name with their labels; on 2xl (wide enough for every column) they line up under the header. */}
        <div className="order-3 flex w-full flex-wrap items-center gap-x-4 gap-y-1.5 text-[12.5px] 2xl:contents">
          <div className="flex flex-wrap gap-1 2xl:min-w-32">
            {project.tags.length ? project.tags.map((t) => <Badge key={t} tone="outline">{t}</Badge>) : <span className="text-[12px] text-text-3">no tags</span>}
          </div>
          <div className="tabular-nums 2xl:w-20 2xl:text-right 2xl:text-[13px]" title="memories">{project.memoryCount.toLocaleString()}<span className="text-text-3 2xl:hidden"> memories</span></div>
          <div className="tabular-nums text-text-2 2xl:w-16 2xl:text-right 2xl:text-[13px]" title="sources">{project.sourceCount.toLocaleString()}<span className="text-text-3 2xl:hidden"> sources</span></div>
          <div className="tabular-nums text-text-2 2xl:w-12 2xl:text-right 2xl:text-[13px]" title="live API keys">{project.keyCount}<span className="text-text-3 2xl:hidden"> keys</span></div>
          <div className="tabular-nums 2xl:w-32 2xl:text-right" title="credits used this month / monthly cap">
            {project.monthlyCreditCap === null
              ? <span className="text-text-3">{project.monthCredits.toLocaleString()} · uncapped</span>
              : <span className={capped ? "font-medium text-bad" : "text-text-2"}>
                  {project.monthCredits.toLocaleString()} / {project.monthlyCreditCap.toLocaleString()}
                </span>}
            <span className="text-text-3 2xl:hidden"> credits</span>
          </div>
          <div className="text-text-2 2xl:w-20 2xl:text-right" title="how long raw sources are kept">
            <span className="text-text-3 2xl:hidden">keeps sources </span>{project.retentionDays === null ? "Forever" : `${project.retentionDays} days`}
          </div>
          <div className="text-text-3 2xl:w-28 2xl:text-right" title={`created ${created}`}><span className="2xl:hidden">last call </span>{lastActivity}</div>
        </div>
        <div className="relative order-2 2xl:order-none" ref={menuRef}>
          <button type="button" aria-label={`Actions for ${project.name}`} aria-expanded={menu} aria-haspopup="menu"
                  className={button("ghost", "sm", "w-8 px-0")} onClick={() => setMenu((m) => !m)}>
            <Ellipsis aria-hidden />
          </button>
          {menu && (
            <div className="absolute right-0 z-20 mt-1 w-44 rounded-lg border border-border-strong bg-surface p-1 shadow-panel" role="menu">
              <form action={openProject}>
                <input type="hidden" name="slug" value={project.slug} />
                <button role="menuitem" className={menuItem}>Open</button>
              </form>
              {canManage && <>
                <button role="menuitem" type="button" className={menuItem} onClick={() => { setMode("edit"); setMenu(false); }}>Edit settings</button>
                <a role="menuitem" href={`/export?project=${encodeURIComponent(project.slug)}`} className={menuItem}>Export data</a>
                <div className="my-1 h-px bg-border" />
                <button role="menuitem" type="button" className={cx(menuItem, "text-bad hover:bg-bad-soft")} onClick={() => { setMode("delete"); setMenu(false); }}>Delete…</button>
              </>}
            </div>
          )}
        </div>
      </div>

      {mode === "edit" && (
        <form action={editAction} className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-surface-2 p-3">
          <input type="hidden" name="slug" value={project.slug} />
          <input name="name" defaultValue={project.name} aria-label="Name" className={`${inputClass} max-w-xs`} />
          <input name="tags" defaultValue={project.tags.join(", ")} placeholder="tags, comma-separated" aria-label="Tags" className={`${inputClass} max-w-xs`} />
          <label className="flex items-center gap-1.5 text-[12.5px] text-text-2">
            Monthly cap
            <input name="cap" type="number" min="0" defaultValue={project.monthlyCreditCap ?? ""} placeholder="uncapped" aria-label="Monthly credit cap" className={`${inputClass} w-28`} />
            credits
          </label>
          <label className="flex items-center gap-1.5 text-[12.5px] text-text-2">
            Keep sources
            <input name="retention" type="number" min="1" max="3650" defaultValue={project.retentionDays ?? ""} placeholder="forever" aria-label="Retention days" className={`${inputClass} w-24`} />
            days
          </label>
          <label className="flex items-center gap-1.5 text-[12.5px] text-text-2" title="Recall abstains unless some memory is at least this relevant to the question (0 to 1). Empty uses the server default.">
            Recall threshold
            <input name="minRelevance" type="number" min="0" max="1" step="0.01" defaultValue={project.recallMinRelevance ?? ""} placeholder="default" aria-label="Recall relevance threshold" className={`${inputClass} w-24`} />
          </label>
          <label className="flex items-center gap-1.5 text-[12.5px] text-text-2" title="Among comparably relevant memories, facts and gotchas said recently rank higher; their weight halves toward a floor every this many days. Decisions, rules and preferences never decay. 0 turns it off; empty uses the server default (90).">
            Recency half-life
            <input name="halfLife" type="number" min="0" max="36500" step="1" defaultValue={project.recallHalfLifeDays ?? ""} placeholder="90" aria-label="Recall recency half-life in days" className={`${inputClass} w-20`} />
            days
          </label>
          <button type="submit" disabled={editing} className={button("primary")}>{editing ? "Saving…" : "Save"}</button>
          <button type="button" className={button("ghost")} onClick={() => setMode("view")}>Cancel</button>
          {editState.error && <div className="w-full"><FormError>{editState.error}</FormError></div>}
        </form>
      )}

      {mode === "delete" && (
        <form action={deleteAction} className="mt-3 rounded-lg border border-bad/30 bg-bad-soft p-3">
          <input type="hidden" name="slug" value={project.slug} />
          <p className="text-[13px] font-medium text-bad">Delete “{project.name}” and everything in it?</p>
          <p className="mt-1 text-[12.5px] text-text-2">
            {project.memoryCount.toLocaleString()} memories, {project.sourceCount.toLocaleString()} sources, its sessions and its {project.keyCount} API key{project.keyCount === 1 ? "" : "s"}. Plugins using those keys stop working. This cannot be undone.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input name="confirm" placeholder={`type ${project.slug} to confirm`} aria-label="Confirm slug" className={`${inputClass} max-w-xs font-mono`} />
            <button type="submit" disabled={deleting} className={button("danger")}>{deleting ? "Deleting…" : "Delete project"}</button>
            <button type="button" className={button("ghost")} onClick={() => setMode("view")}>Cancel</button>
          </div>
          {deleteState.error && <div className="mt-2"><FormError>{deleteState.error}</FormError></div>}
        </form>
      )}
    </li>
  );
}
