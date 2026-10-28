"use client";
import { useActionState, useState } from "react";
import { Plus, X } from "lucide-react";
import { addCategory, removeCategory, saveMemoryInstructions, type MemoryRulesState } from "@/app/actions";
import { Badge, button, cx, FormError, inputClass } from "@/components/ui";
import { MAX_MEMORY_INSTRUCTIONS, MAX_PROJECT_CATEGORIES, type ProjectCategory } from "@/lib/types";

const PLACEHOLDER = "Also keep: on-call runbook steps.\nNever keep: customer names or ticket text from Zendesk.";

/** The current project's memory instructions: free text added to extraction, with a character counter. */
export function InstructionsForm({ value, canEdit }: { value: string; canEdit: boolean }) {
  const [text, setText] = useState(value);
  const [state, run, pending] = useActionState<MemoryRulesState, FormData>(saveMemoryInstructions, {});
  const over = text.length > MAX_MEMORY_INSTRUCTIONS;
  return (
    <form action={run} className="space-y-2">
      <label htmlFor="memory-instructions" className="block text-[12.5px] font-medium text-text-2">Instructions</label>
      <textarea
        id="memory-instructions" name="instructions" rows={4} value={text} onChange={(e) => setText(e.target.value)}
        placeholder={PLACEHOLDER} disabled={!canEdit} aria-describedby="memory-instructions-help"
        className={cx(inputClass, "min-h-24 resize-y leading-relaxed")}
      />
      <div className="flex flex-wrap items-center gap-3">
        <span className={cx("text-[12px] tabular-nums", over ? "font-medium text-bad" : "text-text-3")}>
          {text.length.toLocaleString()} / {MAX_MEMORY_INSTRUCTIONS.toLocaleString()}
        </span>
        <p id="memory-instructions-help" className="flex-1 text-[12px] text-text-3">
          Narrow what is kept (“never keep …”) or name topics worth keeping (“also keep …”). They cannot switch off the
          built-in rules: secrets, progress of the work, run trivia and descriptions of code are never kept.
        </p>
        {canEdit && (
          <button type="submit" disabled={pending || over || text.trim() === value.trim()} className={button("primary")}>
            {pending ? "Saving…" : "Save"}
          </button>
        )}
      </div>
      {state.error && <FormError>{state.error}</FormError>}
      {state.saved && !pending && text.trim() === value.trim() && <p className="text-[12.5px] text-good">Saved. New conversations are judged with these instructions.</p>}
    </form>
  );
}

/** The current project's own categories: listed with their descriptions; up to five. */
export function CategoriesEditor({ categories, canEdit }: { categories: ProjectCategory[]; canEdit: boolean }) {
  const [state, run, pending] = useActionState<MemoryRulesState, FormData>(addCategory, {});
  const full = categories.length >= MAX_PROJECT_CATEGORIES;
  return (
    <div className="space-y-3">
      <p className="text-[12.5px] font-medium text-text-2">
        Categories of this project <span className="font-normal text-text-3">({categories.length} of {MAX_PROJECT_CATEGORIES}, besides decision, rule, gotcha, fact and preference)</span>
      </p>
      {categories.length === 0 ? (
        <p className="text-[12.5px] text-text-3">None yet. Add one when the built-ins do not fit what your team keeps, such as runbook steps or vendor contacts.</p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {categories.map((c) => (
            <li key={c.slug} className="flex items-start gap-3 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 text-[13px] font-medium text-text">{c.name} <Badge tone="outline">{c.slug}</Badge></p>
                {c.description && <p className="mt-0.5 text-[12.5px] text-text-2">{c.description}</p>}
              </div>
              {canEdit && (
                <form action={removeCategory}>
                  <input type="hidden" name="slug" value={c.slug} />
                  <button className={button("ghost", "sm")} aria-label={`Remove ${c.name}`} title="Remove: memories already filed under it keep it">
                    <X aria-hidden />
                  </button>
                </form>
              )}
            </li>
          ))}
        </ul>
      )}
      {canEdit && !full && (
        <form action={run} className="grid gap-2 sm:grid-cols-[minmax(0,10rem)_minmax(0,9rem)_1fr_auto]">
          <input name="name" placeholder="Name, e.g. Runbook step" aria-label="Category name" maxLength={60} className={inputClass} required />
          <input name="slug" placeholder="slug (optional)" aria-label="Category slug" maxLength={24} pattern="[a-z0-9\-]{2,24}" className={cx(inputClass, "font-mono")} />
          <input name="description" placeholder="What belongs in it (the model reads this)" aria-label="Category description" maxLength={300} className={inputClass} />
          <button type="submit" disabled={pending} className={button("secondary")}><Plus aria-hidden /> {pending ? "Adding…" : "Add"}</button>
          {state.error && <div className="sm:col-span-4"><FormError>{state.error}</FormError></div>}
        </form>
      )}
      {canEdit && full && <p className="text-[12px] text-text-3">The limit is {MAX_PROJECT_CATEGORIES}; remove one to add another.</p>}
    </div>
  );
}
