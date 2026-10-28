"use client";
import { useActionState, useState } from "react";
import { Plus } from "lucide-react";
import { createProject, openProject, type ProjectFormState } from "@/app/actions";
import { CommandBlock, SecretReveal } from "@/components/SecretReveal";
import { button, cx, FormError, inputClass } from "@/components/ui";

export function NewProject({ apiUrl, email }: { apiUrl: string; email: string }) {
  const [open, setOpen] = useState(false);
  const [state, action, pending] = useActionState<ProjectFormState, FormData>(createProject, {});

  if (state.created) {
    const { slug, name, secret } = state.created;
    return (
      <div className="w-full">
        <SecretReveal
          title={`Project “${name}” created`}
          note={`Its first API key is below. Copy it now; it will not be shown again. In Claude Code, in the repository this project is for:`}
          secret={secret}
          copyLabel="Copy key"
          actions={
            <form action={openProject}>
              <input type="hidden" name="slug" value={slug} />
              <button className={button("secondary")}>Open project</button>
            </form>
          }
        >
          <CommandBlock>{`/memvine:connect ${secret} ${email} ${apiUrl} --here`}</CommandBlock>
        </SecretReveal>
      </div>
    );
  }

  if (!open) {
    return <button type="button" className={button("primary")} onClick={() => setOpen(true)}><Plus aria-hidden /> New project</button>;
  }

  return (
    <form action={action} className="w-full space-y-3 rounded-xl border border-border bg-surface p-4 sm:p-5">
      <div>
        <p className="text-[14px] font-medium text-text">New project</p>
        <p className="mt-0.5 text-[13px] text-text-2">A project holds the memory of one app, repository or team. Nothing crosses between projects.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <input name="name" placeholder="Name, e.g. Billing service" aria-label="Project name" className={`${inputClass} max-w-xs`} autoFocus />
        <input name="tags" placeholder="Tags, comma-separated (optional)" aria-label="Tags" className={`${inputClass} max-w-xs`} />
      </div>
      <div>
          <label htmlFor="new-project-description" className="mb-1.5 block text-[12.5px] font-medium text-text-2">What is this project about? (optional)</label>
          <textarea id="new-project-description" name="description" rows={2} maxLength={2000}
                    placeholder="e.g. The billing service: invoices, refunds and its releases"
                    className={`${inputClass} resize-y leading-relaxed`} />
          <p className="mt-1 text-[12px] text-text-3">Memvine judges what is worth keeping against this description.</p>
      </div>
      <label className="flex items-start gap-2 text-[13px] text-text-2">
        <input type="checkbox" name="sample" className="mt-0.5 accent-current" />
        <span>
          <span className="font-medium text-text">Start with sample data</span>
          <span className="block text-[12px] text-text-3">A few sources and memories to try recall on before your agent is connected. Free, and deletable in one click.</span>
        </span>
      </label>
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={pending} className={button("primary")}>{pending ? "Creating…" : "Create project"}</button>
        <button type="button" className={button("ghost")} onClick={() => setOpen(false)}>Cancel</button>
      </div>
      {state.error && <FormError>{state.error}</FormError>}
    </form>
  );
}
